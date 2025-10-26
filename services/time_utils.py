#!/usr/bin/env python3
"""
Time Utilities for Market Data

Provides timeframe alignment and candle validation:
- Align timestamps to closed candle boundaries
- Detect partial (forming) candles
- Filter closed candles only

Prevents off-by-one errors and partial candle bugs in technical indicators.
"""

import time
from typing import List

# Timeframe to milliseconds mapping
TF_MS = {
    "1m": 60_000,
    "3m": 180_000,
    "5m": 300_000,
    "15m": 900_000,
    "30m": 1_800_000,
    "1h": 3_600_000,
    "2h": 7_200_000,
    "4h": 14_400_000,
    "6h": 21_600_000,
    "8h": 28_800_000,
    "12h": 43_200_000,
    "1d": 86_400_000,
    "3d": 259_200_000,
    "1w": 604_800_000,
    "1M": 2_592_000_000  # Approximate
}


def align_since_to_closed(since_ms: int, timeframe: str) -> int:
    """
    Align timestamp to closed candle boundary.

    Args:
        since_ms: Timestamp in milliseconds
        timeframe: Timeframe string (e.g., "1m", "5m", "1h")

    Returns:
        Aligned timestamp (start of candle)

    Example:
        >>> align_since_to_closed(1697453723000, "1m")  # 13:42:03
        1697453700000  # 13:42:00 (start of 1m candle)
    """
    if timeframe not in TF_MS:
        raise ValueError(f"Unknown timeframe: {timeframe}")

    step = TF_MS[timeframe]
    return (since_ms // step) * step


def last_closed_candle_open_ms(now_ms: int, timeframe: str) -> int:
    """
    Get opening timestamp of last CLOSED candle.

    Args:
        now_ms: Current timestamp in milliseconds
        timeframe: Timeframe string

    Returns:
        Opening timestamp of last closed candle

    Example:
        If now is 13:42:30 and timeframe is 1m:
        - Current candle: 13:42:00 - 13:43:00 (forming)
        - Last closed:    13:41:00 - 13:42:00 (closed)
        Returns: 13:41:00
    """
    if timeframe not in TF_MS:
        raise ValueError(f"Unknown timeframe: {timeframe}")

    step = TF_MS[timeframe]
    current_candle_start = (now_ms // step) * step
    return current_candle_start - step


def is_closed_candle(candle: List, timeframe: str, now_ms: int) -> bool:
    """
    Check if candle is closed (complete).

    Args:
        candle: OHLCV candle [timestamp, open, high, low, close, volume]
        timeframe: Timeframe string
        now_ms: Current timestamp in milliseconds

    Returns:
        True if candle is closed, False if forming

    Example:
        >>> candle = [1697453700000, 42000, 42100, 41900, 42050, 100]  # 13:42:00
        >>> now_ms = 1697453750000  # 13:42:30 (50s into candle)
        >>> is_closed_candle(candle, "1m", now_ms)
        False  # Candle not closed yet

        >>> now_ms = 1697453780000  # 13:43:00 (candle closed)
        >>> is_closed_candle(candle, "1m", now_ms)
        True
    """
    if timeframe not in TF_MS:
        raise ValueError(f"Unknown timeframe: {timeframe}")

    if not candle or len(candle) < 1:
        return False

    start_ms = candle[0]
    step = TF_MS[timeframe]
    candle_close_time = start_ms + step

    return now_ms >= candle_close_time


def filter_closed_candles(ohlcv: List[List], timeframe: str, now_ms: int = None) -> List[List]:
    """
    Remove forming (partial) candle from OHLCV data.

    This is CRITICAL for technical indicators to prevent:
    - Using incomplete data
    - Recalculation on every tick
    - False signals from partial candles

    Args:
        ohlcv: List of OHLCV candles [[ts, o, h, l, c, v], ...]
        timeframe: Timeframe string
        now_ms: Current timestamp (defaults to now)

    Returns:
        OHLCV data with forming candle removed (if present)

    Example:
        >>> ohlcv = [
        ...     [1697453640000, 42000, 42100, 41900, 42050, 100],  # 13:41 (closed)
        ...     [1697453700000, 42050, 42150, 42000, 42100, 120],  # 13:42 (forming)
        ... ]
        >>> now_ms = 1697453750000  # 13:42:30 (in middle of 13:42 candle)
        >>> filtered = filter_closed_candles(ohlcv, "1m", now_ms)
        >>> len(filtered)
        1  # Forming candle removed
    """
    if not ohlcv:
        return ohlcv

    if now_ms is None:
        now_ms = int(time.time() * 1000)

    if timeframe not in TF_MS:
        raise ValueError(f"Unknown timeframe: {timeframe}")

    # Check if last candle is closed
    last_candle = ohlcv[-1]
    if is_closed_candle(last_candle, timeframe, now_ms):
        return ohlcv

    # Last candle is forming - remove it
    return ohlcv[:-1]


def get_candle_age_ms(candle: List, now_ms: int = None) -> int:
    """
    Get age of candle in milliseconds.

    Args:
        candle: OHLCV candle
        now_ms: Current timestamp (defaults to now)

    Returns:
        Age in milliseconds
    """
    if not candle or len(candle) < 1:
        return 0

    if now_ms is None:
        now_ms = int(time.time() * 1000)

    return now_ms - candle[0]


def validate_ohlcv_monotonic(ohlcv: List[List]) -> bool:
    """
    Validate that OHLCV timestamps are strictly monotonic increasing.

    Args:
        ohlcv: List of OHLCV candles

    Returns:
        True if monotonic, raises ValueError if not

    Raises:
        ValueError: If timestamps are not monotonic or duplicate
    """
    if not ohlcv or len(ohlcv) <= 1:
        return True

    prev_ts = ohlcv[0][0]
    for i, candle in enumerate(ohlcv[1:], start=1):
        current_ts = candle[0]
        if current_ts <= prev_ts:
            raise ValueError(
                f"Non-monotonic OHLCV timestamps at index {i}: "
                f"{prev_ts} -> {current_ts}"
            )
        prev_ts = current_ts

    return True


def validate_ohlcv_values(ohlcv: List[List]) -> bool:
    """
    Validate OHLCV data quality.

    Checks:
    - No negative volumes
    - High >= Low
    - OHLC within High/Low range

    Args:
        ohlcv: List of OHLCV candles

    Returns:
        True if valid, raises ValueError if not
    """
    for i, candle in enumerate(ohlcv):
        if len(candle) < 6:
            raise ValueError(f"Invalid candle at index {i}: insufficient fields")

        ts, o, h, low, c, v = candle[:6]

        # Volume check
        if v is not None and v < 0:
            raise ValueError(f"Negative volume at index {i}: {v}")

        # Price range check
        if h < low:
            raise ValueError(f"High < Low at index {i}: high={h}, low={low}")

        # OHLC consistency
        if not (low <= o <= h and low <= c <= h):
            raise ValueError(
                f"OHLC inconsistent at index {i}: "
                f"open={o}, high={h}, low={low}, close={c}"
            )

    return True


def timeframe_to_seconds(timeframe: str) -> int:
    """
    Convert timeframe string to seconds.

    Args:
        timeframe: Timeframe string (e.g., "1m", "1h")

    Returns:
        Timeframe duration in seconds
    """
    if timeframe not in TF_MS:
        raise ValueError(f"Unknown timeframe: {timeframe}")

    return TF_MS[timeframe] // 1000


def get_timeframe_info(timeframe: str) -> dict:
    """
    Get comprehensive timeframe information.

    Args:
        timeframe: Timeframe string

    Returns:
        Dict with milliseconds, seconds, human-readable duration
    """
    if timeframe not in TF_MS:
        raise ValueError(f"Unknown timeframe: {timeframe}")

    ms = TF_MS[timeframe]
    seconds = ms // 1000
    minutes = seconds // 60
    hours = minutes // 60
    days = hours // 24

    if days > 0:
        duration_str = f"{days}d"
    elif hours > 0:
        duration_str = f"{hours}h"
    elif minutes > 0:
        duration_str = f"{minutes}m"
    else:
        duration_str = f"{seconds}s"

    return {
        "timeframe": timeframe,
        "milliseconds": ms,
        "seconds": seconds,
        "minutes": minutes,
        "hours": hours,
        "days": days,
        "duration_str": duration_str
    }
