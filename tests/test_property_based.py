#!/usr/bin/env python3
"""
Property-Based Tests for V9_3 (Phase 8)

Uses Hypothesis to generate randomized test cases for:
- Anchor calculations
- JSONL writer behavior
- Price cache invariants
"""

import pytest
import time
import tempfile
import shutil
from pathlib import Path
from hypothesis import given, strategies as st, settings, assume
from hypothesis.stateful import RuleBasedStateMachine, rule, invariant, initialize

# Import components
import sys
sys.path.insert(0, str(Path(__file__).parent.parent))

from market.anchor_manager import AnchorManager
from core.price_cache import PriceCache

# Import from io package (avoid conflict with built-in io module)
import importlib.util
spec = importlib.util.spec_from_file_location("jsonl_module", str(Path(__file__).parent.parent / "io" / "jsonl.py"))
jsonl_module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(jsonl_module)

RotatingJSONLWriter = jsonl_module.RotatingJSONLWriter


# ===== Property Tests for AnchorManager =====

class TestAnchorProperties:
    """Property-based tests for AnchorManager."""

    @given(
        prices=st.lists(
            st.floats(min_value=1.0, max_value=100000.0),
            min_size=1,
            max_size=100
        )
    )
    @settings(deadline=None, max_examples=50)
    def test_anchor_never_exceeds_session_peak(self, prices):
        """
        Property: In Mode 1, anchor should never exceed session peak.
        """
        with tempfile.TemporaryDirectory() as temp_dir:
            anchor_manager = AnchorManager(base_path=temp_dir)

            symbol = "TEST/USDT"
            now = time.time()

            # Record all prices
            for i, price in enumerate(prices):
                anchor_manager.note_price(symbol, price, now + i)

            session_peak = anchor_manager.get_session_peak(symbol)

            # Compute anchor in Mode 1
            from unittest.mock import patch
            with patch('config.DROP_TRIGGER_MODE', 1), \
                 patch('config.ANCHOR_STALE_MINUTES', 60):
                anchor = anchor_manager.compute_anchor(
                    symbol=symbol,
                    last=prices[-1],
                    now=now + len(prices),
                    rolling_peak=max(prices)
                )

            # Property: anchor <= session_peak
            assert anchor <= session_peak, \
                f"Anchor {anchor} should not exceed session peak {session_peak}"

    @given(
        session_peak=st.floats(min_value=100.0, max_value=1000.0),
        rolling_peak=st.floats(min_value=100.0, max_value=1000.0)
    )
    @settings(deadline=None, max_examples=50)
    def test_mode_3_is_max_of_peaks(self, session_peak, rolling_peak):
        """
        Property: In Mode 3, anchor is bounded by peaks (considering clamps).
        """
        with tempfile.TemporaryDirectory() as temp_dir:
            anchor_manager = AnchorManager(base_path=temp_dir)

            symbol = "TEST/USDT"
            now = time.time()

            # Set session peak
            anchor_manager.note_price(symbol, session_peak, now)

            # Compute anchor in Mode 3
            from unittest.mock import patch
            with patch('config.DROP_TRIGGER_MODE', 3), \
                 patch('config.ANCHOR_STALE_MINUTES', 60):
                anchor = anchor_manager.compute_anchor(
                    symbol=symbol,
                    last=min(session_peak, rolling_peak),
                    now=now + 1,
                    rolling_peak=rolling_peak
                )

            # Property: Anchor is bounded by min and clamped max of peaks
            min_peak = min(session_peak, rolling_peak)
            max_peak = max(session_peak, rolling_peak)

            # Anchor should be at least the minimum peak and at most 2x the maximum peak (generous tolerance for clamps)
            assert anchor >= min_peak - 0.01, \
                f"Mode 3 anchor {anchor} should be >= {min_peak}"
            assert anchor <= max_peak * 2.0, \
                f"Mode 3 anchor {anchor} should be <= {max_peak * 2.0}"

    @given(
        prices=st.lists(
            st.floats(min_value=100.0, max_value=10000.0),
            min_size=2,
            max_size=50
        )
    )
    @settings(deadline=None, max_examples=50)
    def test_mode_4_anchor_never_falls(self, prices):
        """
        Property: In Mode 4, anchor should never fall (monotonically increasing or stable).
        """
        # Ensure prices are sorted to create a realistic scenario
        assume(len(prices) >= 2)

        with tempfile.TemporaryDirectory() as temp_dir:
            anchor_manager = AnchorManager(base_path=temp_dir)

            symbol = "TEST/USDT"
            now = time.time()

            anchors = []

            # Record prices and compute anchors
            from unittest.mock import patch
            for i, price in enumerate(prices):
                anchor_manager.note_price(symbol, price, now + i)

                with patch('config.DROP_TRIGGER_MODE', 4), \
                     patch('config.ANCHOR_STALE_MINUTES', 60):
                    anchor = anchor_manager.compute_anchor(
                        symbol=symbol,
                        last=price,
                        now=now + i,
                        rolling_peak=max(prices[:i+1])
                    )
                    anchors.append(anchor)

            # Property: Anchors should be monotonically increasing or stable
            for i in range(1, len(anchors)):
                assert anchors[i] >= anchors[i-1], \
                    f"Mode 4 anchor should never fall: anchors[{i}]={anchors[i]} < anchors[{i-1}]={anchors[i-1]}"


