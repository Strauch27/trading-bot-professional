#!/usr/bin/env python3
"""
Unit Tests for AnchorManager (V9_3 Phase 8)

Tests all 4 anchor modes, clamps, and stale-reset logic.
"""

import pytest
import time
import tempfile
import shutil
from pathlib import Path
from unittest.mock import patch, MagicMock

# Import AnchorManager
import sys
sys.path.insert(0, str(Path(__file__).parent.parent))

from market.anchor_manager import AnchorManager


class TestAnchorManagerModes:
    """Test all 4 anchor calculation modes."""

    @pytest.fixture
    def temp_dir(self):
        """Create temporary directory for tests."""
        temp_path = tempfile.mkdtemp()
        yield temp_path
        shutil.rmtree(temp_path, ignore_errors=True)

    @pytest.fixture
    def anchor_manager(self, temp_dir):
        """Create AnchorManager instance for testing."""
        return AnchorManager(base_path=temp_dir)

    def test_mode_1_session_high(self, anchor_manager):
        """Test Mode 1: Session-High (max price since bot start)."""
        symbol = "BTC/USDT"
        now = time.time()

        # Record prices
        anchor_manager.note_price(symbol, 100.0, now)
        anchor_manager.note_price(symbol, 105.0, now + 1)
        anchor_manager.note_price(symbol, 102.0, now + 2)

        # Mode 1: Anchor should be session peak (105.0)
        with patch('config.DROP_TRIGGER_MODE', 1):
            anchor = anchor_manager.compute_anchor(
                symbol=symbol,
                last=102.0,
                now=now + 2,
                rolling_peak=103.0  # Different from session peak
            )

        assert anchor == 105.0, f"Mode 1 should use session peak (105.0), got {anchor}"

    def test_mode_2_rolling_high(self, anchor_manager):
        """Test Mode 2: Rolling-High (uses rolling_peak parameter)."""
        symbol = "ETH/USDT"
        now = time.time()

        # Record prices
        anchor_manager.note_price(symbol, 100.0, now)
        anchor_manager.note_price(symbol, 110.0, now + 1)  # Session peak
        anchor_manager.note_price(symbol, 105.0, now + 2)

        # Mode 2: Anchor should be rolling peak (108.0)
        with patch('config.DROP_TRIGGER_MODE', 2):
            anchor = anchor_manager.compute_anchor(
                symbol=symbol,
                last=105.0,
                now=now + 2,
                rolling_peak=108.0  # Different from session peak
            )

        assert anchor == 108.0, f"Mode 2 should use rolling peak (108.0), got {anchor}"

    def test_mode_3_hybrid(self, anchor_manager):
        """Test Mode 3: Hybrid (max of session + rolling)."""
        symbol = "SOL/USDT"
        now = time.time()

        # Record prices
        anchor_manager.note_price(symbol, 100.0, now)
        anchor_manager.note_price(symbol, 115.0, now + 1)  # Session peak
        anchor_manager.note_price(symbol, 105.0, now + 2)

        # Mode 3: Anchor should be max(session=115.0, rolling=110.0) = 115.0
        with patch('config.DROP_TRIGGER_MODE', 3):
            anchor = anchor_manager.compute_anchor(
                symbol=symbol,
                last=105.0,
                now=now + 2,
                rolling_peak=110.0
            )

        assert anchor == 115.0, f"Mode 3 should be max(115.0, 110.0)=115.0, got {anchor}"

        # Mode 3: Test with rolling higher than session (within clamp limits)
        anchor_manager.clear()
        anchor_manager.note_price(symbol, 100.0, now)
        anchor_manager.note_price(symbol, 105.0, now + 1)  # Session peak

        with patch('config.DROP_TRIGGER_MODE', 3), \
             patch('config.ANCHOR_CLAMP_MAX_ABOVE_PEAK_PCT', 20.0):  # Allow 20% above peak
            anchor = anchor_manager.compute_anchor(
                symbol=symbol,
                last=105.0,
                now=now + 2,
                rolling_peak=120.0  # Rolling higher (within clamp)
            )

        # Anchor should be 120.0 (within clamp: 105 * 1.20 = 126)
        assert anchor == 120.0, f"Mode 3 should be max(105.0, 120.0)=120.0, got {anchor}"

    def test_mode_4_persistent(self, anchor_manager):
        """Test Mode 4: Persistent with clamps."""
        symbol = "AVAX/USDT"
        now = time.time()

        # First anchor calculation
        anchor_manager.note_price(symbol, 100.0, now)
        anchor_manager.note_price(symbol, 105.0, now + 1)

        with patch('config.DROP_TRIGGER_MODE', 4), \
             patch('config.ANCHOR_STALE_MINUTES', 60):
            anchor1 = anchor_manager.compute_anchor(
                symbol=symbol,
                last=105.0,
                now=now + 1,
                rolling_peak=105.0
            )

        assert anchor1 == 105.0, f"Mode 4 first anchor should be 105.0, got {anchor1}"

        # Second call: Anchor should persist and only rise
        anchor_manager.note_price(symbol, 103.0, now + 2)

        with patch('config.DROP_TRIGGER_MODE', 4), \
             patch('config.ANCHOR_STALE_MINUTES', 60):
            anchor2 = anchor_manager.compute_anchor(
                symbol=symbol,
                last=103.0,
                now=now + 2,
                rolling_peak=103.0
            )

        # Anchor should remain 105.0 (persists, doesn't fall)
        assert anchor2 == 105.0, f"Mode 4 anchor should persist at 105.0, got {anchor2}"

        # Third call: New high, anchor should update
        anchor_manager.note_price(symbol, 110.0, now + 3)

        with patch('config.DROP_TRIGGER_MODE', 4), \
             patch('config.ANCHOR_STALE_MINUTES', 60):
            anchor3 = anchor_manager.compute_anchor(
                symbol=symbol,
                last=110.0,
                now=now + 3,
                rolling_peak=110.0
            )

        assert anchor3 == 110.0, f"Mode 4 anchor should rise to 110.0, got {anchor3}"


