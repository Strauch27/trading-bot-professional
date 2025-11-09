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

    # CRITICAL FIX (Punkt 2): Preflight validation before placing order
    # This ensures FSM parity with Legacy buy flow for min_notional auto-bump
    try:
        from services.exchange_filters import get_filters
        from services.order_validation import preflight
        from services.quantize import q_price, q_amount

        # Get exchange instance from context or config
        exchange = ctx.data.get('exchange')
        if not exchange:
            # Try to get exchange from imported module
            try:
                from adapters import get_exchange
                exchange = get_exchange()
            except Exception:
                logger.warning(f"No exchange available for preflight check on {ctx.symbol}")
                exchange = None

        if exchange:
            # 1. Load exchange filters
            filters = get_filters(exchange, ctx.symbol)

            # 2. Get raw price/amount from context
            raw_price = ctx.data.get('order_price')
            raw_amount = ctx.data.get('order_qty')

            if raw_price and raw_amount:
                # 3. Run preflight (quantize + min_notional auto-bump)
                ok, result = preflight(ctx.symbol, raw_price, raw_amount, filters)

                if not ok:
                    # CRITICAL FIX (P1 Issue #3): Abort transition properly
                    # Use FSMTransitionAbort to return to IDLE with cooldown
                    from core.fsm.exceptions import FSMTransitionAbort

                    reason = result.get('reason', 'preflight_failed')
                    logger.warning(
                        f"[PREFLIGHT_FAILED] {ctx.symbol}: {reason} "
                        f"(need={result.get('need')}, got={result.get('got')})"
                    )

                    # Abort transition with cooldown
                    raise FSMTransitionAbort(
                        reason=f"preflight:{reason}",
                        should_cooldown=True
                    )

                # 4. Update context with quantized/auto-bumped values
                ctx.data['order_price'] = result['price']
                ctx.data['order_qty'] = result['amount']

                logger.info(
                    f"[PREFLIGHT_OK] {ctx.symbol}: "
                    f"price={result['price']:.8f}, qty={result['amount']:.6f}, "
                    f"notional={result['price'] * result['amount']:.2f}"
                )
        else:
            logger.debug(f"Skipping preflight for {ctx.symbol} (no exchange available)")

    except Exception as preflight_error:
        logger.error(f"Preflight check failed for {ctx.symbol}: {preflight_error}", exc_info=True)
        # Don't block on preflight errors - continue with original values

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

    # CRITICAL FIX (Punkt 3): Set cooldown to prevent IDLE->ENTRY_EVAL->IDLE loop
    # Use config value instead of hardcoded 30s
    try:
        import config
        cooldown_secs = getattr(config, 'ENTRY_BLOCK_COOLDOWN_S', 30)
    except Exception:
        cooldown_secs = 30  # Fallback to 30s default

    coin_state.cooldown_until = time.time() + cooldown_secs

    logger.info(
        f"[GUARD_BLOCK] {ctx.symbol}: Cooldown set for {cooldown_secs}s "
        f"(reason: {ctx.data.get('block_reason', 'unknown')})"
    )

    try:
        from core.logger_factory import DECISION_LOG, log_event
        log_event(DECISION_LOG(), "fsm_transition",
                  symbol=ctx.symbol,
                  from_phase="ENTRY_EVAL",
                  to_phase="IDLE",
                  event=ctx.event.name,
                  block_reason=ctx.data.get('block_reason'),
                  cooldown_secs=cooldown_secs)
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
    """
    Transition: WAIT_FILL → WAIT_FILL (partial fill)

    CRITICAL FIX (Punkt 4): Open/update position on PARTIAL fills with weighted average
    """
    filled_qty = ctx.filled_qty or 0.0
    avg_price = ctx.avg_price or 0.0

    coin_state.note = f"partial fill: {filled_qty:.6f} @ {avg_price:.4f}"

    # Update FSM data
    if hasattr(coin_state, 'fsm_data') and coin_state.fsm_data and coin_state.fsm_data.buy_order:
        order_ctx = coin_state.fsm_data.buy_order
        order_ctx.cumulative_qty = (order_ctx.cumulative_qty or 0.0) + filled_qty

    # CRITICAL FIX: Open or update position with weighted average
    if filled_qty > 0 and avg_price > 0:
        if coin_state.amount == 0.0:
            # First partial fill - open position
            coin_state.amount = filled_qty
            coin_state.entry_price = avg_price
            coin_state.entry_ts = ctx.timestamp

            logger.info(
                f"[PARTIAL_OPEN] {ctx.symbol}: Position opened with PARTIAL fill "
                f"{filled_qty:.6f} @ {avg_price:.4f}"
            )

            # Initialize TP/SL prices after first fill
            try:
                tp_pct = getattr(config, 'TP_PCT', 3.0)
                sl_pct = getattr(config, 'SL_PCT', 5.0)
                price_tick = ctx.data.get("price_tick", 0.00000001)
                decimals = int(abs(math.log10(price_tick)))

                coin_state.tp_px = round(avg_price * (1 + tp_pct / 100), decimals)
                coin_state.sl_px = round(avg_price * (1 - sl_pct / 100), decimals)
                coin_state.tp_active = True
                coin_state.sl_active = True
                coin_state.peak_price = avg_price
                coin_state.trailing_trigger = 0.0

                logger.info(
                    f"{ctx.symbol}: TP/SL initialized - "
                    f"TP: {coin_state.tp_px:.8f} (+{tp_pct}%), "
                    f"SL: {coin_state.sl_px:.8f} (-{sl_pct}%)"
                )
            except Exception as tp_sl_error:
                logger.warning(f"Failed to initialize TP/SL for {ctx.symbol}: {tp_sl_error}")

        else:
            # Subsequent partial fill - update with weighted average
            new_total_qty = coin_state.amount + filled_qty
            weighted_avg = (
                (coin_state.entry_price * coin_state.amount + avg_price * filled_qty)
                / max(new_total_qty, 1e-12)
            )

            logger.info(
                f"[PARTIAL_UPDATE] {ctx.symbol}: Position updated "
                f"{coin_state.amount:.6f} @ {coin_state.entry_price:.4f} + "
                f"{filled_qty:.6f} @ {avg_price:.4f} = "
                f"{new_total_qty:.6f} @ {weighted_avg:.4f}"
            )

            coin_state.amount = new_total_qty
            coin_state.entry_price = weighted_avg

        # Update FSM data position tracking
        if hasattr(coin_state, 'fsm_data') and coin_state.fsm_data:
            coin_state.fsm_data.position_qty = coin_state.amount
            coin_state.fsm_data.position_entry_price = coin_state.entry_price

    try:
        from core.logger_factory import DECISION_LOG, log_event
        log_event(DECISION_LOG(), "order_partial_fill",
                  symbol=ctx.symbol,
                  filled_qty=filled_qty,
                  avg_price=avg_price,
                  cumulative_qty=coin_state.amount,
                  weighted_avg_entry=coin_state.entry_price,
                  order_id=ctx.order_id)
    except Exception as e:
        logger.debug(f"Failed to log partial_buy: {e}")


