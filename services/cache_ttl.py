#!/usr/bin/env python3
"""
TTL Cache with Soft-TTL Support

Implements L1 cache with two-tier TTL strategy:
- Soft TTL: Serve cached data, trigger async refresh
- Hard TTL: Cache miss, fetch synchronously

Benefits:
- Reduces latency (serve stale while refreshing)
- Prevents thundering herd
- Thread-safe

Example:
    >>> cache = TTLCache(max_items=1000)
    >>> cache.set("BTC/USDT", {"price": 42000}, ttl_s=5.0, soft_ttl_s=2.0)
    >>> value, status = cache.get("BTC/USDT")
    >>> # After 2.5s: status = "STALE" (trigger refresh, serve stale data)
    >>> # After 6s:   status = "MISS" (hard miss)
"""

import time
import threading
from typing import Any, Optional, Tuple, Dict
from dataclasses import dataclass, field
from collections import OrderedDict


@dataclass
class CacheEntry:
    """
    Cache entry with dual TTL.

    Attributes:
        value: Cached value (any type)
        stored_ts: Unix timestamp when stored
        ttl_s: Hard TTL in seconds (absolute expiration)
        soft_ttl_s: Soft TTL in seconds (stale-if-revalidating)
        meta: Metadata dict (e.g., fetch latency, source)
    """
    value: Any
    stored_ts: float
    ttl_s: float
    soft_ttl_s: float
    meta: Dict[str, Any] = field(default_factory=dict)

    @property
    def age(self) -> float:
        """Current age of entry in seconds"""
        return time.time() - self.stored_ts

    @property
    def is_fresh(self) -> bool:
        """True if within soft TTL"""
        return self.age <= self.soft_ttl_s

    @property
    def is_stale(self) -> bool:
        """True if beyond soft TTL but within hard TTL"""
        return self.soft_ttl_s < self.age <= self.ttl_s

    @property
    def is_expired(self) -> bool:
        """True if beyond hard TTL"""
        return self.age > self.ttl_s


class TTLCache:
    """
    Thread-safe TTL cache with soft-TTL support.

    Features:
    - Soft TTL: Serve stale, signal refresh needed
    - Hard TTL: Absolute expiration
    - LRU eviction when full
    - Thread-safe with RLock
    - Statistics tracking

    Thread-Safety:
        All public methods are thread-safe via RLock.
        Use external locking for atomic get-or-set operations.
    """

    def __init__(self, max_items: int = 5000):
        """
        Initialize TTL cache.

        Args:
            max_items: Maximum number of cache entries (LRU eviction)
        """
        self.max_items = max_items
        self._data: OrderedDict[str, CacheEntry] = OrderedDict()
        self._lock = threading.RLock()
        self._stats = {
            "hits": 0,
            "stale_hits": 0,
            "misses": 0,
            "sets": 0,
            "evictions": 0,
            "invalidations": 0
        }

    def get(self, key: str) -> Optional[Tuple[Any, str, Dict[str, Any]]]:
        """
        Get value from cache with status.

        Returns:
            Tuple of (value, status, meta) where status is:
            - "HIT": Fresh cache hit (within soft TTL)
            - "STALE": Stale hit (beyond soft TTL, within hard TTL)
            - None: Cache miss (expired or not found)

        Example:
            >>> val, status, meta = cache.get("BTC/USDT") or (None, "MISS", {})
            >>> if status == "STALE":
            ...     asyncio.create_task(refresh_async(key))  # Refresh in background
            >>> return val  # Serve stale data immediately
        """
        with self._lock:
            entry = self._data.get(key)
            if not entry:
                self._stats["misses"] += 1
                return None

            # Check expiration
            if entry.is_expired:
                # Hard miss - remove from cache
                del self._data[key]
                self._stats["misses"] += 1
                return None

            # Update access order (LRU)
            self._data.move_to_end(key)

            if entry.is_fresh:
                # Fresh hit
                self._stats["hits"] += 1
                return (entry.value, "HIT", entry.meta)
            else:
                # Stale hit (serve stale, trigger refresh)
                self._stats["stale_hits"] += 1
                return (entry.value, "STALE", entry.meta)

    def set(
        self,
        key: str,
        value: Any,
        ttl_s: float,
        soft_ttl_s: float,
        meta: Optional[Dict[str, Any]] = None
    ) -> None:
        """
        Store value in cache with dual TTL.

        Args:
            key: Cache key
            value: Value to cache
            ttl_s: Hard TTL in seconds (absolute expiration)
            soft_ttl_s: Soft TTL in seconds (stale threshold)
            meta: Optional metadata dict

        Raises:
            ValueError: If soft_ttl_s > ttl_s
        """
        if soft_ttl_s > ttl_s:
            raise ValueError(
                f"soft_ttl_s ({soft_ttl_s}) must be <= ttl_s ({ttl_s})"
            )

        with self._lock:
            # Evict oldest if full
            if len(self._data) >= self.max_items and key not in self._data:
                self._evict_oldest()

            entry = CacheEntry(
                value=value,
                stored_ts=time.time(),
                ttl_s=ttl_s,
                soft_ttl_s=soft_ttl_s,
                meta=meta or {}
            )

            self._data[key] = entry
            self._data.move_to_end(key)  # Mark as most recently used
            self._stats["sets"] += 1

    def invalidate(self, key: str) -> bool:
        """
        Invalidate (remove) cache entry.

        Args:
            key: Cache key

        Returns:
            True if entry was removed, False if not found
        """
        with self._lock:
            if key in self._data:
                del self._data[key]
                self._stats["invalidations"] += 1
                return True
            return False

    def invalidate_pattern(self, prefix: str) -> int:
        """
        Invalidate all keys matching prefix.

        Args:
            prefix: Key prefix to match

        Returns:
            Number of entries invalidated
        """
        with self._lock:
            matching_keys = [k for k in self._data.keys() if k.startswith(prefix)]
            for key in matching_keys:
                del self._data[key]
                self._stats["invalidations"] += 1
            return len(matching_keys)

    def _evict_oldest(self) -> None:
        """Evict oldest entry (FIFO)"""
        if self._data:
            self._data.popitem(last=False)  # Remove first (oldest)
            self._stats["evictions"] += 1

    def cleanup_expired(self) -> int:
        """
        Remove all expired entries.

        Returns:
            Number of entries cleaned up
        """
        with self._lock:
            now = time.time()
            expired_keys = []

            for key, entry in self._data.items():
                if (now - entry.stored_ts) > entry.ttl_s:
                    expired_keys.append(key)

            for key in expired_keys:
                del self._data[key]

            return len(expired_keys)

    def get_statistics(self) -> Dict[str, Any]:
        """
        Get cache statistics.

        Returns:
            Dict with hits, misses, hit rate, etc.
        """
        with self._lock:
            stats = self._stats.copy()
            stats["size"] = len(self._data)
            stats["max_items"] = self.max_items

            total_requests = stats["hits"] + stats["stale_hits"] + stats["misses"]
            if total_requests > 0:
                stats["hit_rate"] = stats["hits"] / total_requests
                stats["stale_rate"] = stats["stale_hits"] / total_requests
                stats["miss_rate"] = stats["misses"] / total_requests
                stats["total_requests"] = total_requests
            else:
                stats["hit_rate"] = 0.0
                stats["stale_rate"] = 0.0
                stats["miss_rate"] = 0.0
                stats["total_requests"] = 0

            return stats

    def clear(self) -> None:
        """Clear all cached data"""
        with self._lock:
            self._data.clear()

    def size(self) -> int:
        """Get current cache size"""
        with self._lock:
            return len(self._data)

    def keys(self) -> list:
        """Get all cache keys"""
        with self._lock:
            return list(self._data.keys())

    def __len__(self) -> int:
        """Cache size"""
        return self.size()

    def __contains__(self, key: str) -> bool:
        """Check if key exists (regardless of expiration)"""
        with self._lock:
            return key in self._data


