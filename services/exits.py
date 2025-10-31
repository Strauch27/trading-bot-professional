"""
Exit Management Service (Drop 4)
Centralized exit logic extraction from engine.py
"""

import logging
import time
from dataclasses import dataclass
from threading import RLock
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)


@dataclass
class ExitSignal:
    """Represents an exit signal for a position"""
    symbol: str
    reason: str
    priority: int
    timestamp: float
    data: Dict[str, Any]

    def __lt__(self, other):
        """For priority queue ordering (lower priority number = higher priority)"""
        return self.priority < other.priority


@dataclass
class ExitContext:
    """Context information for exit decisions"""
    symbol: str
    current_price: float
    amount: float
    buying_price: float
    elapsed_minutes: float
    position_data: Dict[str, Any]
    decision_id: Optional[str] = None


@dataclass
class ExitResult:
    """Result of an exit operation"""
    success: bool
    order_id: Optional[str] = None
    filled_amount: float = 0.0
    avg_price: float = 0.0
    reason: str = ""
    error: Optional[str] = None


class ExitEvaluator:
    """Evaluates exit conditions for positions"""

    def __init__(self, trade_ttl_min: int = 60, exit_escalation_bps: List[int] = None,
                 use_atr_exits: bool = False, atr_sl_multiplier: float = 2.0,
                 atr_tp_multiplier: float = 3.0, skip_under_min: bool = False):
        self.trade_ttl_min = trade_ttl_min
        self.exit_escalation_bps = exit_escalation_bps or [50, 100, 200, 500]
        self.use_atr_exits = use_atr_exits
        self.atr_sl_multiplier = atr_sl_multiplier
        self.atr_tp_multiplier = atr_tp_multiplier
        self.skip_under_min = skip_under_min
        self._lock = RLock()

    def evaluate_exit_signals(self, context: ExitContext) -> List[str]:
        """Evaluate all exit conditions and return list of reasons"""
        with self._lock:
            reasons = []

            # Phase 3: Log comprehensive exit evaluation
            try:
                from core.event_schemas import SellTriggerEval
                from core.logger_factory import DECISION_LOG, log_event
                from core.trace_context import Trace, trace_step

                unrealized_pct = ((context.current_price / context.buying_price) - 1.0) * 100 if context.buying_price > 0 else 0
                hold_time_s = context.elapsed_minutes * 60

                # Log exit evaluation start
                trace_step("exit_evaluation_start", symbol=context.symbol,
                          entry_price=context.buying_price,
                          current_price=context.current_price,
                          pnl_pct=unrealized_pct,
                          hold_time_min=context.elapsed_minutes)

                # Comprehensive exit evaluation log
                log_event(
                    DECISION_LOG(),
                    "exit_evaluation",
                    symbol=context.symbol,
                    side="sell",
                    entry_price=context.buying_price,
                    current_price=context.current_price,
                    position_qty=context.amount,
                    pnl_pct=round(unrealized_pct, 4),
                    hold_time_s=round(hold_time_s, 2),
                    hold_time_min=round(context.elapsed_minutes, 2),
                    ttl_threshold_min=self.trade_ttl_min
                )

                # Emergency Sell (TTL Timeout)
                ttl_hit = context.elapsed_minutes >= self.trade_ttl_min
                if ttl_hit:
                    reasons.append("TTL_EMERGENCY")

                    # Log TTL trigger
                    logger.info(
                        f"EXIT TRIGGERED: {context.symbol} | Rule=TTL_EMERGENCY | "
                        f"Entry={context.buying_price:.8f} | Current={context.current_price:.8f} | "
                        f"PnL={unrealized_pct:+.2f}% | Hold={context.elapsed_minutes:.1f}min",
                        extra={
                            'event_type': 'EXIT_TRIGGERED',
                            'symbol': context.symbol,
                            'rule_code': 'TTL_EMERGENCY',
                            'reason': 'max_hold_time'
                        }
                    )

                # Log TTL trigger evaluation
                ttl_eval = SellTriggerEval(
                    symbol=context.symbol,
                    trigger="ttl",
                    entry_price=context.buying_price,
                    current_price=context.current_price,
                    unrealized_pct=unrealized_pct,
                    threshold=self.trade_ttl_min,
                    hit=ttl_hit,
                    reason=f"elapsed={context.elapsed_minutes:.1f}min >= ttl={self.trade_ttl_min}min" if ttl_hit else None
                )

                with Trace(decision_id=context.decision_id) if context.decision_id else Trace():
                    log_event(DECISION_LOG(), "sell_trigger_eval", **ttl_eval.model_dump())

                # Log if no exit triggered
                if not reasons:
                    trace_step("exit_evaluation_no_trigger", symbol=context.symbol,
                              pnl_pct=unrealized_pct, hold_time_min=context.elapsed_minutes)

                    log_event(
                        DECISION_LOG(),
                        "exit_evaluation_no_trigger",
                        symbol=context.symbol,
                        entry_price=context.buying_price,
                        current_price=context.current_price,
                        pnl_pct=round(unrealized_pct, 4),
                        hold_time_min=round(context.elapsed_minutes, 2)
                    )

            except Exception as e:
                # Don't fail evaluation if logging fails
                logger.debug(f"Failed to log sell_trigger_eval for {context.symbol}: {e}")

            # Price-based exits would be evaluated here
            # This is a simplified version - full implementation would include
            # all the complex exit logic from the original engine

            return reasons

    def get_exit_priority(self, reason: str) -> int:
        """Get priority for exit reason (lower = higher priority)"""
        priority_map = {
            "PANIC_SELL": 1,
            "TTL_EMERGENCY": 2,
            "STOP_LOSS": 3,
            "TAKE_PROFIT": 4,
            "TRAILING_STOP": 5,
            "ATR_EXIT": 6,
            "MANUAL_EXIT": 10
        }
        return priority_map.get(reason, 99)


