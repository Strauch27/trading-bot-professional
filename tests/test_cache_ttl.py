#!/usr/bin/env python3
"""
Tests for services/cache_ttl.py

Tests soft-TTL cache behavior, LRU eviction, and request coalescing.
"""

import threading
import time

import pytest

from services.cache_ttl import CacheCoordinator, CacheEntry, TTLCache


class TestCacheEntry:
    """Test CacheEntry dataclass"""

    def test_cache_entry_properties(self):
        """Test CacheEntry age and status properties"""
        entry = CacheEntry(
            value={"price": 42000},
            stored_ts=time.time() - 1.0,  # 1 second ago
            ttl_s=5.0,
            soft_ttl_s=2.0
        )

        assert 0.9 <= entry.age <= 1.1  # ~1 second old
        assert entry.is_fresh is True  # Within soft TTL
        assert entry.is_stale is False
        assert entry.is_expired is False

    def test_cache_entry_stale(self):
        """Test stale cache entry"""
        entry = CacheEntry(
            value={"price": 42000},
            stored_ts=time.time() - 3.0,  # 3 seconds ago
            ttl_s=5.0,
            soft_ttl_s=2.0
        )

        assert entry.is_fresh is False
        assert entry.is_stale is True  # Beyond soft TTL, within hard TTL
        assert entry.is_expired is False

    def test_cache_entry_expired(self):
        """Test expired cache entry"""
        entry = CacheEntry(
            value={"price": 42000},
            stored_ts=time.time() - 6.0,  # 6 seconds ago
            ttl_s=5.0,
            soft_ttl_s=2.0
        )

        assert entry.is_fresh is False
        assert entry.is_stale is False
        assert entry.is_expired is True  # Beyond hard TTL


class TestTTLCache:
    """Test TTLCache with soft-TTL support"""

    def test_cache_hit_fresh(self):
        """Test fresh cache hit"""
        cache = TTLCache(max_items=100)
        cache.set("BTC/USDT", {"price": 42000}, ttl_s=5.0, soft_ttl_s=2.0)

        result = cache.get("BTC/USDT")
        assert result is not None

        value, status, meta = result
        assert value == {"price": 42000}
        assert status == "HIT"

    def test_cache_hit_stale(self):
        """Test stale cache hit"""
        cache = TTLCache(max_items=100)
        cache.set("BTC/USDT", {"price": 42000}, ttl_s=5.0, soft_ttl_s=0.5)

        # Wait for soft TTL to expire
        time.sleep(0.6)

        result = cache.get("BTC/USDT")
        assert result is not None

        value, status, meta = result
        assert value == {"price": 42000}
        assert status == "STALE"

    def test_cache_miss_expired(self):
        """Test cache miss on expiration"""
        cache = TTLCache(max_items=100)
        cache.set("BTC/USDT", {"price": 42000}, ttl_s=0.5, soft_ttl_s=0.2)

        # Wait for hard TTL to expire
        time.sleep(0.6)

        result = cache.get("BTC/USDT")
        assert result is None

    def test_cache_miss_not_found(self):
        """Test cache miss when key not found"""
        cache = TTLCache(max_items=100)
        result = cache.get("BTC/USDT")
        assert result is None

    def test_soft_ttl_validation(self):
        """Test that soft_ttl must be <= ttl"""
        cache = TTLCache(max_items=100)

        with pytest.raises(ValueError, match="soft_ttl_s.*must be"):
            cache.set("BTC/USDT", {"price": 42000}, ttl_s=2.0, soft_ttl_s=5.0)

    def test_lru_eviction(self):
        """Test LRU eviction when cache is full"""
        cache = TTLCache(max_items=3)

        # Fill cache
        cache.set("A", 1, ttl_s=10.0, soft_ttl_s=5.0)
        cache.set("B", 2, ttl_s=10.0, soft_ttl_s=5.0)
        cache.set("C", 3, ttl_s=10.0, soft_ttl_s=5.0)

        # Access A to make it recently used
        cache.get("A")

        # Add D - should evict B (oldest, least recently used)
        cache.set("D", 4, ttl_s=10.0, soft_ttl_s=5.0)

        assert cache.get("A") is not None  # Still there
        assert cache.get("B") is None      # Evicted
        assert cache.get("C") is not None  # Still there
        assert cache.get("D") is not None  # New entry

    def test_cleanup_expired(self):
        """Test cleanup of expired entries"""
        cache = TTLCache(max_items=100)

        cache.set("A", 1, ttl_s=0.3, soft_ttl_s=0.1)
        cache.set("B", 2, ttl_s=0.3, soft_ttl_s=0.1)
        cache.set("C", 3, ttl_s=10.0, soft_ttl_s=5.0)  # Won't expire

        time.sleep(0.4)

        removed = cache.cleanup_expired()
        assert removed == 2  # A and B expired

        assert cache.get("C") is not None  # Still there

    def test_statistics(self):
        """Test cache statistics"""
        cache = TTLCache(max_items=100)

        # Populate cache
        cache.set("A", 1, ttl_s=5.0, soft_ttl_s=2.0)
        cache.set("B", 2, ttl_s=5.0, soft_ttl_s=2.0)

        # Hit
        cache.get("A")

        # Miss
        cache.get("Z")

        stats = cache.get_statistics()
        assert stats["hits"] == 1
        assert stats["misses"] == 1
        assert stats["sets"] == 2
        assert stats["size"] == 2
        assert stats["hit_rate"] == 0.5

    def test_invalidate(self):
        """Test invalidating cache entry"""
        cache = TTLCache(max_items=100)
        cache.set("A", 1, ttl_s=5.0, soft_ttl_s=2.0)

        assert cache.invalidate("A") is True
        assert cache.get("A") is None

        assert cache.invalidate("B") is False  # Doesn't exist

    def test_invalidate_pattern(self):
        """Test invalidating by prefix"""
        cache = TTLCache(max_items=100)
        cache.set("BTC/USDT", 1, ttl_s=5.0, soft_ttl_s=2.0)
        cache.set("BTC/EUR", 2, ttl_s=5.0, soft_ttl_s=2.0)
        cache.set("ETH/USDT", 3, ttl_s=5.0, soft_ttl_s=2.0)

        removed = cache.invalidate_pattern("BTC")
        assert removed == 2

        assert cache.get("BTC/USDT") is None
        assert cache.get("BTC/EUR") is None
        assert cache.get("ETH/USDT") is not None

    def test_clear(self):
        """Test clearing cache"""
        cache = TTLCache(max_items=100)
        cache.set("A", 1, ttl_s=5.0, soft_ttl_s=2.0)
        cache.set("B", 2, ttl_s=5.0, soft_ttl_s=2.0)

        cache.clear()
        assert cache.size() == 0
        assert cache.get("A") is None


