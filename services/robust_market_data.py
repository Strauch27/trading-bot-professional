"""
Robust Market Data Service
Enhanced market data fetching with graceful degradation and advanced error recovery
"""

import time
import logging
import random
from typing import Dict, List, Optional, Any, Set, Tuple
from threading import RLock
from dataclasses import dataclass
from collections import defaultdict, deque
from .market_data import MarketDataProvider, TickerData

logger = logging.getLogger(__name__)


@dataclass
class MarketDataHealth:
    """Track market data connection health"""
    total_requests: int = 0
    successful_requests: int = 0
    failed_requests: int = 0
    consecutive_failures: int = 0
    last_success_time: Optional[float] = None
    last_failure_time: Optional[float] = None

    @property
    def success_rate(self) -> float:
        """Success rate percentage"""
        if self.total_requests == 0:
            return 100.0
        return (self.successful_requests / self.total_requests) * 100.0

    @property
    def is_healthy(self) -> bool:
        """Consider healthy if >80% success rate and <5 consecutive failures"""
        return self.success_rate >= 80.0 and self.consecutive_failures < 5

    @property
    def seconds_since_last_success(self) -> float:
        """Seconds since last successful request"""
        if self.last_success_time is None:
            return float('inf')
        return time.time() - self.last_success_time


class TickerFallbackManager:
    """Manages fallback strategies for ticker fetching"""

    def __init__(self, max_stale_age: float = 30.0):
        self.max_stale_age = max_stale_age
        self._fallback_cache: Dict[str, Tuple[TickerData, float]] = {}
        self._lock = RLock()

    def store_fallback(self, ticker: TickerData) -> None:
        """Store ticker for fallback use"""
        with self._lock:
            self._fallback_cache[ticker.symbol] = (ticker, time.time())

    def get_fallback(self, symbol: str) -> Optional[TickerData]:
        """Get fallback ticker if available and not too stale"""
        with self._lock:
            if symbol in self._fallback_cache:
                ticker, store_time = self._fallback_cache[symbol]
                age = time.time() - store_time
                if age <= self.max_stale_age:
                    return ticker
                else:
                    # Remove stale entry
                    del self._fallback_cache[symbol]
            return None

    def cleanup_stale(self) -> int:
        """Remove stale fallback entries"""
        with self._lock:
            current_time = time.time()
            stale_symbols = []

            for symbol, (ticker, store_time) in self._fallback_cache.items():
                if current_time - store_time > self.max_stale_age:
                    stale_symbols.append(symbol)

            for symbol in stale_symbols:
                del self._fallback_cache[symbol]

            return len(stale_symbols)


