#!/usr/bin/env python3
"""
Rolling Window Manager for Drop/Rise Tracking

Centralized management of rolling price windows for drop/rise detection.
Designed to be updated in the market data pipeline, independent of buy flow.

New Features:
- Tracks both peak (max) and trough (min)
- Computes both drop_pct and rise_pct
- Simplified API for snapshot builder
"""

import os
import json
import time
import logging
from collections import deque
from typing import Dict, Deque, Tuple, Optional

logger = logging.getLogger(__name__)


class RollingWindow:
    """
    Rolling window that tracks prices over a time period.

    Efficiently maintains max/min values and trims old entries.
    """

    def __init__(self, lookback_s: int) -> None:
        """
        Initialize rolling window.

        Args:
            lookback_s: Lookback period in seconds
        """
        self.lookback_s = lookback_s
        self.q: Deque[Tuple[float, float]] = deque()  # (timestamp, price)
        self.max_val: float = float("-inf")  # Peak price in window
        self.min_val: float = float("inf")   # Trough price in window

    def _trim(self, now_ts: float) -> None:
        """
        Remove old entries outside the lookback window.

        Args:
            now_ts: Current timestamp
        """
        lb = now_ts - self.lookback_s
        popped_max = popped_min = False

        while self.q and self.q[0][0] < lb:
            _, v = self.q.popleft()
            popped_max |= (v == self.max_val)
            popped_min |= (v == self.min_val)

        # Recompute extrema if they were removed
        if popped_max:
            self.max_val = max((v for _, v in self.q), default=float("-inf"))
        if popped_min:
            self.min_val = min((v for _, v in self.q), default=float("inf"))

    def add(self, ts: float, price: float) -> None:
        """
        Add a price observation to the window.

        Args:
            ts: Timestamp of observation
            price: Price value
        """
        self.q.append((ts, price))

        # Update extrema
        if price > self.max_val:
            self.max_val = price
        if price < self.min_val:
            self.min_val = price

        # Trim old entries
        self._trim(ts)

    def peak(self) -> Optional[float]:
        """
        Get the peak (maximum) price in the window.

        Returns:
            Peak price or None if window is empty
        """
        return None if self.max_val == float("-inf") else self.max_val

    def trough(self) -> Optional[float]:
        """
        Get the trough (minimum) price in the window.

        Returns:
            Trough price or None if window is empty
        """
        return None if self.min_val == float("inf") else self.min_val

    def drop_pct(self, price: float) -> Optional[float]:
        """
        Calculate drop percentage from peak to current price.

        Args:
            price: Current price

        Returns:
            Drop percentage or None if no peak
        """
        p = self.peak()
        if p is None or p <= 0:
            return None
        return (price - p) / p * 100.0

    def rise_pct(self, price: float) -> Optional[float]:
        """
        Calculate rise percentage from trough to current price.

        Args:
            price: Current price

        Returns:
            Rise percentage or None if no trough
        """
        t = self.trough()
        if t is None or t <= 0:
            return None
        return (price - t) / t * 100.0


class RollingWindowManager:
    """
    Manages rolling windows for multiple symbols with optional persistence.

    This is the central component for drop/rise tracking, designed to be integrated
    into the market data pipeline for guaranteed updates independent of buy flow.
    """

    def __init__(
        self,
        lookback_s: int,
        persist: bool = False,
        base_path: str = "state/drop_windows"
    ) -> None:
        """
        Initialize manager.

        Args:
            lookback_s: Lookback period for all windows
            persist: Whether to persist windows to disk
            base_path: Base directory for persistence files
        """
        self.lookback_s = lookback_s
        self.persist = persist
        self.base_path = base_path
        self.windows: Dict[str, RollingWindow] = {}

        if self.persist:
            os.makedirs(self.base_path, exist_ok=True)
            logger.info(f"RollingWindowManager initialized with persistence: {self.base_path}")
        else:
            logger.info(f"RollingWindowManager initialized (no persistence)")

    def _path(self, sym: str) -> str:
        """Get file path for symbol's persisted window."""
        return os.path.join(self.base_path, f"{sym}.json")

    def ensure(self, sym: str) -> RollingWindow:
        """
        Ensure a rolling window exists for the symbol.

        Args:
            sym: Trading symbol

        Returns:
            RollingWindow for the symbol
        """
        rw = self.windows.get(sym)
        if rw is None:
            rw = RollingWindow(self.lookback_s)
            self.windows[sym] = rw
        return rw

    def update(self, sym: str, ts: float, price: float) -> None:
        """
        Update the rolling window for a symbol with a new price.

        Args:
            sym: Trading symbol
            ts: Timestamp
            price: Price value
        """
        self.ensure(sym).add(ts, price)

    def view(self, sym: str) -> Dict[str, Optional[float]]:
        """
        Get view of window extrema for symbol.

        Args:
            sym: Trading symbol

        Returns:
            Dict with peak and trough values
        """
        rw = self.ensure(sym)
        return {"peak": rw.peak(), "trough": rw.trough()}

    def persist_all(self) -> None:
        """Persist all windows to disk (if persistence enabled)."""
        if not self.persist:
            return

        for sym, rw in self.windows.items():
            try:
                data = {
                    "lookback_s": rw.lookback_s,
                    "data": list(rw.q)
                }
                with open(self._path(sym), "w") as f:
                    json.dump(data, f)
            except Exception as e:
                logger.warning(f"Failed to persist window for {sym}: {e}")

    def clear(self) -> None:
        """Clear all windows (useful for testing)."""
        self.windows.clear()
