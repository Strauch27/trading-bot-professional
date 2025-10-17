"""
Market Data Service (Drop 4 + Drop Tracking)
Centralized market data management extraction from engine.py

Enhanced with:
- Soft-TTL cache (serve stale while refreshing)
- Candle alignment and partial candle guard
- JSONL audit logging
- RollingWindowManager for drop tracking (long-term solution)
"""

import time
import logging
import math
from typing import Dict, List, Optional, Any, Tuple
from threading import RLock
from dataclasses import dataclass, asdict
from collections import defaultdict, deque
from pathlib import Path
from datetime import datetime, timedelta

# Import new services
from services.cache_ttl import TTLCache, CacheCoordinator
from services.time_utils import (
    filter_closed_candles,
    validate_ohlcv_monotonic,
    validate_ohlcv_values,
    align_since_to_closed,
    TF_MS
)
from services.md_audit import MarketDataAuditor

# Import new pipeline components
from core.price_cache import PriceCache
from core.rolling_windows import RollingWindowManager
from features.engine import compute as compute_features
from market.snapshot_builder import build as build_snapshot
from market.anchor_manager import AnchorManager
from telemetry.jsonl_writer import JsonlWriter

# Import V9_3 persistence (Phase 4)
from persistence.jsonl import RotatingJSONLWriter

# Import from market_data submodule
import sys
import os
_market_data_dir = os.path.join(os.path.dirname(__file__), 'market_data')
if _market_data_dir not in sys.path:
    sys.path.insert(0, _market_data_dir)

from coalescing import get_coalescing_cache  # type: ignore[import-not-found]
from rate_limit import get_rate_limiter  # type: ignore[import-not-found]

logger = logging.getLogger(__name__)


@dataclass
class TickerData:
    """Ticker data with metadata"""
    symbol: str
    last: float
    bid: float
    ask: float
    volume: float
    timestamp: int
    high_24h: Optional[float] = None
    low_24h: Optional[float] = None
    change_24h: Optional[float] = None
    change_percent_24h: Optional[float] = None

    @property
    def spread_bps(self) -> float:
        """Spread in basis points"""
        if self.bid > 0:
            return ((self.ask - self.bid) / self.bid) * 10000
        return 0.0

    @property
    def mid_price(self) -> float:
        """Mid price between bid and ask"""
        return (self.bid + self.ask) / 2


@dataclass
class OHLCVBar:
    """OHLCV bar data"""
    timestamp: int
    open: float
    high: float
    low: float
    close: float
    volume: float

    @property
    def body_size(self) -> float:
        """Size of the candle body"""
        return abs(self.close - self.open)

    @property
    def upper_shadow(self) -> float:
        """Upper shadow length"""
        return self.high - max(self.open, self.close)

    @property
    def lower_shadow(self) -> float:
        """Lower shadow length"""
        return min(self.open, self.close) - self.low

    @property
    def is_bullish(self) -> bool:
        """True if close > open"""
        return self.close > self.open


class TickerCache:
    """
    Thread-safe ticker cache with Soft-TTL support.

    Enhanced Features:
    - Soft-TTL: Serve stale data while triggering refresh
    - Hard-TTL: Absolute expiration
    - LRU eviction
    - Cache status tracking (HIT/STALE/MISS)
    """

    def __init__(self, default_ttl: float = 5.0, soft_ttl: float = 2.0, max_size: int = 1000):
        """
        Initialize ticker cache.

        Args:
            default_ttl: Hard TTL in seconds (absolute expiration)
            soft_ttl: Soft TTL in seconds (stale threshold)
            max_size: Maximum cache entries
        """
        self.default_ttl = default_ttl
        self.soft_ttl = soft_ttl
        self.max_size = max_size

        # Use new TTLCache internally
        self._cache = TTLCache(max_items=max_size)
        self._lock = RLock()

    def get_ticker(self, symbol: str) -> Optional[Tuple[TickerData, str]]:
        """
        Get ticker from cache with status.

        Returns:
            Tuple of (ticker, status) where status is HIT/STALE/MISS,
            or None if cache miss
        """
        with self._lock:
            result = self._cache.get(symbol)
            if not result:
                return None

            ticker_data, status, meta = result
            return (ticker_data, status)

    def get_ticker_simple(self, symbol: str) -> Optional[TickerData]:
        """
        Get ticker from cache (backwards compatible).

        Returns only fresh tickers, treats STALE as MISS.
        """
        result = self.get_ticker(symbol)
        if not result:
            return None

        ticker, status = result
        if status == "HIT":
            return ticker
        return None

    def store_ticker(self, ticker: TickerData, ttl: Optional[float] = None,
                     soft_ttl: Optional[float] = None) -> None:
        """
        Store ticker in cache with dual TTL.

        Args:
            ticker: Ticker data to cache
            ttl: Hard TTL (defaults to default_ttl)
            soft_ttl: Soft TTL (defaults to self.soft_ttl)
        """
        with self._lock:
            if ttl is None:
                ttl = self.default_ttl
            if soft_ttl is None:
                soft_ttl = self.soft_ttl

            # Ensure soft_ttl <= ttl
            if soft_ttl > ttl:
                soft_ttl = ttl * 0.6  # Default to 60% of hard TTL

            self._cache.set(
                key=ticker.symbol,
                value=ticker,
                ttl_s=ttl,
                soft_ttl_s=soft_ttl,
                meta={"stored_at": time.time(), "symbol": ticker.symbol}
            )

    def cleanup_expired(self) -> int:
        """Remove expired entries"""
        return self._cache.cleanup_expired()

    def get_statistics(self) -> Dict[str, Any]:
        """Get cache statistics"""
        return self._cache.get_statistics()

    def clear(self) -> None:
        """Clear all cached data"""
        self._cache.clear()


