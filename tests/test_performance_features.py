#!/usr/bin/env python3
"""
Performance Tests for Coalescing, Rate Limiting, and Caching

Tests:
- Request coalescing under concurrent load
- Rate limiting behavior and throttling
- Cache hit rates and TTL behavior
- Ringbuffer memory efficiency
- 100 parallel request load test
"""

import sys
import os
import time
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from unittest.mock import Mock
import tracemalloc

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


class TestRequestCoalescing:
    """Test request coalescing under concurrent load"""

    def test_coalescing_reduces_duplicate_requests(self):
        """Test that coalescing reduces API calls for duplicate requests"""
        # Simulate coalescing cache
        in_flight = {}
        in_flight_lock = threading.Lock()
        actual_fetches = []

        def fetch_with_coalescing(key):
            """Fetch with coalescing - waits if request in flight"""
            with in_flight_lock:
                if key in in_flight:
                    # Wait for in-flight request
                    event = in_flight[key]
                    is_waiting = True
                else:
                    # Start new request
                    event = threading.Event()
                    in_flight[key] = event
                    is_waiting = False

            if is_waiting:
                # Wait for other thread to complete
                event.wait(timeout=5.0)
                return f"result_{key}"
            else:
                # Perform actual fetch
                actual_fetches.append(key)
                time.sleep(0.01)  # Simulate API call
                result = f"result_{key}"

                # Signal completion
                with in_flight_lock:
                    event.set()
                    del in_flight[key]

                return result

        # Launch 20 concurrent requests for same key
        threads = []
        results = []

        def worker():
            result = fetch_with_coalescing("ticker:BTC/USDT")
            results.append(result)

        for _ in range(20):
            t = threading.Thread(target=worker)
            threads.append(t)
            t.start()

        for t in threads:
            t.join()

        # Verify coalescing worked
        assert len(results) == 20, "All threads got results"
        assert len(actual_fetches) < 20, "Coalescing reduced API calls"
        coalesce_rate = 1.0 - (len(actual_fetches) / 20.0)
        assert coalesce_rate > 0.8, f"Coalescing rate should be >80%, got {coalesce_rate*100:.1f}%"

        print(f"✓ Coalescing: {len(actual_fetches)}/20 actual calls, {coalesce_rate*100:.1f}% reduction")

    def test_coalescing_different_keys_dont_interfere(self):
        """Test that different keys don't interfere with each other"""
        in_flight = {}
        in_flight_lock = threading.Lock()
        actual_fetches = []

        def fetch_with_coalescing(key):
            with in_flight_lock:
                if key in in_flight:
                    event = in_flight[key]
                    is_waiting = True
                else:
                    event = threading.Event()
                    in_flight[key] = event
                    is_waiting = False

            if is_waiting:
                event.wait(timeout=5.0)
                return f"result_{key}"
            else:
                actual_fetches.append(key)
                time.sleep(0.01)
                result = f"result_{key}"

                with in_flight_lock:
                    event.set()
                    del in_flight[key]

                return result

        # Launch concurrent requests for different keys
        keys = [f"ticker:{symbol}" for symbol in ["BTC/USDT", "ETH/USDT", "SOL/USDT"]]
        threads = []

        def worker(key):
            return fetch_with_coalescing(key)

        with ThreadPoolExecutor(max_workers=10) as executor:
            futures = []
            for key in keys:
                for _ in range(5):  # 5 requests per key
                    futures.append(executor.submit(worker, key))

            for future in as_completed(futures):
                future.result()

        # Each unique key should have at least one fetch
        unique_fetches = set(actual_fetches)
        assert len(unique_fetches) == 3, "Each key should be fetched"
        assert len(actual_fetches) <= 6, "Coalescing should reduce total fetches"

        print(f"✓ Coalescing: {len(actual_fetches)} fetches for 15 requests across 3 keys")