class CacheCoordinator:
    """
    Coordinates cache operations with request coalescing.

    Prevents thundering herd by ensuring only one concurrent
    fetch per key.

    Example:
        >>> coordinator = CacheCoordinator(cache)
        >>> value = coordinator.get_or_fetch(
        ...     "BTC/USDT",
        ...     fetch_fn=lambda: exchange.fetch_ticker("BTC/USDT"),
        ...     ttl_s=5.0,
        ...     soft_ttl_s=2.0
        ... )
    """

    def __init__(self, cache: TTLCache):
        """
        Initialize coordinator.

        Args:
            cache: TTLCache instance
        """
        self.cache = cache
        self._fetch_locks: Dict[str, threading.Lock] = {}
        self._locks_lock = threading.RLock()

    def _get_lock(self, key: str) -> threading.Lock:
        """Get or create lock for key"""
        with self._locks_lock:
            if key not in self._fetch_locks:
                self._fetch_locks[key] = threading.Lock()
            return self._fetch_locks[key]

    def get_or_fetch(
        self,
        key: str,
        fetch_fn,
        ttl_s: float,
        soft_ttl_s: float,
        refresh_stale: bool = False
    ) -> Tuple[Any, str]:
        """
        Get from cache or fetch with coalescing.

        Args:
            key: Cache key
            fetch_fn: Callable that fetches fresh data
            ttl_s: Hard TTL
            soft_ttl_s: Soft TTL
            refresh_stale: If True, refresh stale data in background

        Returns:
            Tuple of (value, status) where status is HIT/STALE/MISS
        """
        # Try cache first
        result = self.cache.get(key)
        if result:
            value, status, meta = result

            # If stale and refresh requested, trigger background refresh
            if status == "STALE" and refresh_stale:
                # Note: In sync code, we can't truly do async refresh
                # Caller should handle this (e.g., with ThreadPoolExecutor)
                pass

            return value, status

        # Cache miss - fetch with coalescing
        lock = self._get_lock(key)
        with lock:
            # Re-check cache (another thread might have fetched)
            result = self.cache.get(key)
            if result:
                value, status, _ = result
                return value, status

            # Fetch fresh data
            value = fetch_fn()

            # Store in cache
            self.cache.set(
                key,
                value,
                ttl_s=ttl_s,
                soft_ttl_s=soft_ttl_s,
                meta={"fetched_at": time.time()}
            )

            return value, "MISS"