class OHLCVHistory:
    """OHLCV data storage and management"""

    def __init__(self, max_bars_per_symbol: int = 1000):
        self.max_bars_per_symbol = max_bars_per_symbol
        self._data: Dict[str, Dict[str, deque]] = defaultdict(lambda: defaultdict(deque))
        self._lock = RLock()

    def add_bars(self, symbol: str, timeframe: str, bars: List[OHLCVBar]) -> None:
        """Add OHLCV bars for symbol and timeframe"""
        with self._lock:
            symbol_data = self._data[symbol][timeframe]

            for bar in bars:
                # Check if bar already exists (same timestamp)
                existing_timestamps = {existing_bar.timestamp for existing_bar in symbol_data}
                if bar.timestamp not in existing_timestamps:
                    # Insert in chronological order
                    inserted = False
                    for i, existing_bar in enumerate(symbol_data):
                        if bar.timestamp < existing_bar.timestamp:
                            symbol_data.insert(i, bar)
                            inserted = True
                            break

                    if not inserted:
                        symbol_data.append(bar)

            # Limit size
            while len(symbol_data) > self.max_bars_per_symbol:
                symbol_data.popleft()

    def get_bars(self, symbol: str, timeframe: str, limit: Optional[int] = None) -> List[OHLCVBar]:
        """Get OHLCV bars for symbol and timeframe"""
        with self._lock:
            bars = list(self._data[symbol][timeframe])
            if limit:
                return bars[-limit:]
            return bars

    def get_latest_bar(self, symbol: str, timeframe: str) -> Optional[OHLCVBar]:
        """Get latest OHLCV bar"""
        with self._lock:
            bars = self._data[symbol][timeframe]
            return bars[-1] if bars else None

    def get_price_range(self, symbol: str, timeframe: str, lookback_periods: int) -> Tuple[float, float]:
        """Get high/low range over lookback periods"""
        with self._lock:
            bars = list(self._data[symbol][timeframe])
            if not bars:
                return 0.0, 0.0

            recent_bars = bars[-lookback_periods:] if lookback_periods > 0 else bars
            if not recent_bars:
                return 0.0, 0.0

            high = max(bar.high for bar in recent_bars)
            low = min(bar.low for bar in recent_bars)
            return high, low

    def calculate_atr(self, symbol: str, timeframe: str, period: int = 14) -> Optional[float]:
        """Calculate Average True Range"""
        with self._lock:
            bars = list(self._data[symbol][timeframe])
            if len(bars) < period + 1:
                return None

            recent_bars = bars[-(period + 1):]
            true_ranges = []

            for i in range(1, len(recent_bars)):
                prev_bar = recent_bars[i - 1]
                curr_bar = recent_bars[i]

                tr = max(
                    curr_bar.high - curr_bar.low,
                    abs(curr_bar.high - prev_bar.close),
                    abs(curr_bar.low - prev_bar.close)
                )
                true_ranges.append(tr)

            return sum(true_ranges) / len(true_ranges) if true_ranges else None