class RobustMarketDataProvider:
    """
    Enhanced market data provider with graceful degradation
    """

    def __init__(self, base_provider: MarketDataProvider,
                 retry_config: Optional[Dict[str, Any]] = None):
        self.base_provider = base_provider
        self.fallback_manager = TickerFallbackManager()

        # Default retry configuration
        default_retry_config = {
            'max_retries': 5,
            'base_delay': 0.25,
            'max_delay': 8.0,
            'backoff_multiplier': 2.0,
            'jitter_factor': 0.1,
            'circuit_breaker_threshold': 10,
            'circuit_breaker_timeout': 60.0
        }
        self.retry_config = {**default_retry_config, **(retry_config or {})}

        # Health tracking per symbol
        self._health: Dict[str, MarketDataHealth] = defaultdict(MarketDataHealth)
        self._global_health = MarketDataHealth()
        self._circuit_breaker_open_until: float = 0.0
        self._lock = RLock()

        # Statistics
        self._stats = {
            'fetch_attempts': 0,
            'fallback_uses': 0,
            'circuit_breaker_trips': 0,
            'degraded_responses': 0,
            'full_failures': 0
        }

    def _calculate_delay(self, attempt: int) -> float:
        """Calculate exponential backoff delay with jitter"""
        base_delay = self.retry_config['base_delay']
        multiplier = self.retry_config['backoff_multiplier']
        max_delay = self.retry_config['max_delay']
        jitter_factor = self.retry_config['jitter_factor']

        delay = base_delay * (multiplier ** attempt)
        delay = min(delay, max_delay)

        # Add jitter to prevent thundering herd
        jitter = delay * jitter_factor * random.uniform(-1, 1)
        return max(0, delay + jitter)

    def _is_circuit_breaker_open(self) -> bool:
        """Check if circuit breaker is open"""
        return time.time() < self._circuit_breaker_open_until

    def _trip_circuit_breaker(self) -> None:
        """Trip the circuit breaker"""
        timeout = self.retry_config['circuit_breaker_timeout']
        self._circuit_breaker_open_until = time.time() + timeout
        self._stats['circuit_breaker_trips'] += 1
        logger.warning(f"Market data circuit breaker tripped for {timeout}s")

    def _update_health(self, symbol: str, success: bool) -> None:
        """Update health metrics"""
        with self._lock:
            health = self._health[symbol]
            health.total_requests += 1

            if success:
                health.successful_requests += 1
                health.consecutive_failures = 0
                health.last_success_time = time.time()
            else:
                health.failed_requests += 1
                health.consecutive_failures += 1
                health.last_failure_time = time.time()

                # Check if should trip circuit breaker
                threshold = self.retry_config['circuit_breaker_threshold']
                if health.consecutive_failures >= threshold:
                    self._trip_circuit_breaker()

            # Update global health
            self._global_health.total_requests += 1
            if success:
                self._global_health.successful_requests += 1
                self._global_health.consecutive_failures = 0
                self._global_health.last_success_time = time.time()
            else:
                self._global_health.failed_requests += 1
                self._global_health.consecutive_failures += 1
                self._global_health.last_failure_time = time.time()

    def get_ticker_robust(self, symbol: str,
                         allow_fallback: bool = True,
                         allow_degraded: bool = True) -> Optional[TickerData]:
        """
        Get ticker with robust error handling and fallback strategies

        Args:
            symbol: Trading symbol
            allow_fallback: Allow using cached fallback data
            allow_degraded: Allow degraded responses (reduced data quality)

        Returns:
            TickerData or None if all strategies fail
        """
        self._stats['fetch_attempts'] += 1

        # Circuit breaker check
        if self._is_circuit_breaker_open():
            logger.warning(f"Circuit breaker open, using fallback for {symbol}")
            return self._get_fallback_response(symbol, allow_fallback)

        # Primary fetch with retries
        max_retries = self.retry_config['max_retries']
        last_error = None

        for attempt in range(max_retries):
            try:
                ticker = self.base_provider.get_ticker(symbol, use_cache=True)
                if ticker:
                    # Success - store for fallback and update health
                    self.fallback_manager.store_fallback(ticker)
                    self._update_health(symbol, success=True)
                    return ticker
                else:
                    # Null response - treat as temporary failure
                    raise Exception("Exchange returned null ticker")

            except Exception as e:
                last_error = e
                logger.debug(f"Ticker fetch attempt {attempt + 1}/{max_retries} failed for {symbol}: {e}")

                if attempt < max_retries - 1:
                    delay = self._calculate_delay(attempt)
                    time.sleep(delay)

        # All retries failed
        self._update_health(symbol, success=False)
        logger.warning(f"All ticker fetch attempts failed for {symbol}: {last_error}")

        # Try fallback strategies
        return self._get_fallback_response(symbol, allow_fallback, allow_degraded)

    def _get_fallback_response(self, symbol: str,
                              allow_fallback: bool = True,
                              allow_degraded: bool = True) -> Optional[TickerData]:
        """Try fallback strategies when primary fetch fails"""

        # Strategy 1: Use cached fallback data
        if allow_fallback:
            fallback_ticker = self.fallback_manager.get_fallback(symbol)
            if fallback_ticker:
                self._stats['fallback_uses'] += 1
                logger.info(f"Using fallback ticker data for {symbol}")
                return fallback_ticker

        # Strategy 2: Degraded response with synthetic data
        if allow_degraded:
            return self._create_degraded_ticker(symbol)

        # All strategies failed
        self._stats['full_failures'] += 1
        logger.error(f"All fallback strategies failed for {symbol}")
        return None

    def _create_degraded_ticker(self, symbol: str) -> Optional[TickerData]:
        """Create degraded ticker with minimal synthetic data"""
        self._stats['degraded_responses'] += 1

        # Try to get any historical price data
        try:
            latest_bar = self.base_provider.get_latest_bar(symbol, '1m')
            if latest_bar:
                # Use OHLCV data to synthesize ticker
                price = latest_bar.close
                spread = price * 0.001  # Assume 0.1% spread

                ticker = TickerData(
                    symbol=symbol,
                    last=price,
                    bid=price - spread/2,
                    ask=price + spread/2,
                    volume=latest_bar.volume,
                    timestamp=latest_bar.timestamp,
                    high_24h=None,  # Degraded - no 24h data
                    low_24h=None,
                    change_24h=None,
                    change_percent_24h=None
                )

                logger.warning(f"Created degraded ticker for {symbol} from OHLCV data")
                return ticker
        except Exception as e:
            logger.debug(f"Could not create degraded ticker for {symbol}: {e}")

        return None

    def fetch_multiple_tickers_robust(self, symbols: List[str],
                                    max_parallel_failures: int = 3,
                                    fail_fast: bool = False) -> Dict[str, Optional[TickerData]]:
        """
        Fetch multiple tickers with intelligent failure handling

        Args:
            symbols: List of symbols to fetch
            max_parallel_failures: Max parallel failures before degrading strategy
            fail_fast: If True, return immediately on first few failures

        Returns:
            Dict mapping symbols to ticker data (or None if failed)
        """
        results = {}
        failure_count = 0

        # Randomize order to distribute load
        shuffled_symbols = symbols.copy()
        random.shuffle(shuffled_symbols)

        for symbol in shuffled_symbols:
            ticker = self.get_ticker_robust(symbol)
            results[symbol] = ticker

            if ticker is None:
                failure_count += 1

                # Adaptive strategy based on failure rate
                if failure_count >= max_parallel_failures:
                    logger.warning(f"High failure rate ({failure_count} failures), "
                                 f"switching to degraded mode for remaining symbols")

                    # Process remaining symbols with degraded settings
                    for remaining_symbol in shuffled_symbols[len(results):]:
                        degraded_ticker = self.get_ticker_robust(
                            remaining_symbol,
                            allow_fallback=True,
                            allow_degraded=True
                        )
                        results[remaining_symbol] = degraded_ticker
                    break

                if fail_fast and failure_count >= 2:
                    logger.warning("Fail-fast mode: stopping after 2 failures")
                    break

        return results

    def get_health_status(self, symbol: Optional[str] = None) -> Dict[str, Any]:
        """Get health status for symbol or globally"""
        with self._lock:
            if symbol:
                health = self._health[symbol]
                return {
                    'symbol': symbol,
                    'healthy': health.is_healthy,
                    'success_rate': health.success_rate,
                    'consecutive_failures': health.consecutive_failures,
                    'seconds_since_last_success': health.seconds_since_last_success,
                    'total_requests': health.total_requests
                }
            else:
                return {
                    'global_healthy': self._global_health.is_healthy,
                    'global_success_rate': self._global_health.success_rate,
                    'circuit_breaker_open': self._is_circuit_breaker_open(),
                    'circuit_breaker_trips': self._stats['circuit_breaker_trips'],
                    'total_symbols_tracked': len(self._health),
                    'unhealthy_symbols': [
                        sym for sym, health in self._health.items()
                        if not health.is_healthy
                    ]
                }

    def get_statistics(self) -> Dict[str, Any]:
        """Get comprehensive statistics"""
        base_stats = self.base_provider.get_statistics()

        with self._lock:
            robust_stats = {
                'robust_provider': self._stats.copy(),
                'health': {
                    'global': self._global_health.__dict__,
                    'per_symbol_count': len(self._health)
                },
                'fallback_cache_size': len(self.fallback_manager._fallback_cache)
            }

        return {**base_stats, **robust_stats}

    def cleanup_and_maintenance(self) -> Dict[str, int]:
        """Perform maintenance tasks"""
        results = {}

        # Cleanup stale fallback data
        results['stale_fallbacks_removed'] = self.fallback_manager.cleanup_stale()

        # Cleanup expired cache in base provider
        results['expired_cache_removed'] = self.base_provider.cleanup_expired_cache()

        # Reset circuit breaker if enough time has passed
        if self._is_circuit_breaker_open():
            if time.time() >= self._circuit_breaker_open_until:
                logger.info("Circuit breaker reset")
                results['circuit_breaker_reset'] = 1

        return results

    # Delegate other methods to base provider
    def get_price(self, symbol: str) -> Optional[float]:
        """Get current price for symbol"""
        ticker = self.get_ticker_robust(symbol)
        return ticker.last if ticker else None

    def get_spread(self, symbol: str) -> Optional[float]:
        """Get bid-ask spread in basis points"""
        ticker = self.get_ticker_robust(symbol)
        return ticker.spread_bps if ticker else None

    def fetch_ohlcv(self, symbol: str, timeframe: str = '1m',
                   limit: int = 100, store: bool = True):
        """Delegate OHLCV fetching to base provider"""
        return self.base_provider.fetch_ohlcv(symbol, timeframe, limit, store)

    def get_historical_bars(self, symbol: str, timeframe: str = '1m', limit: Optional[int] = None):
        """Delegate historical bars to base provider"""
        return self.base_provider.get_historical_bars(symbol, timeframe, limit)