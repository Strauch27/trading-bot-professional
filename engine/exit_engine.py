#!/usr/bin/env python3
"""
Exit Engine - Prioritized Exit Rules

Consolidates all exit logic with deterministic rule prioritization:
1. Hard Stop Loss (highest priority)
2. Hard Take Profit
3. Trailing Stop Loss
4. Time-based Exit (lowest priority)

Generates exit signals for OrderRouter execution.
"""

from dataclasses import dataclass
from typing import Optional, Dict
import time


@dataclass
class ExitRule:
    """
    Exit rule with priority and reason.

    Attributes:
        code: Rule identifier (HARD_SL, HARD_TP, TRAIL_SL, TIME_EXIT)
        reason: Human-readable exit reason
        limit_price: Limit price for order (None = market order)
        strength: Rule strength 0-1 for telemetry
    """
    code: str
    reason: str
    limit_price: Optional[float]
    strength: float  # 0..1 for telemetry


class ExitEngine:
    """
    Evaluates exit rules in priority order.

    Rule Priority (first match wins):
    1. HARD_SL: Maximum loss protection
    2. HARD_TP: Target profit reached
    3. TRAIL_SL: Trailing stop from peak/trough
    4. TIME_EXIT: Maximum hold time

    Returns exit signal dict for Intent assembly.
    """

    def __init__(self, engine, config):
        """
        Initialize exit engine.

        Args:
            engine: Main engine instance (for portfolio, snapshots)
            config: Configuration module
        """
        self.engine = engine
        self.cfg = config

    def _hard_sl(self, sym: str, pos, snap: Dict) -> Optional[ExitRule]:
        """
        Hard stop loss: absolute maximum loss threshold.

        Args:
            sym: Trading symbol
            pos: Position object
            snap: Market snapshot

        Returns:
            ExitRule if stop loss triggered, None otherwise
        """
        sl_pct = getattr(self.cfg, "EXIT_HARD_SL_PCT", None)
        if sl_pct is None:
            return None

        last = snap["price"]["last"]
        avg = pos.avg_price
        direction = 1 if pos.qty > 0 else -1

        # PnL percentage relative to entry
        pnl_pct = (last - avg) / avg * 100 * direction

        if pnl_pct <= -abs(sl_pct):
            # Stop loss triggered
            limit_price = None if getattr(self.cfg, "EXIT_SL_MARKET", True) else last
            return ExitRule("HARD_SL", "max_loss_reached", limit_price, 1.0)

        return None

    def _hard_tp(self, sym: str, pos, snap: Dict) -> Optional[ExitRule]:
        """
        Hard take profit: absolute profit target.

        Args:
            sym: Trading symbol
            pos: Position object
            snap: Market snapshot

        Returns:
            ExitRule if take profit triggered, None otherwise
        """
        tp_pct = getattr(self.cfg, "EXIT_HARD_TP_PCT", None)
        if tp_pct is None:
            return None

        last = snap["price"]["last"]
        avg = pos.avg_price
        direction = 1 if pos.qty > 0 else -1

        # PnL percentage relative to entry
        pnl_pct = (last - avg) / avg * 100 * direction

        if pnl_pct >= abs(tp_pct):
            # Take profit triggered
            limit_price = None if getattr(self.cfg, "EXIT_TP_MARKET", True) else last
            return ExitRule("HARD_TP", "target_reached", limit_price, 0.9)

        return None

    def _trailing_sl(self, sym: str, pos, snap: Dict) -> Optional[ExitRule]:
        """
        Trailing stop loss: drawdown from peak (long) or trough (short).

        Uses rolling window peak/trough from snapshot.

        Args:
            sym: Trading symbol
            pos: Position object
            snap: Market snapshot

        Returns:
            ExitRule if trailing stop triggered, None otherwise
        """
        if not getattr(self.cfg, "EXIT_TRAILING_ENABLE", True):
            return None

        trail_pct = getattr(self.cfg, "EXIT_TRAILING_PCT", 1.0)
        win = snap.get("windows", {})
        last = snap["price"]["last"]

        # Long position: track drawdown from peak
        if pos.qty > 0 and win.get("peak"):
            draw = (last - win["peak"]) / win["peak"] * 100
            if draw <= -abs(trail_pct):
                return ExitRule("TRAIL_SL", "trail_drawdown_hit", None, 0.8)

        # Short position: track rise from trough
        if pos.qty < 0 and win.get("trough"):
            rise = (last - win["trough"]) / win["trough"] * 100
            if rise >= abs(trail_pct):
                return ExitRule("TRAIL_SL", "trail_drawup_hit", None, 0.8)

        return None

    def _time_exit(self, sym: str, pos, snap: Dict) -> Optional[ExitRule]:
        """
        Time-based exit: maximum position hold time.

        Args:
            sym: Trading symbol
            pos: Position object
            snap: Market snapshot

        Returns:
            ExitRule if hold time exceeded, None otherwise
        """
        ttl = getattr(self.cfg, "EXIT_MAX_HOLD_S", None)
        if not ttl or ttl <= 0:
            return None

        if time.time() - pos.opened_ts >= ttl:
            return ExitRule("TIME_EXIT", "max_hold_time", None, 0.5)

        return None

    def choose(self, sym: str) -> Optional[Dict]:
        """
        Evaluate all exit rules in priority order.

        Priority: HARD_SL > HARD_TP > TRAIL_SL > TIME_EXIT

        Args:
            sym: Trading symbol

        Returns:
            Exit signal dict for Intent assembly, or None if no exit
        """
        pos = self.engine.portfolio.positions.get(sym)
        snap = self.engine.snapshots.get(sym)

        if not pos or not snap or pos.qty == 0:
            return None

        # Evaluate rules in priority order (first match wins)
        rules = [
            self._hard_sl(sym, pos, snap),
            self._hard_tp(sym, pos, snap),
            self._trailing_sl(sym, pos, snap),
            self._time_exit(sym, pos, snap),
        ]

        chosen = next((r for r in rules if r is not None), None)

        if not chosen:
            return None

        # Build exit signal
        last = snap["price"]["last"]
        qty = abs(pos.qty)  # Close entire position
        side = "sell" if pos.qty > 0 else "buy"  # Reverse side to close

        return {
            "symbol": sym,
            "side": side,
            "qty": qty,
            "limit_price": chosen.limit_price or last,
            "reason": chosen.reason,
            "rule_code": chosen.code,
            "strength": chosen.strength
        }
