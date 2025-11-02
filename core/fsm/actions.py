#!/usr/bin/env python3
"""
Action functions executed during state transitions.

All actions must be:
1. Pure functions (no hidden state)
2. Idempotent (safe to call multiple times)
3. Fast (< 10ms)
4. Logged (structured events)
"""

import logging
import math

import config
from core.fsm.fsm_events import EventContext
from core.fsm.state import CoinState

logger = logging.getLogger(__name__)


def action_warmup_complete(ctx: EventContext, coin_state: CoinState) -> None:
    """Transition: WARMUP → IDLE"""
    try:
        from core.logger_factory import DECISION_LOG, log_event
        log_event(DECISION_LOG(), "fsm_transition",
                  symbol=ctx.symbol,
                  from_phase="WARMUP",
                  to_phase="IDLE",
                  event=ctx.event.name,
                  reason="warmup_period_complete")
    except Exception as e:
        logger.debug(f"Failed to log warmup_complete: {e}")


def action_idle_tick(ctx: EventContext, coin_state: CoinState) -> None:
    """Transition: IDLE → IDLE (no-op)"""
    pass  # Tick processing happens in evaluator


def action_evaluate_entry(ctx: EventContext, coin_state: CoinState) -> None:
    """Transition: IDLE → ENTRY_EVAL"""
    coin_state.note = "evaluating entry"

    # Store signal information if available
    if hasattr(coin_state, 'fsm_data') and coin_state.fsm_data:
        coin_state.fsm_data.signal_detected_at = ctx.timestamp
        coin_state.fsm_data.signal_type = ctx.data.get('signal_type')

    try:
        from core.logger_factory import DECISION_LOG, log_event
        log_event(DECISION_LOG(), "fsm_transition",
                  symbol=ctx.symbol,
                  from_phase="IDLE",
                  to_phase="ENTRY_EVAL",
                  event=ctx.event.name,
                  signal_type=ctx.data.get('signal_type'))
    except Exception as e:
        logger.debug(f"Failed to log evaluate_entry: {e}")


def action_prepare_buy(ctx: EventContext, coin_state: CoinState) -> None:
    """Transition: ENTRY_EVAL → PLACE_BUY"""
    coin_state.note = "preparing buy order"

    if hasattr(coin_state, 'fsm_data') and coin_state.fsm_data:
        coin_state.fsm_data.buy_order_prepared_at = ctx.timestamp

    try:
        from core.logger_factory import DECISION_LOG, log_event
        log_event(DECISION_LOG(), "fsm_transition",
                  symbol=ctx.symbol,
                  from_phase="ENTRY_EVAL",
                  to_phase="PLACE_BUY",
                  event=ctx.event.name,
                  order_price=ctx.data.get('order_price'),
                  order_qty=ctx.data.get('order_qty'))
    except Exception as e:
        logger.debug(f"Failed to log prepare_buy: {e}")


def action_log_blocked(ctx: EventContext, coin_state: CoinState) -> None:
    """Transition: ENTRY_EVAL → IDLE (blocked)"""
    import time

    coin_state.note = f"blocked: {ctx.data.get('block_reason', 'unknown')}"

    # CRITICAL FIX: Set cooldown to prevent IDLE->ENTRY_EVAL->IDLE loop
    # When guards block, wait 30s before re-evaluating
    coin_state.cooldown_until = time.time() + 30.0

    try:
        from core.logger_factory import DECISION_LOG, log_event
        log_event(DECISION_LOG(), "fsm_transition",
                  symbol=ctx.symbol,
                  from_phase="ENTRY_EVAL",
                  to_phase="IDLE",
                  event=ctx.event.name,
                  block_reason=ctx.data.get('block_reason'))
    except Exception as e:
        logger.debug(f"Failed to log blocked: {e}")