class MarketDataProvider:
    """
    Main market data service with enhanced caching and audit logging.

    Features:
    - Soft-TTL ticker cache (serve stale while refreshing)
    - Partial candle guard for OHLCV
    - JSONL audit logging
    - Latency tracking
    - RollingWindowManager for drop tracking (long-term solution)
    """

    def __init__(
        self,
        exchange_adapter,
        ticker_cache_ttl: float = 5.0,
        ticker_soft_ttl: float = 2.0,
        max_cache_size: int = 1000,
        max_bars_per_symbol: int = 1000,
        audit_log_dir: Optional[Path] = None,
        enable_audit: bool = False,
        enable_coalescing: bool = True,
        enable_rate_limiting: bool = True,
        enable_drop_tracking: bool = True,
        event_bus = None
    ):
        """
        Initialize market data provider.

        Args:
            exchange_adapter: Exchange adapter for fetching data
            ticker_cache_ttl: Hard TTL for ticker cache
            ticker_soft_ttl: Soft TTL for ticker cache
            max_cache_size: Maximum cache size
            max_bars_per_symbol: Maximum OHLCV bars per symbol
            audit_log_dir: Directory for audit logs
            enable_audit: Enable audit logging
            enable_coalescing: Enable request coalescing (deduplication)
            enable_rate_limiting: Enable API rate limiting (token bucket)
            enable_drop_tracking: Enable RollingWindowManager for drop tracking
            event_bus: Event bus for publishing drop snapshots (optional)
        """
        self.exchange_adapter = exchange_adapter
        self.ticker_cache = TickerCache(ticker_cache_ttl, ticker_soft_ttl, max_cache_size)
        self.ohlcv_history = OHLCVHistory(max_bars_per_symbol)
        self._lock = RLock()
        self._statistics = {
            'ticker_requests': 0,
            'ticker_cache_hits': 0,
            'ticker_stale_hits': 0,
            'ohlcv_requests': 0,
            'ohlcv_bars_stored': 0,
            'ohlcv_partial_candles_removed': 0,
            'errors': 0,
            'drop_snapshots_emitted': 0
        }

        # V9_3: Per-symbol retry state tracking (Phase 3)
        # {symbol: {"attempts": int, "next_retry": float, "last_error": str}}
        self._retry_state: Dict[str, Dict[str, Any]] = {}

        # Request coalescing (prevents duplicate API calls)
        self.enable_coalescing = enable_coalescing
        self.coalescing_cache = get_coalescing_cache() if enable_coalescing else None

        # Rate limiting (token bucket for API limits)
        self.enable_rate_limiting = enable_rate_limiting
        self.rate_limiter = get_rate_limiter() if enable_rate_limiting else None

        # Audit logger (optional)
        self.auditor = None
        if enable_audit and audit_log_dir:
            self.auditor = MarketDataAuditor(
                log_dir=Path(audit_log_dir),
                enabled=True
            )

        # New Pipeline Components
        self.enable_drop_tracking = enable_drop_tracking
        # Use get_event_bus() as fallback if no event_bus provided
        from core.events import get_event_bus as _get_event_bus
        self.event_bus = event_bus or _get_event_bus()
        self.price_cache = None
        self.rw_manager = None
        self.anchor_manager = None
        self.telemetry = None

        if self.enable_drop_tracking:
            # Import config for pipeline settings
            import config

            lookback_s = getattr(config, 'WINDOW_LOOKBACK_S', 300)
            persist = getattr(config, 'PERSIST_WINDOWS', True)
            base_path = getattr(config, 'WINDOW_STORE', 'state/drop_windows')

            # Store config values as instance attributes for _loop()
            self.lookback_s = lookback_s
            self.persist = persist
            self.base_path = base_path

            # Initialize pipeline components
            self.price_cache = PriceCache(seconds=lookback_s)
            self.rw_manager = RollingWindowManager(
                lookback_s=lookback_s,
                persist=persist,
                base_path=base_path
            )
            self.anchor_manager = AnchorManager(base_path=f"{base_path}/anchors")
            self.telemetry = JsonlWriter(base="telemetry")

            # V9_3: 4-Stream JSONL Writers (Phase 4)
            self.tick_writers: Dict[str, RotatingJSONLWriter] = {}  # Per-symbol tick persistence
            self.snapshot_writer: Optional[RotatingJSONLWriter] = None
            self.windows_writer: Optional[RotatingJSONLWriter] = None
            self.anchors_writer: Optional[RotatingJSONLWriter] = None

            # Initialize writers based on feature flags
            persist_ticks = getattr(config, 'PERSIST_TICKS', True)
            persist_snapshots = getattr(config, 'PERSIST_SNAPSHOTS', True)
            max_file_mb = getattr(config, 'MAX_FILE_MB', 50)

            if persist and getattr(config, 'FEATURE_PERSIST_STREAMS', True):
                # Snapshot stream (all symbols)
                if persist_snapshots:
                    self.snapshot_writer = RotatingJSONLWriter(
                        base_dir=f"{base_path}/snapshots",
                        prefix="snapshots",
                        max_mb=max_file_mb
                    )
                    logger.debug("Snapshot stream writer initialized")

                # Windows stream
                self.windows_writer = RotatingJSONLWriter(
                    base_dir=f"{base_path}/windows",
                    prefix="windows",
                    max_mb=max_file_mb
                )
                logger.debug("Windows stream writer initialized")

                # Anchors stream
                self.anchors_writer = RotatingJSONLWriter(
                    base_dir=f"{base_path}/anchors",
                    prefix="anchors",
                    max_mb=max_file_mb
                )
                logger.debug("Anchors stream writer initialized")

                logger.info(
                    f"4-Stream JSONL persistence enabled: "
                    f"ticks={persist_ticks}, snapshots={persist_snapshots}, "
                    f"windows=True, anchors=True (max_mb={max_file_mb})"
                )

            logger.info(
                f"Snapshot pipeline enabled: lookback={lookback_s}s, "
                f"persist={persist}, base_path={base_path}"
            )

    def get_ticker(self, symbol: str, use_cache: bool = True) -> Optional[TickerData]:
        """
        Get ticker data with soft-TTL caching.

        Supports serving stale data (within soft-TTL window) while
        still returning fresh data immediately.

        Args:
            symbol: Trading pair symbol
            use_cache: Enable cache lookup

        Returns:
            TickerData or None
        """
        from services.shutdown_coordinator import get_shutdown_coordinator
        co = get_shutdown_coordinator()

        co.beat(f"get_ticker_enter:{symbol}")
        start_time = time.time()

        try:
            with self._lock:
                self._statistics['ticker_requests'] += 1
                cache_status = "MISS"

                # Try cache first
                if use_cache:
                    cache_result = self.ticker_cache.get_ticker(symbol)
                    if cache_result:
                        ticker, status = cache_result
                        cache_status = status

                        if status == "HIT":
                            # Fresh cache hit
                            co.beat(f"get_ticker_cache_hit:{symbol}")
                            self._statistics['ticker_cache_hits'] += 1
                            latency_ms = (time.time() - start_time) * 1000

                            # Audit log
                            if self.auditor:
                                self.auditor.log_ticker(
                                    symbol=symbol,
                                    status="HIT",
                                    latency_ms=latency_ms,
                                    source="cache"
                                )

                            return ticker

                        elif status == "STALE":
                            # Stale hit - serve stale, but still fetch fresh in background
                            # (For now, we'll just serve stale immediately and not refresh)
                            co.beat(f"get_ticker_stale_hit:{symbol}")
                            self._statistics['ticker_stale_hits'] += 1
                            latency_ms = (time.time() - start_time) * 1000

                            # Audit log
                            if self.auditor:
                                self.auditor.log_ticker(
                                    symbol=symbol,
                                    status="STALE",
                                    latency_ms=latency_ms,
                                    source="cache",
                                    meta={"age_ms": int((time.time() - ticker.timestamp / 1000) * 1000)}
                                )

                            # Serve stale data immediately
                            return ticker

                # Cache miss - fetch from exchange
                try:
                    co.beat(f"get_ticker_exchange_call:{symbol}")

                    # Define fetch function with rate limiting
                    def _fetch_ticker():
                        # Apply rate limiting if enabled
                        if self.enable_rate_limiting and self.rate_limiter:
                            with self.rate_limiter.acquire_context(endpoint_type="public"):
                                return self.exchange_adapter.fetch_ticker(symbol)
                        else:
                            return self.exchange_adapter.fetch_ticker(symbol)

                    # Use request coalescing to deduplicate parallel requests
                    if self.enable_coalescing and self.coalescing_cache:
                        # Multiple threads requesting same symbol will share one API call
                        raw_ticker = self.coalescing_cache.get_or_fetch(
                            key=f"ticker:{symbol}",
                            fetch_fn=_fetch_ticker,
                            timeout_ms=5000
                        )
                    else:
                        # Direct fetch
                        raw_ticker = _fetch_ticker()

                    ticker = TickerData(
                        symbol=symbol,
                        last=raw_ticker['last'],
                        bid=raw_ticker['bid'],
                        ask=raw_ticker['ask'],
                        volume=raw_ticker['baseVolume'] or 0,
                        timestamp=raw_ticker['timestamp'],
                        high_24h=raw_ticker.get('high'),
                        low_24h=raw_ticker.get('low'),
                        change_24h=raw_ticker.get('change'),
                        change_percent_24h=raw_ticker.get('percentage')
                    )

                    # persist to soft-TTL cache so snapshot emitter sees it
                    try:
                        self.ticker_cache.store_ticker(ticker)
                    except Exception:
                        logger.debug(f"ticker_cache.store_ticker failed for {symbol}")

                    latency_ms = (time.time() - start_time) * 1000

                    # Audit log
                    if self.auditor:
                        self.auditor.log_ticker(
                            symbol=symbol,
                            status=cache_status,
                            latency_ms=latency_ms,
                            source="exchange"
                        )

                    co.beat(f"get_ticker_success:{symbol}")

                    # Nur bei kritischen Symbolen oder nach längerer Zeit loggen
                    if symbol in ["BTC/USDT"] or self._statistics['ticker_requests'] % 50 == 0:
                        logger.info(f"HEARTBEAT - Ticker fetched: {symbol}",
                                   extra={"event_type": "HEARTBEAT"})
                    return ticker

                except Exception as e:
                    self._statistics['errors'] += 1
                    latency_ms = (time.time() - start_time) * 1000

                    # Audit log error
                    if self.auditor:
                        self.auditor.log_error(
                            route="ticker",
                            symbol=symbol,
                            error_type=type(e).__name__,
                            error_msg=str(e)
                        )

                    co.beat(f"get_ticker_error:{symbol}")
                    logger.error(f"Error fetching ticker for {symbol}: {e}")
                    logger.info(f"HEARTBEAT - Ticker fetch failed but handled: {symbol}",
                               extra={"event_type": "HEARTBEAT"})
                    return None
        finally:
            co.beat(f"get_ticker_exit:{symbol}")

    def get_price(self, symbol: str, prefer_cache: bool = True) -> Optional[float]:
        """
        Get current price for symbol with cache preference.

        Supports soft-TTL: serves stale prices when fresh data unavailable.
        """
        # 1) Cache bevorzugen (accept stale data)
        if prefer_cache:
            cache_result = self.ticker_cache.get_ticker(symbol)
            if cache_result:
                cached_ticker, status = cache_result
                # Accept both HIT and STALE for price retrieval
                p = float(cached_ticker.last or 0.0)
                if p > 0:
                    return p
                # Fallback auf ask/bid, falls last unbrauchbar ist
                if float(cached_ticker.ask or 0.0) > 0:
                    return float(cached_ticker.ask)
                if float(cached_ticker.bid or 0.0) > 0:
                    return float(cached_ticker.bid)

        # 2) Live holen
        ticker = self.get_ticker(symbol, use_cache=True)
        if not ticker:
            return None
        p = float(ticker.last or 0.0)
        if p > 0:
            return p
        if float(ticker.ask or 0.0) > 0:
            return float(ticker.ask)
        if float(ticker.bid or 0.0) > 0:
            return float(ticker.bid)
        return None

    def get_spread(self, symbol: str) -> Optional[float]:
        """Get bid-ask spread in basis points"""
        ticker = self.get_ticker(symbol)
        return ticker.spread_bps if ticker else None

    def _validate_ticker(self, ticker: Optional[TickerData], symbol: str, now: float) -> Tuple[bool, Optional[str]]:
        """
        Validate ticker data quality (V9_3 Phase 3).

        Guards:
        1. Ticker exists
        2. last > 0
        3. last is not NaN
        4. Timestamp fresh (≤ 2 × POLL_MS)

        Args:
            ticker: Ticker data to validate
            symbol: Trading symbol
            now: Current timestamp

        Returns:
            Tuple of (is_valid, error_reason)
        """
        if not ticker:
            return False, "ticker_none"

        # Guard 1: last > 0
        if not ticker.last or ticker.last <= 0:
            return False, "invalid_last"

        # Guard 2: last is not NaN
        if math.isnan(ticker.last):
            return False, "nan_price"

        # Guard 3: Timestamp freshness
        import config
        poll_ms = getattr(config, "POLL_MS", 300)
        max_age_ms = 2 * poll_ms

        if ticker.timestamp:
            age_ms = (now - ticker.timestamp / 1000.0) * 1000
            if age_ms > max_age_ms:
                return False, "stale_timestamp"

        return True, None

    def _calculate_spread(self, ticker: TickerData) -> Tuple[float, float]:
        """
        Calculate bid-ask spread with fallback (V9_3 Phase 3).

        Args:
            ticker: Ticker data

        Returns:
            Tuple of (spread_bps, spread_pct)
            Falls back to (0.0, 0.0) if bid/ask unavailable
        """
        if not ticker.bid or not ticker.ask or ticker.bid <= 0 or ticker.ask <= 0:
            # Fallback: No spread (use last price)
            return 0.0, 0.0

        mid = (ticker.bid + ticker.ask) / 2.0
        if mid <= 0:
            return 0.0, 0.0

        spread = ticker.ask - ticker.bid
        spread_bps = (spread / mid) * 10000.0
        spread_pct = (spread / mid) * 100.0

        return spread_bps, spread_pct

    def get_ticker_with_retry(
        self,
        symbol: str,
        max_attempts: int = 5,
        base_backoff_s: float = 1.0
    ) -> Optional[TickerData]:
        """
        Get ticker with exponential backoff retry (V9_3 Phase 3).

        Retry strategy:
        - Attempt 1: Immediate
        - Attempt 2: 1s backoff
        - Attempt 3: 2s backoff
        - Attempt 4: 4s backoff
        - Attempt 5: 8s backoff
        - Max backoff: 16s

        Args:
            symbol: Trading symbol
            max_attempts: Maximum retry attempts (default: 5)
            base_backoff_s: Base backoff in seconds (default: 1.0)

        Returns:
            TickerData or None if all attempts failed
        """
        import config
        now = time.time()

        # Check if symbol is in backoff period
        with self._lock:
            if symbol in self._retry_state:
                retry_info = self._retry_state[symbol]
                next_retry = retry_info.get("next_retry", 0.0)

                if now < next_retry:
                    # Still in backoff period - skip
                    logger.debug(f"Symbol {symbol} in backoff period (retry in {next_retry - now:.1f}s)")
                    return None

        # Attempt fetch with retry logic
        for attempt in range(max_attempts):
            try:
                ticker = self.get_ticker(symbol, use_cache=False)

                # Validate ticker
                is_valid, error = self._validate_ticker(ticker, symbol, now)

                if is_valid and ticker:
                    # Success - clear retry state
                    with self._lock:
                        if symbol in self._retry_state:
                            del self._retry_state[symbol]

                    # Feature flag check
                    if not getattr(config, "FEATURE_RETRY_BACKOFF", True):
                        return ticker

                    return ticker

                # Validation failed - log and retry
                if attempt < max_attempts - 1:
                    backoff = base_backoff_s * (2 ** attempt)
                    backoff = min(backoff, 16.0)  # Cap at 16s

                    logger.debug(
                        f"Ticker validation failed for {symbol}: {error}, "
                        f"retry {attempt + 1}/{max_attempts} in {backoff:.1f}s"
                    )

                    time.sleep(backoff)
                else:
                    # Final attempt failed
                    logger.warning(
                        f"Ticker validation failed for {symbol} after {max_attempts} attempts: {error}",
                        extra={
                            'event_type': 'TICKER_VALIDATION_FAILED',
                            'symbol': symbol,
                            'attempts': max_attempts,
                            'error': error or "unknown"
                        }
                    )

                    # Update retry state
                    with self._lock:
                        next_backoff = base_backoff_s * (2 ** max_attempts)
                        next_backoff = min(next_backoff, 16.0)

                        self._retry_state[symbol] = {
                            "attempts": max_attempts,
                            "next_retry": now + next_backoff,
                            "last_error": error or "unknown"
                        }

                    return None

            except Exception as e:
                if attempt < max_attempts - 1:
                    backoff = base_backoff_s * (2 ** attempt)
                    backoff = min(backoff, 16.0)

                    logger.debug(
                        f"Ticker fetch error for {symbol}: {e}, "
                        f"retry {attempt + 1}/{max_attempts} in {backoff:.1f}s"
                    )

                    time.sleep(backoff)
                else:
                    logger.error(
                        f"Ticker fetch failed for {symbol} after {max_attempts} attempts: {e}",
                        extra={
                            'event_type': 'TICKER_FETCH_FAILED',
                            'symbol': symbol,
                            'attempts': max_attempts,
                            'error': str(e)
                        }
                    )

                    # Update retry state
                    with self._lock:
                        next_backoff = base_backoff_s * (2 ** max_attempts)
                        next_backoff = min(next_backoff, 16.0)

                        self._retry_state[symbol] = {
                            "attempts": max_attempts,
                            "next_retry": now + next_backoff,
                            "last_error": str(e)
                        }

                    return None

        return None

    def get_top5_depth(self, symbol: str, levels: int = 5) -> Tuple[float, float]:
        """
        Get cumulative notional depth for top N levels (Phase 7).

        Returns:
            Tuple of (bid_notional_usd, ask_notional_usd)
            Returns (0.0, 0.0) on error.

        Usage:
            bid_depth, ask_depth = provider.get_top5_depth("BTC/USDT", levels=5)
            if bid_depth < DEPTH_MIN_NOTIONAL_USD:
                # Skip buy - insufficient depth
        """
        try:
            # Fetch order book with rate limiting
            if self.enable_rate_limiting and self.rate_limiter:
                with self.rate_limiter.acquire_context(endpoint_type="public"):
                    orderbook = self.exchange_adapter.fetch_order_book(symbol, limit=levels)
            else:
                orderbook = self.exchange_adapter.fetch_order_book(symbol, limit=levels)

            # Calculate bid depth (cumulative notional)
            bid_depth = 0.0
            for bid_price, bid_qty in orderbook.get('bids', [])[:levels]:
                bid_depth += float(bid_price) * float(bid_qty)

            # Calculate ask depth (cumulative notional)
            ask_depth = 0.0
            for ask_price, ask_qty in orderbook.get('asks', [])[:levels]:
                ask_depth += float(ask_price) * float(ask_qty)

            return bid_depth, ask_depth

        except Exception as e:
            logger.error(f"Error fetching depth for {symbol}: {e}")
            if self.auditor:
                self.auditor.log_error(
                    route="depth",
                    symbol=symbol,
                    error_type=type(e).__name__,
                    error_msg=str(e)
                )
            return 0.0, 0.0

    def fetch_ohlcv(
        self,
        symbol: str,
        timeframe: str = '1m',
        limit: int = 100,
        store: bool = True,
        filter_partial: bool = True
    ) -> List[OHLCVBar]:
        """
        Fetch OHLCV data with partial candle guard.

        Args:
            symbol: Trading pair symbol
            timeframe: Timeframe (1m, 5m, 1h, etc.)
            limit: Number of candles to fetch
            store: Store in history
            filter_partial: Remove forming (partial) candle

        Returns:
            List of OHLCV bars (closed candles only if filter_partial=True)
        """
        with self._lock:
            self._statistics['ohlcv_requests'] += 1
            start_time = time.time()

            try:
                # Fetch from exchange with rate limiting
                if self.enable_rate_limiting and self.rate_limiter:
                    with self.rate_limiter.acquire_context(endpoint_type="public"):
                        raw_ohlcv = self.exchange_adapter.fetch_ohlcv(symbol, timeframe, limit=limit)
                else:
                    raw_ohlcv = self.exchange_adapter.fetch_ohlcv(symbol, timeframe, limit=limit)

                original_count = len(raw_ohlcv)

                # Filter partial candles BEFORE parsing
                if filter_partial and raw_ohlcv:
                    now_ms = int(time.time() * 1000)
                    raw_ohlcv = filter_closed_candles(raw_ohlcv, timeframe, now_ms)

                    removed_count = original_count - len(raw_ohlcv)
                    if removed_count > 0:
                        self._statistics['ohlcv_partial_candles_removed'] += removed_count
                        logger.debug(f"Removed {removed_count} partial candle(s) for {symbol} {timeframe}")

                # Validate OHLCV data quality
                if raw_ohlcv:
                    try:
                        validate_ohlcv_monotonic(raw_ohlcv)
                        validate_ohlcv_values(raw_ohlcv)
                    except ValueError as ve:
                        logger.warning(f"OHLCV validation failed for {symbol} {timeframe}: {ve}")
                        # Continue anyway, but log the issue
                        if self.auditor:
                            self.auditor.log_error(
                                route="ohlcv",
                                symbol=symbol,
                                error_type="ValidationError",
                                error_msg=str(ve),
                                meta={"timeframe": timeframe}
                            )

                # Parse to OHLCVBar objects
                bars = []
                for ohlcv in raw_ohlcv:
                    bar = OHLCVBar(
                        timestamp=ohlcv[0],
                        open=ohlcv[1],
                        high=ohlcv[2],
                        low=ohlcv[3],
                        close=ohlcv[4],
                        volume=ohlcv[5]
                    )
                    bars.append(bar)

                # Store in history
                if store and bars:
                    self.ohlcv_history.add_bars(symbol, timeframe, bars)
                    self._statistics['ohlcv_bars_stored'] += len(bars)

                latency_ms = (time.time() - start_time) * 1000

                # Audit log
                if self.auditor:
                    self.auditor.log_ohlcv(
                        symbol=symbol,
                        timeframe=timeframe,
                        status="HIT",
                        latency_ms=latency_ms,
                        source="exchange",
                        candles_count=len(bars),
                        meta={
                            "original_count": original_count,
                            "filtered_count": len(bars),
                            "partial_removed": original_count - len(bars)
                        }
                    )

                return bars

            except Exception as e:
                self._statistics['errors'] += 1
                latency_ms = (time.time() - start_time) * 1000

                # Audit log error
                if self.auditor:
                    self.auditor.log_error(
                        route="ohlcv",
                        symbol=symbol,
                        error_type=type(e).__name__,
                        error_msg=str(e),
                        meta={"timeframe": timeframe}
                    )

                logger.error(f"Error fetching OHLCV for {symbol}: {e}")
                return []

    def get_historical_bars(self, symbol: str, timeframe: str = '1m',
                          limit: Optional[int] = None) -> List[OHLCVBar]:
        """Get stored historical bars"""
        return self.ohlcv_history.get_bars(symbol, timeframe, limit)

    def get_latest_bar(self, symbol: str, timeframe: str = '1m') -> Optional[OHLCVBar]:
        """Get latest OHLCV bar"""
        return self.ohlcv_history.get_latest_bar(symbol, timeframe)

    def get_price_range(self, symbol: str, timeframe: str = '1m',
                       lookback_periods: int = 20) -> Tuple[float, float]:
        """Get high/low range over lookback periods"""
        return self.ohlcv_history.get_price_range(symbol, timeframe, lookback_periods)

    def calculate_atr(self, symbol: str, timeframe: str = '1m', period: int = 14) -> Optional[float]:
        """Calculate Average True Range"""
        return self.ohlcv_history.calculate_atr(symbol, timeframe, period)

    def backfill_history(self, symbols: List[str], timeframe: str = '1m',
                        minutes: int = 120) -> Dict[str, int]:
        """Backfill historical data for symbols"""
        results = {}

        for symbol in symbols:
            try:
                bars = self.fetch_ohlcv(symbol, timeframe, limit=minutes, store=True)
                results[symbol] = len(bars)
                logger.info(f"Backfilled {len(bars)} bars for {symbol}")
            except Exception as e:
                logger.error(f"Backfill failed for {symbol}: {e}")
                results[symbol] = 0

        return results

    def _warm_start(self, symbols: List[str], max_ticks: int = 300) -> Dict[str, int]:
        """
        Warm-start from persisted ticks (V9_3 Phase 5).

        Loads last N ticks per symbol from JSONL and replays them into:
        - PriceCache
        - RollingWindows
        - AnchorManager

        This restores the market data state after restart.

        Args:
            symbols: List of symbols to warm-start
            max_ticks: Maximum ticks to load per symbol (default: 300)

        Returns:
            Dict mapping symbols to number of ticks loaded
        """
        import config
        from persistence.jsonl import read_jsonl_tail

        if not getattr(config, 'FEATURE_WARMSTART_TICKS', True):
            logger.info("Warm-start disabled by feature flag")
            return {}

        logger.info(f"Starting warm-start for {len(symbols)} symbols (max_ticks={max_ticks})")

        results = {}
        total_ticks = 0

        # Load persisted rolling windows first (V9_3: symbol-by-symbol)
        if self.rw_manager:
            for symbol in symbols:
                try:
                    self.rw_manager.load(symbol)
                except Exception as e:
                    logger.debug(f"Failed to load rolling window for {symbol}: {e}")

        # Load last N ticks from JSONL per symbol
        for symbol in symbols:
            try:
                # Build tick file path
                symbol_safe = symbol.replace('/', '_')
                tick_file = Path(f"{self.base_path}/ticks/tick_{symbol_safe}")

                # Find latest tick file (today or yesterday)
                today = datetime.now().strftime("%Y%m%d")
                yesterday = (datetime.now() - timedelta(days=1)).strftime("%Y%m%d")

                tick_path = None
                for date_str in [today, yesterday]:
                    candidate = Path(f"{tick_file}_{date_str}.jsonl")
                    if candidate.exists():
                        tick_path = candidate
                        break

                if not tick_path or not tick_path.exists():
                    logger.debug(f"No tick file found for {symbol}")
                    results[symbol] = 0
                    continue

                # Load last N ticks
                ticks = read_jsonl_tail(str(tick_path), n=max_ticks)

                if not ticks:
                    results[symbol] = 0
                    continue

                # Replay ticks into PriceCache and RollingWindows
                for tick in ticks:
                    ts = tick.get('ts')
                    last = tick.get('last')

                    if not ts or not last or last <= 0:
                        continue

                    # Update PriceCache
                    if self.price_cache:
                        self.price_cache.update({symbol: last}, ts)

                    # Update RollingWindows
                    if self.rw_manager:
                        self.rw_manager.update(symbol, ts, last)

                    # Update AnchorManager
                    if self.anchor_manager:
                        self.anchor_manager.note_price(symbol, last, ts)

                results[symbol] = len(ticks)
                total_ticks += len(ticks)

                logger.debug(f"Warm-start: {symbol} loaded {len(ticks)} ticks")

            except Exception as e:
                logger.warning(f"Warm-start failed for {symbol}: {e}")
                results[symbol] = 0

        success_symbols = sum(1 for v in results.values() if v > 0)

        logger.info(
            f"Warm-start complete: {len(symbols)} symbols, "
            f"{total_ticks} total ticks, "
            f"{success_symbols} successful",
            extra={
                'event_type': 'WARMSTART_COMPLETE',
                'symbols_total': len(symbols),
                'symbols_success': success_symbols,
                'ticks_loaded': total_ticks,
                'max_ticks_per_symbol': max_ticks
            }
        )

        return results

    def update_market_data(self, symbols: List[str]) -> Dict[str, bool]:
        """
        Update market data for multiple symbols with new snapshot pipeline.

        Pipeline flow:
        1. Fetch tickers (normalize)
        2. Update PriceCache
        3. Update RollingWindows
        4. Compute features
        5. Build MarketSnapshots
        6. Publish via EventBus
        7. Log to JSONL
        """
        from services.shutdown_coordinator import get_shutdown_coordinator
        co = get_shutdown_coordinator()

        co.beat("md_update_start")
        now = time.time()
        results = {}

        # Check if new pipeline is enabled
        import config
        use_new_pipeline = getattr(config, 'USE_NEW_PIPELINE', False)

        if not use_new_pipeline or not self.enable_drop_tracking:
            # Fallback to legacy behavior
            for symbol in symbols:
                try:
                    ticker = self.get_ticker(symbol, use_cache=False)
                    results[symbol] = ticker is not None

                    if ticker and self.enable_drop_tracking and self.rw_manager:
                        try:
                            self.rw_manager.update(symbol, now, ticker.last)
                        except Exception as e:
                            logger.debug(f"Failed to update rolling window for {symbol}: {e}")

                except Exception as e:
                    logger.error(f"Market data update failed for {symbol}: {e}")
                    results[symbol] = False

            co.beat("md_update_end")
            return results

        # New Pipeline: Fetch all tickers first
        tickers = {}
        persist_ticks = getattr(config, 'PERSIST_TICKS', True)

        for symbol in symbols:
            try:
                ticker = self.get_ticker(symbol, use_cache=False)
                if ticker and ticker.last > 0:
                    tickers[symbol] = ticker
                    results[symbol] = True

                    # V9_3 Phase 4: Persist tick data per symbol
                    if persist_ticks and getattr(config, 'FEATURE_PERSIST_STREAMS', True):
                        try:
                            # Get or create tick writer for this symbol
                            if symbol not in self.tick_writers:
                                symbol_safe = symbol.replace('/', '_')
                                self.tick_writers[symbol] = RotatingJSONLWriter(
                                    base_dir=f"{self.base_path}/ticks",
                                    prefix=f"tick_{symbol_safe}",
                                    max_mb=getattr(config, 'MAX_FILE_MB', 50)
                                )

                            # Write tick
                            tick_obj = {
                                "ts": now,
                                "symbol": symbol,
                                "last": ticker.last,
                                "bid": ticker.bid,
                                "ask": ticker.ask,
                                "volume": ticker.volume,
                                "spread_bps": ticker.spread_bps
                            }
                            self.tick_writers[symbol].append(tick_obj)
                        except Exception as e:
                            logger.debug(f"Failed to persist tick for {symbol}: {e}")
                else:
                    results[symbol] = False
            except Exception as e:
                logger.debug(f"Failed to fetch ticker for {symbol}: {e}")
                results[symbol] = False

        # Update PriceCache with all current prices
        if self.price_cache:
            price_dict = {sym: t.last for sym, t in tickers.items()}
            self.price_cache.update(price_dict, now)

        # Update RollingWindows and build snapshots
        snapshots = []
        for symbol, ticker in tickers.items():
            try:
                # Update rolling window
                rolling_peak = None
                if self.rw_manager:
                    self.rw_manager.update(symbol, now, ticker.last)

                    # Get window state (peak/trough)
                    window = self.rw_manager.windows.get(symbol)
                    if window:
                        peak = window.max_val if window.max_val != float('-inf') else None
                        trough = window.min_val if window.min_val != float('inf') else None
                        rolling_peak = peak

                        windows_dict = {
                            "peak": peak,
                            "trough": trough
                        }
                    else:
                        windows_dict = {"peak": None, "trough": None}
                else:
                    windows_dict = {"peak": None, "trough": None}

                # Update anchor (V9_3)
                anchor = None
                if self.anchor_manager:
                    self.anchor_manager.note_price(symbol, ticker.last, now)
                    anchor = self.anchor_manager.compute_anchor(
                        symbol=symbol,
                        last=ticker.last,
                        now=now,
                        rolling_peak=rolling_peak or ticker.last
                    )
                    windows_dict["anchor"] = anchor

                # Compute features from price cache observations
                features_dict = {}
                if self.price_cache:
                    obs = self.price_cache.buffers.get(symbol, [])
                    if obs:
                        features_dict = compute_features(obs)

                # Build MarketSnapshot
                snapshot = build_snapshot(
                    symbol=symbol,
                    ts=now,
                    last=ticker.last,
                    bid=ticker.bid,
                    ask=ticker.ask,
                    windows=windows_dict,
                    features=features_dict,
                    spread_bps=ticker.spread_bps
                )

                snapshots.append(snapshot)

            except Exception as e:
                logger.debug(f"Failed to build snapshot for {symbol}: {e}")

        # Publish all snapshots via EventBus
        if snapshots and self.event_bus:
            try:
                logger.debug("PUBLISHING_SNAPSHOTS", extra={"n": len(snapshots)})
                self.event_bus.publish("market.snapshots", snapshots)
                self._statistics['drop_snapshots_emitted'] += 1
            except Exception as e:
                logger.debug(f"Failed to publish snapshots: {e}")

        # V9_3 Phase 4: Persist snapshots to JSONL
        if snapshots and self.snapshot_writer and getattr(config, 'FEATURE_PERSIST_STREAMS', True):
            try:
                for snapshot in snapshots:
                    self.snapshot_writer.append(snapshot)
            except Exception as e:
                logger.debug(f"Failed to persist snapshots: {e}")

        # Log to JSONL telemetry (sample 10%)
        if self.telemetry and snapshots and self._statistics['drop_snapshots_emitted'] % 10 == 0:
            try:
                for snap in snapshots:
                    self.telemetry.write("market_snapshot", snap)
            except Exception:
                pass  # Silent fail for telemetry

        # V9_3 Phase 4: Persist windows and anchors periodically
        if self._statistics['drop_snapshots_emitted'] % 20 == 0:
            # Persist rolling windows (legacy + JSONL)
            if self.rw_manager and self.rw_manager.persist:
                try:
                    self.rw_manager.persist_all()  # Legacy persistence
                except Exception as e:
                    logger.debug(f"Failed to persist windows (legacy): {e}")

                # V9_3: JSONL persistence for windows
                if self.windows_writer and getattr(config, 'FEATURE_PERSIST_STREAMS', True):
                    try:
                        for symbol, window in self.rw_manager.windows.items():
                            window_obj = {
                                "ts": now,
                                "symbol": symbol,
                                "peak": window.max_val if window.max_val != float('-inf') else None,
                                "trough": window.min_val if window.min_val != float('inf') else None,
                                "lookback_s": window.lookback_s
                            }
                            self.windows_writer.append(window_obj)
                    except Exception as e:
                        logger.debug(f"Failed to persist windows (JSONL): {e}")

            # Persist anchors (legacy + JSONL)
            if self.anchor_manager:
                try:
                    self.anchor_manager.save()  # Legacy JSON persistence
                except Exception as e:
                    logger.debug(f"Failed to persist anchors (legacy): {e}")

                # V9_3: JSONL persistence for anchors
                if self.anchors_writer and getattr(config, 'FEATURE_PERSIST_STREAMS', True):
                    try:
                        for symbol, anchor_data in self.anchor_manager._anchors.items():
                            anchor_obj = {
                                "ts": now,
                                "symbol": symbol,
                                "anchor": anchor_data.get("anchor"),
                                "anchor_ts": anchor_data.get("ts"),
                                "session_peak": self.anchor_manager.get_session_peak(symbol),
                                "session_start": self.anchor_manager.get_session_start(symbol)
                            }
                            self.anchors_writer.append(anchor_obj)
                    except Exception as e:
                        logger.debug(f"Failed to persist anchors (JSONL): {e}")

        co.beat("md_update_end")
        logger.info(f"HEARTBEAT - Market data update completed: {len(symbols)} symbols, {sum(results.values())} successful, {len(snapshots)} snapshots",
                   extra={"event_type": "HEARTBEAT"})
        return results

    def cleanup_expired_cache(self) -> int:
        """Clean up expired cache entries"""
        return self.ticker_cache.cleanup_expired()

    def get_statistics(self) -> Dict[str, Any]:
        """Get comprehensive market data statistics"""
        with self._lock:
            ticker_cache_keys = self.ticker_cache._cache.keys()
            ohlcv_keys = list(self.ohlcv_history._data.keys())

            stats = {
                'provider': self._statistics.copy(),
                'ticker_cache': self.ticker_cache.get_statistics(),
                'symbols_tracked': len(set(ticker_cache_keys + ohlcv_keys))
            }

            # Add coalescing statistics if enabled
            if self.enable_coalescing and self.coalescing_cache:
                stats['coalescing'] = self.coalescing_cache.get_statistics()

            # Add rate limiter statistics if enabled
            if self.enable_rate_limiting and self.rate_limiter:
                stats['rate_limiting'] = self.rate_limiter.get_statistics()

            return stats

    def clear_cache(self) -> None:
        """Clear all cached data"""
        self.ticker_cache.clear()

    def start(self):
        """Start market data polling loop in background thread"""
        # ULTRA DEBUG: Write to file
        try:
            with open("/tmp/market_data_start.txt", "a") as f:
                import datetime
                f.write(f"{datetime.datetime.now()} - MarketDataProvider.start() ENTRY\n")
                f.flush()
        except:
            pass

        if hasattr(self, '_running') and self._running:
            logger.warning("Market data loop already running")
            # ULTRA DEBUG
            try:
                with open("/tmp/market_data_start.txt", "a") as f:
                    f.write(f"  Already running, returning early\n")
                    f.flush()
            except:
                pass
            return

        self._running = True
        self._thread = None

        import threading
        self._thread = threading.Thread(target=self._loop, name="MarketDataLoop", daemon=True)
        self._thread.start()

        # ULTRA DEBUG
        try:
            with open("/tmp/market_data_start.txt", "a") as f:
                f.write(f"  Thread started, thread={self._thread}, is_alive={self._thread.is_alive() if self._thread else False}\n")
                f.flush()
        except:
            pass

        # Log thread start with explicit event
        logger.info("MARKET_DATA_THREAD_STARTED", extra={"event_type":"MARKET_DATA_LOOP_STARTED"})

        # Get symbols and poll_ms for debug logging
        import config
        symbols = getattr(config, 'TOPCOINS_SYMBOLS', [])
        poll_ms = getattr(config, "MD_POLL_MS", 1000)

        logger.info(
            f"MARKET_DATA_LOOP_STARTED symbols={len(symbols)} poll_ms={poll_ms}",
            extra={
                "event_type": "MARKET_DATA_LOOP_STARTED",
                "symbols": symbols[:5] if symbols else [],  # Log first 5 symbols
                "poll_ms": poll_ms
            }
        )

    def stop(self):
        """Stop market data polling loop"""
        self._running = False
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=5.0)

        # V9_3: Save rolling windows per symbol (defensive)
        if self.rw_manager and self.rw_manager.persist:
            try:
                import config
                symbols = getattr(config, 'TOPCOINS_SYMBOLS', [])
                for symbol in symbols:
                    try:
                        self.rw_manager.save(symbol)
                    except Exception as e:
                        logger.debug(f"Failed to save rolling window for {symbol}: {e}")
            except Exception as e:
                logger.warning(f"Failed to save rolling windows on shutdown: {e}")

        # Persist anchor state before shutdown
        if self.anchor_manager:
            try:
                self.anchor_manager.save()
            except Exception as e:
                logger.warning(f"Failed to save anchors on shutdown: {e}")

        logger.info("Market data loop stopped")

    def _loop(self):
        """Market data polling loop - fetches and publishes snapshots"""
        # ULTRA DEBUG: Write to file
        try:
            with open("/tmp/market_data_loop.txt", "a") as f:
                import datetime
                f.write(f"{datetime.datetime.now()} - _loop() ENTRY\n")
                f.flush()
        except:
            pass

        import config
        import traceback
        from pathlib import Path

        try:
            # ULTRA DEBUG
            try:
                with open("/tmp/market_data_loop.txt", "a") as f:
                    f.write(f"  Inside try block\n")
                    f.flush()
            except:
                pass

            # ULTRA DEBUG - Test 1
            try:
                with open("/tmp/market_data_loop.txt", "a") as f:
                    f.write(f"  Test 1: About to check self.persist\n")
                    f.flush()
                    persist_value = self.persist
                    f.write(f"  Test 2: self.persist={persist_value}\n")
                    f.flush()
            except Exception as e:
                try:
                    with open("/tmp/market_data_loop.txt", "a") as f:
                        f.write(f"  ERROR accessing self.persist: {e}\n")
                        f.flush()
                except:
                    pass
            # Pfade vorbereiten (falls Persistenz aktiviert)
            if self.persist:
                # ULTRA DEBUG
                try:
                    with open("/tmp/market_data_loop.txt", "a") as f:
                        f.write(f"  Creating paths...\n")
                        f.flush()
                except:
                    pass
                Path(self.base_path).mkdir(parents=True, exist_ok=True)
                Path(f"{self.base_path}/snapshots").mkdir(parents=True, exist_ok=True)
                Path(f"{self.base_path}/drop_windows").mkdir(parents=True, exist_ok=True)
                logger.info("SNAPSHOT_PIPELINE_ENABLED",
                           extra={"lookback": self.lookback_s, "persist": self.persist, "base": self.base_path})

            poll_ms = getattr(config, "MD_POLL_MS", 1000)
            poll_s = poll_ms / 1000.0
            debug_drops = getattr(config, "DEBUG_DROPS", False)

            # ULTRA DEBUG
            try:
                with open("/tmp/market_data_loop.txt", "a") as f:
                    f.write(f"  poll_ms={poll_ms}, debug_drops={debug_drops}\n")
                    f.flush()
            except:
                pass

            logger.info(f"Market data loop started with poll interval {poll_ms}ms")

            # Get symbols from config or use defaults
            symbols = getattr(config, 'TOPCOINS_SYMBOLS', [])

            # ULTRA DEBUG
            try:
                with open("/tmp/market_data_loop.txt", "a") as f:
                    f.write(f"  symbols_count={len(symbols)}\n")
                    f.flush()
            except:
                pass

            # Log configuration
            logger.info("MD_LOOP_CFG", extra={"symbols_count": len(symbols), "first5": symbols[:5], "poll_ms": poll_ms})

            if not symbols:
                logger.error("NO_SYMBOLS_FOR_MD_LOOP")
                symbols = ["BTC/USDT", "ETH/USDT"]  # Fallback

            logger.info(f"MD_LOOP symbols_count={len(symbols)} first_5={symbols[:5] if symbols else []}")

            # V9_3 Phase 5: Warm-start from persisted ticks
            if self.enable_drop_tracking and getattr(config, 'FEATURE_WARMSTART_TICKS', True):
                try:
                    logger.info("Starting warm-start from persisted ticks...")
                    warmstart_results = self._warm_start(symbols, max_ticks=300)
                    success_count = sum(1 for v in warmstart_results.values() if v > 0)
                    total_ticks = sum(warmstart_results.values())
                    logger.info(
                        f"Warm-start complete: {success_count}/{len(symbols)} symbols, "
                        f"{total_ticks} ticks loaded"
                    )
                except Exception as e:
                    logger.warning(f"Warm-start failed: {e}", exc_info=True)

            # ULTRA DEBUG
            try:
                with open("/tmp/market_data_loop.txt", "a") as f:
                    f.write(f"  Entering while loop (_running={self._running})\n")
                    f.flush()
            except:
                pass

            loop_counter = 0
            while self._running:
                try:
                    loop_counter += 1
                    # ULTRA DEBUG (first 3 loops only)
                    if loop_counter <= 3:
                        try:
                            with open("/tmp/market_data_loop.txt", "a") as f:
                                f.write(f"  Loop iteration #{loop_counter}\n")
                                f.flush()
                        except:
                            pass

                    # Fetch all market snapshots (returns Dict[str, bool] indicating success per symbol)
                    results = self.update_market_data(symbols)
                    success_count = sum(1 for v in results.values() if v)

                    # DEBUG_DROPS: Detailed logging
                    if debug_drops:
                        # Get first symbol price for sample logging
                        first_sym = symbols[0] if symbols else None
                        first_price = None
                        if first_sym:
                            cache_result = self.ticker_cache.get_ticker(first_sym)
                            if cache_result:
                                ticker, _ = cache_result
                                first_price = ticker.last

                        if success_count > 0:
                            logger.debug(
                                f"MD_POLL published count={success_count}/{len(symbols)} "
                                f"first={first_sym} last={first_price:.8f if first_price else -1}"
                            )
                        else:
                            logger.warning(
                                f"MD_POLL produced 0 snapshots; symbols={symbols[:5] if symbols else []}"
                            )
                    else:
                        # Normal logging (less verbose)
                        if success_count > 0:
                            logger.debug(f"MD_POLL tick published count={success_count}/{len(symbols)}")
                        else:
                            logger.warning("MD_POLL tick produced 0 snapshots")

                    # Log telemetry (sample 10%)
                    if self.telemetry and self._statistics.get('drop_snapshots_emitted', 0) % 10 == 0:
                        try:
                            self.telemetry.write("market", {"count": success_count, "total": len(symbols)})
                        except Exception:
                            pass

                    time.sleep(poll_s)

                except Exception as e:
                    logger.error(f"Market data loop error: {e}", exc_info=True)
                    time.sleep(poll_s)

        except Exception as e:
            logger.exception("MD_LOOP_FATAL")
            logger.error(str(e))
            logger.error(traceback.format_exc())
            self._running = False
            return
        finally:
            logger.info("Market data loop ended")