class TestCacheCoordinator:
    """Test CacheCoordinator for request coalescing"""

    def test_get_or_fetch_cache_hit(self):
        """Test get_or_fetch with cache hit"""
        cache = TTLCache(max_items=100)
        coordinator = CacheCoordinator(cache)

        # Pre-populate cache
        cache.set("BTC/USDT", {"price": 42000}, ttl_s=5.0, soft_ttl_s=2.0)

        fetch_called = False

        def fetch_fn():
            nonlocal fetch_called
            fetch_called = True
            return {"price": 99999}

        value, status = coordinator.get_or_fetch(
            "BTC/USDT",
            fetch_fn,
            ttl_s=5.0,
            soft_ttl_s=2.0
        )

        assert value == {"price": 42000}
        assert status == "HIT"
        assert fetch_called is False  # Should not fetch

    def test_get_or_fetch_cache_miss(self):
        """Test get_or_fetch with cache miss"""
        cache = TTLCache(max_items=100)
        coordinator = CacheCoordinator(cache)

        fetch_called = False

        def fetch_fn():
            nonlocal fetch_called
            fetch_called = True
            return {"price": 42000}

        value, status = coordinator.get_or_fetch(
            "BTC/USDT",
            fetch_fn,
            ttl_s=5.0,
            soft_ttl_s=2.0
        )

        assert value == {"price": 42000}
        assert status == "MISS"
        assert fetch_called is True

    def test_request_coalescing(self):
        """Test that concurrent requests coalesce to single fetch"""
        cache = TTLCache(max_items=100)
        coordinator = CacheCoordinator(cache)

        fetch_count = 0
        fetch_lock = threading.Lock()

        def slow_fetch_fn():
            nonlocal fetch_count
            with fetch_lock:
                fetch_count += 1
            time.sleep(0.1)  # Simulate slow fetch
            return {"price": 42000}

        # Launch 10 concurrent requests
        results = []
        threads = []

        def worker():
            value, status = coordinator.get_or_fetch(
                "BTC/USDT",
                slow_fetch_fn,
                ttl_s=5.0,
                soft_ttl_s=2.0
            )
            results.append((value, status))

        for _ in range(10):
            t = threading.Thread(target=worker)
            threads.append(t)
            t.start()

        for t in threads:
            t.join()

        # All threads should get the same result
        assert len(results) == 10
        assert all(v == {"price": 42000} for v, _ in results)

        # But fetch should only happen once (coalescing)
        assert fetch_count == 1


class TestThreadSafety:
    """Test thread safety of TTLCache"""

    def test_concurrent_access(self):
        """Test concurrent reads and writes"""
        cache = TTLCache(max_items=100)
        errors = []

        def writer(symbol_id):
            try:
                for i in range(100):
                    cache.set(f"SYM_{symbol_id}", i, ttl_s=5.0, soft_ttl_s=2.0)
                    time.sleep(0.001)
            except Exception as e:
                errors.append(e)

        def reader(symbol_id):
            try:
                for _ in range(100):
                    cache.get(f"SYM_{symbol_id}")
                    time.sleep(0.001)
            except Exception as e:
                errors.append(e)

        threads = []
        for i in range(5):
            threads.append(threading.Thread(target=writer, args=(i,)))
            threads.append(threading.Thread(target=reader, args=(i,)))

        for t in threads:
            t.start()

        for t in threads:
            t.join()

        assert len(errors) == 0  # No race conditions


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