def action_wait_for_fill(ctx: EventContext, coin_state: CoinState) -> None:
    """Transition: PLACE_BUY → WAIT_FILL"""
    coin_state.order_id = ctx.order_id
    coin_state.order_placed_ts = ctx.timestamp
    coin_state.note = f"waiting for fill: {ctx.order_id}"

    if hasattr(coin_state, 'fsm_data') and coin_state.fsm_data and coin_state.fsm_data.buy_order:
        coin_state.fsm_data.buy_order.order_id = ctx.order_id
        coin_state.fsm_data.buy_order.placed_at = ctx.timestamp

    try:
        from core.logger_factory import DECISION_LOG, log_event
        log_event(DECISION_LOG(), "fsm_transition",
                  symbol=ctx.symbol,
                  from_phase="PLACE_BUY",
                  to_phase="WAIT_FILL",
                  event=ctx.event.name,
                  order_id=ctx.order_id)
    except Exception as e:
        logger.debug(f"Failed to log wait_for_fill: {e}")


def action_open_position(ctx: EventContext, coin_state: CoinState) -> None:
    """Transition: WAIT_FILL → POSITION"""
    # CRITICAL FIX (C-FSM-04): Idempotency guard - prevent double application
    if coin_state.amount > 0 and coin_state.entry_ts > 0:
        logger.debug(f"action_open_position already applied for {ctx.symbol}, skipping")
        return

    coin_state.amount = ctx.filled_qty or 0.0
    coin_state.entry_price = ctx.avg_price or 0.0
    coin_state.entry_ts = ctx.timestamp
    coin_state.note = f"position opened: {ctx.filled_qty:.6f} @ {ctx.avg_price:.4f}"

    # P0-2: Initialize TP/SL prices after fill
    entry_price = ctx.avg_price or 0.0
    if entry_price > 0:
        # Get TP/SL percentages from config
        tp_pct = getattr(config, 'TP_PCT', 3.0)  # Default 3% TP
        sl_pct = getattr(config, 'SL_PCT', 5.0)  # Default 5% SL

        # Get price tick for rounding (default to 8 decimals for crypto)
        price_tick = ctx.data.get("price_tick", 0.00000001)
        decimals = int(abs(math.log10(price_tick)))

        # Calculate and set TP/SL prices
        coin_state.tp_px = round(entry_price * (1 + tp_pct / 100), decimals)
        coin_state.sl_px = round(entry_price * (1 - sl_pct / 100), decimals)
        coin_state.tp_active = True
        coin_state.sl_active = True

        # Initialize trailing stop
        coin_state.peak_price = entry_price
        coin_state.trailing_trigger = 0.0  # Will be set when price rises

        logger.info(f"{ctx.symbol}: TP/SL initialized - TP: {coin_state.tp_px:.8f} (+{tp_pct}%), SL: {coin_state.sl_px:.8f} (-{sl_pct}%)")

    if hasattr(coin_state, 'fsm_data') and coin_state.fsm_data:
        coin_state.fsm_data.position_opened_at = ctx.timestamp
        coin_state.fsm_data.position_qty = ctx.filled_qty or 0.0
        coin_state.fsm_data.position_entry_price = ctx.avg_price or 0.0

    try:
        from core.logger_factory import DECISION_LOG, log_event
        log_event(DECISION_LOG(), "position_opened",
                  symbol=ctx.symbol,
                  qty=ctx.filled_qty,
                  avg_entry=ctx.avg_price,
                  notional=(ctx.filled_qty or 0.0) * (ctx.avg_price or 0.0),
                  opened_at=ctx.timestamp)

        log_event(DECISION_LOG(), "fsm_transition",
                  symbol=ctx.symbol,
                  from_phase="WAIT_FILL",
                  to_phase="POSITION",
                  event=ctx.event.name)
    except Exception as e:
        logger.debug(f"Failed to log open_position: {e}")


def action_handle_partial_buy(ctx: EventContext, coin_state: CoinState) -> None:
    """Transition: WAIT_FILL → WAIT_FILL (partial fill)"""
    coin_state.note = f"partial fill: {ctx.filled_qty:.6f}"

    if hasattr(coin_state, 'fsm_data') and coin_state.fsm_data and coin_state.fsm_data.buy_order:
        order_ctx = coin_state.fsm_data.buy_order
        order_ctx.cumulative_qty = (order_ctx.cumulative_qty or 0.0) + (ctx.filled_qty or 0.0)

    try:
        from core.logger_factory import DECISION_LOG, log_event
        log_event(DECISION_LOG(), "order_partial_fill",
                  symbol=ctx.symbol,
                  filled_qty=ctx.filled_qty,
                  order_id=ctx.order_id)
    except Exception as e:
        logger.debug(f"Failed to log partial_buy: {e}")