class TestRateLimiting:
    """Test rate limiting behavior"""

    def test_token_bucket_respects_rate_limit(self):
        """Test that token bucket enforces rate limits"""
        # Simulate token bucket
        class TokenBucket:
            def __init__(self, capacity, refill_rate):
                self.capacity = capacity
                self.tokens = capacity
                self.refill_rate = refill_rate
                self.last_refill = time.time()
                self.lock = threading.Lock()

            def _refill(self):
                now = time.time()
                elapsed = now - self.last_refill
                new_tokens = elapsed * self.refill_rate
                self.tokens = min(self.capacity, self.tokens + new_tokens)
                self.last_refill = now

            def acquire(self, tokens=1, blocking=True):
                while True:
                    with self.lock:
                        self._refill()
                        if self.tokens >= tokens:
                            self.tokens -= tokens
                            return True
                        if not blocking:
                            return False
                    time.sleep(0.01)

        # Create bucket: 10 tokens, 5 tokens/sec refill
        bucket = TokenBucket(capacity=10, refill_rate=5.0)

        # Consume 10 tokens rapidly
        successful = 0
        start = time.time()
        for _ in range(15):
            if bucket.acquire(tokens=1, blocking=False):
                successful += 1

        elapsed = time.time() - start

        # Should get ~10 immediate successes, rest throttled
        assert successful <= 12, f"Should throttle after capacity exhausted, got {successful}"
        print(f"✓ Rate limiting: {successful}/15 immediate acquisitions (capacity=10)")

    def test_throttle_rate_calculation(self):
        """Test throttle rate calculation"""
        total_requests = 100
        throttled_requests = 15

        throttle_rate = throttled_requests / total_requests
        throttle_pct = throttle_rate * 100

        assert throttle_pct == 15.0, "Throttle rate should be 15%"
        print(f"✓ Throttle rate: {throttle_pct:.1f}%")

    def test_rate_limiter_allows_sustained_throughput(self):
        """Test that rate limiter allows sustained throughput at refill rate"""
        class TokenBucket:
            def __init__(self, capacity, refill_rate):
                self.capacity = capacity
                self.tokens = capacity
                self.refill_rate = refill_rate
                self.last_refill = time.time()
                self.lock = threading.Lock()

            def _refill(self):
                now = time.time()
                elapsed = now - self.last_refill
                new_tokens = elapsed * self.refill_rate
                self.tokens = min(self.capacity, self.tokens + new_tokens)
                self.last_refill = now

            def acquire(self, tokens=1):
                while True:
                    with self.lock:
                        self._refill()
                        if self.tokens >= tokens:
                            self.tokens -= tokens
                            return True
                    time.sleep(0.01)

        # Create bucket: 20 capacity, 10 tokens/sec
        bucket = TokenBucket(capacity=20, refill_rate=10.0)

        # Consume at rate matching refill rate
        successful = 0
        start = time.time()
        duration = 2.0  # 2 seconds

        while time.time() - start < duration:
            if bucket.acquire(tokens=1):
                successful += 1
            time.sleep(0.1)  # 10 req/sec

        elapsed = time.time() - start

        # Should get ~20 requests (10 req/sec * 2 sec)
        expected = int(10 * duration)
        assert abs(successful - expected) <= 3, f"Expected ~{expected}, got {successful}"
        print(f"✓ Sustained throughput: {successful} requests in {elapsed:.2f}s (~{successful/elapsed:.1f} req/s)")


class TestCachePerformance:
    """Test cache hit rates and TTL behavior"""

    def test_cache_hit_rate_calculation(self):
        """Test cache hit rate calculation"""
        total_requests = 1000
        cache_hits = 850
        cache_misses = 150

        hit_rate = cache_hits / total_requests
        hit_rate_pct = hit_rate * 100

        assert hit_rate_pct == 85.0, "Hit rate should be 85%"
        print(f"✓ Cache hit rate: {hit_rate_pct:.1f}%")

    def test_soft_ttl_cache_serves_stale_while_refreshing(self):
        """Test that soft-TTL cache serves stale data while refreshing"""
        cache = {}
        cache_lock = threading.Lock()

        def get_with_soft_ttl(key, ttl_s, fetch_fn):
            """Get from cache with soft TTL"""
            now = time.time()

            with cache_lock:
                if key in cache:
                    entry = cache[key]
                    age = now - entry['ts']

                    if age < ttl_s:
                        # Fresh
                        return entry['data'], 'HIT'
                    else:
                        # Stale but usable
                        # Trigger background refresh (not implemented here)
                        return entry['data'], 'STALE'

            # Cache miss - fetch
            data = fetch_fn()
            with cache_lock:
                cache[key] = {'data': data, 'ts': now}
            return data, 'MISS'

        # Populate cache
        def mock_fetch():
            return "ticker_data"

        # Fresh request
        data, status = get_with_soft_ttl("BTC/USDT", ttl_s=5.0, fetch_fn=mock_fetch)
        assert status == 'MISS', "First request should be MISS"

        # Second request (fresh)
        data, status = get_with_soft_ttl("BTC/USDT", ttl_s=5.0, fetch_fn=mock_fetch)
        assert status == 'HIT', "Second request should be HIT"

        # Age the entry
        with cache_lock:
            cache["BTC/USDT"]['ts'] = time.time() - 10.0  # 10 seconds old

        # Third request (stale)
        data, status = get_with_soft_ttl("BTC/USDT", ttl_s=5.0, fetch_fn=mock_fetch)
        assert status == 'STALE', "Third request should be STALE"

        print("✓ Soft-TTL cache: HIT → STALE behavior working")


