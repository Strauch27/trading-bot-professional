#!/usr/bin/env python3
"""
StateData - Rich context for FSM state

Extends CoinState with order-specific tracking fields.
"""

from dataclasses import dataclass, field
from typing import Dict, List, Optional


@dataclass
class OrderContext:
    """Order lifecycle tracking"""
    order_id: Optional[str] = None
    client_order_id: Optional[str] = None
    placed_at: Optional[float] = None
    ack_at: Optional[float] = None
    filled_at: Optional[float] = None
    cancelled_at: Optional[float] = None

    # Fill tracking
    cumulative_qty: float = 0.0
    target_qty: float = 0.0
    avg_price: float = 0.0
    total_fees: float = 0.0

    # Status
    status: str = "pending"  # pending, ack, filled, cancelled, timeout
    fill_trades: List[Dict] = field(default_factory=list)

    # Retry tracking
    retry_count: int = 0
    last_retry_at: Optional[float] = None

    def is_filled(self, tolerance: float = 0.001) -> bool:
        """Check if order is fully filled (within tolerance)"""
        if self.target_qty == 0:
            return False
        fill_ratio = self.cumulative_qty / self.target_qty
        return fill_ratio >= (1.0 - tolerance)

    def get_fill_ratio(self) -> float:
        """Get fill ratio (0.0 - 1.0)"""
        if self.target_qty == 0:
            return 0.0
        return min(1.0, self.cumulative_qty / self.target_qty)

    def get_remaining_qty(self) -> float:
        """Get remaining quantity to be filled"""
        return max(0.0, self.target_qty - self.cumulative_qty)


@dataclass
class StateData:
    """
    Extended state data for FSM.
    Attached to CoinState.fsm_data
    """
    # Order contexts
    buy_order: Optional[OrderContext] = None
    sell_order: Optional[OrderContext] = None

    # Entry evaluation
    signal_detected_at: Optional[float] = None
    signal_type: Optional[str] = None
    guards_eval_result: Optional[Dict] = None
    risk_limits_eval_result: Optional[Dict] = None

    # Position tracking
    position_opened_at: Optional[float] = None
    position_entry_price: float = 0.0
    position_qty: float = 0.0
    position_fees_accum: float = 0.0

    # PnL tracking
    unrealized_pnl: Optional[float] = None
    unrealized_pct: Optional[float] = None

    # Exit evaluation
    exit_signal: Optional[str] = None
    exit_detected_at: Optional[float] = None
    peak_price: float = 0.0
    trailing_stop: Optional[float] = None

    # Cooldown
    cooldown_started_at: Optional[float] = None

    # Error tracking
    error_count: int = 0
    last_error: Optional[str] = None
    last_error_at: Optional[float] = None

    # Phase timing (for performance analysis)
    buy_order_prepared_at: Optional[float] = None
    sell_order_prepared_at: Optional[float] = None

    def get_position_age_seconds(self, current_time: float) -> float:
        """Get position age in seconds"""
        if self.position_opened_at is None:
            return 0.0
        return current_time - self.position_opened_at

    def get_cooldown_remaining_seconds(self, current_time: float, cooldown_duration: float) -> float:
        """Get remaining cooldown time in seconds"""
        if self.cooldown_started_at is None:
            return 0.0
        elapsed = current_time - self.cooldown_started_at
        remaining = cooldown_duration - elapsed
        return max(0.0, remaining)

    def is_cooldown_expired(self, current_time: float, cooldown_duration: float) -> bool:
        """Check if cooldown period has expired"""
        return self.get_cooldown_remaining_seconds(current_time, cooldown_duration) == 0.0

    def reset_buy_order(self):
        """Reset buy order context"""
        self.buy_order = None
        self.buy_order_prepared_at = None

    def reset_sell_order(self):
        """Reset sell order context"""
        self.sell_order = None
        self.sell_order_prepared_at = None

    def reset_position(self):
        """Reset position tracking"""
        self.position_opened_at = None
        self.position_entry_price = 0.0
        self.position_qty = 0.0
        self.position_fees_accum = 0.0
        self.unrealized_pnl = None
        self.unrealized_pct = None
        self.peak_price = 0.0
        self.trailing_stop = None

    def reset_all(self):
        """Reset all state data (for new cycle)"""
        self.reset_buy_order()
        self.reset_sell_order()
        self.reset_position()
        self.signal_detected_at = None
        self.signal_type = None
        self.guards_eval_result = None
        self.risk_limits_eval_result = None
        self.exit_signal = None
        self.exit_detected_at = None
        self.cooldown_started_at = None
        self.error_count = 0
        self.last_error = None
        self.last_error_at = None