def action_cancel_and_cleanup(ctx: EventContext, coin_state: CoinState) -> None:
    """Transition: WAIT_FILL → IDLE (timeout)"""
    coin_state.note = "order timeout - cleaned up"
    coin_state.order_id = None
    coin_state.order_placed_ts = 0.0

    try:
        from core.logger_factory import DECISION_LOG, log_event
        log_event(DECISION_LOG(), "fsm_transition",
                  symbol=ctx.symbol,
                  from_phase="WAIT_FILL",
                  to_phase="IDLE",
                  event=ctx.event.name,
                  reason="order_timeout")
    except Exception as e:
        logger.debug(f"Failed to log cancel_and_cleanup: {e}")


def action_cleanup_cancelled(ctx: EventContext, coin_state: CoinState) -> None:
    """Transition: WAIT_FILL → IDLE (cancelled)"""
    coin_state.note = "order cancelled"
    coin_state.order_id = None
    coin_state.order_placed_ts = 0.0

    try:
        from core.logger_factory import DECISION_LOG, log_event
        log_event(DECISION_LOG(), "fsm_transition",
                  symbol=ctx.symbol,
                  from_phase="WAIT_FILL",
                  to_phase="IDLE",
                  event=ctx.event.name,
                  reason="order_cancelled")
    except Exception as e:
        logger.debug(f"Failed to log cleanup_cancelled: {e}")


def action_handle_reject(ctx: EventContext, coin_state: CoinState) -> None:
    """Transition: PLACE_BUY → IDLE (rejected)"""
    coin_state.note = f"order rejected: {ctx.data.get('reject_reason', 'unknown')}"
    coin_state.retry_count += 1

    try:
        from core.logger_factory import DECISION_LOG, log_event
        log_event(DECISION_LOG(), "fsm_transition",
                  symbol=ctx.symbol,
                  from_phase="PLACE_BUY",
                  to_phase="IDLE",
                  event=ctx.event.name,
                  reject_reason=ctx.data.get('reject_reason'))
    except Exception as e:
        logger.debug(f"Failed to log reject: {e}")


def action_check_exit(ctx: EventContext, coin_state: CoinState) -> None:
    """Transition: POSITION → EXIT_EVAL"""
    # Tick triggers exit evaluation - no state changes needed
    pass


def action_update_pnl(ctx: EventContext, coin_state: CoinState) -> None:
    """Transition: POSITION → POSITION (PnL update)"""
    coin_state.current_price = ctx.data.get('current_price', coin_state.current_price)

    if hasattr(coin_state, 'fsm_data') and coin_state.fsm_data:
        coin_state.fsm_data.unrealized_pnl = ctx.data.get('unrealized_pnl')
        coin_state.fsm_data.unrealized_pct = ctx.data.get('unrealized_pct')


def action_prepare_sell(ctx: EventContext, coin_state: CoinState) -> None:
    """Transition: EXIT_EVAL → PLACE_SELL"""
    coin_state.exit_reason = ctx.data.get('exit_signal', 'unknown')
    coin_state.note = f"preparing sell: {coin_state.exit_reason}"

    if hasattr(coin_state, 'fsm_data') and coin_state.fsm_data:
        coin_state.fsm_data.exit_signal = ctx.data.get('exit_signal')
        coin_state.fsm_data.exit_detected_at = ctx.timestamp

    try:
        from core.logger_factory import DECISION_LOG, log_event
        log_event(DECISION_LOG(), "fsm_transition",
                  symbol=ctx.symbol,
                  from_phase="EXIT_EVAL",
                  to_phase="PLACE_SELL",
                  event=ctx.event.name,
                  exit_signal=coin_state.exit_reason)
    except Exception as e:
        logger.debug(f"Failed to log prepare_sell: {e}")


