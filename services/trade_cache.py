"""
Trade Cache Service
Intelligent caching for API calls with bulk operations and performance optimization
"""

import hashlib
import json
import logging
import threading
import time
from collections import defaultdict, deque
from dataclasses import dataclass, field
from threading import RLock
from typing import Any, Dict, List, Optional, Union

logger = logging.getLogger(__name__)


@dataclass
class CacheEntry:
    """Cache entry with metadata"""
    data: Any
    timestamp: float
    ttl: float
    access_count: int = 0
    last_access: float = field(default_factory=time.time)
    size_bytes: int = 0

    @property
    def is_expired(self) -> bool:
        """Check if entry is expired"""
        return time.time() > (self.timestamp + self.ttl)

    @property
    def age_seconds(self) -> float:
        """Age of entry in seconds"""
        return time.time() - self.timestamp

    def mark_access(self) -> None:
        """Mark entry as accessed"""
        self.access_count += 1
        self.last_access = time.time()


@dataclass
class BulkRequest:
    """Represents a bulk request"""
    symbols: List[str]
    request_type: str  # 'trades', 'orders', 'tickers'
    params: Dict[str, Any]
    timestamp: float = field(default_factory=time.time)
    priority: int = 1  # 1=low, 2=medium, 3=high


class TradeCache:
    """
    Intelligent cache for trade and order data with bulk optimization
    """

    def __init__(self, default_ttl: float = 300.0, max_size_mb: float = 100.0):
        self.default_ttl = default_ttl
        self.max_size_bytes = int(max_size_mb * 1024 * 1024)

        # Cache storage
        self._cache: Dict[str, CacheEntry] = {}
        self._access_order: deque = deque()  # LRU tracking
        self._lock = RLock()

        # Bulk request optimization
        self._pending_bulk_requests: List[BulkRequest] = []
        self._bulk_request_lock = threading.Lock()
        self._bulk_processor_thread: Optional[threading.Thread] = None
        self._shutdown_event = threading.Event()

        # Statistics
        self._stats = {
            'cache_hits': 0,
            'cache_misses': 0,
            'cache_stores': 0,
            'cache_evictions': 0,
            'bulk_requests_processed': 0,
            'api_calls_saved': 0,
            'total_size_bytes': 0
        }

        # Performance tracking
        self._request_history: deque = deque(maxlen=1000)  # Last 1000 requests

    def start_bulk_processor(self, batch_interval: float = 2.0):
        """Start bulk request processor"""
        if self._bulk_processor_thread and self._bulk_processor_thread.is_alive():
            return

        def bulk_processor():
            logger.info(f"Trade cache bulk processor started (interval: {batch_interval}s)")

            while not self._shutdown_event.is_set():
                try:
                    self._shutdown_event.wait(batch_interval)

                    if self._shutdown_event.is_set():
                        break

                    self._process_pending_bulk_requests()

                except Exception as e:
                    logger.error(f"Bulk processor error: {e}")

            logger.info("Trade cache bulk processor stopped")

        self._bulk_processor_thread = threading.Thread(
            target=bulk_processor,
            name="TradeCacheBulkProcessor",
            daemon=True
        )
        self._bulk_processor_thread.start()

    def stop_bulk_processor(self):
        """Stop bulk request processor"""
        self._shutdown_event.set()
        if self._bulk_processor_thread and self._bulk_processor_thread.is_alive():
            self._bulk_processor_thread.join(timeout=5.0)

    def _generate_cache_key(self, key_components: Union[str, List[str], Dict[str, Any]]) -> str:
        """Generate cache key from components"""
        if isinstance(key_components, str):
            return key_components

        # Create deterministic key from complex data
        key_data = json.dumps(key_components, sort_keys=True)
        return hashlib.md5(key_data.encode()).hexdigest()

    def _estimate_size(self, data: Any) -> int:
        """Estimate size of data in bytes"""
        try:
            # Simple estimation - convert to JSON and measure
            json_str = json.dumps(data, default=str)
            return len(json_str.encode('utf-8'))
        except Exception:
            # Fallback estimation
            return len(str(data)) * 2

    def get(self, key: Union[str, List[str], Dict[str, Any]],
           default: Any = None) -> Optional[Any]:
        """Get data from cache"""
        cache_key = self._generate_cache_key(key)

        with self._lock:
            if cache_key not in self._cache:
                self._stats['cache_misses'] += 1
                return default

            entry = self._cache[cache_key]

            if entry.is_expired:
                # Remove expired entry
                del self._cache[cache_key]
                if cache_key in self._access_order:
                    self._access_order.remove(cache_key)
                self._stats['cache_misses'] += 1
                return default

            # Mark access and update LRU
            entry.mark_access()
            if cache_key in self._access_order:
                self._access_order.remove(cache_key)
            self._access_order.append(cache_key)

            self._stats['cache_hits'] += 1
            return entry.data

    def put(self, key: Union[str, List[str], Dict[str, Any]],
           data: Any, ttl: Optional[float] = None) -> None:
        """Store data in cache"""
        cache_key = self._generate_cache_key(key)
        if ttl is None:
            ttl = self.default_ttl

        # Estimate data size
        data_size = self._estimate_size(data)

        with self._lock:
            # Create cache entry
            entry = CacheEntry(
                data=data,
                timestamp=time.time(),
                ttl=ttl,
                size_bytes=data_size
            )

            # Evict if necessary to make space
            self._evict_to_make_space(data_size)

            # Store entry
            self._cache[cache_key] = entry
            self._access_order.append(cache_key)

            self._stats['cache_stores'] += 1
            self._stats['total_size_bytes'] += data_size

    def _evict_to_make_space(self, needed_bytes: int) -> None:
        """Evict entries to make space for new data"""
        current_size = self._calculate_current_size()

        # Evict if we would exceed max size
        while current_size + needed_bytes > self.max_size_bytes and self._access_order:
            # Evict least recently used
            oldest_key = self._access_order.popleft()
            if oldest_key in self._cache:
                evicted_entry = self._cache[oldest_key]
                current_size -= evicted_entry.size_bytes
                del self._cache[oldest_key]
                self._stats['cache_evictions'] += 1

    def _calculate_current_size(self) -> int:
        """Calculate current cache size"""
        return sum(entry.size_bytes for entry in self._cache.values())

    def get_trades_cached(self, symbol: str, exchange_adapter,
                         since: Optional[int] = None,
                         limit: int = 100,
                         params: Optional[Dict] = None,
                         cache_ttl: float = 60.0) -> List[Dict]:
        """Get trades with caching"""
        cache_key = {
            'type': 'trades',
            'symbol': symbol,
            'since': since,
            'limit': limit,
            'params': params or {}
        }

        # Try cache first
        cached_trades = self.get(cache_key)
        if cached_trades is not None:
            self._stats['api_calls_saved'] += 1
            return cached_trades

        # Fetch from exchange
        start_time = time.time()
        try:
            trades = exchange_adapter.fetch_my_trades(symbol, since, limit, params or {})

            # Cache the result
            self.put(cache_key, trades, ttl=cache_ttl)

            # Track request performance
            duration = time.time() - start_time
            self._request_history.append({
                'type': 'trades',
                'symbol': symbol,
                'duration': duration,
                'cached': False,
                'timestamp': time.time()
            })

            return trades

        except Exception as e:
            logger.error(f"Error fetching trades for {symbol}: {e}")
            return []

    def get_orders_cached(self, symbol: str, exchange_adapter,
                         since: Optional[int] = None,
                         limit: int = 100,
                         params: Optional[Dict] = None,
                         cache_ttl: float = 30.0) -> List[Dict]:
        """Get orders with caching"""
        cache_key = {
            'type': 'orders',
            'symbol': symbol,
            'since': since,
            'limit': limit,
            'params': params or {}
        }

        # Try cache first
        cached_orders = self.get(cache_key)
        if cached_orders is not None:
            self._stats['api_calls_saved'] += 1
            return cached_orders

        # Fetch from exchange (this would need to be implemented in exchange adapter)
        start_time = time.time()
        try:
            # Note: This assumes exchange_adapter has a fetch_orders method
            # Most exchanges use fetch_open_orders, fetch_closed_orders, etc.
            orders = []
            if hasattr(exchange_adapter, 'fetch_orders'):
                orders = exchange_adapter.fetch_orders(symbol, since, limit, params or {})

            # Cache the result
            self.put(cache_key, orders, ttl=cache_ttl)

            # Track request performance
            duration = time.time() - start_time
            self._request_history.append({
                'type': 'orders',
                'symbol': symbol,
                'duration': duration,
                'cached': False,
                'timestamp': time.time()
            })

            return orders

        except Exception as e:
            logger.error(f"Error fetching orders for {symbol}: {e}")
            return []

    def queue_bulk_request(self, symbols: List[str], request_type: str,
                          params: Optional[Dict] = None, priority: int = 1) -> None:
        """Queue a bulk request for batch processing"""
        bulk_request = BulkRequest(
            symbols=symbols,
            request_type=request_type,
            params=params or {},
            priority=priority
        )

        with self._bulk_request_lock:
            self._pending_bulk_requests.append(bulk_request)

    def _process_pending_bulk_requests(self) -> None:
        """Process all pending bulk requests"""
        with self._bulk_request_lock:
            if not self._pending_bulk_requests:
                return

            # Group requests by type and params
            grouped_requests = defaultdict(list)
            for request in self._pending_bulk_requests:
                group_key = (request.request_type, json.dumps(request.params, sort_keys=True))
                grouped_requests[group_key].append(request)

            self._pending_bulk_requests.clear()

        # Process each group
        for (request_type, params_str), requests in grouped_requests.items():
            try:
                self._process_bulk_group(request_type, requests)
                self._stats['bulk_requests_processed'] += len(requests)
            except Exception as e:
                logger.error(f"Error processing bulk {request_type} requests: {e}")

    def _process_bulk_group(self, request_type: str, requests: List[BulkRequest]) -> None:
        """Process a group of similar bulk requests"""
        # Collect all unique symbols
        all_symbols = set()
        for request in requests:
            all_symbols.update(request.symbols)

        symbols_list = list(all_symbols)

        # Sort by priority (high priority first)
        requests.sort(key=lambda r: r.priority, reverse=True)

        logger.debug(f"Processing bulk {request_type} request for {len(symbols_list)} symbols")

        # Note: This is a framework - actual bulk processing would depend on
        # exchange capabilities. Most exchanges don't support true bulk operations,
        # but we can optimize by batching sequential requests.

        # For now, we'll process in optimized batches
        batch_size = 10  # Process 10 symbols at a time
        for i in range(0, len(symbols_list), batch_size):
            batch_symbols = symbols_list[i:i + batch_size]
            try:
                self._process_symbol_batch(request_type, batch_symbols, requests[0].params)
            except Exception as e:
                logger.error(f"Error processing symbol batch {batch_symbols}: {e}")

    def _process_symbol_batch(self, request_type: str, symbols: List[str],
                             params: Dict[str, Any]) -> None:
        """Process a batch of symbols for a specific request type"""
        # This is where you'd implement optimized batch processing
        # For now, we'll mark it as processed
        logger.debug(f"Processed {request_type} batch for symbols: {symbols}")

    def invalidate_symbol(self, symbol: str) -> int:
        """Invalidate all cache entries for a symbol"""
        invalidated_count = 0

        with self._lock:
            keys_to_remove = []
            for key, entry in self._cache.items():
                try:
                    # Check if this cache entry is related to the symbol
                    if isinstance(entry.data, list) and entry.data:
                        # Check if first item has symbol field
                        if isinstance(entry.data[0], dict) and entry.data[0].get('symbol') == symbol:
                            keys_to_remove.append(key)
                    elif isinstance(entry.data, dict) and entry.data.get('symbol') == symbol:
                        keys_to_remove.append(key)
                except Exception:
                    pass

            for key in keys_to_remove:
                del self._cache[key]
                if key in self._access_order:
                    self._access_order.remove(key)
                invalidated_count += 1

        logger.debug(f"Invalidated {invalidated_count} cache entries for {symbol}")
        return invalidated_count

    def cleanup_expired(self) -> int:
        """Remove expired entries"""
        expired_count = 0

        with self._lock:
            time.time()
            keys_to_remove = []

            for key, entry in self._cache.items():
                if entry.is_expired:
                    keys_to_remove.append(key)

            for key in keys_to_remove:
                del self._cache[key]
                if key in self._access_order:
                    self._access_order.remove(key)
                expired_count += 1

        return expired_count

    def get_cache_stats(self) -> Dict[str, Any]:
        """Get cache statistics"""
        with self._lock:
            current_size = self._calculate_current_size()
            hit_rate = 0.0
            total_requests = self._stats['cache_hits'] + self._stats['cache_misses']
            if total_requests > 0:
                hit_rate = (self._stats['cache_hits'] / total_requests) * 100

            # Calculate average request times
            recent_requests = list(self._request_history)[-100:]  # Last 100 requests
            cached_times = [r['duration'] for r in recent_requests if r.get('cached', False)]
            uncached_times = [r['duration'] for r in recent_requests if not r.get('cached', False)]

            stats = {
                'entries': len(self._cache),
                'size_bytes': current_size,
                'size_mb': current_size / (1024 * 1024),
                'max_size_mb': self.max_size_bytes / (1024 * 1024),
                'utilization_percent': (current_size / self.max_size_bytes) * 100,
                'hit_rate_percent': hit_rate,
                'avg_cached_request_time': sum(cached_times) / len(cached_times) if cached_times else 0,
                'avg_uncached_request_time': sum(uncached_times) / len(uncached_times) if uncached_times else 0,
                'pending_bulk_requests': len(self._pending_bulk_requests),
                **self._stats
            }

            return stats

    def clear_cache(self) -> None:
        """Clear all cache entries"""
        with self._lock:
            self._cache.clear()
            self._access_order.clear()
            self._stats['total_size_bytes'] = 0


# Global cache instance
_global_trade_cache: Optional[TradeCache] = None


def get_trade_cache() -> TradeCache:
    """Get or create global trade cache"""
    global _global_trade_cache
    if _global_trade_cache is None:
        _global_trade_cache = TradeCache(default_ttl=300.0, max_size_mb=50.0)
        _global_trade_cache.start_bulk_processor(batch_interval=2.0)
    return _global_trade_cache


def shutdown_trade_cache():
    """Shutdown global trade cache"""
    global _global_trade_cache
    if _global_trade_cache:
        _global_trade_cache.stop_bulk_processor()
        _global_trade_cache = None
