#!/usr/bin/env python3
"""
Price Cache - Time-series Ringbuffer for Price Data

Maintains a rolling window of price observations per symbol.
Provides O(1) last-price lookup and efficient historical views.
"""

from collections import deque
from typing import Deque, Dict, Iterable, Optional, Tuple


class PriceCache:
    """
    Ringbuffer for time-series price data.

    Stores (timestamp, price) tuples per symbol.
    Automatically trims old data outside the lookback window.
    """

    def __init__(self, seconds: int = 300) -> None:
        """
        Initialize price cache.

        Args:
            seconds: Lookback window in seconds
        """
        self.seconds = seconds
        self.buffers: Dict[str, Deque[Tuple[float, float]]] = {}
        self.last: Dict[str, Tuple[float, float]] = {}  # symbol -> (ts, price)

    def update(self, ticks: Dict[str, float], ts: float) -> None:
        """
        Update cache with new price observations.

        Args:
            ticks: Dict mapping symbol to price
            ts: Timestamp of observations
        """
        lb = ts - self.seconds  # Lookback boundary

        for sym, price in ticks.items():
            # Get or create buffer for symbol
            buf = self.buffers.setdefault(sym, deque())

            # Append new observation
            buf.append((ts, price))
            self.last[sym] = (ts, price)

            # Trim old observations
            while buf and buf[0][0] < lb:
                buf.popleft()

    def view(self, symbol: str) -> Iterable[Tuple[float, float]]:
        """
        Get historical view of price observations.

        Args:
            symbol: Trading symbol

        Returns:
            Iterable of (timestamp, price) tuples
        """
        return tuple(self.buffers.get(symbol, ()))

    def last_price(self, symbol: str) -> Optional[float]:
        """
        Get last observed price for symbol.

        Args:
            symbol: Trading symbol

        Returns:
            Last price or None if symbol not found
        """
        tup = self.last.get(symbol)
        return None if tup is None else tup[1]

    def last_timestamp(self, symbol: str) -> Optional[float]:
        """
        Get timestamp of last observation.

        Args:
            symbol: Trading symbol

        Returns:
            Last timestamp or None if symbol not found
        """
        tup = self.last.get(symbol)
        return None if tup is None else tup[0]

    def has_data(self, symbol: str) -> bool:
        """Check if symbol has any cached data."""
        return symbol in self.last

    def clear(self) -> None:
        """Clear all cached data."""
        self.buffers.clear()
        self.last.clear()