class TestAnchorClamps:
    """Test anchor clamp logic."""

    @pytest.fixture
    def temp_dir(self):
        """Create temporary directory for tests."""
        temp_path = tempfile.mkdtemp()
        yield temp_path
        shutil.rmtree(temp_path, ignore_errors=True)

    @pytest.fixture
    def anchor_manager(self, temp_dir):
        """Create AnchorManager instance for testing."""
        return AnchorManager(base_path=temp_dir)

    def test_over_peak_clamp(self, anchor_manager):
        """Test over-peak clamp: anchor <= session_peak * (1 + clamp_pct)."""
        symbol = "BTC/USDT"
        now = time.time()

        # Session peak: 100.0
        anchor_manager.note_price(symbol, 100.0, now)

        # Try to set anchor way above session peak
        with patch('config.DROP_TRIGGER_MODE', 4), \
             patch('config.ANCHOR_CLAMP_MAX_ABOVE_PEAK_PCT', 0.5), \
             patch('config.ANCHOR_STALE_MINUTES', 60):
            anchor = anchor_manager.compute_anchor(
                symbol=symbol,
                last=100.0,
                now=now + 1,
                rolling_peak=150.0  # Far above session peak
            )

        # Anchor should be clamped to 100.0 * 1.005 = 100.5
        expected_max = 100.0 * (1.0 + 0.5 / 100.0)
        assert anchor <= expected_max, f"Anchor {anchor} should be <= {expected_max}"

    def test_start_drop_clamp(self, anchor_manager):
        """Test start-drop clamp: anchor >= start_price * (1 - max_drop_pct)."""
        symbol = "ETH/USDT"
        now = time.time()

        # Start price: 100.0
        anchor_manager.note_price(symbol, 100.0, now)

        # Try to force very low anchor
        with patch('config.DROP_TRIGGER_MODE', 4), \
             patch('config.ANCHOR_MAX_START_DROP_PCT', 8.0), \
             patch('config.ANCHOR_STALE_MINUTES', 60):
            anchor = anchor_manager.compute_anchor(
                symbol=symbol,
                last=80.0,
                now=now + 1,
                rolling_peak=80.0
            )

        # Anchor should be clamped to 100.0 * (1 - 0.08) = 92.0
        expected_min = 100.0 * (1.0 - 8.0 / 100.0)
        assert anchor >= expected_min, f"Anchor {anchor} should be >= {expected_min}"