# Utility functions for backwards compatibility
def fetch_ticker_cached(exchange_or_provider, symbol: str) -> Dict[str, Any]:
    """Backwards compatible ticker fetching"""
    if hasattr(exchange_or_provider, 'get_ticker'):
        # New MarketDataProvider
        ticker = exchange_or_provider.get_ticker(symbol)
        if ticker:
            return {
                'symbol': ticker.symbol,
                'last': ticker.last,
                'bid': ticker.bid,
                'ask': ticker.ask,
                'baseVolume': ticker.volume,
                'timestamp': ticker.timestamp,
                'high': ticker.high_24h,
                'low': ticker.low_24h,
                'change': ticker.change_24h,
                'percentage': ticker.change_percent_24h
            }
    else:
        # Fallback to direct exchange call
        return exchange_or_provider.fetch_ticker(symbol)

    return None


def fetch_ticker_with_retry(exchange_or_provider, symbol: str, retries: int = 3,
                          base_sleep: float = 0.25) -> Dict[str, Any]:
    """Fetch ticker with retry logic"""
    for attempt in range(retries):
        try:
            return fetch_ticker_cached(exchange_or_provider, symbol)
        except Exception as e:
            if attempt == retries - 1:
                raise e
            time.sleep(min(1.0, base_sleep * (2 ** attempt)))

    return exchange_or_provider.fetch_ticker(symbol)