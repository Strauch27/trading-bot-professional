# signals/confirm.py

class Stabilizer:
    """Stabilizer for confirming consistent signals over multiple ticks"""

    def __init__(self, confirm_ticks=None):
        # V9_3 aggressiver: 1 Tick, wenn nicht via Config gesetzt
        if confirm_ticks is None:
            from config import CONFIRM_TICKS
            confirm_ticks = CONFIRM_TICKS
        self.need = confirm_ticks
        self._cnt = {}  # symbol -> count

    def arm(self, symbol):
        """Arm the stabilizer for a specific symbol"""
        self._cnt[symbol] = 0

    def step(self, symbol, condition_ok=True):
        """
        Step the stabilizer for a symbol with current condition status.

        Args:
            symbol: Trading symbol
            condition_ok: True if condition is met, False otherwise

        Returns:
            True if condition has been stable for needed ticks, False otherwise
        """
        if symbol not in self._cnt:
            self._cnt[symbol] = 0

        self._cnt[symbol] = self._cnt[symbol] + 1 if condition_ok else 0
        return self._cnt[symbol] >= self.need

    def reset(self, symbol=None):
        """Reset the stabilizer counter"""
        if symbol:
            self._cnt[symbol] = 0
        else:
            self._cnt.clear()

    def count(self, symbol):
        """Get current count of consecutive successful conditions"""
        return self._cnt.get(symbol, 0)