#!/usr/bin/env python3
"""
FSM Phase Audit Logging

Logs every phase transition with full context for debugging and compliance.
"""

from core.fsm.fsm_events import EventContext
from core.fsm.phases import Phase
from core.fsm.state import CoinState
import time
import logging

logger = logging.getLogger(__name__)


def log_phase_transition(
    symbol: str,
    from_phase: Phase,
    to_phase: Phase,
    event: str,
    coin_state: CoinState,
    ctx: EventContext
):
    """
    Log FSM phase transition to audit log.

    Captures:
    - Phase transition (from → to)
    - Triggering event
    - Order IDs
    - State snapshot
    - Timing information
    """
    try:
        from core.logger_factory import log_event, AUDIT_LOG
    except ImportError:
        logger.debug("AUDIT_LOG not available, skipping audit log")
        return

    state_data = getattr(coin_state, 'fsm_data', None)

    # Build state snapshot
    state_snapshot = {
        'phase_from': from_phase.name,
        'phase_to': to_phase.name,
        'event': event,
        'symbol': symbol,
        'timestamp': ctx.timestamp,
    }

    # Add order context if present
    if state_data:
        if state_data.buy_order:
            state_snapshot['buy_order_id'] = state_data.buy_order.order_id
            state_snapshot['buy_order_status'] = state_data.buy_order.status
            state_snapshot['buy_filled_qty'] = state_data.buy_order.cumulative_qty
            state_snapshot['buy_fill_ratio'] = state_data.buy_order.get_fill_ratio()

        if state_data.sell_order:
            state_snapshot['sell_order_id'] = state_data.sell_order.order_id
            state_snapshot['sell_order_status'] = state_data.sell_order.status
            state_snapshot['sell_filled_qty'] = state_data.sell_order.cumulative_qty
            state_snapshot['sell_fill_ratio'] = state_data.sell_order.get_fill_ratio()

        if state_data.position_opened_at:
            state_snapshot['position_age_secs'] = ctx.timestamp - state_data.position_opened_at
            state_snapshot['position_qty'] = state_data.position_qty
            state_snapshot['position_entry'] = state_data.position_entry_price

        if state_data.unrealized_pnl is not None:
            state_snapshot['unrealized_pnl'] = state_data.unrealized_pnl
            state_snapshot['unrealized_pct'] = state_data.unrealized_pct

        if state_data.error_count > 0:
            state_snapshot['error_count'] = state_data.error_count
            state_snapshot['last_error'] = state_data.last_error

    # Log to audit log
    try:
        log_event(
            AUDIT_LOG(),
            "fsm_phase_transition",
            **state_snapshot
        )
    except Exception as e:
        logger.debug(f"Failed to log phase transition to audit log: {e}")


def log_phase_entry(symbol: str, phase: Phase, coin_state: CoinState):
    """Log entry into a phase"""
    try:
        from core.logger_factory import log_event, AUDIT_LOG

        log_event(
            AUDIT_LOG(),
            "fsm_phase_entry",
            symbol=symbol,
            phase=phase.name,
            timestamp=time.time(),
            decision_id=coin_state.decision_id,
            order_id=coin_state.order_id
        )
    except Exception as e:
        logger.debug(f"Failed to log phase entry: {e}")


def log_phase_exit(symbol: str, phase: Phase, coin_state: CoinState, reason: str):
    """Log exit from a phase"""
    try:
        from core.logger_factory import log_event, AUDIT_LOG

        # Calculate phase duration
        phase_duration = coin_state.age_seconds()

        log_event(
            AUDIT_LOG(),
            "fsm_phase_exit",
            symbol=symbol,
            phase=phase.name,
            reason=reason,
            phase_duration_secs=phase_duration,
            timestamp=time.time()
        )
    except Exception as e:
        logger.debug(f"Failed to log phase exit: {e}")


def log_order_attempt(
    symbol: str,
    side: str,
    order_type: str,
    quantity: float,
    price: Optional[float],
    coin_state: CoinState
):
    """Log order placement attempt"""
    try:
        from core.logger_factory import log_event, AUDIT_LOG

        log_event(
            AUDIT_LOG(),
            "fsm_order_attempt",
            symbol=symbol,
            side=side,
            order_type=order_type,
            quantity=quantity,
            price=price,
            phase=coin_state.phase.name,
            decision_id=coin_state.decision_id,
            timestamp=time.time()
        )
    except Exception as e:
        logger.debug(f"Failed to log order attempt: {e}")


def log_order_result(
    symbol: str,
    side: str,
    order_id: str,
    status: str,
    filled_qty: float,
    avg_price: float,
    coin_state: CoinState
):
    """Log order result (filled/cancelled/timeout)"""
    try:
        from core.logger_factory import log_event, AUDIT_LOG

        log_event(
            AUDIT_LOG(),
            "fsm_order_result",
            symbol=symbol,
            side=side,
            order_id=order_id,
            status=status,
            filled_qty=filled_qty,
            avg_price=avg_price,
            phase=coin_state.phase.name,
            decision_id=coin_state.decision_id,
            timestamp=time.time()
        )
    except Exception as e:
        logger.debug(f"Failed to log order result: {e}")


def log_position_snapshot(
    symbol: str,
    coin_state: CoinState,
    current_price: float,
    unrealized_pnl: float
):
    """Log position state snapshot (for periodic monitoring)"""
    try:
        from core.logger_factory import log_event, AUDIT_LOG

        state_data = getattr(coin_state, 'fsm_data', None)

        snapshot = {
            'symbol': symbol,
            'phase': coin_state.phase.name,
            'position_qty': coin_state.amount,
            'entry_price': coin_state.entry_price,
            'current_price': current_price,
            'unrealized_pnl': unrealized_pnl,
            'timestamp': time.time()
        }

        if state_data:
            if state_data.position_opened_at:
                snapshot['position_age_secs'] = time.time() - state_data.position_opened_at

            if state_data.peak_price:
                snapshot['peak_price'] = state_data.peak_price

            if state_data.trailing_stop:
                snapshot['trailing_stop'] = state_data.trailing_stop

        log_event(
            AUDIT_LOG(),
            "fsm_position_snapshot",
            **snapshot
        )
    except Exception as e:
        logger.debug(f"Failed to log position snapshot: {e}")


from typing import Optional


def log_error_recovery(
    symbol: str,
    error_type: str,
    recovery_action: str,
    coin_state: CoinState
):
    """Log error and recovery action"""
    try:
        from core.logger_factory import log_event, AUDIT_LOG

        log_event(
            AUDIT_LOG(),
            "fsm_error_recovery",
            symbol=symbol,
            error_type=error_type,
            recovery_action=recovery_action,
            error_count=coin_state.error_count,
            last_error=coin_state.last_error,
            phase=coin_state.phase.name,
            timestamp=time.time()
        )
    except Exception as e:
        logger.debug(f"Failed to log error recovery: {e}")
