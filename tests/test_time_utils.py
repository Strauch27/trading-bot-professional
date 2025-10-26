#!/usr/bin/env python3
"""
Tests for services/time_utils.py

Tests candle alignment, validation, and partial candle filtering.
"""

import time

import pytest

from services.time_utils import (
    align_since_to_closed,
    filter_closed_candles,
    get_candle_age_ms,
    get_timeframe_info,
    is_closed_candle,
    last_closed_candle_open_ms,
    timeframe_to_seconds,
    validate_ohlcv_monotonic,
    validate_ohlcv_values,
)


class TestCandleAlignment:
    """Test candle timestamp alignment"""

    def test_align_1m_candle(self):
        """Test 1m candle alignment"""
        # 13:42:37 -> 13:42:00
        ts = 1697453757000  # Some arbitrary timestamp with seconds
        aligned = align_since_to_closed(ts, "1m")
        assert aligned % 60_000 == 0
        assert aligned <= ts

    def test_align_5m_candle(self):
        """Test 5m candle alignment"""
        ts = 1697453757000
        aligned = align_since_to_closed(ts, "5m")
        assert aligned % 300_000 == 0
        assert aligned <= ts

    def test_align_1h_candle(self):
        """Test 1h candle alignment"""
        ts = 1697453757000
        aligned = align_since_to_closed(ts, "1h")
        assert aligned % 3_600_000 == 0
        assert aligned <= ts

    def test_align_unknown_timeframe(self):
        """Test error on unknown timeframe"""
        with pytest.raises(ValueError, match="Unknown timeframe"):
            align_since_to_closed(1697453757000, "invalid")


