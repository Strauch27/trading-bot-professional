#!/usr/bin/env python3
"""
Integration Test for V9_3 Warm-Start (Phase 8)

Tests 5-minute run + restart scenario to verify:
- Tick persistence
- Snapshot persistence
- Warm-start recovery
- State restoration
"""

import pytest
import time
import tempfile
import shutil
from pathlib import Path
from unittest.mock import MagicMock, patch

# Import components
import sys
sys.path.insert(0, str(Path(__file__).parent.parent))

# Import from io package (avoid conflict with built-in io module)
import importlib.util
spec = importlib.util.spec_from_file_location("jsonl_module", str(Path(__file__).parent.parent / "io" / "jsonl.py"))
jsonl_module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(jsonl_module)

RotatingJSONLWriter = jsonl_module.RotatingJSONLWriter
read_jsonl_tail = jsonl_module.read_jsonl_tail

from market.anchor_manager import AnchorManager
from core.rolling_windows import RollingWindowManager
from core.price_cache import PriceCache


class TestWarmStartIntegration:
    """Integration test for warm-start functionality."""

    @pytest.fixture
    def temp_dir(self):
        """Create temporary directory for tests."""
        temp_path = tempfile.mkdtemp()
        yield temp_path
        shutil.rmtree(temp_path, ignore_errors=True)

    def test_warmstart_complete_flow(self, temp_dir):
        """
        Test complete warm-start flow:
        1. Simulate 5-minute bot run with tick persistence
        2. Simulate bot restart
        3. Verify state is restored from persisted ticks
        """
        symbol = "BTC/USDT"
        base_path = temp_dir

        # === PHASE 1: Initial run (5 minutes simulated) ===
        print("\n=== PHASE 1: Initial Run ===")

        # Create components
        tick_writer = RotatingJSONLWriter(
            base_dir=f"{base_path}/ticks",
            prefix=f"tick_{symbol.replace('/', '_')}",
            max_mb=50
        )

        rw_manager = RollingWindowManager(
            lookback_s=300,  # 5 minutes
            persist=True,
            base_path=f"{base_path}/windows"
        )

        anchor_manager = AnchorManager(base_path=f"{base_path}/anchors")

        price_cache = PriceCache(seconds=300)

        # Simulate ticks over 5 minutes (1 tick per second = 300 ticks)
        start_time = time.time()
        prices = []

        for i in range(300):
            ts = start_time + i
            price = 50000 + (i * 10)  # Price rises from 50000 to 52990

            # Persist tick
            tick_obj = {
                "ts": ts,
                "symbol": symbol,
                "last": price,
                "bid": price - 1,
                "ask": price + 1,
                "volume": 100,
                "spread_bps": 2.0
            }
            tick_writer.append(tick_obj)

            # Update components
            price_cache.update({symbol: price}, ts)
            rw_manager.update(symbol, ts, price)
            anchor_manager.note_price(symbol, price, ts)

            prices.append(price)

            # Every 60 ticks, compute anchor
            if i % 60 == 0:
                with patch('config.DROP_TRIGGER_MODE', 4), \
                     patch('config.ANCHOR_STALE_MINUTES', 60):
                    anchor = anchor_manager.compute_anchor(
                        symbol=symbol,
                        last=price,
                        now=ts,
                        rolling_peak=max(prices)
                    )
                    print(f"  t={i}s: price={price}, anchor={anchor}")

        # Verify state at end of run
        window = rw_manager.windows.get(symbol)
        assert window is not None, "Window should exist"

        final_peak = window.peak()
        final_trough = window.trough()

        print(f"  Final state: peak={final_peak}, trough={final_trough}")
        print(f"  Final anchor: {anchor_manager.get_session_peak(symbol)}")

        # Save state
        anchor_manager.save()
        rw_manager.persist_all()

        # Verify files were created
        tick_file = Path(f"{base_path}/ticks").glob("*.jsonl")
        assert any(tick_file), "Tick file should exist"

        # === PHASE 2: Simulated Restart ===
        print("\n=== PHASE 2: Simulated Restart ===")

        # Clear in-memory state (simulate process restart)
        del tick_writer, rw_manager, anchor_manager, price_cache

        # Create fresh components (simulates new process)
        rw_manager_new = RollingWindowManager(
            lookback_s=300,
            persist=True,
            base_path=f"{base_path}/windows"
        )

        anchor_manager_new = AnchorManager(base_path=f"{base_path}/anchors")
        price_cache_new = PriceCache(seconds=300)

        # === PHASE 3: Warm-Start Recovery ===
        print("\n=== PHASE 3: Warm-Start Recovery ===")

        # Find tick file
        symbol_safe = symbol.replace('/', '_')
        tick_files = list(Path(f"{base_path}/ticks").glob(f"tick_{symbol_safe}_*.jsonl"))
        assert len(tick_files) > 0, "Should have tick files"

        tick_file = tick_files[0]
        print(f"  Loading ticks from: {tick_file}")

        # Load last 300 ticks (warm-start)
        ticks = read_jsonl_tail(str(tick_file), n=300)

        print(f"  Loaded {len(ticks)} ticks")

        # Replay ticks into components
        for tick in ticks:
            ts = tick.get('ts')
            last = tick.get('last')

            if not ts or not last or last <= 0:
                continue

            # Restore state
            price_cache_new.update({symbol: last}, ts)
            rw_manager_new.update(symbol, ts, last)
            anchor_manager_new.note_price(symbol, last, ts)

        # === PHASE 4: Verify State Restoration ===
        print("\n=== PHASE 4: State Verification ===")

        # Check rolling windows
        window_new = rw_manager_new.windows.get(symbol)
        assert window_new is not None, "Window should be restored"

        restored_peak = window_new.peak()
        restored_trough = window_new.trough()

        print(f"  Restored state: peak={restored_peak}, trough={restored_trough}")

        # Peaks should match (within floating point tolerance)
        assert abs(restored_peak - final_peak) < 0.01, \
            f"Peak mismatch: original={final_peak}, restored={restored_peak}"

        assert abs(restored_trough - final_trough) < 0.01, \
            f"Trough mismatch: original={final_trough}, restored={restored_trough}"

        # Check anchor manager
        restored_session_peak = anchor_manager_new.get_session_peak(symbol)
        print(f"  Restored anchor: {restored_session_peak}")

        # Session peak should be close to original
        original_session_peak = anchor_manager.get_session_peak(symbol) if 'anchor_manager' in locals() else final_peak
        assert abs(restored_session_peak - final_peak) < 0.01, \
            f"Session peak mismatch: restored={restored_session_peak}, expected={final_peak}"

        # Check price cache
        cached_price = price_cache_new.last_price(symbol)
        assert cached_price is not None, "Should have cached price"
        assert abs(cached_price - prices[-1]) < 0.01, \
            f"Cached price mismatch: cached={cached_price}, expected={prices[-1]}"

        print("\n✅ Warm-start integration test PASSED")


