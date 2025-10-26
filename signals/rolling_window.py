# signals/rolling_window.py
from collections import deque


class RollingWindow:
    """Rolling window for tracking maximum values efficiently"""

    def __init__(self, maxlen):
        self.q = deque(maxlen=maxlen)
        self._max = None

    def push(self, price: float):
        """Add new price to rolling window and update maximum"""
        self.q.append(price)
        if self._max is None or price > self._max:
            self._max = price
        # Recompute max if it fell out of the window
        if len(self.q) == self.q.maxlen and self._max not in self.q:
            self._max = max(self.q) if self.q else None

    def add(self, timestamp, price: float):
        """Add new price to rolling window (alias for push, ignores timestamp)"""
        self.push(price)

    @property
    def max(self):
        """Get current maximum value in window"""
        return self._max if self._max is not None else float("-inf")

    @property
    def size(self):
        """Get current window size"""
        return len(self.q)

    @property
    def is_full(self):
        """Check if window is at maximum capacity"""
        return len(self.q) == self.q.maxlen

    def get_window_start_price(self):
        """Get the first (oldest) price in the rolling window (anchor price)"""
        if len(self.q) > 0:
            return self.q[0]
        return None