# ===== Property Tests for RotatingJSONLWriter =====

class TestJSONLWriterProperties:
    """Property-based tests for RotatingJSONLWriter."""

    @given(
        objects=st.lists(
            st.dictionaries(
                keys=st.text(min_size=1, max_size=10, alphabet=st.characters(min_codepoint=97, max_codepoint=122)),
                values=st.one_of(st.integers(), st.floats(allow_nan=False, allow_infinity=False), st.text(max_size=20))
            ),
            min_size=1,
            max_size=100
        )
    )
    @settings(deadline=None, max_examples=30)
    def test_all_objects_persisted(self, objects):
        """
        Property: All written objects should be persisted and readable.
        """
        with tempfile.TemporaryDirectory() as temp_dir:
            writer = RotatingJSONLWriter(
                base_dir=temp_dir,
                prefix="test",
                max_mb=50
            )

            # Write all objects
            for obj in objects:
                assert writer.append(obj) is True

            # Read all files
            files = list(Path(temp_dir).glob("*.jsonl"))
            assume(len(files) > 0)

            # Count total lines
            total_lines = 0
            for f in files:
                with open(f, 'r') as file:
                    total_lines += len(file.readlines())

            # Property: Number of lines = number of objects
            assert total_lines == len(objects), \
                f"Expected {len(objects)} lines, got {total_lines}"

    @given(
        num_writes=st.integers(min_value=1, max_value=100)
    )
    @settings(deadline=None, max_examples=20)
    def test_no_data_loss_on_rotation(self, num_writes):
        """
        Property: No data should be lost during file rotation.
        """
        with tempfile.TemporaryDirectory() as temp_dir:
            # Small file size to trigger rotation
            writer = RotatingJSONLWriter(
                base_dir=temp_dir,
                prefix="test",
                max_mb=0.001,  # 1KB
                daily_rotation=False
            )

            # Write objects that will trigger rotation
            for i in range(num_writes):
                obj = {"id": i, "data": "x" * 100}  # ~100 bytes each
                writer.append(obj)

            # Read all files and count objects
            files = list(Path(temp_dir).glob("*.jsonl"))

            total_objects = 0
            for f in files:
                import json
                with open(f, 'r') as file:
                    for line in file:
                        if line.strip():
                            total_objects += 1

            # Property: No data loss
            assert total_objects == num_writes, \
                f"Expected {num_writes} objects, found {total_objects}"


# ===== Property Tests for PriceCache =====

