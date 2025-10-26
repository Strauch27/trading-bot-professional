#!/usr/bin/env python3
"""
Test Runner for Market Data Services

Runs tests for time_utils, cache_ttl, and md_audit modules.
"""

import json
import os
import sys
import tempfile
import time
from pathlib import Path

# Add project root to path
project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, project_root)

# Import directly from module files to avoid dependency issues
import importlib.util


def import_module_from_file(module_name, file_path):
    """Import a Python module from a file path"""
    spec = importlib.util.spec_from_file_location(module_name, file_path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module

# Import modules directly
time_utils = import_module_from_file("time_utils", os.path.join(project_root, "services", "time_utils.py"))
cache_ttl = import_module_from_file("cache_ttl", os.path.join(project_root, "services", "cache_ttl.py"))
md_audit = import_module_from_file("md_audit", os.path.join(project_root, "services", "md_audit.py"))

# Extract needed functions/classes
align_since_to_closed = time_utils.align_since_to_closed
last_closed_candle_open_ms = time_utils.last_closed_candle_open_ms
is_closed_candle = time_utils.is_closed_candle
filter_closed_candles = time_utils.filter_closed_candles
validate_ohlcv_monotonic = time_utils.validate_ohlcv_monotonic
validate_ohlcv_values = time_utils.validate_ohlcv_values
timeframe_to_seconds = time_utils.timeframe_to_seconds
TF_MS = time_utils.TF_MS

TTLCache = cache_ttl.TTLCache
CacheCoordinator = cache_ttl.CacheCoordinator

MarketDataAuditor = md_audit.MarketDataAuditor
AuditStats = md_audit.AuditStats


class TestRunner:
    """Simple test runner"""

    def __init__(self):
        self.tests_run = 0
        self.tests_passed = 0
        self.tests_failed = 0

    def assert_equal(self, actual, expected, msg=""):
        """Assert that actual equals expected"""
        self.tests_run += 1
        if actual == expected:
            self.tests_passed += 1
            return True
        else:
            self.tests_failed += 1
            print(f"  ❌ FAIL: {msg}")
            print(f"     Expected: {expected}")
            print(f"     Got: {actual}")
            return False

    def assert_true(self, condition, msg=""):
        """Assert that condition is True"""
        return self.assert_equal(condition, True, msg)

    def assert_false(self, condition, msg=""):
        """Assert that condition is False"""
        return self.assert_equal(condition, False, msg)

    def assert_not_none(self, value, msg=""):
        """Assert that value is not None"""
        self.tests_run += 1
        if value is not None:
            self.tests_passed += 1
            return True
        else:
            self.tests_failed += 1
            print(f"  ❌ FAIL: {msg} - Expected not None, got None")
            return False

    def assert_is_none(self, value, msg=""):
        """Assert that value is None"""
        self.tests_run += 1
        if value is None:
            self.tests_passed += 1
            return True
        else:
            self.tests_failed += 1
            print(f"  ❌ FAIL: {msg} - Expected None, got {value}")
            return False

    def summary(self):
        """Print test summary"""
        print("\n" + "=" * 60)
        print(f"Tests run: {self.tests_run}")
        print(f"  ✅ Passed: {self.tests_passed}")
        if self.tests_failed > 0:
            print(f"  ❌ Failed: {self.tests_failed}")
        print("=" * 60)

        if self.tests_failed == 0:
            print("✅ ALL TESTS PASSED")
            return True
        else:
            print(f"❌ {self.tests_failed} TESTS FAILED")
            return False


def test_time_utils():
    """Test time_utils module"""
    print("\n=== Testing time_utils ===")
    t = TestRunner()

    # Test candle alignment
    ts = 1697453757000  # Some timestamp with seconds
    aligned = align_since_to_closed(ts, "1m")
    t.assert_equal(aligned % 60_000, 0, "1m alignment")
    t.assert_true(aligned <= ts, "Aligned timestamp <= original")

    # Test last closed candle
    now_ms = int(time.time() * 1000)
    last_closed = last_closed_candle_open_ms(now_ms, "1m")
    current_candle_start = (now_ms // 60_000) * 60_000
    t.assert_equal(last_closed, current_candle_start - 60_000, "Last closed 1m candle")

    # Test closed candle detection
    candle_start = int(time.time() * 1000) - 120_000  # 2 minutes ago
    candle = [candle_start, 42000, 42100, 41900, 42050, 100]
    t.assert_true(is_closed_candle(candle, "1m", now_ms), "Closed candle detection")

    # Test forming candle
    current_candle_start = (now_ms // 60_000) * 60_000
    forming_candle = [current_candle_start, 42000, 42100, 41900, 42050, 100]
    t.assert_false(is_closed_candle(forming_candle, "1m", now_ms), "Forming candle detection")

    # Test filter closed candles
    last_closed_start = last_closed_candle_open_ms(now_ms, "1m")
    current_candle_start = last_closed_start + 60_000
    ohlcv = [
        [last_closed_start - 60_000, 42000, 42100, 41900, 42050, 100],
        [last_closed_start, 42050, 42150, 42000, 42100, 120],
        [current_candle_start, 42100, 42200, 42050, 42150, 130],  # Forming
    ]
    filtered = filter_closed_candles(ohlcv, "1m", now_ms)
    t.assert_equal(len(filtered), 2, "Filter removes forming candle")

    # Test OHLCV validation - monotonic
    valid_ohlcv = [
        [1000, 100, 110, 90, 105, 1000],
        [2000, 105, 115, 95, 110, 1200],
        [3000, 110, 120, 100, 115, 1100],
    ]
    try:
        validate_ohlcv_monotonic(valid_ohlcv)
        t.assert_true(True, "Valid monotonic timestamps")
    except ValueError:
        t.assert_true(False, "Valid monotonic timestamps")

    # Test OHLCV validation - values
    try:
        validate_ohlcv_values(valid_ohlcv)
        t.assert_true(True, "Valid OHLCV values")
    except ValueError:
        t.assert_true(False, "Valid OHLCV values")

    # Test timeframe conversion
    t.assert_equal(timeframe_to_seconds("1m"), 60, "1m to seconds")
    t.assert_equal(timeframe_to_seconds("1h"), 3600, "1h to seconds")

    return t


def test_cache_ttl():
    """Test cache_ttl module"""
    print("\n=== Testing cache_ttl ===")
    t = TestRunner()

    # Test fresh cache hit
    cache = TTLCache(max_items=100)
    cache.set("BTC/USDT", {"price": 42000}, ttl_s=5.0, soft_ttl_s=2.0)

    result = cache.get("BTC/USDT")
    t.assert_not_none(result, "Cache hit returns result")

    if result:
        value, status, meta = result
        t.assert_equal(value["price"], 42000, "Cache hit value")
        t.assert_equal(status, "HIT", "Fresh cache hit status")

    # Test stale cache hit
    cache2 = TTLCache(max_items=100)
    cache2.set("ETH/USDT", {"price": 3000}, ttl_s=5.0, soft_ttl_s=0.3)
    time.sleep(0.4)  # Wait for soft TTL to expire

    result2 = cache2.get("ETH/USDT")
    t.assert_not_none(result2, "Stale hit returns result")

    if result2:
        value, status, meta = result2
        t.assert_equal(status, "STALE", "Stale cache hit status")

    # Test expired cache
    cache3 = TTLCache(max_items=100)
    cache3.set("SOL/USDT", {"price": 100}, ttl_s=0.3, soft_ttl_s=0.1)
    time.sleep(0.4)  # Wait for hard TTL to expire

    result3 = cache3.get("SOL/USDT")
    t.assert_is_none(result3, "Expired cache returns None")

    # Test LRU eviction
    cache4 = TTLCache(max_items=3)
    cache4.set("A", 1, ttl_s=10.0, soft_ttl_s=5.0)
    cache4.set("B", 2, ttl_s=10.0, soft_ttl_s=5.0)
    cache4.set("C", 3, ttl_s=10.0, soft_ttl_s=5.0)
    cache4.get("A")  # Make A recently used
    cache4.set("D", 4, ttl_s=10.0, soft_ttl_s=5.0)

    t.assert_not_none(cache4.get("A"), "LRU: A still in cache")
    t.assert_is_none(cache4.get("B"), "LRU: B evicted")
    t.assert_not_none(cache4.get("C"), "LRU: C still in cache")
    t.assert_not_none(cache4.get("D"), "LRU: D in cache")

    # Test statistics
    cache5 = TTLCache(max_items=100)
    cache5.set("X", 1, ttl_s=5.0, soft_ttl_s=2.0)
    cache5.get("X")  # Hit
    cache5.get("Y")  # Miss

    stats = cache5.get_statistics()
    t.assert_equal(stats["hits"], 1, "Stats: hits")
    t.assert_equal(stats["misses"], 1, "Stats: misses")
    t.assert_equal(stats["hit_rate"], 0.5, "Stats: hit rate")

    # Test CacheCoordinator
    cache6 = TTLCache(max_items=100)
    coordinator = CacheCoordinator(cache6)

    fetch_count = [0]

    def fetch_fn():
        fetch_count[0] += 1
        return {"price": 42000}

    # First call - should fetch
    value1, status1 = coordinator.get_or_fetch("BTC/USDT", fetch_fn, ttl_s=5.0, soft_ttl_s=2.0)
    t.assert_equal(status1, "MISS", "Coordinator: first call is MISS")
    t.assert_equal(fetch_count[0], 1, "Coordinator: fetch called once")

    # Second call - should hit cache
    value2, status2 = coordinator.get_or_fetch("BTC/USDT", fetch_fn, ttl_s=5.0, soft_ttl_s=2.0)
    t.assert_equal(status2, "HIT", "Coordinator: second call is HIT")
    t.assert_equal(fetch_count[0], 1, "Coordinator: fetch not called again")

    return t


def test_md_audit():
    """Test md_audit module"""
    print("\n=== Testing md_audit ===")
    t = TestRunner()

    with tempfile.TemporaryDirectory() as tmpdir:
        # Test audit logging
        auditor = MarketDataAuditor(log_dir=Path(tmpdir), enabled=True)

        auditor.log_ticker(
            symbol="BTC/USDT",
            status="HIT",
            latency_ms=1.5,
            source="cache",
            decision_id="dec_123",
            meta={"age_ms": 500}
        )
        auditor.flush()

        # Verify log file created
        log_files = list(Path(tmpdir).glob("market_data_audit_*.jsonl"))
        t.assert_equal(len(log_files), 1, "Log file created")

        if log_files:
            # Read and verify log entry
            with open(log_files[0], "r") as f:
                line = f.readline()
                entry = json.loads(line)

            t.assert_equal(entry["schema"], "mds_v1", "Log schema version")
            t.assert_equal(entry["route"], "ticker", "Log route")
            t.assert_equal(entry["symbol"], "BTC/USDT", "Log symbol")
            t.assert_equal(entry["status"], "HIT", "Log status")
            t.assert_equal(entry["latency_ms"], 1.5, "Log latency")
            t.assert_equal(entry["source"], "cache", "Log source")
            t.assert_equal(entry["decision_id"], "dec_123", "Log decision_id")

        # Test OHLCV logging
        auditor.log_ohlcv(
            symbol="ETH/USDT",
            timeframe="1m",
            status="MISS",
            latency_ms=15.3,
            source="exchange",
            candles_count=100
        )
        auditor.flush()

        # Test error logging
        auditor.log_error(
            route="ticker",
            symbol="SOL/USDT",
            error_type="NetworkError",
            error_msg="Connection timeout"
        )
        auditor.flush()

        auditor.close()

        # Test AuditStats
        events = [
            {"schema": "mds_v1", "ts": 1.0, "route": "ticker", "symbol": "BTC/USDT", "status": "HIT", "latency_ms": 1.0, "source": "cache"},
            {"schema": "mds_v1", "ts": 2.0, "route": "ticker", "symbol": "ETH/USDT", "status": "STALE", "latency_ms": 0.8, "source": "cache"},
            {"schema": "mds_v1", "ts": 3.0, "route": "ticker", "symbol": "BTC/USDT", "status": "MISS", "latency_ms": 15.0, "source": "exchange"},
            {"schema": "mds_v1", "ts": 4.0, "route": "ticker", "symbol": "BTC/USDT", "status": "ERROR", "latency_ms": 0.0, "source": "error"},
        ]

        with tempfile.TemporaryDirectory() as tmpdir2:
            log_path = Path(tmpdir2) / "test_audit.jsonl"
            with open(log_path, "w") as f:
                for event in events:
                    f.write(json.dumps(event) + "\n")

            stats = AuditStats.from_file(log_path)
            t.assert_equal(stats.total_requests, 4, "Stats: total requests")
            t.assert_equal(stats.hits, 1, "Stats: hits")
            t.assert_equal(stats.stale_hits, 1, "Stats: stale hits")
            t.assert_equal(stats.misses, 1, "Stats: misses")
            t.assert_equal(stats.errors, 1, "Stats: errors")

            # Test rates (hits/misses exclude errors)
            t.assert_true(abs(stats.hit_rate - (1.0 / 3.0)) < 0.01, "Stats: hit rate")
            t.assert_true(abs(stats.error_rate - 0.25) < 0.01, "Stats: error rate")

    return t


def main():
    """Run all tests"""
    print("\n" + "=" * 60)
    print("MARKET DATA SERVICES TEST SUITE")
    print("=" * 60)

    try:
        # Run all tests
        t1 = test_time_utils()
        t2 = test_cache_ttl()
        t3 = test_md_audit()

        # Combined summary
        print("\n" + "=" * 60)
        print("COMBINED TEST RESULTS")
        print("=" * 60)

        total_run = t1.tests_run + t2.tests_run + t3.tests_run
        total_passed = t1.tests_passed + t2.tests_passed + t3.tests_passed
        total_failed = t1.tests_failed + t2.tests_failed + t3.tests_failed

        print(f"Total tests run: {total_run}")
        print(f"  ✅ Passed: {total_passed}")
        if total_failed > 0:
            print(f"  ❌ Failed: {total_failed}")
        print("=" * 60)

        if total_failed == 0:
            print("✅ ALL TESTS PASSED")
            print("\n")
            return 0
        else:
            print(f"❌ {total_failed} TESTS FAILED")
            print("\n")
            return 1

    except Exception as e:
        print(f"\n❌ TEST RUNNER FAILED: {e}")
        import traceback
        traceback.print_exc()
        return 1


if __name__ == "__main__":
    sys.exit(main())