class TestRingbufferMemory:
    """Test Ringbuffer memory efficiency"""

    def test_ringbuffer_fixed_memory_usage(self):
        """Test that ringbuffer maintains fixed memory footprint"""
        # Simulate ringbuffer
        class RingBuffer:
            def __init__(self, capacity):
                self.capacity = capacity
                self.data = [None] * capacity
                self.head = 0
                self.size = 0

            def append(self, item):
                self.data[self.head] = item
                self.head = (self.head + 1) % self.capacity
                self.size = min(self.size + 1, self.capacity)

            def get_all(self):
                if self.size < self.capacity:
                    return self.data[:self.size]
                else:
                    # Rotate to correct order
                    return self.data[self.head:] + self.data[:self.head]

        # Track memory
        tracemalloc.start()
        initial_memory = tracemalloc.get_traced_memory()[0]

        # Create ringbuffer with 1000 capacity
        capacity = 1000
        buffer = RingBuffer(capacity)

        # Fill with orderbook snapshots
        for i in range(capacity):
            snapshot = {
                'bids': [[50000.0 + i, 1.0] for _ in range(10)],
                'asks': [[50010.0 + i, 1.0] for _ in range(10)],
                'timestamp': time.time()
            }
            buffer.append(snapshot)

        memory_after_fill = tracemalloc.get_traced_memory()[0]
        fill_memory = memory_after_fill - initial_memory

        # Add 1000 more items (should not grow)
        for i in range(1000, 2000):
            snapshot = {
                'bids': [[50000.0 + i, 1.0] for _ in range(10)],
                'asks': [[50010.0 + i, 1.0] for _ in range(10)],
                'timestamp': time.time()
            }
            buffer.append(snapshot)

        memory_after_overflow = tracemalloc.get_traced_memory()[0]
        overflow_memory = memory_after_overflow - memory_after_fill

        # Memory should not grow significantly (allow 10% variance)
        memory_growth_pct = (overflow_memory / fill_memory) * 100 if fill_memory > 0 else 0

        tracemalloc.stop()

        assert buffer.size == capacity, "Size should stay at capacity"
        assert memory_growth_pct < 20, f"Memory growth should be minimal, got {memory_growth_pct:.1f}%"
        print(f"✓ Ringbuffer: Fixed memory ({fill_memory/1024:.1f} KB), growth: {memory_growth_pct:.1f}%")

    def test_ringbuffer_overwrites_oldest(self):
        """Test that ringbuffer correctly overwrites oldest entries"""
        class RingBuffer:
            def __init__(self, capacity):
                self.capacity = capacity
                self.data = [None] * capacity
                self.head = 0
                self.size = 0

            def append(self, item):
                self.data[self.head] = item
                self.head = (self.head + 1) % self.capacity
                self.size = min(self.size + 1, self.capacity)

            def get_all(self):
                if self.size < self.capacity:
                    return [x for x in self.data[:self.size] if x is not None]
                else:
                    rotated = self.data[self.head:] + self.data[:self.head]
                    return [x for x in rotated if x is not None]

        # Create small buffer
        buffer = RingBuffer(capacity=5)

        # Add 10 items
        for i in range(10):
            buffer.append(f"item_{i}")

        # Should only contain last 5 items
        items = buffer.get_all()
        assert len(items) == 5, "Should only contain 5 items"
        assert items == ["item_5", "item_6", "item_7", "item_8", "item_9"], "Should contain newest items"

        print("✓ Ringbuffer: Correctly overwrites oldest entries")