class ExitOrderManager:
    """Manages exit order placement and execution"""

    def __init__(self, exchange_adapter, order_service, never_market_sells: bool = False,
                 max_slippage_bps: int = 50, exit_ioc_ttl_ms: int = 3000,
                 skip_under_min: bool = False):
        self.exchange_adapter = exchange_adapter
        self.order_service = order_service
        self.never_market_sells = never_market_sells
        self.max_slippage_bps = max_slippage_bps
        self.exit_ioc_ttl_ms = exit_ioc_ttl_ms
        self.skip_under_min = skip_under_min
        self._lock = RLock()

        # FIX H1: Exit intent deduplication
        self.pending_exit_intents = {}  # symbol -> exit_metadata
        self._exit_intents_lock = RLock()

        self._statistics = {
            'exits_placed': 0,
            'exits_filled': 0,
            'exits_failed': 0,
            'market_fallbacks': 0,
            'ioc_fallbacks': 0
        }

    def execute_exit_order(self, context: ExitContext, reason: str) -> ExitResult:
        """Execute exit order with escalation strategy"""
        with self._lock:
            # FIX H1: Check if exit already in progress for this symbol
            with self._exit_intents_lock:
                if context.symbol in self.pending_exit_intents:
                    existing_intent = self.pending_exit_intents[context.symbol]
                    logger.warning(
                        f"EXIT DEDUPLICATION: Exit already in progress for {context.symbol} | "
                        f"Existing reason={existing_intent['reason']}, age={time.time() - existing_intent['start_ts']:.1f}s | "
                        f"Skipping new exit with reason={reason}",
                        extra={
                            'event_type': 'EXIT_DEDUPLICATED',
                            'symbol': context.symbol,
                            'existing_reason': existing_intent['reason'],
                            'new_reason': reason,
                            'intent_age_s': time.time() - existing_intent['start_ts']
                        }
                    )
                    return ExitResult(
                        success=False,
                        reason="exit_already_in_progress",
                        error=f"Exit already in progress (reason: {existing_intent['reason']})"
                    )

                # Register exit intent
                self.pending_exit_intents[context.symbol] = {
                    'reason': reason,
                    'start_ts': time.time(),
                    'amount': context.amount,
                    'price': context.current_price
                }
                logger.debug(
                    f"EXIT INTENT REGISTERED: {context.symbol} | Reason={reason}",
                    extra={'event_type': 'EXIT_INTENT_REGISTERED', 'symbol': context.symbol}
                )

            idempotency_store = None
            try:
                self._statistics['exits_placed'] += 1

                # Log exit order execution start with full context
                entry_price = context.buying_price
                pnl_pct = ((context.current_price - entry_price) / entry_price * 100) if entry_price > 0 else 0
                logger.info(
                    f"EXIT ORDER START: {context.symbol} | Reason={reason} | "
                    f"Entry={entry_price:.8f} | Current={context.current_price:.8f} | "
                    f"Qty={context.amount:.8f} | PnL={pnl_pct:+.2f}%",
                    extra={
                        'event_type': 'EXIT_ORDER_START',
                        'symbol': context.symbol,
                        'reason': reason,
                        'decision_id': context.decision_id
                    }
                )

                # Phase 2: Generate order_req_id for idempotency tracking
                from core.trace_context import new_order_req_id
                order_req_id = new_order_req_id()

                # Phase 2: Idempotency Check - Prevent duplicate sell orders
                from core.idempotency import get_idempotency_store

                try:
                    idempotency_store = get_idempotency_store()
                    existing_order_id = idempotency_store.register_order(
                        order_req_id=order_req_id,
                        symbol=context.symbol,
                        side="sell",
                        amount=context.amount,
                        price=context.current_price,
                        client_order_id=f"exit_{reason}_{int(time.time())}"
                    )

                    if existing_order_id:
                        # Duplicate detected - fetch and return existing order
                        logger.warning(
                            f"Duplicate sell order detected for {context.symbol} ({reason}), "
                            f"returning existing order {existing_order_id}"
                        )

                        try:
                            from core.trace_context import trace_step
                            existing_order = self.exchange_adapter.fetch_order(existing_order_id, context.symbol)

                            trace_step("duplicate_sell_order_returned", symbol=context.symbol,
                                     order_id=existing_order_id, status=existing_order.get('status'))

                            # Convert to ExitResult
                            return ExitResult(
                                success=existing_order.get('status') == 'closed',
                                order_id=existing_order_id,
                                filled_amount=existing_order.get('filled', 0),
                                avg_price=existing_order.get('average', 0),
                                reason=reason
                            )
                        except Exception as fetch_error:
                            logger.error(f"Failed to fetch duplicate sell order {existing_order_id}: {fetch_error}")
                            # Continue with new order placement if fetch fails

                except Exception as idempotency_error:
                    # Don't fail order placement if idempotency check fails
                    logger.error(f"Idempotency check failed for sell order {context.symbol}: {idempotency_error}")

                # Phase 2: Client Order ID Deduplication - Check exchange for existing order
                try:
                    from trading.orders import verify_order_not_duplicate

                    client_order_id = f"exit_{reason}_{int(time.time())}"
                    existing_exchange_order = verify_order_not_duplicate(
                        exchange=self.exchange_adapter._exchange,
                        client_order_id=client_order_id,
                        symbol=context.symbol
                    )

                    if existing_exchange_order:
                        # Duplicate found on exchange
                        logger.warning(
                            f"Duplicate sell order detected on exchange for {context.symbol} ({reason}) "
                            f"(client_order_id: {client_order_id}, exchange_order_id: {existing_exchange_order.get('id')})"
                        )

                        # Convert to ExitResult
                        return ExitResult(
                            success=existing_exchange_order.get('status') == 'closed',
                            order_id=existing_exchange_order.get('id'),
                            filled_amount=existing_exchange_order.get('filled', 0),
                            avg_price=existing_exchange_order.get('average', 0),
                            reason=reason
                        )

                except Exception as dedup_error:
                    # Don't fail order placement if deduplication check fails
                    logger.debug(f"Client Order ID deduplication check failed for sell order {context.symbol}: {dedup_error}")

                # Phase 3: Log sell sizing calculation
                try:
                    from core.event_schemas import SellSizingCalc
                    from core.logger_factory import DECISION_LOG, log_event
                    from core.trace_context import Trace

                    # Get exchange limits
                    min_qty = self._get_min_amount(context.symbol)
                    min_notional = self._get_min_notional(context.symbol)
                    qty_rounded = context.amount
                    notional = qty_rounded * context.current_price

                    # Validate sizing
                    passed = qty_rounded >= min_qty and notional >= min_notional
                    fail_reason = None
                    if not passed:
                        if qty_rounded < min_qty:
                            fail_reason = f"qty {qty_rounded} < min_qty {min_qty}"
                        elif notional < min_notional:
                            fail_reason = f"notional {notional} < min_notional {min_notional}"

                    sizing_calc = SellSizingCalc(
                        symbol=context.symbol,
                        pos_qty_avail=context.amount,
                        qty_raw=context.amount,
                        qty_rounded=qty_rounded,
                        min_qty=min_qty,
                        min_notional=min_notional,
                        notional=notional,
                        passed=passed,
                        fail_reason=fail_reason
                    )

                    with Trace(decision_id=context.decision_id) if context.decision_id else Trace():
                        log_event(DECISION_LOG(), "sell_sizing_calc", **sizing_calc.model_dump())

                except Exception as e:
                    logger.debug(f"Failed to log sell_sizing_calc for {context.symbol}: {e}")

                # Validate minimum amounts
                if self.skip_under_min and context.amount < self._get_min_amount(context.symbol):
                    return ExitResult(
                        success=False,
                        reason=reason,
                        error="Amount below minimum threshold"
                    )

                # Try limit IOC first
                result = self._try_limit_ioc_exit(context, reason)
                if result.success:
                    self._statistics['exits_filled'] += 1

                    # Log successful exit with comprehensive details
                    entry_price = context.buying_price
                    realized_pnl_pct = ((result.avg_price - entry_price) / entry_price * 100) if entry_price > 0 else 0
                    logger.info(
                        f"EXIT ORDER SUCCESS (IOC): {context.symbol} | Reason={reason} | "
                        f"Entry={entry_price:.8f} | Exit={result.avg_price:.8f} | "
                        f"Filled={result.filled_amount:.8f} | PnL={realized_pnl_pct:+.2f}% | "
                        f"Order={result.order_id}",
                        extra={
                            'event_type': 'EXIT_ORDER_SUCCESS',
                            'symbol': context.symbol,
                            'reason': reason,
                            'strategy': 'limit_ioc',
                            'order_id': result.order_id,
                            'decision_id': context.decision_id
                        }
                    )

                    # Phase 2: Update idempotency store with exchange order ID
                    if result.order_id and idempotency_store:
                        try:
                            idempotency_store.update_order_status(
                                order_req_id=order_req_id,
                                exchange_order_id=result.order_id,
                                status='closed'
                            )
                        except Exception as update_error:
                            logger.debug(f"Failed to update idempotency store for sell order: {update_error}")

                    return result

                # Escalation strategy if limit IOC fails
                logger.warning(
                    f"EXIT IOC FAILED: {context.symbol} | Escalating to market order",
                    extra={'event_type': 'EXIT_IOC_FAILED', 'symbol': context.symbol}
                )

                if not self.never_market_sells:
                    result = self._try_market_exit(context, reason)
                    if result.success:
                        self._statistics['market_fallbacks'] += 1
                        self._statistics['exits_filled'] += 1

                        # Log successful market exit
                        entry_price = context.buying_price
                        realized_pnl_pct = ((result.avg_price - entry_price) / entry_price * 100) if entry_price > 0 else 0
                        logger.info(
                            f"EXIT ORDER SUCCESS (MARKET): {context.symbol} | Reason={reason} | "
                            f"Entry={entry_price:.8f} | Exit={result.avg_price:.8f} | "
                            f"Filled={result.filled_amount:.8f} | PnL={realized_pnl_pct:+.2f}% | "
                            f"Order={result.order_id}",
                            extra={
                                'event_type': 'EXIT_ORDER_SUCCESS',
                                'symbol': context.symbol,
                                'reason': reason,
                                'strategy': 'market',
                                'order_id': result.order_id,
                                'decision_id': context.decision_id
                            }
                        )

                        # Phase 2: Update idempotency store with exchange order ID
                        if result.order_id and idempotency_store:
                            try:
                                idempotency_store.update_order_status(
                                    order_req_id=order_req_id,
                                    exchange_order_id=result.order_id,
                                    status='closed'
                                )
                            except Exception as update_error:
                                logger.debug(f"Failed to update idempotency store for sell order: {update_error}")

                        return result
                else:
                    # Use IOC fallback instead of market
                    result = self._try_ioc_fallback(context, reason)
                    if result.success:
                        self._statistics['ioc_fallbacks'] += 1
                        self._statistics['exits_filled'] += 1

                        # Phase 2: Update idempotency store with exchange order ID
                        if result.order_id and idempotency_store:
                            try:
                                idempotency_store.update_order_status(
                                    order_req_id=order_req_id,
                                    exchange_order_id=result.order_id,
                                    status='closed'
                                )
                            except Exception as update_error:
                                logger.debug(f"Failed to update idempotency store for sell order: {update_error}")

                        return result

                self._statistics['exits_failed'] += 1

                # Log final exit failure with all attempted strategies
                logger.error(
                    f"EXIT ORDER FAILED: {context.symbol} | Reason={reason} | "
                    f"All strategies exhausted (IOC, Market/IOC Fallback)",
                    extra={
                        'event_type': 'EXIT_ORDER_FAILED',
                        'symbol': context.symbol,
                        'reason': reason,
                        'decision_id': context.decision_id
                    }
                )

                # Phase 2: Update idempotency store with failure status
                if idempotency_store:
                    try:
                        idempotency_store.update_order_status(
                            order_req_id=order_req_id,
                            exchange_order_id="failed",
                            status='failed'
                        )
                    except Exception as update_error:
                        logger.debug(f"Failed to update idempotency store for failed sell order: {update_error}")

                return ExitResult(
                    success=False,
                    reason=reason,
                    error="All exit strategies failed"
                )

            except Exception as e:
                self._statistics['exits_failed'] += 1

                # Log exception with full context
                logger.error(
                    f"EXIT EXECUTION EXCEPTION: {context.symbol} | Reason={reason} | Error={str(e)}",
                    extra={
                        'event_type': 'EXIT_EXECUTION_ERROR',
                        'symbol': context.symbol,
                        'reason': reason,
                        'error': str(e),
                        'decision_id': context.decision_id
                    },
                    exc_info=True
                )

                return ExitResult(
                    success=False,
                    reason=reason,
                    error=str(e)
                )

            finally:
                # FIX H1: Always clear exit intent when done (success or failure)
                with self._exit_intents_lock:
                    if context.symbol in self.pending_exit_intents:
                        duration = time.time() - self.pending_exit_intents[context.symbol]['start_ts']
                        self.pending_exit_intents.pop(context.symbol, None)
                        logger.debug(
                            f"EXIT INTENT CLEARED: {context.symbol} | Duration={duration:.2f}s",
                            extra={'event_type': 'EXIT_INTENT_CLEARED', 'symbol': context.symbol, 'duration_s': duration}
                        )

    def _try_limit_ioc_exit(self, context: ExitContext, reason: str) -> ExitResult:
        """Try limit IOC exit order with aggressive BID-based pricing and liquidity check"""
        try:
            # Phase 1: Check for low liquidity conditions
            # This prevents orders from failing with 'Oversold' errors
            ticker = None
            try:
                ticker = self.exchange_adapter.fetch_ticker(context.symbol)

                # Check if bid/ask spread is too wide (indicates low liquidity)
                if ticker and 'bid' in ticker and 'ask' in ticker:
                    bid = ticker.get('bid', 0)
                    ask = ticker.get('ask', 0)

                    if bid > 0 and ask > 0:
                        spread_pct = ((ask - bid) / bid) * 100

                        # Warn if spread is excessive (>5%)
                        if spread_pct > 5.0:
                            logger.warning(
                                f"Low liquidity detected for {context.symbol}: "
                                f"Spread={spread_pct:.2f}%, Bid={bid:.8f}, Ask={ask:.8f}",
                                extra={'event_type': 'LOW_LIQUIDITY_WARNING', 'symbol': context.symbol}
                            )

                        # FIX ACTION 1.2: Block exit if spread is too wide
                        import config as cfg
                        max_spread = getattr(cfg, 'EXIT_MIN_LIQUIDITY_SPREAD_PCT', 10.0)

                        if spread_pct > max_spread:
                            logger.error(
                                f"EXIT BLOCKED - Low Liquidity: {context.symbol} | "
                                f"Spread={spread_pct:.2f}% > {max_spread}% | "
                                f"Bid={bid:.8f}, Ask={ask:.8f}",
                                extra={
                                    'event_type': 'EXIT_BLOCKED_LOW_LIQUIDITY',
                                    'symbol': context.symbol,
                                    'spread_pct': spread_pct,
                                    'threshold': max_spread
                                }
                            )

                            # Handle based on configured action
                            action = getattr(cfg, 'EXIT_LOW_LIQUIDITY_ACTION', 'skip')

                            if action == "skip":
                                return ExitResult(
                                    success=False,
                                    reason="low_liquidity",
                                    error=f"Spread too wide: {spread_pct:.2f}% > {max_spread}%"
                                )
                            elif action == "wait":
                                # Return error indicating requeue needed
                                delay_s = getattr(cfg, 'EXIT_LOW_LIQUIDITY_REQUEUE_DELAY_S', 60)
                                logger.info(f"Exit should be requeued for {context.symbol} after {delay_s}s")
                                return ExitResult(
                                    success=False,
                                    reason="low_liquidity_requeue",
                                    error=f"Requeue after {delay_s}s - spread too wide"
                                )
                            elif action == "market":
                                logger.warning(
                                    f"Proceeding with exit despite low liquidity for {context.symbol} (action=market)"
                                )
                                # Continue with execution below

                        # Warn if spread is high but not blocking
                        elif spread_pct > 5.0:
                            logger.warning(
                                f"Low liquidity detected for {context.symbol}: "
                                f"Spread={spread_pct:.2f}%, Bid={bid:.8f}, Ask={ask:.8f}",
                                extra={'event_type': 'LOW_LIQUIDITY_WARNING', 'symbol': context.symbol, 'spread_pct': spread_pct}
                            )

            except Exception as ticker_err:
                logger.debug(f"Failed to fetch ticker for liquidity check: {ticker_err}")

            # Get BID price from ticker for aggressive SELL pricing
            # (consistent with BUY using ASK + premium)
            bid = context.current_price  # Default fallback
            ask = None

            try:
                # Fetch ticker to get real BID price
                ticker = self.exchange_adapter.fetch_ticker(context.symbol)
                if ticker and 'bid' in ticker and ticker['bid']:
                    bid = ticker['bid']
                    ask = ticker.get('ask')
                    logger.debug(f"Using BID price for exit: {bid} (current: {context.current_price})")

                    # Phase 3: Log price_snapshot before order
                    try:
                        from core.event_schemas import PriceSnapshot
                        from core.logger_factory import ORDER_LOG, log_event
                        from core.trace_context import Trace

                        spread = (ask - bid) if (ask and bid) else None
                        spread_bps = (spread / ask * 10000) if (spread and ask and ask > 0) else None

                        price_snapshot = PriceSnapshot(
                            symbol=context.symbol,
                            bid=bid,
                            ask=ask,
                            last=context.current_price,
                            spread_bps=spread_bps
                        )

                        with Trace(decision_id=context.decision_id) if context.decision_id else Trace():
                            log_event(ORDER_LOG(), "price_snapshot", **price_snapshot.model_dump())

                    except Exception as e:
                        logger.debug(f"Failed to log price_snapshot for {context.symbol}: {e}")
            except Exception as ticker_error:
                logger.warning(f"Failed to fetch ticker for exit pricing, using current price: {ticker_error}")

            # Calculate aggressive SELL price: BID - premium (taker side, immediate fill)
            # This mirrors the BUY logic: ASK + premium
            premium_bps = self.max_slippage_bps
            aggressive_price = bid * (1 - premium_bps / 10000.0)

            logger.info(f"SELL IOC pricing for {context.symbol}: BID={bid:.6f}, premium={premium_bps}bp, "
                       f"aggressive_price={aggressive_price:.6f} (vs current={context.current_price:.6f})")

            order = self.order_service.place_limit_ioc(
                symbol=context.symbol,
                side="sell",
                amount=context.amount,
                price=aggressive_price,
                client_order_id=f"exit_{reason}_{int(time.time())}"
            )

            # Phase 0: Check and log order fills
            if order:
                try:
                    from trading.orders import check_and_log_order_fills
                    check_and_log_order_fills(order, context.symbol)
                except Exception as fill_log_error:
                    logger.debug(f"Fill logging failed for exit order: {fill_log_error}")

            if order and order.get('status') == 'closed':
                return ExitResult(
                    success=True,
                    order_id=order['id'],
                    filled_amount=order.get('filled', 0),
                    avg_price=order.get('average', 0),
                    reason=reason
                )

            return ExitResult(success=False, reason=reason, error="Limit IOC not filled")

        except Exception as e:
            error_msg = str(e)

            # Check for known low-liquidity errors
            if 'Oversold' in error_msg or '30005' in error_msg:
                logger.error(
                    f"EXIT FAILED - NO LIQUIDITY: {context.symbol} | "
                    f"Exchange reports 'Oversold' - no buyers available",
                    extra={
                        'event_type': 'EXIT_NO_LIQUIDITY',
                        'symbol': context.symbol,
                        'error_code': '30005',
                        'error': 'Oversold'
                    }
                )

            return ExitResult(success=False, reason=reason, error=error_msg)

    def _try_market_exit(self, context: ExitContext, reason: str) -> ExitResult:
        """Try market exit order with aggressive pricing"""
        try:
            # MEXC requires price parameter even for market IOC orders
            # Use current price or bid as reference
            exit_price = context.current_price

            # Try to get bid price for more aggressive fill
            try:
                ticker = self.exchange_adapter.fetch_ticker(context.symbol)
                if ticker and 'bid' in ticker and ticker['bid']:
                    exit_price = ticker['bid']
            except Exception:
                pass  # Use current price as fallback

            # Use limit IOC at bid price instead of market order
            # This works better on MEXC and provides price protection
            order = self.order_service.place_limit_ioc(
                symbol=context.symbol,
                side="sell",
                amount=context.amount,
                price=exit_price,
                client_order_id=f"market_exit_{reason}_{int(time.time())}"
            )

            # Phase 0: Check and log order fills
            if order:
                try:
                    from trading.orders import check_and_log_order_fills
                    check_and_log_order_fills(order, context.symbol)
                except Exception as fill_log_error:
                    logger.debug(f"Fill logging failed for market exit: {fill_log_error}")

            if order and order.get('status') == 'closed':
                return ExitResult(
                    success=True,
                    order_id=order['id'],
                    filled_amount=order.get('filled', 0),
                    avg_price=order.get('average', 0),
                    reason=reason
                )

            return ExitResult(success=False, reason=reason, error="Market order not filled")

        except Exception as e:
            return ExitResult(success=False, reason=reason, error=str(e))

    def _try_ioc_fallback(self, context: ExitContext, reason: str) -> ExitResult:
        """Try IOC fallback when market sells are disabled (with BID-based pricing)"""
        try:
            # Get BID price for aggressive pricing (fallback to current price)
            bid = context.current_price

            try:
                ticker = self.exchange_adapter.fetch_ticker(context.symbol)
                if ticker and 'bid' in ticker and ticker['bid']:
                    bid = ticker['bid']
            except Exception:
                pass  # Use fallback

            # Use BID directly for maximum aggression in fallback scenario
            # (no premium deduction since this is already a retry)
            order = self.order_service.place_limit_ioc(
                symbol=context.symbol,
                side="sell",
                amount=context.amount,
                price=bid,
                client_order_id=f"ioc_fallback_{reason}_{int(time.time())}"
            )

            # Phase 0: Check and log order fills
            if order:
                try:
                    from trading.orders import check_and_log_order_fills
                    check_and_log_order_fills(order, context.symbol)
                except Exception as fill_log_error:
                    logger.debug(f"Fill logging failed for IOC fallback: {fill_log_error}")

            if order and order.get('status') == 'closed':
                return ExitResult(
                    success=True,
                    order_id=order['id'],
                    filled_amount=order.get('filled', 0),
                    avg_price=order.get('average', 0),
                    reason=reason
                )

            return ExitResult(success=False, reason=reason, error="IOC fallback not filled")

        except Exception as e:
            return ExitResult(success=False, reason=reason, error=str(e))

    def _get_min_amount(self, symbol: str) -> float:
        """Get minimum amount for symbol"""
        # This would integrate with exchange info
        return 0.001  # Simplified

    def _get_min_notional(self, symbol: str) -> float:
        """Get minimum notional for symbol"""
        # This would integrate with exchange info
        return 5.0  # Simplified (common USDT minimum)

    def place_or_replace_exit_protection(self, symbol: str, exit_type: str,
                                       amount: float, target_price: float,
                                       active_order_id: Optional[str] = None) -> Optional[str]:
        """Place or replace exit protection order (TP/SL)"""
        with self._lock:
            try:
                # Phase 2: Generate order_req_id for idempotency tracking
                from core.trace_context import new_order_req_id
                order_req_id = new_order_req_id()

                # Phase 2: Idempotency Check - Prevent duplicate TP/SL orders
                from core.idempotency import get_idempotency_store

                try:
                    idempotency_store = get_idempotency_store()
                    existing_order_id = idempotency_store.register_order(
                        order_req_id=order_req_id,
                        symbol=symbol,
                        side="sell",
                        amount=amount,
                        price=target_price,
                        client_order_id=f"{exit_type.lower()}_{int(time.time())}"
                    )

                    if existing_order_id:
                        # Duplicate detected - return existing order ID
                        logger.warning(
                            f"Duplicate {exit_type} order detected for {symbol}, "
                            f"returning existing order {existing_order_id}"
                        )
                        return existing_order_id

                except Exception as idempotency_error:
                    # Don't fail order placement if idempotency check fails
                    logger.error(f"Idempotency check failed for {exit_type} order {symbol}: {idempotency_error}")

                # Phase 2: Client Order ID Deduplication - Check exchange for existing order
                try:
                    from trading.orders import verify_order_not_duplicate

                    client_order_id = f"{exit_type.lower()}_{int(time.time())}"
                    existing_exchange_order = verify_order_not_duplicate(
                        exchange=self.exchange_adapter._exchange,
                        client_order_id=client_order_id,
                        symbol=symbol
                    )

                    if existing_exchange_order:
                        # Duplicate found on exchange
                        logger.warning(
                            f"Duplicate {exit_type} order detected on exchange for {symbol} "
                            f"(client_order_id: {client_order_id}, exchange_order_id: {existing_exchange_order.get('id')})"
                        )
                        return existing_exchange_order.get('id')

                except Exception as dedup_error:
                    # Don't fail order placement if deduplication check fails
                    logger.debug(f"Client Order ID deduplication check failed for {exit_type} order {symbol}: {dedup_error}")

                # Cancel existing order if present
                if active_order_id:
                    self.order_service.cancel_order(active_order_id, symbol, reason="replaced")

                # Place new protection order
                if exit_type == "TAKE_PROFIT":
                    # TP is above current price for long positions
                    order = self.order_service.place_limit_gtc(
                        symbol=symbol,
                        side="sell",
                        amount=amount,
                        price=target_price,
                        post_only=True  # Ensure it goes on the book
                    )
                elif exit_type == "STOP_LOSS":
                    # SL is below current price for long positions
                    # Note: This is simplified - real implementation would use stop-limit orders
                    order = self.order_service.place_limit_gtc(
                        symbol=symbol,
                        side="sell",
                        amount=amount,
                        price=target_price,
                        post_only=False
                    )
                else:
                    return None

                # Phase 2: Update idempotency store with exchange order ID
                if order and order.get('id'):
                    try:
                        idempotency_store.update_order_status(
                            order_req_id=order_req_id,
                            exchange_order_id=order['id'],
                            status=order.get('status', 'open')
                        )
                    except Exception as update_error:
                        logger.debug(f"Failed to update idempotency store for {exit_type} order: {update_error}")

                # Phase 0: Check and log order fills (for immediate fills)
                if order:
                    try:
                        from trading.orders import check_and_log_order_fills
                        check_and_log_order_fills(order, symbol)
                    except Exception as fill_log_error:
                        logger.debug(f"Fill logging failed for {exit_type} order: {fill_log_error}")

                return order['id'] if order else None

            except Exception as e:
                logger.error(f"Exit protection placement error for {symbol} {exit_type}: {e}")
                return None

    def restore_exit_protections(self, symbol: str, position_data: Dict,
                               current_price: float) -> bool:
        """Restore TP/SL orders if missing"""
        with self._lock:
            try:
                restored = False

                # Restore Take Profit
                tp_price = position_data.get('tp_px')
                if tp_price and not position_data.get('tp_order_id'):
                    amount = position_data.get('amount', 0)
                    if amount > 0 and tp_price > current_price:
                        tp_order_id = self.place_or_replace_exit_protection(
                            symbol, "TAKE_PROFIT", amount, tp_price
                        )
                        if tp_order_id:
                            position_data['tp_order_id'] = tp_order_id
                            restored = True

                # Restore Stop Loss
                sl_price = position_data.get('sl_px')
                if sl_price and not position_data.get('sl_order_id'):
                    amount = position_data.get('amount', 0)
                    if amount > 0 and sl_price < current_price:
                        sl_order_id = self.place_or_replace_exit_protection(
                            symbol, "STOP_LOSS", amount, sl_price
                        )
                        if sl_order_id:
                            position_data['sl_order_id'] = sl_order_id
                            restored = True

                return restored

            except Exception as e:
                logger.error(f"Exit protection restoration error for {symbol}: {e}")
                return False

    def get_statistics(self) -> Dict[str, Any]:
        """Get exit order statistics"""
        with self._lock:
            stats = self._statistics.copy()
            if stats['exits_placed'] > 0:
                stats['fill_rate'] = stats['exits_filled'] / stats['exits_placed']
                stats['failure_rate'] = stats['exits_failed'] / stats['exits_placed']
            else:
                stats['fill_rate'] = 0.0
                stats['failure_rate'] = 0.0
            return stats


