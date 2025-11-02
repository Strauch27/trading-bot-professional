#!/usr/bin/env python3
"""
FSM Exit Engine - Prioritized Exit Rule Evaluator

Provides deterministic exit decision making with strict priority:
    0. HARD_SL (highest priority)
    1. HARD_TP
    2. TRAILING
    3. TIME (lowest priority)

When multiple exits trigger simultaneously, highest priority wins.
"""

import logging
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Literal, Optional

import config

logger = logging.getLogger(__name__)


@dataclass
class ExitDecision:
    """Represents a triggered exit rule with priority."""
    rule: Literal["HARD_SL", "HARD_TP", "TRAILING", "TIME"]
    price: float
    reason: str
    priority: int  # Lower = higher priority (0=HARD_SL, 3=TIME)


class FSMExitEngine:
    """
    Prioritized exit rule evaluator for FSM.

    Evaluates all exit conditions and returns the highest-priority
    triggered rule. Ensures deterministic exits even when multiple
    rules trigger simultaneously.
    """

    def __init__(self):
        self.logger = logger

    def choose_exit(self,
                   coin_state,
                   current_price: float,
                   current_time: Optional[float] = None) -> Optional[ExitDecision]:
        """
        Returns highest-priority triggered exit rule.

        Priority order (0 = highest):
        0. HARD_SL - Stop loss (e.g. -5%)
        1. HARD_TP - Take profit (e.g. +3%)
        2. TRAILING - Trailing stop (e.g. -2% from peak)
        3. TIME - Time-based exit (e.g. 24h hold)

        Args:
            coin_state: CoinState with position data
            current_price: Current market price
            current_time: Optional timestamp (defaults to now)

        Returns:
            ExitDecision if any rule triggered, None otherwise
        """
        if current_time is None:
            current_time = time.time()

        candidates = []

        # Check HARD_SL (priority 0)
        if decision := self._check_hard_sl(coin_state, current_price):
            candidates.append(decision)

        # Check HARD_TP (priority 1)
        if decision := self._check_hard_tp(coin_state, current_price):
            candidates.append(decision)

        # Check TRAILING (priority 2)
        if decision := self._check_trailing(coin_state, current_price):
            candidates.append(decision)

        # Check TIME (priority 3)
        if decision := self._check_time_exit(coin_state, current_time):
            candidates.append(decision)

        # Return highest priority (lowest number)
        if candidates:
            best = min(candidates, key=lambda d: d.priority)
            self.logger.info(f"{coin_state.symbol}: Exit decision: {best.rule} (priority {best.priority}) - {best.reason}")
            return best

        return None

    def _check_hard_sl(self, coin_state, current_price: float) -> Optional[ExitDecision]:
        """Hard stop loss - unconditional, highest priority."""
        if not hasattr(coin_state, 'sl_px') or not hasattr(coin_state, 'sl_active'):
            return None

        if not coin_state.sl_active:
            return None

        if coin_state.sl_px <= 0:
            return None

        if current_price <= coin_state.sl_px:
            pnl_pct = ((current_price - coin_state.entry_price) / coin_state.entry_price) * 100
            return ExitDecision(
                rule="HARD_SL",
                price=coin_state.sl_px,
                reason=f"Hard SL hit at {coin_state.sl_px:.8f} (PnL: {pnl_pct:.2f}%)",
                priority=0
            )

        return None

    def _check_hard_tp(self, coin_state, current_price: float) -> Optional[ExitDecision]:
        """Hard take profit - unconditional."""
        if not hasattr(coin_state, 'tp_px') or not hasattr(coin_state, 'tp_active'):
            return None

        if not coin_state.tp_active:
            return None

        if coin_state.tp_px <= 0:
            return None

        if current_price >= coin_state.tp_px:
            pnl_pct = ((current_price - coin_state.entry_price) / coin_state.entry_price) * 100
            return ExitDecision(
                rule="HARD_TP",
                price=coin_state.tp_px,
                reason=f"Hard TP hit at {coin_state.tp_px:.8f} (PnL: {pnl_pct:.2f}%)",
                priority=1
            )

        return None

    def _check_trailing(self, coin_state, current_price: float) -> Optional[ExitDecision]:
        """Trailing stop - only active after activation threshold."""
        # Check if trailing is enabled in config
        if not getattr(config, 'USE_TRAILING_STOP', False):
            return None

        # Check if coin_state has trailing fields
        if not hasattr(coin_state, 'peak_price') or not hasattr(coin_state, 'trailing_trigger'):
            return None

        # Update peak price if current price is higher
        if current_price > coin_state.peak_price:
            coin_state.peak_price = current_price
            # Recalculate trailing trigger
            trail_distance_pct = getattr(config, 'TRAILING_DISTANCE_PCT', 0.98)  # 2% below peak
            coin_state.trailing_trigger = coin_state.peak_price * trail_distance_pct

        # Check if trailing stop hit
        if coin_state.trailing_trigger > 0 and current_price <= coin_state.trailing_trigger:
            # Calculate PnL from peak (not from entry)
            drop_from_peak_pct = ((current_price - coin_state.peak_price) / coin_state.peak_price) * 100
            pnl_pct = ((current_price - coin_state.entry_price) / coin_state.entry_price) * 100

            return ExitDecision(
                rule="TRAILING",
                price=current_price,
                reason=f"Trailing stop from peak {coin_state.peak_price:.8f} (drop: {drop_from_peak_pct:.2f}%, PnL: {pnl_pct:.2f}%)",
                priority=2
            )

        return None

    def _check_time_exit(self, coin_state, current_time: float) -> Optional[ExitDecision]:
        """Time-based exit - lowest priority."""
        if not hasattr(coin_state, 'entry_ts') or coin_state.entry_ts <= 0:
            return None

        # Get max hold time from config (in minutes)
        max_hold_minutes = getattr(config, 'MAX_POSITION_HOLD_MINUTES', 60)
        max_hold_seconds = max_hold_minutes * 60

        hold_time = current_time - coin_state.entry_ts

        if hold_time >= max_hold_seconds:
            hold_time_minutes = hold_time / 60.0
            return ExitDecision(
                rule="TIME",
                price=0.0,  # Market price
                reason=f"Max hold time {max_hold_minutes} min reached (held: {hold_time_minutes:.1f} min)",
                priority=3
            )

        return None


# Convenience function for direct usage
def evaluate_exit(coin_state, current_price: float, current_time: Optional[float] = None) -> Optional[ExitDecision]:
    """
    Convenience function to evaluate exits without creating engine instance.

    Usage:
        from core.fsm.exit_engine import evaluate_exit

        decision = evaluate_exit(coin_state, current_price)
        if decision:
            if decision.rule == "HARD_SL":
                # Handle stop loss exit
            elif decision.rule == "HARD_TP":
                # Handle take profit exit
    """
    engine = FSMExitEngine()
    return engine.choose_exit(coin_state, current_price, current_time)


# Alias for backward compatibility
ExitEngine = FSMExitEngine