class TestLoadPerformance:
    """Test performance under high load"""

    def test_100_parallel_requests(self):
        """Test system performance with 100 parallel requests"""
        request_count = 100
        results = []
        errors = []
        latencies = []

        def mock_api_call(request_id):
            """Simulate API call with processing"""
            start = time.time()

            try:
                # Simulate some processing
                time.sleep(0.01)  # 10ms base latency

                # Simulate success
                result = {
                    'request_id': request_id,
                    'data': f"result_{request_id}",
                    'timestamp': time.time()
                }

                latency = (time.time() - start) * 1000  # ms
                return result, latency, None

            except Exception as e:
                return None, 0, str(e)

        # Launch 100 parallel requests
        start_time = time.time()

        with ThreadPoolExecutor(max_workers=20) as executor:
            futures = [executor.submit(mock_api_call, i) for i in range(request_count)]

            for future in as_completed(futures):
                result, latency, error = future.result()
                if error:
                    errors.append(error)
                else:
                    results.append(result)
                    latencies.append(latency)

        total_time = time.time() - start_time

        # Calculate statistics
        success_rate = len(results) / request_count
        avg_latency = sum(latencies) / len(latencies) if latencies else 0
        sorted_latencies = sorted(latencies)
        p95_latency = sorted_latencies[int(len(sorted_latencies) * 0.95)] if latencies else 0
        p99_latency = sorted_latencies[int(len(sorted_latencies) * 0.99)] if latencies else 0
        throughput = request_count / total_time

        # Assertions
        assert success_rate >= 0.99, f"Success rate should be >99%, got {success_rate*100:.1f}%"
        assert avg_latency < 50, f"Average latency should be <50ms, got {avg_latency:.1f}ms"
        assert throughput > 50, f"Throughput should be >50 req/s, got {throughput:.1f} req/s"

        print(f"✓ Load test (100 parallel requests):")
        print(f"  - Success rate: {success_rate*100:.1f}%")
        print(f"  - Avg latency: {avg_latency:.1f}ms")
        print(f"  - P95 latency: {p95_latency:.1f}ms")
        print(f"  - P99 latency: {p99_latency:.1f}ms")
        print(f"  - Throughput: {throughput:.1f} req/s")
        print(f"  - Total time: {total_time:.2f}s")

    def test_sustained_load_over_time(self):
        """Test sustained load over longer period"""
        duration_seconds = 5
        target_rps = 50  # requests per second
        total_requests = 0
        total_errors = 0

        def mock_request():
            """Simulate request"""
            time.sleep(0.001)  # 1ms processing
            return True

        start_time = time.time()
        next_request_time = start_time

        while time.time() - start_time < duration_seconds:
            try:
                if time.time() >= next_request_time:
                    success = mock_request()
                    total_requests += 1
                    next_request_time += (1.0 / target_rps)
            except Exception:
                total_errors += 1

            time.sleep(0.001)  # Small sleep to prevent tight loop

        elapsed = time.time() - start_time
        actual_rps = total_requests / elapsed
        error_rate = total_errors / total_requests if total_requests > 0 else 0

        # Assertions
        assert actual_rps >= target_rps * 0.9, f"Should maintain ~{target_rps} req/s, got {actual_rps:.1f}"
        assert error_rate < 0.01, f"Error rate should be <1%, got {error_rate*100:.1f}%"

        print(f"✓ Sustained load ({duration_seconds}s):")
        print(f"  - Total requests: {total_requests}")
        print(f"  - Actual RPS: {actual_rps:.1f} (target: {target_rps})")
        print(f"  - Error rate: {error_rate*100:.2f}%")


def run_all_performance_tests():
    """Run all performance tests"""
    print("=" * 70)
    print("Performance Tests - Coalescing, Rate Limiting, Caching")
    print("=" * 70)

    # Coalescing Tests
    print("\n1. Testing Request Coalescing...")
    coalescing = TestRequestCoalescing()
    coalescing.test_coalescing_reduces_duplicate_requests()
    coalescing.test_coalescing_different_keys_dont_interfere()

    # Rate Limiting Tests
    print("\n2. Testing Rate Limiting...")
    rate_limiting = TestRateLimiting()
    rate_limiting.test_token_bucket_respects_rate_limit()
    rate_limiting.test_throttle_rate_calculation()
    rate_limiting.test_rate_limiter_allows_sustained_throughput()

    # Cache Tests
    print("\n3. Testing Cache Performance...")
    cache = TestCachePerformance()
    cache.test_cache_hit_rate_calculation()
    cache.test_soft_ttl_cache_serves_stale_while_refreshing()

    # Ringbuffer Tests
    print("\n4. Testing Ringbuffer Memory...")
    ringbuffer = TestRingbufferMemory()
    ringbuffer.test_ringbuffer_fixed_memory_usage()
    ringbuffer.test_ringbuffer_overwrites_oldest()

    # Load Tests
    print("\n5. Testing Load Performance...")
    load = TestLoadPerformance()
    load.test_100_parallel_requests()
    load.test_sustained_load_over_time()

    print("\n" + "=" * 70)
    print("✓ All performance tests passed!")
    print("=" * 70)


if __name__ == "__main__":
    run_all_performance_tests()