def action_continue_holding(ctx: EventContext, coin_state: CoinState) -> None:
    """Transition: EXIT_EVAL → POSITION (no exit signal)"""
    # No exit signal - continue holding
    pass


def action_wait_for_sell(ctx: EventContext, coin_state: CoinState) -> None:
    """Transition: PLACE_SELL → WAIT_SELL_FILL"""
    coin_state.order_id = ctx.order_id
    coin_state.order_placed_ts = ctx.timestamp
    coin_state.note = f"waiting for sell fill: {ctx.order_id}"

    if hasattr(coin_state, 'fsm_data') and coin_state.fsm_data and coin_state.fsm_data.sell_order:
        coin_state.fsm_data.sell_order.order_id = ctx.order_id
        coin_state.fsm_data.sell_order.placed_at = ctx.timestamp

    try:
        from core.logger_factory import DECISION_LOG, log_event
        log_event(DECISION_LOG(), "fsm_transition",
                  symbol=ctx.symbol,
                  from_phase="PLACE_SELL",
                  to_phase="WAIT_SELL_FILL",
                  event=ctx.event.name,
                  order_id=ctx.order_id)
    except Exception as e:
        logger.debug(f"Failed to log wait_for_sell: {e}")


def action_close_position(ctx: EventContext, coin_state: CoinState) -> None:
    """Transition: WAIT_SELL_FILL → POST_TRADE"""
    # CRITICAL FIX (C-FSM-04): Idempotency guard - prevent double application
    if coin_state.amount == 0.0 and coin_state.entry_price == 0.0 and coin_state.entry_ts == 0.0:
        logger.debug(f"action_close_position already applied for {ctx.symbol}, skipping")
        return

    realized_pnl = ((ctx.avg_price or 0.0) - coin_state.entry_price) * (ctx.filled_qty or 0.0)

    coin_state.note = f"position closed: PnL={realized_pnl:.4f}"

    try:
        from core.logger_factory import DECISION_LOG, log_event
        log_event(DECISION_LOG(), "position_closed",
                  symbol=ctx.symbol,
                  qty_closed=ctx.filled_qty,
                  exit_price=ctx.avg_price,
                  realized_pnl_usdt=realized_pnl,
                  reason=coin_state.exit_reason)

        log_event(DECISION_LOG(), "fsm_transition",
                  symbol=ctx.symbol,
                  from_phase="WAIT_SELL_FILL",
                  to_phase="POST_TRADE",
                  event=ctx.event.name)
    except Exception as e:
        logger.debug(f"Failed to log close_position: {e}")

    # Clear position data
    coin_state.amount = 0.0
    coin_state.entry_price = 0.0
    coin_state.entry_ts = 0.0


def action_handle_partial_sell(ctx: EventContext, coin_state: CoinState) -> None:
    """Transition: WAIT_SELL_FILL → WAIT_SELL_FILL (partial)"""
    coin_state.note = f"partial sell: {ctx.filled_qty:.6f}"

    if hasattr(coin_state, 'fsm_data') and coin_state.fsm_data and coin_state.fsm_data.sell_order:
        order_ctx = coin_state.fsm_data.sell_order
        order_ctx.cumulative_qty = (order_ctx.cumulative_qty or 0.0) + (ctx.filled_qty or 0.0)

    try:
        from core.logger_factory import DECISION_LOG, log_event
        log_event(DECISION_LOG(), "order_partial_fill",
                  symbol=ctx.symbol,
                  filled_qty=ctx.filled_qty,
                  order_id=ctx.order_id)
    except Exception as e:
        logger.debug(f"Failed to log partial_sell: {e}")


def action_retry_sell(ctx: EventContext, coin_state: CoinState) -> None:
    """Transition: PLACE_SELL/WAIT_SELL_FILL → POSITION (retry)"""
    coin_state.retry_count += 1
    coin_state.note = f"sell failed - retry {coin_state.retry_count}"

    if hasattr(coin_state, 'fsm_data') and coin_state.fsm_data and coin_state.fsm_data.sell_order:
        coin_state.fsm_data.sell_order.retry_count = coin_state.retry_count

    try:
        from core.logger_factory import DECISION_LOG, log_event
        log_event(DECISION_LOG(), "fsm_transition",
                  symbol=ctx.symbol,
                  to_phase="POSITION",
                  event=ctx.event.name,
                  reason="sell_failed_retry",
                  retry_count=coin_state.retry_count)
    except Exception as e:
        logger.debug(f"Failed to log retry_sell: {e}")