class TestLastClosedCandle:
    """Test last closed candle calculation"""

    def test_last_closed_1m(self):
        """Test last closed 1m candle"""
        # If now is 13:42:30 (middle of 13:42-13:43 candle)
        # Last closed is 13:41:00
        now_ms = 1697453750000  # 13:42:30
        last_closed = last_closed_candle_open_ms(now_ms, "1m")

        # Verify it's one minute before current candle
        current_candle_start = (now_ms // 60_000) * 60_000
        assert last_closed == current_candle_start - 60_000

    def test_last_closed_5m(self):
        """Test last closed 5m candle"""
        now_ms = int(time.time() * 1000)
        last_closed = last_closed_candle_open_ms(now_ms, "5m")

        current_candle_start = (now_ms // 300_000) * 300_000
        assert last_closed == current_candle_start - 300_000


class TestClosedCandleDetection:
    """Test closed vs forming candle detection"""

    def test_closed_candle(self):
        """Test detecting closed candle"""
        candle_start = 1697453700000  # 13:42:00
        candle = [candle_start, 42000, 42100, 41900, 42050, 100]

        # Check at 13:43:00 (candle closed)
        now_ms = candle_start + 60_000
        assert is_closed_candle(candle, "1m", now_ms) is True

    def test_forming_candle(self):
        """Test detecting forming candle"""
        candle_start = 1697453700000  # 13:42:00
        candle = [candle_start, 42000, 42100, 41900, 42050, 100]

        # Check at 13:42:30 (50% into candle)
        now_ms = candle_start + 30_000
        assert is_closed_candle(candle, "1m", now_ms) is False

    def test_empty_candle(self):
        """Test empty candle returns False"""
        assert is_closed_candle([], "1m", int(time.time() * 1000)) is False


class TestFilterClosedCandles:
    """Test partial candle filtering"""

    def test_filter_removes_forming_candle(self):
        """Test that forming candle is removed"""
        now_ms = int(time.time() * 1000)
        last_closed_start = last_closed_candle_open_ms(now_ms, "1m")
        current_candle_start = last_closed_start + 60_000

        ohlcv = [
            [last_closed_start - 60_000, 42000, 42100, 41900, 42050, 100],  # Closed
            [last_closed_start, 42050, 42150, 42000, 42100, 120],          # Closed
            [current_candle_start, 42100, 42200, 42050, 42150, 130],       # Forming
        ]

        filtered = filter_closed_candles(ohlcv, "1m", now_ms)
        assert len(filtered) == 2
        assert filtered[-1][0] == last_closed_start

    def test_filter_keeps_all_closed(self):
        """Test that all closed candles are kept"""
        now_ms = int(time.time() * 1000)
        last_closed_start = last_closed_candle_open_ms(now_ms, "1m")

        ohlcv = [
            [last_closed_start - 120_000, 42000, 42100, 41900, 42050, 100],
            [last_closed_start - 60_000, 42050, 42150, 42000, 42100, 120],
            [last_closed_start, 42100, 42200, 42050, 42150, 130],
        ]

        filtered = filter_closed_candles(ohlcv, "1m", now_ms)
        assert len(filtered) == 3

    def test_filter_empty_list(self):
        """Test filtering empty list"""
        filtered = filter_closed_candles([], "1m")
        assert filtered == []


class TestOHLCVValidation:
    """Test OHLCV data validation"""

    def test_monotonic_valid(self):
        """Test valid monotonic timestamps"""
        ohlcv = [
            [1000, 100, 110, 90, 105, 1000],
            [2000, 105, 115, 95, 110, 1200],
            [3000, 110, 120, 100, 115, 1100],
        ]
        assert validate_ohlcv_monotonic(ohlcv) is True

    def test_monotonic_invalid_duplicate(self):
        """Test invalid: duplicate timestamps"""
        ohlcv = [
            [1000, 100, 110, 90, 105, 1000],
            [1000, 105, 115, 95, 110, 1200],  # Duplicate
        ]
        with pytest.raises(ValueError, match="Non-monotonic"):
            validate_ohlcv_monotonic(ohlcv)

    def test_monotonic_invalid_backwards(self):
        """Test invalid: backwards timestamps"""
        ohlcv = [
            [3000, 100, 110, 90, 105, 1000],
            [2000, 105, 115, 95, 110, 1200],  # Goes backwards
        ]
        with pytest.raises(ValueError, match="Non-monotonic"):
            validate_ohlcv_monotonic(ohlcv)

    def test_values_valid(self):
        """Test valid OHLCV values"""
        ohlcv = [
            [1000, 100, 110, 90, 105, 1000],  # Valid: low <= o,c <= high
            [2000, 95, 115, 95, 110, 1200],
        ]
        assert validate_ohlcv_values(ohlcv) is True

    def test_values_negative_volume(self):
        """Test invalid: negative volume"""
        ohlcv = [[1000, 100, 110, 90, 105, -100]]
        with pytest.raises(ValueError, match="Negative volume"):
            validate_ohlcv_values(ohlcv)

    def test_values_high_less_than_low(self):
        """Test invalid: high < low"""
        ohlcv = [[1000, 100, 90, 110, 105, 1000]]  # high=90, low=110
        with pytest.raises(ValueError, match="High < Low"):
            validate_ohlcv_values(ohlcv)

    def test_values_ohlc_inconsistent(self):
        """Test invalid: OHLC outside high/low range"""
        ohlcv = [[1000, 100, 110, 90, 120, 1000]]  # close=120 > high=110
        with pytest.raises(ValueError, match="OHLC inconsistent"):
            validate_ohlcv_values(ohlcv)


class TestCandleAge:
    """Test candle age calculation"""

    def test_candle_age(self):
        """Test calculating candle age"""
        candle_ts = int(time.time() * 1000) - 5000  # 5 seconds ago
        candle = [candle_ts, 100, 110, 90, 105, 1000]

        age_ms = get_candle_age_ms(candle)
        assert 4900 <= age_ms <= 5100  # Allow 100ms tolerance


class TestTimeframeConversion:
    """Test timeframe conversion utilities"""

    def test_timeframe_to_seconds(self):
        """Test converting timeframes to seconds"""
        assert timeframe_to_seconds("1m") == 60
        assert timeframe_to_seconds("5m") == 300
        assert timeframe_to_seconds("1h") == 3600
        assert timeframe_to_seconds("1d") == 86400

    def test_get_timeframe_info(self):
        """Test getting timeframe info"""
        info = get_timeframe_info("1h")
        assert info["timeframe"] == "1h"
        assert info["seconds"] == 3600
        assert info["minutes"] == 60
        assert info["hours"] == 1
        assert info["duration_str"] == "1h"

    def test_timeframe_info_days(self):
        """Test timeframe info for days"""
        info = get_timeframe_info("1d")
        assert info["days"] == 1
        assert info["duration_str"] == "1d"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
