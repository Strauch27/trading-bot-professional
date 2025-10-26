# signals/drop_trigger.py
import time

from config import DEBOUNCE_S, DROP_TRIGGER_VALUE, HYSTERESIS_BPS


class DropTrigger:
    """
    Drop trigger with re-anchoring, hysteresis, and debounce mechanism
    """

    def __init__(self, threshold_bp=None, hysteresis_bps=None, debounce_s=None):
        if threshold_bp is None:
            threshold_bp = int((1.0 - DROP_TRIGGER_VALUE) * 10000)
        if hysteresis_bps is None: hysteresis_bps = HYSTERESIS_BPS
        if debounce_s is None: debounce_s = DEBOUNCE_S
        self.th = threshold_bp
        self.hys = hysteresis_bps
        self.db = debounce_s
        self._last_fire_ts = {}
        self._anchor_by_symbol = {}

    def reanchor(self, symbol, rolling_high):
        """
        Re-anchor only when new high is achieved.

        Args:
            symbol: Trading symbol
            rolling_high: Current rolling high price

        Returns:
            Current anchor price for the symbol
        """
        prev = self._anchor_by_symbol.get(symbol)
        if prev is None or rolling_high > prev:
            self._anchor_by_symbol[symbol] = rolling_high
        return self._anchor_by_symbol[symbol]

    def reanchor_if_new_high(self, symbol, rolling_high, now_ts):
        """Re-anchor if new high is reached"""
        return self.reanchor(symbol, rolling_high)

    def evaluate(self, symbol, price, now_ts):
        """
        Evaluate drop trigger condition with hysteresis and debounce.

        Args:
            symbol: Trading symbol
            price: Current price
            now_ts: Current timestamp

        Returns:
            Tuple of (triggered: bool, info: dict)
        """
        now = time.time()
        anchor = self._anchor_by_symbol.get(symbol)

        if not anchor or anchor <= 0:
            return (False, {"reason": "no_anchor"})

        drop_bp = (1.0 - price / anchor) * 10000.0

        if drop_bp < self.th:
            return (False, {"reason": "below_threshold", "drop_bp": round(drop_bp, 1)})

        # Hysterese: nur auslösen wenn wir die Schwelle + Hysterese überschreiten
        if drop_bp < (self.th + self.hys):
            return (False, {"reason": "need_hysteresis", "drop_bp": round(drop_bp, 1)})

        # Debounce check
        last = self._last_fire_ts.get(symbol, 0)
        if now - last < self.db:
            return (False, {
                "reason": "debounce",
                "remaining_s": round(self.db - (now - last), 1)
            })

        # Trigger confirmed
        self._last_fire_ts[symbol] = now
        return (True, {"drop_bp": round(drop_bp, 1), "anchor": anchor})

    def get_anchor(self, symbol):
        """Get current anchor for symbol"""
        return self._anchor_by_symbol.get(symbol)

    def reset_debounce(self, symbol):
        """Reset debounce timer for symbol"""
        if symbol in self._last_fire_ts:
            del self._last_fire_ts[symbol]