def action_start_cooldown(ctx: EventContext, coin_state: CoinState) -> None:
    """Transition: POST_TRADE → COOLDOWN"""
    import config
    cooldown_secs = getattr(config, 'COOLDOWN_SECS', 60)
    coin_state.cooldown_until = ctx.timestamp + cooldown_secs
    coin_state.note = f"cooldown for {cooldown_secs}s"

    if hasattr(coin_state, 'fsm_data') and coin_state.fsm_data:
        coin_state.fsm_data.cooldown_started_at = ctx.timestamp

    try:
        from core.logger_factory import DECISION_LOG, log_event
        log_event(DECISION_LOG(), "fsm_transition",
                  symbol=ctx.symbol,
                  from_phase="POST_TRADE",
                  to_phase="COOLDOWN",
                  event=ctx.event.name,
                  cooldown_secs=cooldown_secs)
    except Exception as e:
        logger.debug(f"Failed to log start_cooldown: {e}")


def action_check_cooldown(ctx: EventContext, coin_state: CoinState) -> None:
    """Transition: COOLDOWN → COOLDOWN (waiting)"""
    # Still in cooldown - no action needed
    pass


def action_reset_to_idle(ctx: EventContext, coin_state: CoinState) -> None:
    """Transition: COOLDOWN → IDLE"""
    # Reset state
    coin_state.order_id = None
    coin_state.order_placed_ts = 0.0
    coin_state.amount = 0.0
    coin_state.entry_price = 0.0
    coin_state.entry_ts = 0.0
    coin_state.cooldown_until = 0.0
    coin_state.retry_count = 0
    coin_state.exit_reason = ""
    coin_state.note = "cooldown expired - ready"

    if hasattr(coin_state, 'fsm_data') and coin_state.fsm_data:
        # Clear FSM data
        coin_state.fsm_data.buy_order = None
        coin_state.fsm_data.sell_order = None
        coin_state.fsm_data.exit_signal = None

    try:
        from core.logger_factory import DECISION_LOG, log_event
        log_event(DECISION_LOG(), "fsm_transition",
                  symbol=ctx.symbol,
                  from_phase="COOLDOWN",
                  to_phase="IDLE",
                  event=ctx.event.name,
                  reason="cooldown_expired")
    except Exception as e:
        logger.debug(f"Failed to log reset_to_idle: {e}")


def action_log_error(ctx: EventContext, coin_state: CoinState) -> None:
    """Transition: * → ERROR"""
    coin_state.error_count += 1
    coin_state.last_error = str(ctx.error) if ctx.error else "unknown error"
    coin_state.note = f"ERROR: {coin_state.last_error[:50]}"

    if hasattr(coin_state, 'fsm_data') and coin_state.fsm_data:
        coin_state.fsm_data.error_count = coin_state.error_count
        coin_state.fsm_data.last_error = coin_state.last_error
        coin_state.fsm_data.last_error_at = ctx.timestamp

    try:
        from core.logger_factory import DECISION_LOG, log_event
        log_event(DECISION_LOG(), "fsm_transition",
                  symbol=ctx.symbol,
                  to_phase="ERROR",
                  event=ctx.event.name,
                  error=coin_state.last_error)
    except Exception as e:
        logger.debug(f"Failed to log error: {e}")


def action_safe_halt(ctx: EventContext, coin_state: CoinState) -> None:
    """Transition: ERROR → ERROR (halted)"""
    coin_state.note = "HALTED - manual intervention required"

    try:
        from core.logger_factory import DECISION_LOG, log_event
        log_event(DECISION_LOG(), "fsm_halted",
                  symbol=ctx.symbol,
                  reason="manual_halt")
    except Exception as e:
        logger.debug(f"Failed to log safe_halt: {e}")
