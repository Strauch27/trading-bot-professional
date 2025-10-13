"""
Market Data Service (Drop 4)
Centralized market data management extraction from engine.py

Enhanced with:
- Soft-TTL cache (serve stale while refreshing)
- Candle alignment and partial candle guard
- JSONL audit logging
"""

import time
import logging
from typing import Dict, List, Optional, Any, Tuple
from threading import RLock
from dataclasses import dataclass, asdict
from collections import defaultdict, deque
from pathlib import Path

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
from services.market_data.coalescing import get_coalescing_cache
from services.market_data.rate_limit import get_rate_limiter

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
        enable_rate_limiting: bool = True
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
            'errors': 0
        }

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

                    # Store in cache
                    if use_cache:
                        self.ticker_cache.store_ticker(ticker)

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

    def update_market_data(self, symbols: List[str]) -> Dict[str, bool]:
        """Update market data for multiple symbols"""
        from services.shutdown_coordinator import get_shutdown_coordinator
        co = get_shutdown_coordinator()

        co.beat("md_update_start")
        results = {}

        for symbol in symbols:
            try:
                # Update ticker
                ticker = self.get_ticker(symbol, use_cache=False)
                results[symbol] = ticker is not None

                # Optionally fetch latest OHLCV
                if ticker:
                    self.fetch_ohlcv(symbol, '1m', limit=1, store=True)

            except Exception as e:
                logger.error(f"Market data update failed for {symbol}: {e}")
                results[symbol] = False

        co.beat("md_update_end")
        logger.info(f"HEARTBEAT - Market data update completed for {len(symbols)} symbols, {sum(results.values())} successful",
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