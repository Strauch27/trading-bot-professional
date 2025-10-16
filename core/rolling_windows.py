#!/usr/bin/env python3
"""
Rolling Window Manager for Drop Tracking

Centralized management of rolling price windows for drop detection.
Designed to be updated in the market data pipeline, independent of buy flow.
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
    Rolling window that tracks prices over a time period and computes peak.

    Efficiently maintains the maximum value and trims old entries.
    """

    def __init__(self, lookback_s: int):
        """
        Initialize rolling window.

        Args:
            lookback_s: Lookback period in seconds
        """
        self.lookback_s = lookback_s
        self.q: Deque[Tuple[float, float]] = deque()  # (timestamp, price)
        self.max_val: float = float("-inf")  # Peak price in window

    def _trim(self, now_ts: float) -> bool:
        """
        Remove old entries outside the lookback window.

        Args:
            now_ts: Current timestamp

        Returns:
            True if any entries were removed
        """
        lb = now_ts - self.lookback_s
        popped = False

        while self.q and self.q[0][0] < lb:
            t, v = self.q.popleft()
            popped = True

            # If we removed the max value, recompute max
            if v == self.max_val:
                self.max_val = max((x[1] for x in self.q), default=float("-inf"))

        return popped

    def add(self, ts: float, price: float):
        """
        Add a price observation to the window.

        Args:
            ts: Timestamp of observation
            price: Price value
        """
        self.q.append((ts, price))

        # Update max if this is a new peak
        if price > self.max_val:
            self.max_val = price

        # Trim old entries
        self._trim(ts)

    def peak(self) -> Optional[float]:
        """
        Get the peak (maximum) price in the window.

        Returns:
            Peak price or None if window is empty
        """
        return None if self.max_val == float("-inf") else self.max_val

    def drop_pct(self, price: float) -> Optional[float]:
        """
        Calculate drop percentage from peak to current price.

        Args:
            price: Current price

        Returns:
            Drop percentage (negative value) or None if no peak
        """
        p = self.peak()
        if p is None or p <= 0:
            return None
        return (price - p) / p * 100.0

    def snapshot(self, symbol: str, current_price: float, ts: float) -> Dict:
        """
        Create a snapshot of current drop state.

        Args:
            symbol: Trading symbol
            current_price: Current price
            ts: Current timestamp

        Returns:
            Snapshot dict with ts, symbol, current, peak, drop_pct
        """
        peak = self.peak()
        drop = None if peak in (None, 0.0) else (current_price - peak) / peak * 100.0

        return {
            "ts": ts,
            "symbol": symbol,
            "current": current_price,
            "peak": peak,
            "drop_pct": drop
        }

    def to_json(self) -> Dict:
        """Serialize window to JSON for persistence."""
        return {
            "lookback_s": self.lookback_s,
            "data": list(self.q)
        }

    @staticmethod
    def from_json(obj: Dict) -> 'RollingWindow':
        """Deserialize window from JSON."""
        rw = RollingWindow(obj["lookback_s"])
        for t, v in obj["data"]:
            rw.q.append((t, v))
            if v > rw.max_val:
                rw.max_val = v
        return rw


class RollingWindowManager:
    """
    Manages rolling windows for multiple symbols with optional persistence.

    This is the central component for drop tracking, designed to be integrated
    into the market data pipeline for guaranteed updates independent of buy flow.
    """

    def __init__(
        self,
        lookback_s: int,
        persist: bool = False,
        base_path: str = "state/drop_windows"
    ):
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

    def _path(self, symbol: str) -> str:
        """Get file path for symbol's persisted window."""
        return os.path.join(self.base_path, f"{symbol}.json")

    def ensure(self, symbol: str) -> RollingWindow:
        """
        Ensure a rolling window exists for the symbol.

        If persistence is enabled and a file exists, loads from disk.
        Otherwise creates a new window.

        Args:
            symbol: Trading symbol

        Returns:
            RollingWindow for the symbol
        """
        if symbol not in self.windows:
            rw = None

            # Try to load from persistence
            if self.persist:
                try:
                    with open(self._path(symbol), "r") as f:
                        rw = RollingWindow.from_json(json.load(f))
                    logger.debug(f"Loaded persisted window for {symbol}")
                except FileNotFoundError:
                    pass  # Expected for new symbols
                except Exception as e:
                    logger.warning(f"Failed to load persisted window for {symbol}: {e}")

            # Create new window if not loaded
            if rw is None:
                rw = RollingWindow(self.lookback_s)

            self.windows[symbol] = rw

        return self.windows[symbol]

    def update(self, symbol: str, ts: float, price: float):
        """
        Update the rolling window for a symbol with a new price.

        Args:
            symbol: Trading symbol
            ts: Timestamp
            price: Price value
        """
        rw = self.ensure(symbol)
        rw.add(ts, price)

    def snapshot(self, symbol: str, price: float, ts: float) -> Dict:
        """
        Get a drop snapshot for a symbol.

        Args:
            symbol: Trading symbol
            price: Current price
            ts: Current timestamp

        Returns:
            Snapshot dict
        """
        rw = self.ensure(symbol)
        return rw.snapshot(symbol, price, ts)

    def persist_all(self):
        """Persist all windows to disk (if persistence enabled)."""
        if not self.persist:
            return

        for sym, rw in self.windows.items():
            try:
                with open(self._path(sym), "w") as f:
                    json.dump(rw.to_json(), f)
            except Exception as e:
                logger.warning(f"Failed to persist window for {sym}: {e}")

    def get_all_snapshots(self, prices: Dict[str, float], ts: float) -> list[Dict]:
        """
        Get drop snapshots for all symbols with prices.

        Args:
            prices: Dict mapping symbol to current price
            ts: Current timestamp

        Returns:
            List of snapshot dicts
        """
        snapshots = []
        for symbol, price in prices.items():
            snap = self.snapshot(symbol, price, ts)
            if snap["drop_pct"] is not None:
                snapshots.append(snap)
        return snapshots