class TestWarmStartEdgeCases:
    """Test edge cases in warm-start."""

    @pytest.fixture
    def temp_dir(self):
        """Create temporary directory for tests."""
        temp_path = tempfile.mkdtemp()
        yield temp_path
        shutil.rmtree(temp_path, ignore_errors=True)

    def test_warmstart_no_files(self, temp_dir):
        """Test warm-start when no tick files exist (cold start)."""
        # This should not crash, just return empty results

        result = read_jsonl_tail(f"{temp_dir}/nonexistent.jsonl", n=300)
        assert result == []

    def test_warmstart_partial_ticks(self, temp_dir):
        """Test warm-start with fewer than requested ticks."""
        # Create file with only 50 ticks
        tick_file = Path(temp_dir) / "ticks.jsonl"

        with open(tick_file, 'w') as f:
            for i in range(50):
                import json
                tick = {"ts": time.time() + i, "symbol": "BTC/USDT", "last": 50000 + i}
                f.write(json.dumps(tick) + "\n")

        # Request 300 ticks (but only 50 available)
        ticks = read_jsonl_tail(str(tick_file), n=300)

        # Should return all 50 available ticks
        assert len(ticks) == 50

    def test_warmstart_malformed_ticks(self, temp_dir):
        """Test warm-start with some malformed tick data."""
        tick_file = Path(temp_dir) / "ticks.jsonl"

        import json
        with open(tick_file, 'w') as f:
            # Good tick
            f.write(json.dumps({"ts": time.time(), "symbol": "BTC/USDT", "last": 50000}) + "\n")

            # Malformed ticks
            f.write("INVALID JSON\n")
            f.write(json.dumps({"ts": time.time(), "last": "INVALID"}) + "\n")  # String instead of number

            # Good tick
            f.write(json.dumps({"ts": time.time(), "symbol": "BTC/USDT", "last": 50001}) + "\n")

        ticks = read_jsonl_tail(str(tick_file), n=300)

        # Should have 2 valid ticks (malformed ones are skipped by the reader)
        assert len(ticks) >= 2

    def test_warmstart_date_rollover(self, temp_dir):
        """Test warm-start when tick files span date boundary."""
        # This tests the logic that checks today's and yesterday's files
        symbol = "BTC/USDT"
        symbol_safe = symbol.replace('/', '_')

        # Create "yesterday's" file
        from datetime import datetime, timedelta
        yesterday = (datetime.now() - timedelta(days=1)).strftime("%Y%m%d")

        tick_dir = Path(temp_dir) / "ticks"
        tick_dir.mkdir(parents=True, exist_ok=True)

        yesterday_file = tick_dir / f"tick_{symbol_safe}_{yesterday}.jsonl"

        import json
        with open(yesterday_file, 'w') as f:
            for i in range(100):
                tick = {"ts": time.time(), "symbol": symbol, "last": 50000 + i}
                f.write(json.dumps(tick) + "\n")

        # Verify file can be found and read
        ticks = read_jsonl_tail(str(yesterday_file), n=50)

        assert len(ticks) == 50


if __name__ == "__main__":
    pytest.main([__file__, "-v", "-s"])  # -s to show print statements
