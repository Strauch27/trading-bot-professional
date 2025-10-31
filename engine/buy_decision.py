#!/usr/bin/env python3
"""
Buy Decision Module

Contains:
- Buy signal evaluation logic
- Buy order execution
- Buy flow handling and tracking
"""

import logging
import time
from typing import Any, Dict, Optional

import config
from core.event_schemas import RiskLimitsEval
from core.logger_factory import DECISION_LOG, ORDER_LOG, log_event
from core.logging.adaptive_logger import guard_stats_record, notify_trade_completed, track_error, track_guard_block
from core.logging.debug_tracer import trace_error, trace_function, trace_step
from core.logging.logger import new_decision_id

# Risk management imports (avoid hot-path imports)
from core.risk_limits import RiskLimitChecker

# Phase 1 Structured Logging
from core.trace_context import Trace
from decision.assembler import assemble as assemble_intent

logger = logging.getLogger(__name__)


class BuyDecisionHandler:
    """
    Handles buy signal evaluation and order execution.

    Separates buy decision logic from main engine orchestration.
    """

    def __init__(self, engine):
        """
        Initialize with reference to main engine.

        Args:
            engine: Main TradingEngine instance for accessing services
        """
        self.engine = engine

        # Initialize risk checker once (avoid hot-path instantiation)
        self.risk_checker = RiskLimitChecker(engine.portfolio, config)

    @trace_function(include_args=False, include_result=True)
    def evaluate_buy_signal(self, symbol: str, coin_data: Dict, current_price: float,
                          market_health: str = "unknown") -> Optional[str]:
        """
        Evaluate buy signal using BuySignalService and MarketGuards.

        Returns:
            Signal reason string if buy is triggered, None otherwise
        """
        # Start decision tracking with timing
        decision_start_time = time.time()
        decision_id = new_decision_id()
        self.engine.current_decision_id = decision_id

        # Log decision start with telemetry
        logger.debug(f"[DECISION_START] {symbol} @ {current_price:.8f} | market_health={market_health}",
                    extra={'decision_id': decision_id})

        self.engine.jsonl_logger.decision_start(
            decision_id=decision_id,
            symbol=symbol,
            side="buy",
            current_price=current_price,
            volume=coin_data.get('volume', 0) if coin_data else 0,
            bid=coin_data.get('bid') if coin_data else None,
            ask=coin_data.get('ask') if coin_data else None,
            market_health=market_health,
            timestamp=decision_start_time
        )

        # Start Buy Flow Logger
        self.engine.buy_flow_logger.start_evaluation(symbol)

        try:
            trace_step("decision_started", symbol=symbol, price=current_price, decision_id=decision_id)

            # 0. NEW PIPELINE: Read drop data from MarketSnapshot store
            # RollingWindows are now updated centrally in MarketDataService
            snapshot, snapshot_ts = self.engine.get_snapshot_entry(symbol)
            stale_ttl = getattr(config, 'SNAPSHOT_STALE_TTL_S', 30.0)
            if snapshot and snapshot_ts is not None and (time.time() - snapshot_ts) > stale_ttl:
                trace_step("snapshot_stale", symbol=symbol, age=time.time() - snapshot_ts)
                snapshot = None

            if snapshot:
                drop_pct = snapshot.get('windows', {}).get('drop_pct')
                peak = snapshot.get('windows', {}).get('peak')
                trace_step("snapshot_read", symbol=symbol, drop_pct=drop_pct, peak=peak,
                          snapshot_ts=snapshot.get('ts'))
            else:
                trace_step("no_snapshot", symbol=symbol, info="No snapshot in store yet")

            # 1. Update price data in buy signal service
            trace_step("update_buy_signal_service", symbol=symbol, price=current_price)
            self.engine.buy_signal_service.update_price(symbol, current_price)

            # 2. Update market guards with current data
            volume = coin_data.get('volume', 0) if coin_data else 0
            trace_step("update_market_guards", symbol=symbol, price=current_price, volume=volume)
            self.engine.market_guards.update_price_data(symbol, current_price, volume)

            # Update orderbook data if available
            if coin_data and 'bid' in coin_data and 'ask' in coin_data:
                trace_step("update_orderbook", symbol=symbol, bid=coin_data['bid'], ask=coin_data['ask'])
                self.engine.market_guards.update_orderbook(symbol, coin_data['bid'], coin_data['ask'])

            # 3. Check market guards first (fail fast)
            trace_step("check_guards_start", symbol=symbol)
            passes_guards, failed_guards = self.engine.market_guards.passes_all_guards(symbol, current_price)
            trace_step("check_guards_result", symbol=symbol, passes=passes_guards, failed_guards=failed_guards)

            # Extract details for stats
            details = self.engine.market_guards.get_detailed_guard_status(symbol, current_price)
            guard_stats_record(details)

            # Phase 1: Log structured guards_eval event
            with Trace(decision_id=decision_id):
                # Build guard results for structured logging
                guard_results = []
                for guard_name, guard_info in details.get('guards', {}).items():
                    guard_results.append({
                        "name": guard_name,
                        "passed": guard_info.get('passed', False),
                        "value": guard_info.get('value'),
                        "threshold": guard_info.get('threshold')
                    })

                log_event(
                    DECISION_LOG(),
                    "guards_eval",
                    symbol=symbol,
                    guards=guard_results,
                    all_passed=passes_guards
                )

            if not passes_guards:
                trace_step("guards_failed", symbol=symbol, failed_guards=failed_guards, details=details.get("summary", ""))

                # Track guard block for adaptive logging
                track_guard_block(symbol, failed_guards)

                # CRITICAL: Log why buy is blocked for debugging
                logger.info(f"BUY BLOCKED {symbol} → {','.join(failed_guards)} | price={current_price:.10f}",
                           extra={'event_type':'GUARD_BLOCK_SUMMARY','symbol':symbol,'failed_guards':failed_guards,'decision_id':decision_id})

                self.engine.jsonl_logger.guard_block(
                    decision_id=decision_id,
                    symbol=symbol,
                    failed_guards=failed_guards,
                    guard_details=details
                )

                self.engine.jsonl_logger.decision_end(
                    decision_id=decision_id,
                    symbol=symbol,
                    decision="blocked",
                    reason="market_guards_failed",
                    failed_guards=failed_guards
                )

                # Phase 3: Log structured decision_outcome event
                with Trace(decision_id=decision_id):
                    log_event(
                        DECISION_LOG(),
                        "decision_outcome",
                        symbol=symbol,
                        action="blocked",
                        reason="market_guards_failed",
                        failed_guards=failed_guards
                    )

                # Verbose logging if enabled
                if getattr(config, "VERBOSE_GUARD_LOGS", False):
                    logger.info(details.get("summary", f"Guard block {symbol}: {failed_guards}"),
                                extra={'event_type': 'GUARD_BLOCK_SUMMARY', 'symbol': symbol})
                return None

            trace_step("guards_passed", symbol=symbol, details=details.get("summary", ""))

            # Log successful guard passage
            self.engine.jsonl_logger.guard_pass(
                decision_id=decision_id,
                symbol=symbol,
                guard_details=details
            )
            if getattr(config, "VERBOSE_GUARD_LOGS", False):
                logger.debug(details.get("summary", f"Guards passed for {symbol}"),
                             extra={'event_type': 'GUARD_PASS_SUMMARY', 'symbol': symbol})

            # 4. Check minimums before evaluating buy signal
            if hasattr(self.engine.exchange_adapter, 'meets_minimums'):
                # Import current POSITION_SIZE_USDT value
                from config import POSITION_SIZE_USDT
                trace_step("check_minimum_sizing", symbol=symbol, position_size=POSITION_SIZE_USDT)
                sizing_ok, why = self.engine.exchange_adapter.meets_minimums(symbol, current_price, POSITION_SIZE_USDT)
                if not sizing_ok:
                    trace_step("sizing_failed", symbol=symbol, reason=why)
                    logger.info(f"[SIZING_BLOCK] {symbol} blocked: {why}")
                    self.engine.jsonl_logger.decision_end(
                        decision_id=decision_id,
                        symbol=symbol,
                        decision="blocked",
                        reason=f"sizing:{why}",
                        market_health=market_health
                    )

                    # Phase 3: Log structured decision_outcome event
                    with Trace(decision_id=decision_id):
                        log_event(
                            DECISION_LOG(),
                            "decision_outcome",
                            symbol=symbol,
                            action="blocked",
                            reason=f"sizing_check:{why}"
                        )
                    return None
                else:
                    trace_step("sizing_passed", symbol=symbol)

            # 5. Signal Stabilization Check
            trace_step("signal_stabilization", symbol=symbol)
            # Simple condition: spread reasonable (could be enhanced)
            stabilize_condition = True
            if getattr(config, 'USE_SPREAD_GUARD', False):
                if coin_data and 'bid' in coin_data and 'ask' in coin_data:
                    spread_bp = ((coin_data['ask'] - coin_data['bid']) / coin_data['ask'] * 10000.0)
                    max_spread = getattr(config, 'MAX_SPREAD_BP_BY_SYMBOL', {}).get(symbol, 30)
                    stabilize_condition = spread_bp <= max_spread

            if not self.engine.stabilizer.step(stabilize_condition):
                trace_step("stabilization_waiting", symbol=symbol)
                # Still waiting for stabilization
                decision_time = time.time() - decision_start_time
                self.engine.monitoring.performance_metrics['decision_times'].append(decision_time)

                self.engine.jsonl_logger.decision_end(
                    decision_id=decision_id,
                    symbol=symbol,
                    decision="no_buy",
                    reason="awaiting_stabilization",
                    decision_time_ms=decision_time * 1000
                )

                # Phase 3: Log structured decision_outcome event
                with Trace(decision_id=decision_id):
                    log_event(
                        DECISION_LOG(),
                        "decision_outcome",
                        symbol=symbol,
                        action="skip",
                        reason="awaiting_stabilization"
                    )
                return None

            # 5c. Evaluate buy signal with DROP_TRIGGER logic (V9_3)
            trace_step("evaluate_buy_signal_start", symbol=symbol)
            buy_triggered, context = self.engine.buy_signal_service.evaluate_buy_signal(
                symbol, current_price, drop_snapshot_store=self.engine.drop_snapshot_store
            )
            trace_step("evaluate_buy_signal_result", symbol=symbol, triggered=buy_triggered, context=context)

            # Console output for debugging buy signals
            if buy_triggered:
                print(f"\n[BUY TRIGGER] {symbol} @ {current_price:.6f} | "
                      f"Drop: {context.get('drop_pct', 0):.2f}% | "
                      f"Anchor: {context.get('anchor', 0):.6f}\n", flush=True)

                # Dashboard event
                try:
                    from ui.dashboard import emit_dashboard_event
                    emit_dashboard_event("BUY_TRIGGER", f"{symbol} @ ${current_price:.4f} (Drop: {context.get('drop_pct', 0):.2f}%)")
                except Exception:
                    pass

            # Phase 1: Log structured drop_trigger_eval event
            # CRITICAL: Wrap in try-except to prevent logging errors from blocking order execution
            try:
                with Trace(decision_id=decision_id):
                    # Get peak (anchor) price from MarketSnapshot
                    anchor_price = None
                    if snapshot:
                        anchor_price = snapshot.get('windows', {}).get('peak')

                    # Fallback to context if snapshot not available
                    if anchor_price is None:
                        anchor_price = context.get('anchor')

                    log_event(
                        DECISION_LOG(),
                        "drop_trigger_eval",
                        symbol=symbol,
                        anchor=anchor_price,
                        current_price=current_price,
                        drop_pct=context.get('drop_pct', 0),
                        threshold=getattr(config, 'DROP_TRIGGER_VALUE', 0.96) * 100 - 100,  # Convert to pct
                        threshold_hit=buy_triggered,
                        mode=context.get('mode')
                    )
            except Exception as log_error:
                # CRITICAL: Don't let logging errors prevent order execution!
                print(f"\n[ERROR] Structured logging failed for {symbol}: {type(log_error).__name__}: {log_error}\n", flush=True)
                logger.warning(f"Structured logging failed for {symbol}, continuing with order: {log_error}")
                # Continue with order execution anyway

            if buy_triggered:
                signal_reason = f"DROP_TRIGGER_MODE_{context['mode']}"

                # Track decision timing
                decision_time = time.time() - decision_start_time
                self.engine.monitoring.performance_metrics['decision_times'].append(decision_time)

                logger.info(f"[BUY_TRIGGERED] {symbol} @ {current_price:.8f} | reason={signal_reason}",
                           extra={'decision_id': decision_id})

                self.engine.jsonl_logger.decision_end(
                    decision_id=decision_id,
                    symbol=symbol,
                    decision="buy_triggered",
                    reason=signal_reason,
                    buy_context=context,
                    guards_passed=True,
                    market_health=market_health,
                    decision_time_ms=decision_time * 1000
                )

                # Phase 3: Log structured decision_outcome event
                with Trace(decision_id=decision_id):
                    log_event(
                        DECISION_LOG(),
                        "decision_outcome",
                        symbol=symbol,
                        action="buy",
                        reason=signal_reason,
                        drop_pct=context.get('drop_pct', 0),
                        mode=context.get('mode')
                    )

                logger.info(f"Buy signal triggered for {symbol}: {signal_reason} "
                           f"(drop: {context['drop_pct']:.2f}%)")
                return signal_reason
            else:
                # Track decision timing
                decision_time = time.time() - decision_start_time
                self.engine.monitoring.performance_metrics['decision_times'].append(decision_time)

                logger.debug(f"[NO_SIGNAL] {symbol} @ {current_price:.8f} | reason=no_trigger")

                self.engine.jsonl_logger.decision_end(
                    decision_id=decision_id,
                    symbol=symbol,
                    decision="no_buy",
                    reason="no_trigger",
                    buy_context=context,
                    guards_passed=True,
                    market_health=market_health,
                    decision_time_ms=decision_time * 1000
                )

                # Phase 3: Log structured decision_outcome event
                with Trace(decision_id=decision_id):
                    log_event(
                        DECISION_LOG(),
                        "decision_outcome",
                        symbol=symbol,
                        action="skip",
                        reason="no_trigger",
                        drop_pct=context.get('drop_pct', 0)
                    )

            return None

        except Exception as e:
            # CRITICAL: Force console output for debugging
            print(f"\n[EXCEPTION] evaluate_buy_signal() failed for {symbol}: {type(e).__name__}: {e}\n", flush=True)
            import traceback
            traceback.print_exc()

            trace_error(e, symbol=symbol, decision_id=decision_id, context="buy_signal_evaluation")

            # Track error for adaptive logging
            track_error("BUY_SIGNAL_EVALUATION_ERROR", symbol, str(e))

            # Track decision timing even for errors
            decision_time = time.time() - decision_start_time
            self.engine.monitoring.performance_metrics['decision_times'].append(decision_time)

            self.engine.jsonl_logger.decision_end(
                decision_id=decision_id,
                symbol=symbol,
                decision="error",
                reason="evaluation_exception",
                error=str(e),
                decision_time_ms=decision_time * 1000
            )

            # Phase 3: Log structured decision_outcome event
            with Trace(decision_id=decision_id):
                log_event(
                    DECISION_LOG(),
                    "decision_outcome",
                    symbol=symbol,
                    action="error",
                    reason="evaluation_exception",
                    error_type=type(e).__name__,
                    error_message=str(e)
                )

            logger.error(f"Error evaluating buy signal for {symbol}: {e}",
                        extra={'decision_id': decision_id})
            return None

    @trace_function(include_args=True, include_result=False)
    def execute_buy_order(self, symbol: str, coin_data: Dict, current_price: float, signal: str):
        """Execute buy order via Order Service"""
        decision_id = self.engine.current_decision_id or new_decision_id()

        # CRITICAL: Log buy candidate for debugging
        usdt_balance = self.engine.portfolio.get_balance("USDT")
        trace_step("buy_candidate", symbol=symbol, price=current_price, budget=usdt_balance, signal=signal)
        logger.info(f"BUY CANDIDATE {symbol} price={current_price:.10f} budget={usdt_balance:.2f} signal={signal}",
                   extra={'event_type':'BUY_CANDIDATE','symbol':symbol,'price':current_price,'budget':usdt_balance,'decision_id':decision_id})

        # Console output for buy candidate visibility
        print(f"\n[BUY CANDIDATE] {symbol} @ {current_price:.6f} | Budget: ${usdt_balance:.2f} | Signal: {signal}\n", flush=True)

        # Dashboard event
        try:
            from ui.dashboard import emit_dashboard_event
            emit_dashboard_event("BUY_CANDIDATE", f"{symbol} @ ${current_price:.4f}")
        except Exception:
            pass

        try:
            # Calculate position size
            quote_budget = self._calculate_position_size(symbol, current_price, usdt_balance)
            logger.debug(f"DEBUG: quote_budget={quote_budget} for {symbol}")
            if not quote_budget:
                logger.warning(
                    f"BUY SKIP {symbol} - Position sizing returned None (likely missing market data or insufficient budget)",
                    extra={
                        'event_type': 'BUY_SKIP_NO_QUOTE_BUDGET',
                        'symbol': symbol,
                        'current_price': current_price,
                        'usdt_balance': usdt_balance,
                        'coin_data_empty': not bool(coin_data),
                        'has_bid_ask': bool(coin_data and 'bid' in coin_data and 'ask' in coin_data) if coin_data else False
                    }
                )
                print(f"\n[DEBUG] BUY SKIP: quote_budget=None for {symbol}\n", flush=True)
                return

            # Phase 1: Risk Limits Check - BEFORE order placement
            # Note: RiskLimitChecker is initialized once in __init__ to avoid hot-path overhead
            try:
                all_passed, limit_checks = self.risk_checker.check_limits(symbol, quote_budget)

                # Determine blocking limit (with safe fallback)
                blocking_limit = None
                if not all_passed:
                    hit_limits = [c['limit'] for c in limit_checks if c.get('hit')]
                    blocking_limit = hit_limits[0] if hit_limits else 'unknown'

                # CRITICAL: Wrap risk evaluation logging in try-except to prevent blocking orders
                try:
                    risk_eval = RiskLimitsEval(
                        symbol=symbol,
                        limit_checks=limit_checks,
                        all_passed=all_passed,
                        blocking_limit=blocking_limit
                    )

                    with Trace(decision_id=decision_id):
                        log_event(DECISION_LOG(), "risk_limits_eval", **risk_eval.model_dump())
                except Exception as risk_eval_error:
                    print(f"\n[ERROR] Risk eval logging failed for {symbol}: {type(risk_eval_error).__name__}: {risk_eval_error}\n", flush=True)
                    logger.warning(f"Risk evaluation logging failed for {symbol}: {risk_eval_error}")

                if not all_passed:
                    print(f"\n[BUY BLOCKED] {symbol} - Risk limit: {blocking_limit}\n", flush=True)
                    logger.info(f"BUY BLOCKED {symbol} - Risk limit exceeded: {blocking_limit}")

                    # Wrap all logging in try-except to ensure we return and don't place order
                    try:
                        self.engine.jsonl_logger.decision_end(
                            decision_id=decision_id,
                            symbol=symbol,
                            decision="blocked",
                            reason=f"risk_limit:{blocking_limit}",
                            limit_checks=limit_checks
                        )
                    except Exception as decision_end_error:
                        print(f"\n[ERROR] decision_end logging failed for {symbol}: {decision_end_error}\n", flush=True)
                        logger.warning(f"decision_end logging failed for {symbol}: {decision_end_error}")

                    # Log structured decision_outcome event (with error protection)
                    try:
                        with Trace(decision_id=decision_id):
                            log_event(
                                DECISION_LOG(),
                                "decision_outcome",
                                symbol=symbol,
                                action="blocked",
                                reason=f"risk_limit:{blocking_limit}",
                                blocking_limit=blocking_limit
                            )
                    except Exception as outcome_log_error:
                        print(f"\n[ERROR] Decision outcome logging failed for {symbol}: {outcome_log_error}\n", flush=True)
                        logger.warning(f"Decision outcome logging failed for {symbol}: {outcome_log_error}")

                    print(f"\n[DEBUG] ORDER BLOCKED - RETURNING for {symbol}\n", flush=True)
                    return

            except Exception as risk_check_error:
                # Don't fail order placement if risk check fails - log and continue
                logger.warning(f"Risk limits check failed for {symbol}, continuing with order: {risk_check_error}")

            # Generate client order ID for tracking
            # Apply Symbol-specific Spread/Slippage Caps
            current_price = self._apply_spread_slippage_caps(symbol, coin_data, current_price, decision_id)
            if not current_price:
                print(f"\n[DEBUG] BUY SKIP: spread/slippage check failed for {symbol}\n", flush=True)
                return

            amount = quote_budget / current_price

            # CRITICAL: Log order details before placement
            logger.info(f"BUY ORDER {symbol} → amount={amount:.10f} value={quote_budget:.2f} price={current_price:.10f}",
                       extra={'event_type':'BUY_ORDER_PREP','symbol':symbol,'amount':amount,'value':quote_budget,'price':current_price,'decision_id':decision_id})

            # Dry-Run Preview
            if not self._handle_dry_run_preview(symbol, current_price, amount, quote_budget, signal, decision_id):
                print(f"\n[DEBUG] BUY SKIP: dry-run preview blocked for {symbol}\n", flush=True)
                return

            # Assemble intent for OrderRouter execution
            signal_payload = {
                "symbol": symbol,
                "side": "buy",
                "reason": signal,
                "limit_price": self._intent_limit_price(symbol, current_price, coin_data)
            }
            guards_payload = {"passed": True}
            risk_payload = {
                "allowed_qty": amount,
                "budget": self.engine.portfolio.get_free_usdt(),
                "quote_budget": quote_budget
            }

            intent = assemble_intent(signal_payload, guards_payload, risk_payload)
            if not intent:
                logger.warning(f"BUY intent assembly returned None for {symbol}")
                return

            intent_metadata = {
                "decision_id": decision_id,
                "symbol": symbol,
                "signal": signal,
                "quote_budget": quote_budget,
                "intended_price": signal_payload["limit_price"],
                "amount": amount,
                "start_ts": time.time(),
                "market_snapshot": {
                    "bid": coin_data.get('bid') if coin_data else None,
                    "ask": coin_data.get('ask') if coin_data else None
                }
            }

            # CRITICAL FIX (H-ENG-01): Enforce capacity limit on pending_buy_intents
            MAX_PENDING_INTENTS = 100  # Prevent unbounded memory growth
            if len(self.engine.pending_buy_intents) >= MAX_PENDING_INTENTS:
                # Evict oldest intent to make room
                oldest_intent = min(
                    self.engine.pending_buy_intents.items(),
                    key=lambda x: x[1].get('start_ts', float('inf'))
                )
                oldest_id, oldest_meta = oldest_intent
                logger.warning(
                    f"Pending intents at capacity ({MAX_PENDING_INTENTS}), "
                    f"evicting oldest intent {oldest_id} for {oldest_meta.get('symbol')}",
                    extra={
                        'event_type': 'INTENT_CAPACITY_EVICTION',
                        'evicted_id': oldest_id,
                        'evicted_symbol': oldest_meta.get('symbol'),
                        'age_s': time.time() - oldest_meta.get('start_ts', time.time())
                    }
                )
                self.engine.clear_intent(oldest_id, reason="capacity_eviction")

            self.engine.pending_buy_intents[intent.intent_id] = intent_metadata

            # P1: Persist intent state (debounced)
            self.engine._persist_intent_state()

            with Trace(decision_id=decision_id):
                log_event(
                    ORDER_LOG(),
                    "order_intent",
                    symbol=symbol,
                    side="buy",
                    intent_id=intent.intent_id,
                    qty=amount,
                    limit_price=signal_payload["limit_price"],
                    reason=signal
                )

            self.engine.jsonl_logger.order_sent(
                decision_id=decision_id,
                symbol=symbol,
                side="buy",
                amount=amount,
                price=signal_payload["limit_price"],
                intent_id=intent.intent_id,
                quote_budget=quote_budget,
                timestamp=time.time()
            )

            self.engine.event_bus.publish("order.intent", intent.to_dict())
            logger.info(
                f"BUY INTENT submitted {symbol} qty={amount:.10f} limit={signal_payload['limit_price']:.10f} "
                f"(intent_id={intent.intent_id})"
            )

        except Exception as e:
            # Track error for adaptive logging
            track_error("BUY_ORDER_EXECUTION_ERROR", symbol, str(e))

            self.engine.jsonl_logger.order_canceled(
                decision_id=decision_id,
                symbol=symbol,
                reason="execution_error",
                error=str(e)
            )
            logger.error(f"Buy order execution error for {symbol}: {e}",
                        extra={'decision_id': decision_id})

    def _calculate_position_size(self, symbol: str, current_price: float, usdt_balance: float) -> Optional[float]:
        """Calculate position size with budget checks"""
        trace_step("calculating_position_size", symbol=symbol)

        # Get config values for sizing_calc event
        position_size_cfg = getattr(config, 'POSITION_SIZE_USDT', 25.0)
        min_slot = getattr(config, 'MIN_SLOT_USDT', 10.0)
        fees_bps = getattr(config, 'TRADING_FEE_BPS', 10)
        slippage_bps = getattr(config, 'SLIPPAGE_BPS_ALLOWED', 5)

        # Calculate position size
        if self.engine.sizing_service:
            quote_budget = self.engine.sizing_service.calculate_position_size(
                symbol, current_price, usdt_balance
            )
            trace_step("position_size_calculated", symbol=symbol, quote_budget=quote_budget, method="sizing_service")
        else:
            # Simplified position sizing
            quote_budget = min(usdt_balance * 0.1, 100.0)  # 10% or $100 max
            trace_step("position_size_calculated", symbol=symbol, quote_budget=quote_budget, method="simplified")

        # Calculate qty details for sizing_calc event
        qty_raw = quote_budget / current_price if current_price > 0 else 0

        # Get market limits and hash for change detection
        market_limits = {}
        market_limits_hash = None
        min_notional = min_slot

        if hasattr(self.engine.exchange_adapter, 'get_market_info'):
            try:
                market_info = self.engine.exchange_adapter.get_market_info(symbol)
                limits = market_info.get('limits', {})
                precision = market_info.get('precision', {})

                # Extract all relevant limits
                market_limits = {
                    'min_notional': float(limits.get('cost', {}).get('min', min_slot)),
                    'max_notional': float(limits.get('cost', {}).get('max', 0)),
                    'min_qty': float(limits.get('amount', {}).get('min', 0)),
                    'max_qty': float(limits.get('amount', {}).get('max', 0)),
                    'qty_step': 10 ** -precision.get('amount', 8),  # Step size from precision
                    'price_tick': 10 ** -precision.get('price', 8),  # Tick size from precision
                }
                min_notional = market_limits['min_notional']

                # Calculate hash for change detection
                import hashlib
                import json
                limits_str = json.dumps(market_limits, sort_keys=True)
                market_limits_hash = hashlib.sha256(limits_str.encode()).hexdigest()[:16]

            except Exception as e:
                logger.debug(f"Failed to get market limits for {symbol}: {e}")
                # Fallback to basic min_notional
                if hasattr(self.engine.exchange_adapter, 'get_min_notional'):
                    try:
                        min_notional = self.engine.exchange_adapter.get_min_notional(symbol) or min_slot
                    except Exception:
                        pass

        # Phase 3: Log sizing_calc event with market_limits_snapshot
        with Trace(decision_id=self.engine.current_decision_id):
            # Use exchange-specific min_notional, not global min_slot
            passed = quote_budget >= min_notional
            fail_reason = None if passed else "insufficient_budget_after_sizing"

            log_event(
                DECISION_LOG(),
                "sizing_calc",
                symbol=symbol,
                position_size_usdt_cfg=position_size_cfg,
                quote_budget=quote_budget,
                min_notional=min_notional,
                fees_bps=fees_bps,
                slippage_bps=slippage_bps,
                qty_raw=qty_raw,
                qty_rounded=qty_raw,  # Rounding happens later in order placement
                quote_after_round=quote_budget,
                passed=passed,
                fail_reason=fail_reason,
                market_limits=market_limits if market_limits else None,
                market_limits_hash=market_limits_hash
            )

        trace_step("budget_check", symbol=symbol, quote_budget=quote_budget, min_slot=min_slot, min_notional=min_notional)

        # Check against exchange-specific min_notional, not just global min_slot
        if quote_budget < min_notional:
            trace_step("insufficient_budget", symbol=symbol, quote_budget=quote_budget, min_notional=min_notional)
            logger.info(f"BUY SKIP {symbol} – insufficient budget: {quote_budget:.2f} < {min_notional:.2f} (exchange min_notional)",
                       extra={'event_type':'BUY_SKIP_BUDGET','symbol':symbol,'quote_budget':quote_budget,'min_notional':min_notional,'min_slot':min_slot})
            self.engine.jsonl_logger.order_canceled(
                decision_id=self.engine.current_decision_id,
                symbol=symbol,
                reason="insufficient_budget",
                quote_budget=quote_budget,
                min_slot=min_slot
            )
            return None

        return quote_budget

    def _apply_spread_slippage_caps(self, symbol: str, coin_data: Dict, current_price: float,
                                   decision_id: str) -> Optional[float]:
        """Apply spread and slippage caps to price"""
        trace_step("symbol_spread_slippage_check", symbol=symbol)

        # Check spread if bid/ask available
        if coin_data and 'bid' in coin_data and 'ask' in coin_data:
            bid, ask = coin_data['bid'], coin_data['ask']
            if ask > bid:
                spread_bp = (ask - bid) / ask * 10000.0
                max_spread = getattr(config, 'MAX_SPREAD_BP_BY_SYMBOL', {}).get(
                    symbol, getattr(config, 'DEFAULT_MAX_SPREAD_BPS', 30))

                if spread_bp > max_spread:
                    trace_step("spread_too_wide", symbol=symbol, spread_bp=spread_bp, max_spread=max_spread)
                    logger.info(f"BUY BLOCKED {symbol} - spread too wide: {spread_bp:.1f}bp > {max_spread}bp")
                    self.engine.jsonl_logger.decision_end(
                        decision_id=decision_id,
                        symbol=symbol,
                        decision="blocked",
                        reason="spread_too_wide",
                        spread_bp=spread_bp,
                        max_spread_bp=max_spread
                    )
                    return None

                # Apply slippage cap to price
                mid_price = (bid + ask) / 2
                max_slippage_bp = getattr(config, 'SLIPPAGE_BP_ALLOWED_BY_SYMBOL', {}).get(
                    symbol, getattr(config, 'DEFAULT_SLIPPAGE_BPS', 25))
                price_cap = mid_price * (1 + max_slippage_bp / 10000.0)
                effective_price = min(current_price, price_cap)

                if effective_price < current_price:
                    logger.info(f"PRICE CAPPED {symbol}: {current_price:.8f} -> {effective_price:.8f} (slippage cap: {max_slippage_bp}bp)")
                    current_price = effective_price
                    trace_step("price_capped", symbol=symbol, original_price=current_price, capped_price=effective_price,
                               max_slippage_bp=max_slippage_bp)

        return current_price

    def _handle_dry_run_preview(self, symbol: str, current_price: float, amount: float,
                               quote_budget: float, signal: str, decision_id: str) -> bool:
        """Handle dry-run preview - returns True if should proceed"""
        trace_step("dry_run_preview", symbol=symbol, enabled=getattr(config, 'DRY_RUN_PREVIEW', False))

        if getattr(config, 'DRY_RUN_PREVIEW', False):
            preview = {
                "symbol": symbol,
                "price": current_price,
                "amount": amount,
                "qty_quote": quote_budget,
                "signal": signal,
                "would_place": True,
                "reason": None
            }
            trace_step("showing_preview", symbol=symbol, preview=preview)
            logger.info(f"[PREVIEW] BUY {preview}")
            self.engine.jsonl_logger.emit("DECISION_PREVIEW", {"side":"BUY", **preview})

            if not getattr(config, 'DRY_RUN_EXECUTE', True):
                trace_step("dry_run_skip", symbol=symbol, reason="DRY_RUN_EXECUTE=False")
                logger.info(f"[DRY_RUN] Skipping actual order placement for {symbol}")
                self.engine.jsonl_logger.order_canceled(
                    decision_id=decision_id,
                    symbol=symbol,
                    reason="dry_run_mode",
                    quote_budget=quote_budget
                )
                return False

        return True

    def _intent_limit_price(
        self,
        symbol: str,
        current_price: float,
        coin_data: Optional[Dict]
    ) -> float:
        """Calculate an aggressive buy limit price for intent submission."""
        reference_price = current_price
        if coin_data:
            ask = coin_data.get("ask")
            if ask and ask > 0:
                reference_price = ask
        premium_bps = getattr(config, "BUY_LIMIT_PREMIUM_BPS", 10)
        limit_price = reference_price * (1 + premium_bps / 10000.0)
        if limit_price <= 0:
            limit_price = current_price
        return limit_price

    def handle_router_fill(
        self,
        intent_id: str,
        event_data: Dict[str, Any],
        summary: Optional[Dict[str, Any]]
    ) -> None:
        """Finalize buy-side processing once OrderRouter reports a fill."""
        metadata = self.engine.pending_buy_intents.pop(intent_id, None)

        # P1: Persist intent state after removal (debounced)
        self.engine._persist_intent_state()

        # P2: Calculate intent-to-fill latency with breakdown
        intent_to_fill_ms = None
        latency_breakdown = {}

        if metadata:
            start_ts = metadata.get("start_ts")
            if start_ts:
                intent_to_fill_ms = (time.time() - start_ts) * 1000

                # Build latency breakdown (with defaults to avoid KeyErrors)
                latency_breakdown = {
                    "total_ms": intent_to_fill_ms,
                    "placement_ms": event_data.get("placement_latency_ms", 0),
                    "exchange_fill_ms": event_data.get("fill_latency_ms", 0),
                    "reconcile_ms": event_data.get("reconcile_latency_ms", 0),
                    "fetch_order_ms": event_data.get("fetch_order_latency_ms", 0)
                }

                # Track in rolling stats (NPE-safe)
                if hasattr(self.engine, 'rolling_stats') and self.engine.rolling_stats:
                    try:
                        # Add dedicated intent_to_fill metric
                        if hasattr(self.engine.rolling_stats, 'add_latency'):
                            self.engine.rolling_stats.add_latency("intent_to_fill", intent_to_fill_ms)
                        # Also add to general latency tracking
                        if hasattr(self.engine.rolling_stats, 'add_fill'):
                            self.engine.rolling_stats.add_fill(time.time(), 0)  # Slippage tracked separately
                    except Exception as stats_error:
                        logger.debug(f"Rolling stats update failed: {stats_error}")

                # P2: Log intent latency for monitoring
                logger.info(
                    f"INTENT_TO_FILL_LATENCY: {intent_to_fill_ms:.1f}ms | "
                    f"placement={latency_breakdown.get('placement_ms', 0):.1f}ms, "
                    f"exchange={latency_breakdown.get('exchange_fill_ms', 0):.1f}ms, "
                    f"reconcile={latency_breakdown.get('reconcile_ms', 0):.1f}ms, "
                    f"fetch_order={latency_breakdown.get('fetch_order_ms', 0):.1f}ms",
                    extra={
                        "event_type": "INTENT_LATENCY",
                        "intent_id": intent_id,
                        "symbol": event_data.get("symbol"),
                        "latency_ms": intent_to_fill_ms,
                        "breakdown": latency_breakdown
                    }
                )

        symbol = event_data.get("symbol") or (metadata or {}).get("symbol")
        if not symbol:
            logger.warning(f"BUY fill received without symbol (intent_id={intent_id})")
            return

        decision_id = (
            (metadata or {}).get("decision_id")
            or self.engine.current_decision_id
            or new_decision_id()
        )
        signal = (metadata or {}).get("signal") or event_data.get("reason") or "UNKNOWN"

        try:
            order_data = event_data.get("order") or {}
            filled_amount = float(event_data.get("filled_qty") or 0.0)
            if filled_amount <= 0 and summary:
                filled_amount = float(summary.get("qty_delta") or 0.0)

            if filled_amount <= 0:
                logger.warning(f"BUY fill for {symbol} had zero quantity (intent_id={intent_id})")
                return

            avg_price = event_data.get("average_price")
            if avg_price is None:
                avg_price = order_data.get("average")
            if (avg_price is None or avg_price == 0) and summary:
                notional = float(summary.get("notional") or 0.0)
                if filled_amount > 0 and notional > 0:
                    avg_price = notional / filled_amount
            if not avg_price or avg_price <= 0:
                avg_price = (
                    (metadata or {}).get("intended_price")
                    or order_data.get("price")
                    or event_data.get("limit_price")
                )
            if not avg_price or avg_price <= 0:
                logger.warning(f"BUY fill for {symbol} missing price (intent_id={intent_id})")
                return
            avg_price = float(avg_price)

            fees = 0.0
            if summary:
                fees = float(summary.get("fees") or 0.0)
            if fees <= 0 and order_data.get("fee"):
                try:
                    fees = float((order_data.get("fee") or {}).get("cost") or 0.0)
                except Exception:
                    fees = 0.0
            if fees <= 0:
                fees = filled_amount * avg_price * getattr(config, 'TRADING_FEE_RATE', 0.001)
            fee_quote = fees

            self.engine.pnl_tracker.on_fill(symbol, "BUY", avg_price, filled_amount)

            # Reset anchor after buy fill (Mode 4 behavior)
            if hasattr(self.engine, 'market_data') and hasattr(self.engine.market_data, 'anchor_manager'):
                try:
                    self.engine.market_data.anchor_manager.reset_anchor(
                        symbol=symbol,
                        price=avg_price,
                        now=time.time()
                    )
                    logger.info(f"Anchor reset for {symbol} to {avg_price:.6f} (post-buy)")
                except Exception as e:
                    logger.warning(f"Failed to reset anchor for {symbol}: {e}")

            trade_fill_event = {
                "event_type": "TRADE_FILL",
                "symbol": symbol,
                "side": "BUY",
                "price": avg_price,
                "qty": filled_amount,
                "fee_quote": fee_quote,
                "ts": time.time()
            }

            reference_price = order_data.get("price") or (metadata or {}).get("intended_price")
            if reference_price and reference_price > 0:
                slippage_bp = abs(avg_price - reference_price) / reference_price * 10000.0
                trade_fill_event["slippage_bp"] = slippage_bp
                self.engine.rolling_stats.add_fill(trade_fill_event["ts"], slippage_bp)

            from core.utils.order_flow import on_order_update
            on_order_update(trade_fill_event, self.engine.pnl_tracker)

            logger.info("TRADE_FILL", extra=trade_fill_event)

            self.engine.pnl_service.record_fill(
                symbol=symbol,
                side="buy",
                quantity=filled_amount,
                avg_price=avg_price,
                fee_quote=fee_quote
            )

            order_id = event_data.get("order_id") or order_data.get("id")
            client_order_id = event_data.get("client_order_id") or order_data.get("clientOrderId")
            total_cost = filled_amount * avg_price

            self.engine.jsonl_logger.order_filled(
                decision_id=decision_id,
                symbol=symbol,
                side="buy",
                client_order_id=client_order_id,
                order_id=order_id,
                filled_amount=filled_amount,
                average_price=avg_price,
                total_cost=total_cost,
                signal_reason=signal,
                intent_id=intent_id,
                timestamp=time.time()
            )

            self.engine.jsonl_logger.trade_open(
                decision_id=decision_id,
                symbol=symbol,
                entry_price=avg_price,
                amount=filled_amount,
                total_cost=total_cost,
                signal_reason=signal,
                order_id=order_id,
                timestamp=time.time()
            )

            order_ts = None
            if order_data.get("timestamp"):
                try:
                    order_ts = order_data["timestamp"] / 1000.0
                except Exception:
                    order_ts = None
            if not order_ts and metadata and metadata.get("start_ts"):
                order_ts = metadata["start_ts"]
            latency_ms_total = int((time.time() - order_ts) * 1000) if order_ts else None

            with Trace(decision_id=decision_id, exchange_order_id=order_id):
                # P2: Enhanced latency tracking with breakdown
                event_payload = {
                    "symbol": symbol,
                    "side": "buy",
                    "final_status": "filled",
                    "filled_qty": filled_amount,
                    "avg_price": avg_price,
                    "total_cost": total_cost,
                    "fee_quote": fee_quote,
                    "latency_ms_total": latency_ms_total
                }

                # Add latency breakdown if available
                if latency_breakdown:
                    event_payload["latency_breakdown"] = latency_breakdown

                log_event(ORDER_LOG(), "order_done", **event_payload)

            notify_trade_completed(symbol, "buy")

            portfolio_position = self.engine.portfolio.positions.get(symbol)
            if portfolio_position:
                try:
                    position_qty = float(portfolio_position.qty)
                    position_avg = float(portfolio_position.avg_price)
                except AttributeError:
                    position_qty = filled_amount
                    position_avg = avg_price
            else:
                position_qty = filled_amount
                position_avg = avg_price

            position_data = {
                "symbol": symbol,
                "amount": position_qty,
                "buying_price": position_avg,
                "time": time.time(),
                "signal": signal,
                "order_id": order_id,
                "decision_id": decision_id
            }
            self.engine.positions[symbol] = position_data

            try:
                from datetime import datetime

                from core.event_schemas import PositionOpened

                position_opened = PositionOpened(
                    symbol=symbol,
                    qty=position_qty,
                    avg_entry=position_avg,
                    notional=position_qty * position_avg,
                    fee_accum=fee_quote,
                    opened_at=datetime.now().isoformat()
                )

                with Trace(decision_id=decision_id, exchange_order_id=order_id):
                    log_event(DECISION_LOG(), "position_opened", **position_opened.model_dump())

            except Exception as exc:
                logger.debug(f"Failed to log position_opened for {symbol}: {exc}")

            self.engine.session_digest['buys'].append({
                'sym': symbol,
                'px': avg_price,
                'qty': filled_amount,
                't': int(time.time()),
                'signal': signal
            })

            buy_msg = f"[BUY] {symbol} @{avg_price:.6f} x{filled_amount:.6f} [{signal}] Cost: ${total_cost:.2f}"
            print(f"\n>>> {buy_msg}\n", flush=True)

            try:
                from ui.dashboard import emit_dashboard_event
                emit_dashboard_event("BUY_FILLED", f"{symbol} @ ${avg_price:.4f} x{filled_amount:.4f} (Cost: ${total_cost:.2f})")
            except Exception:
                pass

            logger.info(buy_msg)

            if hasattr(self.engine, 'telegram') and self.engine.telegram:
                try:
                    self.engine.telegram.send_message(buy_msg)
                except Exception as exc:
                    logger.warning(f"Telegram BUY notification failed: {exc}")

            start_ts = metadata.get("start_ts") if metadata else None
            duration_ms = (time.time() - start_ts) * 1000 if start_ts else 0.0
            try:
                self.engine.buy_flow_logger.end_evaluation("BUY_COMPLETED", duration_ms, reason="order_router_fill")
            except Exception:
                pass

        except Exception as e:
            logger.error(f"Buy fill handling error for intent {intent_id}: {e}", exc_info=True)