def action_cancel_and_cleanup(ctx: EventContext, coin_state: CoinState) -> None:
    """Transition: WAIT_FILL → IDLE (timeout)"""
    coin_state.note = "order timeout - cleaned up"
    # CRITICAL FIX: Clear order IDs on timeout (final state for this path)
    coin_state.order_id = None
    coin_state.client_order_id = None
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
    """Transition: WAIT_FILL → IDLE (canceled) - legacy name for compatibility."""
    coin_state.note = "order canceled"
    # CRITICAL FIX: Clear order IDs on cancellation (final state for this path)
    coin_state.order_id = None
    coin_state.client_order_id = None
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
    """
    Transition: EXIT_EVAL → PLACE_SELL

    CRITICAL FIX (P1 Issue #8): Validate exit price against slippage limits
    Prevents disaster sells during flash crashes
    """
    coin_state.exit_reason = ctx.data.get('exit_signal', 'unknown')
    coin_state.note = f"preparing sell: {coin_state.exit_reason}"

    # CRITICAL FIX (P1 Issue #8): Slippage pre-check before sell
    # Prevent selling at disaster prices (flash crash protection)
    current_price = ctx.data.get('current_price', 0.0)
    entry_price = coin_state.entry_price

    if current_price > 0 and entry_price > 0:
        try:
            import config

            # Calculate slippage vs entry
            price_change_pct = ((current_price - entry_price) / entry_price) * 100
            slippage_bps = abs(price_change_pct) * 100

            max_slippage_bps = getattr(config, 'MAX_SLIPPAGE_BPS_EXIT', 500)  # Default 5%

            # Check if slippage is too extreme (potential flash crash)
            if slippage_bps > max_slippage_bps:
                # Price too far from entry - may be flash crash or data error
                logger.warning(
                    f"[SLIPPAGE_BREACH] {ctx.symbol}: Exit slippage too high: "
                    f"{slippage_bps:.1f} bps > {max_slippage_bps} bps "
                    f"(current: {current_price:.8f}, entry: {entry_price:.8f}, "
                    f"change: {price_change_pct:+.2f}%)"
                )

                # Option 1: Block sell if it's a massive loss (flash crash)
                # Only block if it's a significant downward move
                if price_change_pct < -10.0:  # More than -10% loss
                    from core.fsm.exceptions import FSMTransitionAbort

                    logger.error(
                        f"[FLASH_CRASH_PROTECTION] {ctx.symbol}: Blocking sell - "
                        f"price dropped {price_change_pct:.2f}% from entry. "
                        f"Waiting for recovery or manual intervention."
                    )

                    # Abort transition - return to POSITION and wait
                    raise FSMTransitionAbort(
                        reason=f"flash_crash_protection:slippage_{slippage_bps:.0f}bps",
                        should_cooldown=False  # No cooldown - allow re-check on next cycle
                    )

                # Option 2: Log warning but proceed (for take-profit scenarios)
                logger.warning(
                    f"[SLIPPAGE_WARNING] {ctx.symbol}: Proceeding with sell despite high slippage "
                    f"(change: {price_change_pct:+.2f}%)"
                )

        except Exception as slippage_check_error:
            logger.warning(
                f"Slippage check failed for {ctx.symbol}: {slippage_check_error}"
            )
            # Don't block sell on check errors

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
                  exit_signal=coin_state.exit_reason,
                  current_price=current_price,
                  entry_price=entry_price,
                  price_change_pct=((current_price - entry_price) / entry_price) * 100 if entry_price > 0 else 0)
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

    # CRITICAL FIX: Store sell order data BEFORE clearing position
    # This ensures POST_TRADE has fallback data if order is archived
    if hasattr(coin_state, 'fsm_data') and coin_state.fsm_data:
        if not coin_state.fsm_data.sell_order:
            coin_state.fsm_data.sell_order = EventContext(
                timestamp=ctx.timestamp,
                symbol=ctx.symbol,
                event=ctx.event
            )
        coin_state.fsm_data.sell_order.cumulative_qty = ctx.filled_qty or 0.0
        coin_state.fsm_data.sell_order.avg_price = ctx.avg_price or 0.0
        coin_state.fsm_data.sell_order.total_fees = ctx.fee or 0.0

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
    # CRITICAL FIX: Use COOLDOWN_MIN (in minutes) instead of COOLDOWN_SECS
    # This was causing cooldown to be only 60 seconds instead of 15 minutes (900 seconds)
    cooldown_minutes = getattr(config, 'COOLDOWN_MIN', 15)
    cooldown_secs = cooldown_minutes * 60
    coin_state.cooldown_until = ctx.timestamp + cooldown_secs
    coin_state.note = f"cooldown for {cooldown_secs}s ({cooldown_minutes}min)"

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
    # CRITICAL FIX: Only clear order IDs after COOLDOWN (final state)
    # This preserves order_id/client_order_id through WAIT_SELL_FILL → POST_TRADE → COOLDOWN
    # for proper logging and reconciliation
    coin_state.order_id = None
    coin_state.client_order_id = None
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


# Alias for American spelling (consistency with ORDER_CANCELED event)
action_cleanup_canceled = action_cleanup_cancelled