class TestStaleReset:
    """Test anchor stale-reset logic."""

    @pytest.fixture
    def temp_dir(self):
        """Create temporary directory for tests."""
        temp_path = tempfile.mkdtemp()
        yield temp_path
        shutil.rmtree(temp_path, ignore_errors=True)

    @pytest.fixture
    def anchor_manager(self, temp_dir):
        """Create AnchorManager instance for testing."""
        return AnchorManager(base_path=temp_dir)

    def test_stale_reset(self, anchor_manager):
        """Test that anchor resets when stale."""
        symbol = "BTC/USDT"
        now = time.time()

        # Set initial anchor
        anchor_manager.note_price(symbol, 100.0, now)

        with patch('config.DROP_TRIGGER_MODE', 4), \
             patch('config.ANCHOR_STALE_MINUTES', 60):
            anchor1 = anchor_manager.compute_anchor(
                symbol=symbol,
                last=100.0,
                now=now,
                rolling_peak=100.0
            )

        assert anchor1 == 100.0

        # Wait 61 minutes (stale threshold is 60 minutes)
        stale_time = now + (61 * 60)

        # New price: 120.0
        anchor_manager.note_price(symbol, 120.0, stale_time)

        with patch('config.DROP_TRIGGER_MODE', 4), \
             patch('config.ANCHOR_STALE_MINUTES', 60):
            anchor2 = anchor_manager.compute_anchor(
                symbol=symbol,
                last=120.0,
                now=stale_time,
                rolling_peak=120.0
            )

        # Anchor should reset to new base (120.0) because old anchor is stale
        assert anchor2 == 120.0, f"Stale anchor should reset to 120.0, got {anchor2}"


class TestPersistence:
    """Test anchor persistence (Mode 4)."""

    @pytest.fixture
    def temp_dir(self):
        """Create temporary directory for tests."""
        temp_path = tempfile.mkdtemp()
        yield temp_path
        shutil.rmtree(temp_path, ignore_errors=True)

    def test_save_and_load(self, temp_dir):
        """Test saving and loading anchors."""
        # Create manager and set anchors
        manager1 = AnchorManager(base_path=temp_dir)

        symbol = "BTC/USDT"
        now = time.time()

        manager1.note_price(symbol, 100.0, now)

        with patch('config.DROP_TRIGGER_MODE', 4), \
             patch('config.ANCHOR_STALE_MINUTES', 60):
            anchor = manager1.compute_anchor(
                symbol=symbol,
                last=100.0,
                now=now,
                rolling_peak=100.0
            )

        # Save
        manager1.save()

        # Create new manager (simulates restart)
        manager2 = AnchorManager(base_path=temp_dir)

        # Check anchors were loaded
        assert symbol in manager2._anchors
        assert manager2._anchors[symbol]["anchor"] == 100.0

    def test_atomic_write(self, temp_dir):
        """Test atomic write (.tmp + rename)."""
        manager = AnchorManager(base_path=temp_dir)

        symbol = "ETH/USDT"
        now = time.time()

        manager.note_price(symbol, 100.0, now)

        with patch('config.DROP_TRIGGER_MODE', 4), \
             patch('config.ANCHOR_STALE_MINUTES', 60):
            manager.compute_anchor(
                symbol=symbol,
                last=100.0,
                now=now,
                rolling_peak=100.0
            )

        # Save
        manager.save()

        # Check .tmp file doesn't exist (was renamed)
        anchor_file = Path(temp_dir) / "anchors.json"
        tmp_file = Path(temp_dir) / "anchors.json.tmp"

        assert anchor_file.exists(), "Anchor file should exist"
        assert not tmp_file.exists(), ".tmp file should not exist after save"


class TestSessionTracking:
    """Test session-high and session-start tracking."""

    @pytest.fixture
    def temp_dir(self):
        """Create temporary directory for tests."""
        temp_path = tempfile.mkdtemp()
        yield temp_path
        shutil.rmtree(temp_path, ignore_errors=True)

    @pytest.fixture
    def anchor_manager(self, temp_dir):
        """Create AnchorManager instance for testing."""
        return AnchorManager(base_path=temp_dir)

    def test_session_peak_tracking(self, anchor_manager):
        """Test that session peak is tracked correctly."""
        symbol = "BTC/USDT"
        now = time.time()

        # Record prices
        anchor_manager.note_price(symbol, 100.0, now)
        assert anchor_manager.get_session_peak(symbol) == 100.0

        anchor_manager.note_price(symbol, 110.0, now + 1)
        assert anchor_manager.get_session_peak(symbol) == 110.0

        anchor_manager.note_price(symbol, 105.0, now + 2)
        assert anchor_manager.get_session_peak(symbol) == 110.0  # Peak stays at 110

    def test_session_start_tracking(self, anchor_manager):
        """Test that session start price is tracked correctly."""
        symbol = "ETH/USDT"
        now = time.time()

        # First price is session start
        anchor_manager.note_price(symbol, 95.0, now)
        assert anchor_manager.get_session_start(symbol) == 95.0

        # Subsequent prices don't change session start
        anchor_manager.note_price(symbol, 100.0, now + 1)
        anchor_manager.note_price(symbol, 105.0, now + 2)
        assert anchor_manager.get_session_start(symbol) == 95.0


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
