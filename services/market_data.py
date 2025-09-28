"""
Market Data Service (Drop 4)
Centralized market data management extraction from engine.py
"""

import time
import logging
from typing import Dict, List, Optional, Any, Tuple
from threading import RLock
from dataclasses import dataclass, asdict
from collections import defaultdict, deque

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
    """Thread-safe ticker cache with TTL"""

    def __init__(self, default_ttl: float = 5.0, max_size: int = 1000):
        self.default_ttl = default_ttl
        self.max_size = max_size
        self._cache: Dict[str, Tuple[TickerData, float]] = {}
        self._access_order: deque = deque()
        self._lock = RLock()
        self._statistics = {
            'hits': 0,
            'misses': 0,
            'stores': 0,
            'evictions': 0
        }

    def get_ticker(self, symbol: str) -> Optional[TickerData]:
        """Get ticker from cache if not expired"""
        with self._lock:
            current_time = time.time()

            if symbol in self._cache:
                ticker, expire_time = self._cache[symbol]
                if current_time < expire_time:
                    self._statistics['hits'] += 1
                    # Update access order
                    if symbol in self._access_order:
                        self._access_order.remove(symbol)
                    self._access_order.append(symbol)
                    return ticker
                else:
                    # Expired
                    del self._cache[symbol]

            self._statistics['misses'] += 1
            return None

    def store_ticker(self, ticker: TickerData, ttl: Optional[float] = None) -> None:
        """Store ticker in cache"""
        with self._lock:
            if ttl is None:
                ttl = self.default_ttl

            expire_time = time.time() + ttl

            # Evict if cache is full
            if len(self._cache) >= self.max_size and ticker.symbol not in self._cache:
                self._evict_oldest()

            self._cache[ticker.symbol] = (ticker, expire_time)

            # Update access order
            if ticker.symbol in self._access_order:
                self._access_order.remove(ticker.symbol)
            self._access_order.append(ticker.symbol)

            self._statistics['stores'] += 1

    def _evict_oldest(self) -> None:
        """Evict oldest entry"""
        if self._access_order:
            oldest = self._access_order.popleft()
            if oldest in self._cache:
                del self._cache[oldest]
                self._statistics['evictions'] += 1

    def cleanup_expired(self) -> int:
        """Remove expired entries"""
        with self._lock:
            current_time = time.time()
            expired_symbols = []

            for symbol, (_, expire_time) in self._cache.items():
                if current_time >= expire_time:
                    expired_symbols.append(symbol)

            for symbol in expired_symbols:
                del self._cache[symbol]
                if symbol in self._access_order:
                    self._access_order.remove(symbol)

            return len(expired_symbols)

    def get_statistics(self) -> Dict[str, Any]:
        """Get cache statistics"""
        with self._lock:
            stats = self._statistics.copy()
            stats['size'] = len(self._cache)
            total_requests = stats['hits'] + stats['misses']
            stats['hit_rate'] = stats['hits'] / total_requests if total_requests > 0 else 0.0
            return stats

    def clear(self) -> None:
        """Clear all cached data"""
        with self._lock:
            self._cache.clear()
            self._access_order.clear()


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
    """Main market data service"""

    def __init__(self, exchange_adapter, ticker_cache_ttl: float = 5.0,
                 max_cache_size: int = 1000, max_bars_per_symbol: int = 1000):
        self.exchange_adapter = exchange_adapter
        self.ticker_cache = TickerCache(ticker_cache_ttl, max_cache_size)
        self.ohlcv_history = OHLCVHistory(max_bars_per_symbol)
        self._lock = RLock()
        self._statistics = {
            'ticker_requests': 0,
            'ticker_cache_hits': 0,
            'ohlcv_requests': 0,
            'ohlcv_bars_stored': 0,
            'errors': 0
        }

    def get_ticker(self, symbol: str, use_cache: bool = True) -> Optional[TickerData]:
        """Get ticker data with optional caching"""
        from services.shutdown_coordinator import get_shutdown_coordinator
        co = get_shutdown_coordinator()

        co.beat(f"get_ticker_enter:{symbol}")
        try:
            with self._lock:
                self._statistics['ticker_requests'] += 1

                # Try cache first
                if use_cache:
                    cached_ticker = self.ticker_cache.get_ticker(symbol)
                    if cached_ticker:
                        co.beat(f"get_ticker_cache_hit:{symbol}")
                        self._statistics['ticker_cache_hits'] += 1
                        return cached_ticker

                # Fetch from exchange
                try:
                    co.beat(f"get_ticker_exchange_call:{symbol}")
                    raw_ticker = self.exchange_adapter.fetch_ticker(symbol)

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

                    co.beat(f"get_ticker_success:{symbol}")
                    # Nur bei kritischen Symbolen oder nach längerer Zeit loggen
                    if symbol in ["BTC/USDT"] or self._statistics['ticker_requests'] % 50 == 0:
                        logger.info(f"HEARTBEAT - Ticker fetched: {symbol}",
                                   extra={"event_type": "HEARTBEAT"})
                    return ticker

                except Exception as e:
                    self._statistics['errors'] += 1
                    co.beat(f"get_ticker_error:{symbol}")
                    logger.error(f"Error fetching ticker for {symbol}: {e}")
                    logger.info(f"HEARTBEAT - Ticker fetch failed but handled: {symbol}",
                               extra={"event_type": "HEARTBEAT"})
                    return None
        finally:
            co.beat(f"get_ticker_exit:{symbol}")

    def get_price(self, symbol: str, prefer_cache: bool = True) -> Optional[float]:
        """Get current price for symbol with cache preference"""
        # 1) Cache bevorzugen
        if prefer_cache:
            cached_ticker = self.ticker_cache.get_ticker(symbol)
            if cached_ticker:
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

    def fetch_ohlcv(self, symbol: str, timeframe: str = '1m',
                   limit: int = 100, store: bool = True) -> List[OHLCVBar]:
        """Fetch OHLCV data"""
        with self._lock:
            self._statistics['ohlcv_requests'] += 1

            try:
                raw_ohlcv = self.exchange_adapter.fetch_ohlcv(symbol, timeframe, limit=limit)

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

                if store:
                    self.ohlcv_history.add_bars(symbol, timeframe, bars)
                    self._statistics['ohlcv_bars_stored'] += len(bars)

                return bars

            except Exception as e:
                self._statistics['errors'] += 1
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
            return {
                'provider': self._statistics.copy(),
                'ticker_cache': self.ticker_cache.get_statistics(),
                'symbols_tracked': len(set(
                    list(self.ticker_cache._cache.keys()) +
                    list(self.ohlcv_history._data.keys())
                ))
            }

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
            time.sleep(base_sleep * (2 ** attempt))

    return exchange_or_provider.fetch_ticker(symbol)