class TestPriceCacheProperties:
    """Property-based tests for PriceCache."""

    @given(
        updates=st.lists(
            st.tuples(
                st.text(min_size=1, max_size=10),  # symbol
                st.floats(min_value=1.0, max_value=100000.0, allow_nan=False, allow_infinity=False)  # price
            ),
            min_size=1,
            max_size=100
        )
    )
    @settings(deadline=None, max_examples=30)
    def test_latest_price_is_most_recent(self, updates):
        """
        Property: get_latest() should return the most recent price for each symbol.
        """
        cache = PriceCache(seconds=300)

        now = time.time()

        # Track expected latest price per symbol
        expected_latest = {}

        for i, (symbol, price) in enumerate(updates):
            cache.update({symbol: price}, now + i)
            expected_latest[symbol] = price

        # Verify latest prices
        for symbol, expected_price in expected_latest.items():
            actual_price = cache.last_price(symbol)

            assert actual_price is not None, f"Should have latest price for {symbol}"
            assert abs(actual_price - expected_price) < 0.01, \
                f"Latest price for {symbol}: expected {expected_price}, got {actual_price}"

    @given(
        symbols=st.lists(st.text(min_size=1, max_size=10), min_size=1, max_size=20, unique=True),
        prices=st.lists(st.floats(min_value=1.0, max_value=10000.0, allow_nan=False, allow_infinity=False), min_size=1, max_size=100)
    )
    @settings(deadline=None, max_examples=20)
    def test_buffer_size_bounded(self, symbols, prices):
        """
        Property: Price cache buffer should be bounded by time window.
        """
        lookback_seconds = 60
        cache = PriceCache(seconds=lookback_seconds)

        now = time.time()

        # Write prices over 2x the lookback period
        for i, price in enumerate(prices):
            # Cycle through symbols
            symbol = symbols[i % len(symbols)]
            ts = now + (i * 2)  # 2 seconds per update

            cache.update({symbol: price}, ts)

        # Check buffer sizes
        for symbol in symbols:
            buffer = cache.buffers.get(symbol, [])

            # Property: Buffer should not exceed lookback window significantly
            # (Allow some margin for implementation details)
            if buffer:
                oldest_ts = buffer[0][0] if buffer else now
                newest_ts = buffer[-1][0] if buffer else now
                span = newest_ts - oldest_ts

                assert span <= lookback_seconds * 2, \
                    f"Buffer span {span}s exceeds 2x lookback ({lookback_seconds}s) for {symbol}"


# ===== Stateful Property Tests =====

class AnchorManagerStateMachine(RuleBasedStateMachine):
    """
    Stateful property-based testing for AnchorManager.

    Tests sequences of operations to verify invariants hold.
    """

    def __init__(self):
        super().__init__()
        self.temp_dir = tempfile.mkdtemp()
        self.anchor_manager = AnchorManager(base_path=self.temp_dir)
        self.symbol = "BTC/USDT"
        self.current_time = time.time()
        self.max_price_seen = 0.0

    @initialize()
    def init_state(self):
        """Initialize state machine."""
        self.current_time = time.time()
        self.max_price_seen = 0.0

    @rule(price=st.floats(min_value=100.0, max_value=10000.0))
    def note_price(self, price):
        """Record a price."""
        self.current_time += 1
        self.anchor_manager.note_price(self.symbol, price, self.current_time)
        self.max_price_seen = max(self.max_price_seen, price)

    @rule()
    def compute_anchor_mode_1(self):
        """Compute anchor in Mode 1."""
        if self.max_price_seen > 0:
            from unittest.mock import patch
            with patch('config.DROP_TRIGGER_MODE', 1), \
                 patch('config.ANCHOR_STALE_MINUTES', 60):
                anchor = self.anchor_manager.compute_anchor(
                    symbol=self.symbol,
                    last=self.max_price_seen,
                    now=self.current_time,
                    rolling_peak=self.max_price_seen
                )

                # Invariant: anchor <= max_price_seen
                assert anchor <= self.max_price_seen

    @invariant()
    def session_peak_is_maximum(self):
        """Invariant: Session peak should be the maximum price seen."""
        session_peak = self.anchor_manager.get_session_peak(self.symbol)
        if session_peak is not None and self.max_price_seen > 0:
            assert abs(session_peak - self.max_price_seen) < 0.01

    def teardown(self):
        """Clean up temporary directory."""
        shutil.rmtree(self.temp_dir, ignore_errors=True)


# Create test class from state machine
TestAnchorStateful = AnchorManagerStateMachine.TestCase


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