class ExitManager:
    """Main exit management service combining evaluation and execution"""

    def __init__(self, exchange_adapter, order_service, signal_manager,
                 trade_ttl_min: int = 60, exit_escalation_bps: List[int] = None,
                 never_market_sells: bool = False, max_slippage_bps: int = 50):
        self.evaluator = ExitEvaluator(
            trade_ttl_min=trade_ttl_min,
            exit_escalation_bps=exit_escalation_bps
        )
        self.order_manager = ExitOrderManager(
            exchange_adapter=exchange_adapter,
            order_service=order_service,
            never_market_sells=never_market_sells,
            max_slippage_bps=max_slippage_bps
        )
        self.signal_manager = signal_manager
        self._lock = RLock()

        # FIX H1: Exit Intent Registry for deduplication
        self.pending_exit_intents = {}  # symbol -> exit_metadata
        self._exit_intents_lock = RLock()

    def process_exit_signals(self, max_per_cycle: int = 5) -> List[Tuple[Dict, ExitResult]]:
        """Process pending exit signals from signal queue"""
        with self._lock:
            results: List[Tuple[Dict, ExitResult]] = []
            processed_count = 0

            while processed_count < max_per_cycle:
                signal = self.signal_manager.process_next_signal()
                if not signal:
                    break

                try:
                    # Create exit context from signal
                    context = ExitContext(
                        symbol=signal['symbol'],
                        current_price=signal['data'].get('current_price', 0),
                        amount=signal['data'].get('amount', 0),
                        buying_price=signal['data'].get('buying_price', 0),
                        elapsed_minutes=signal['data'].get('elapsed_minutes', 0),
                        position_data=signal['data'],
                        decision_id=signal['data'].get('decision_id')
                    )

                    # Execute exit
                    signal_type = signal.get('signal_type', 'UNKNOWN')
                    result = self.order_manager.execute_exit_order(context, signal_type)

                    results.append((signal, result))

                    if result.success:
                        logger.info(f"Exit executed for {signal['symbol']}: {signal_type}")
                    else:
                        logger.warning(f"Exit failed for {signal['symbol']}: {result.error}")

                    processed_count += 1

                except Exception as e:
                    logger.error(f"Exit signal processing error: {e}")
                    processed_count += 1

            return results

    def evaluate_position_exits(self, symbol: str, position_data: Dict,
                              current_price: float) -> List[str]:
        """Evaluate exit conditions for a position"""
        context = ExitContext(
            symbol=symbol,
            current_price=current_price,
            amount=position_data.get('amount', 0),
            buying_price=position_data.get('buying_price', 0),
            elapsed_minutes=(time.time() - position_data.get('time', 0)) / 60,
            position_data=position_data
        )

        return self.evaluator.evaluate_exit_signals(context)

    def queue_exit_signal(self, symbol: str, reason: str, position_data: Dict,
                         current_price: float) -> bool:
        """Queue an exit signal for processing"""
        try:
            # Filter out 'symbol' from position_data to avoid duplicate kwarg
            # (position_data may contain 'symbol' key which would conflict with symbol parameter)
            extra_data = {k: v for k, v in position_data.items() if k != 'symbol'}

            # Use the SignalManager's add_exit_signal method
            self.signal_manager.add_exit_signal(
                symbol=symbol,
                signal_type=reason,
                reason=reason,
                current_price=current_price,
                **extra_data
            )
            return True

        except Exception as e:
            logger.error(f"Error queuing exit signal for {symbol}: {e}")
            return False

    def execute_immediate_exit(self, symbol: str, position_data: Dict,
                             current_price: float, reason: str = "MANUAL_EXIT") -> ExitResult:
        """Execute immediate exit bypassing queue"""
        context = ExitContext(
            symbol=symbol,
            current_price=current_price,
            amount=position_data.get('amount', 0),
            buying_price=position_data.get('buying_price', 0),
            elapsed_minutes=(time.time() - position_data.get('time', 0)) / 60,
            position_data=position_data
        )

        return self.order_manager.execute_exit_order(context, reason)

    def restore_exit_protections(self, symbol: str, position_data: Dict,
                               current_price: float) -> bool:
        """Restore missing TP/SL orders"""
        return self.order_manager.restore_exit_protections(symbol, position_data, current_price)

    def place_exit_protection(self, symbol: str, exit_type: str, amount: float,
                            target_price: float, active_order_id: Optional[str] = None) -> Optional[str]:
        """Place or replace exit protection order"""
        return self.order_manager.place_or_replace_exit_protection(
            symbol, exit_type, amount, target_price, active_order_id
        )

    def get_statistics(self) -> Dict[str, Any]:
        """Get comprehensive exit management statistics"""
        return {
            'order_manager': self.order_manager.get_statistics(),
            'signal_queue_size': self.signal_manager.queue.get_signal_count()
        }
