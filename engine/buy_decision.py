#!/usr/bin/env python3
"""
Buy Decision Module

Contains:
- Buy signal evaluation logic
- Buy order execution
- Buy flow handling and tracking
"""

import time
import logging
from typing import Optional, Dict

import config
from core.logging.logger import new_decision_id, new_client_order_id
from core.logging.debug_tracer import trace_function, trace_step, trace_error
from core.logging.adaptive_logger import track_error, track_guard_block, notify_trade_completed
from core.logging.adaptive_logger import guard_stats_record

# Phase 1 Structured Logging
from core.trace_context import Trace, new_order_req_id
from core.logger_factory import DECISION_LOG, ORDER_LOG, log_event

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
        logger.debug(f"[DECISION_START] {symbol} @ {current_price:.8f} | market_health={market_health}")

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
                           extra={'event_type':'GUARD_BLOCK_SUMMARY','symbol':symbol,'failed_guards':failed_guards})

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
                print(f"\n🔥 BUY TRIGGER HIT: {symbol} @ {current_price:.6f} | "
                      f"Drop: {context.get('drop_pct', 0):.2f}% | "
                      f"Anchor: {context.get('anchor', 0):.6f}\n", flush=True)

                # Dashboard event
                try:
                    from ui.dashboard import emit_dashboard_event
                    emit_dashboard_event("BUY_TRIGGER", f"{symbol} @ ${current_price:.4f} (Drop: {context.get('drop_pct', 0):.2f}%)")
                except Exception:
                    pass

            # Phase 1: Log structured drop_trigger_eval event
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

            if buy_triggered:
                signal_reason = f"DROP_TRIGGER_MODE_{context['mode']}"

                # Track decision timing
                decision_time = time.time() - decision_start_time
                self.engine.monitoring.performance_metrics['decision_times'].append(decision_time)

                logger.info(f"[BUY_TRIGGERED] {symbol} @ {current_price:.8f} | reason={signal_reason}")

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

            logger.error(f"Error evaluating buy signal for {symbol}: {e}")
            return None

    @trace_function(include_args=True, include_result=False)
    def execute_buy_order(self, symbol: str, coin_data: Dict, current_price: float, signal: str):
        """Execute buy order via Order Service"""
        decision_id = self.engine.current_decision_id or new_decision_id()

        # CRITICAL: Log buy candidate for debugging
        usdt_balance = self.engine.portfolio.get_balance("USDT")
        trace_step("buy_candidate", symbol=symbol, price=current_price, budget=usdt_balance, signal=signal)
        logger.info(f"BUY CANDIDATE {symbol} 🔥 price={current_price:.10f} budget={usdt_balance:.2f} signal={signal}",
                   extra={'event_type':'BUY_CANDIDATE','symbol':symbol,'price':current_price,'budget':usdt_balance})

        # Console output for buy candidate visibility
        print(f"\n💰 BUY CANDIDATE: {symbol} @ {current_price:.6f} | Budget: ${usdt_balance:.2f} | Signal: {signal}\n", flush=True)

        # Dashboard event
        try:
            from ui.dashboard import emit_dashboard_event
            emit_dashboard_event("BUY_CANDIDATE", f"{symbol} @ ${current_price:.4f}")
        except Exception:
            pass

        try:
            # Calculate position size
            quote_budget = self._calculate_position_size(symbol, current_price, usdt_balance)
            if not quote_budget:
                return

            # Phase 1 (TODO 6): Risk Limits Check - BEFORE order placement
            try:
                from core.risk_limits import RiskLimitChecker
                from core.event_schemas import RiskLimitsEval

                risk_checker = RiskLimitChecker(self.engine.portfolio, config)
                all_passed, limit_checks = risk_checker.check_limits(symbol, quote_budget)

                risk_eval = RiskLimitsEval(
                    symbol=symbol,
                    limit_checks=limit_checks,
                    all_passed=all_passed,
                    blocking_limit=[c['limit'] for c in limit_checks if c['hit']][0] if not all_passed else None
                )

                with Trace(decision_id=decision_id):
                    log_event(DECISION_LOG(), "risk_limits_eval", **risk_eval.model_dump())

                if not all_passed:
                    blocking_limit = risk_eval.blocking_limit
                    logger.info(f"BUY BLOCKED {symbol} - Risk limit exceeded: {blocking_limit}")

                    self.engine.jsonl_logger.decision_end(
                        decision_id=decision_id,
                        symbol=symbol,
                        decision="blocked",
                        reason=f"risk_limit:{blocking_limit}",
                        limit_checks=limit_checks
                    )

                    # Log structured decision_outcome event
                    with Trace(decision_id=decision_id):
                        log_event(
                            DECISION_LOG(),
                            "decision_outcome",
                            symbol=symbol,
                            action="blocked",
                            reason=f"risk_limit:{blocking_limit}",
                            blocking_limit=blocking_limit
                        )

                    return

            except Exception as risk_check_error:
                # Don't fail order placement if risk check fails - log and continue
                logger.warning(f"Risk limits check failed for {symbol}, continuing with order: {risk_check_error}")

            # Generate client order ID for tracking
            client_order_id = new_client_order_id(decision_id, "buy")

            # Apply Symbol-specific Spread/Slippage Caps
            current_price = self._apply_spread_slippage_caps(symbol, coin_data, current_price, decision_id)
            if not current_price:
                return

            amount = quote_budget / current_price

            # CRITICAL: Log order details before placement
            logger.info(f"BUY ORDER {symbol} → amount={amount:.10f} value={quote_budget:.2f} price={current_price:.10f}",
                       extra={'event_type':'BUY_ORDER_PREP','symbol':symbol,'amount':amount,'value':quote_budget,'price':current_price})

            # Dry-Run Preview
            if not self._handle_dry_run_preview(symbol, current_price, amount, quote_budget, signal, decision_id):
                return

            # Place buy order
            order = self._place_buy_order(symbol, amount, current_price, quote_budget, signal, decision_id, client_order_id, coin_data)

            if order and order.get('status') == 'closed':
                self._handle_buy_fill(symbol, order, signal, decision_id)
            elif order and order.get('status') in ['canceled', 'cancelled', 'expired']:
                # Phase 3: Log order_done for cancelled/expired orders
                order_timestamp = order.get('timestamp', 0) / 1000 if order.get('timestamp') else None
                latency_ms_total = int((time.time() - order_timestamp) * 1000) if order_timestamp else None

                with Trace(decision_id=decision_id, client_order_id=client_order_id, exchange_order_id=order.get('id')):
                    log_event(
                        ORDER_LOG(),
                        "order_done",
                        symbol=symbol,
                        side="buy",
                        final_status=order.get('status'),
                        filled_qty=order.get('filled', 0),
                        latency_ms_total=latency_ms_total,
                        reason="ioc_not_filled" if order.get('status') == 'canceled' else "expired"
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
            logger.error(f"Buy order execution error for {symbol}: {e}")

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
            passed = quote_budget >= min_slot
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

        trace_step("budget_check", symbol=symbol, quote_budget=quote_budget, min_slot=min_slot)

        if quote_budget < min_slot:
            trace_step("insufficient_budget", symbol=symbol, quote_budget=quote_budget, min_slot=min_slot)
            logger.info(f"BUY SKIP {symbol} – insufficient budget: {quote_budget:.2f} < {min_slot:.2f}",
                       extra={'event_type':'BUY_SKIP_BUDGET','symbol':symbol,'quote_budget':quote_budget,'min_slot':min_slot})
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

    def _place_buy_order(self, symbol: str, amount: float, current_price: float, quote_budget: float,
                        signal: str, decision_id: str, client_order_id: str, coin_data: Dict = None):
        """Place buy order with appropriate strategy"""
        # Log order placement attempt
        order_start_time = time.time()
        self.engine.jsonl_logger.order_sent(
            decision_id=decision_id,
            symbol=symbol,
            side="buy",
            amount=amount,
            price=current_price,
            client_order_id=client_order_id,
            signal_reason=signal,
            quote_budget=quote_budget,
            timestamp=order_start_time
        )

        # Place buy order with IOC for aggressive fills (slight overpay but guaranteed execution)
        # Calculate aggressive price: ASK + premium for guaranteed fill
        bid = coin_data.get('bid', current_price) if coin_data else current_price
        ask = coin_data.get('ask', current_price) if coin_data else current_price
        premium_bps = getattr(config, 'BUY_LIMIT_PREMIUM_BPS', 10)

        # BUY aggressively: Limit price slightly above ASK (TAKER side)
        aggressive_price = ask * (1 + premium_bps / 10000.0)

        trace_step("placing_order", symbol=symbol, amount=amount, price=aggressive_price,
                  client_order_id=client_order_id, premium_bps=premium_bps,
                  original_price=current_price, bid=bid, ask=ask)

        # Phase 1: Log structured order events
        order_req_id = new_order_req_id()

        # Phase 2: Idempotency Check - Prevent duplicate orders
        from core.idempotency import get_idempotency_store

        try:
            idempotency_store = get_idempotency_store()
            existing_order_id = idempotency_store.register_order(
                order_req_id=order_req_id,
                symbol=symbol,
                side="buy",
                amount=amount,
                price=aggressive_price,
                client_order_id=client_order_id
            )

            if existing_order_id:
                # Duplicate detected - fetch and return existing order
                logger.warning(
                    f"Duplicate buy order detected for {symbol}, "
                    f"returning existing order {existing_order_id}"
                )

                try:
                    # Fetch existing order from exchange
                    existing_order = self.engine.exchange_adapter.fetch_order(existing_order_id, symbol)
                    trace_step("duplicate_order_returned", symbol=symbol, order_id=existing_order_id,
                              status=existing_order.get('status'))
                    return existing_order
                except Exception as fetch_error:
                    logger.error(f"Failed to fetch duplicate order {existing_order_id}: {fetch_error}")
                    # Continue with new order placement if fetch fails
                    pass

        except Exception as idempotency_error:
            # Don't fail order placement if idempotency check fails
            logger.error(f"Idempotency check failed for {symbol}: {idempotency_error}")

        # Phase 2: Client Order ID Deduplication - Check exchange for existing order
        try:
            from trading.orders import verify_order_not_duplicate

            existing_exchange_order = verify_order_not_duplicate(
                exchange=self.engine.exchange_adapter._exchange,
                client_order_id=client_order_id,
                symbol=symbol
            )

            if existing_exchange_order:
                # Duplicate found on exchange
                logger.warning(
                    f"Duplicate order detected on exchange for {symbol} "
                    f"(client_order_id: {client_order_id}, exchange_order_id: {existing_exchange_order.get('id')})"
                )
                trace_step("duplicate_on_exchange", symbol=symbol,
                          client_order_id=client_order_id,
                          exchange_order_id=existing_exchange_order.get('id'))
                return existing_exchange_order

        except Exception as dedup_error:
            # Don't fail order placement if deduplication check fails
            logger.debug(f"Client Order ID deduplication check failed for {symbol}: {dedup_error}")

        with Trace(decision_id=decision_id, order_req_id=order_req_id, client_order_id=client_order_id):
            # Log price snapshot before order
            if coin_data and 'bid' in coin_data and 'ask' in coin_data:
                spread = coin_data['ask'] - coin_data['bid']
                spread_bp = (spread / coin_data['ask']) * 10000 if coin_data['ask'] > 0 else 0
                log_event(
                    ORDER_LOG(),
                    "price_snapshot",
                    symbol=symbol,
                    bid=coin_data['bid'],
                    ask=coin_data['ask'],
                    spread=spread,
                    spread_bp=spread_bp,
                    mid_price=(coin_data['bid'] + coin_data['ask']) / 2
                )

            # Log order attempt
            log_event(
                ORDER_LOG(),
                "order_attempt",
                symbol=symbol,
                side="buy",
                type="limit",
                tif="IOC",
                price=aggressive_price,
                qty=amount,
                notional=amount * aggressive_price
            )

        # Use IOC for guaranteed fills
        try:
            order = self.engine.order_service.place_limit_ioc(
                symbol=symbol,
                side="buy",
                amount=amount,
                price=aggressive_price,
                client_order_id=client_order_id
            )

            # Phase 0: Check and log order fills (already done in order_service, but double-check for safety)
            if order:
                try:
                    from trading.orders import check_and_log_order_fills
                    check_and_log_order_fills(order, symbol)
                except Exception as fill_log_error:
                    logger.debug(f"Fill logging failed for buy order: {fill_log_error}")

            # Phase 2: Update idempotency store with exchange order ID
            if order and order.get('id'):
                try:
                    idempotency_store.update_order_status(
                        order_req_id=order_req_id,
                        exchange_order_id=order['id'],
                        status=order.get('status', 'open')
                    )
                except Exception as update_error:
                    logger.debug(f"Failed to update idempotency store: {update_error}")

            trace_step("order_placed", symbol=symbol, order_id=order.get('id') if order else None,
                      status=order.get('status') if order else None)

            # Phase 1: Log order acknowledgment
            with Trace(decision_id=decision_id, order_req_id=order_req_id, client_order_id=client_order_id):
                if order:
                    exchange_order_id = order.get('id')
                    with Trace(exchange_order_id=exchange_order_id):
                        log_event(
                            ORDER_LOG(),
                            "order_ack",
                            symbol=symbol,
                            exchange_order_id=exchange_order_id,
                            status=order.get('status'),
                            latency_ms=int((time.time() - order_start_time) * 1000),
                            exchange_raw=order
                        )

        except Exception as e:
            # Phase 1: Log order error
            with Trace(decision_id=decision_id, order_req_id=order_req_id, client_order_id=client_order_id):
                log_event(
                    ORDER_LOG(),
                    "order_error",
                    message=f"Order placement failed: {type(e).__name__}: {str(e)}",
                    symbol=symbol,
                    error_class=type(e).__name__,
                    error_message=str(e),
                    latency_ms=int((time.time() - order_start_time) * 1000),
                    level=logging.ERROR
                )
            raise

        # Track order latency
        order_latency = time.time() - order_start_time
        self.engine.monitoring.performance_metrics['order_latencies'].append(order_latency)

        if order:
            # Log order update
            self.engine.jsonl_logger.order_update(
                decision_id=decision_id,
                symbol=symbol,
                client_order_id=client_order_id,
                order_id=order.get('id'),
                status=order.get('status'),
                filled=order.get('filled', 0),
                remaining=order.get('remaining', 0),
                average_price=order.get('average'),
                order_latency_ms=order_latency * 1000,
                timestamp=time.time()
            )

        return order

    def _handle_buy_fill(self, symbol: str, order: Dict, signal: str, decision_id: str = None):
        """Handle buy order fill - create position"""
        decision_id = decision_id or new_decision_id()

        try:
            filled_amount = order.get('filled', 0)
            avg_price = order.get('average', 0)

            # Log order fill
            self.engine.jsonl_logger.order_filled(
                decision_id=decision_id,
                symbol=symbol,
                side="buy",
                client_order_id=order.get('clientOrderId'),
                order_id=order.get('id'),
                filled_amount=filled_amount,
                average_price=avg_price,
                total_cost=filled_amount * avg_price,
                signal_reason=signal,
                timestamp=time.time()
            )

            if filled_amount <= 0 or avg_price <= 0:
                return

            # Record fill in PnL Service with trade-based fee collection using cache
            buy_fee = order.get('fee', {}).get('cost', 0)
            if buy_fee == 0:
                # Fallback: get fees from trades with intelligent caching
                trades = order.get("trades") or []
                if not trades and order.get("id"):
                    try:
                        # Use trade cache for efficient API calls
                        from services.trade_cache import get_trade_cache
                        trade_cache = get_trade_cache()

                        trades = trade_cache.get_trades_cached(
                            symbol=symbol,
                            exchange_adapter=self.engine.exchange_adapter,
                            params={"orderId": order["id"]},
                            cache_ttl=60.0  # Cache for 1 minute
                        )
                    except Exception:
                        pass
                buy_fee = sum(t.get("fee", {}).get("cost", 0) for t in (trades or []))

            # Process fill in enhanced PnL tracker
            fee_quote = buy_fee if buy_fee > 0 else filled_amount * avg_price * getattr(config, 'TRADING_FEE_RATE', 0.001)
            self.engine.pnl_tracker.on_fill(symbol, "BUY", avg_price, filled_amount)

            # Create TRADE_FILL event for telemetry
            trade_fill_event = {
                "event_type": "TRADE_FILL",
                "symbol": symbol,
                "side": "BUY",
                "price": avg_price,
                "qty": filled_amount,
                "fee_quote": fee_quote,
                "ts": time.time()
            }

            # Calculate slippage if possible
            if order.get('price') and avg_price:
                slippage_bp = abs(avg_price - order['price']) / order['price'] * 10000.0
                trade_fill_event["slippage_bp"] = slippage_bp
                self.engine.rolling_stats.add_fill(trade_fill_event["ts"], slippage_bp)

            # Process fill event through order flow
            from core.utils.order_flow import on_order_update
            on_order_update(trade_fill_event, self.engine.pnl_tracker)

            # Log TRADE_FILL event
            logger.info("TRADE_FILL", extra=trade_fill_event)

            self.engine.pnl_service.record_fill(
                symbol=symbol,
                side="buy",
                quantity=filled_amount,
                avg_price=avg_price,
                fee_quote=buy_fee
            )

            # Create position
            position_data = {
                'symbol': symbol,
                'amount': filled_amount,
                'buying_price': avg_price,
                'time': time.time(),
                'signal': signal,
                'order_id': order['id'],
                'decision_id': decision_id  # Track decision ID for position
            }

            self.engine.positions[symbol] = position_data

            # Phase 3: Log position_opened event
            try:
                from core.event_schemas import PositionOpened
                from datetime import datetime

                position_opened = PositionOpened(
                    symbol=symbol,
                    qty=filled_amount,
                    avg_entry=avg_price,
                    notional=filled_amount * avg_price,
                    fee_accum=buy_fee,
                    opened_at=datetime.now().isoformat()
                )

                with Trace(decision_id=decision_id, exchange_order_id=order.get('id')):
                    log_event(DECISION_LOG(), "position_opened", **position_opened.model_dump())

            except Exception as e:
                logger.debug(f"Failed to log position_opened for {symbol}: {e}")

            # Add to portfolio with fee information for later net PnL calculation
            fee_per_unit = (buy_fee / filled_amount) if filled_amount > 0 else 0.0
            self.engine.portfolio.add_held_asset(symbol, {
                "amount": filled_amount,
                "entry_price": avg_price,
                "buy_fee_quote_per_unit": fee_per_unit,
                "buy_price": avg_price
            })

            # Log trade opening
            self.engine.jsonl_logger.trade_open(
                decision_id=decision_id,
                symbol=symbol,
                entry_price=avg_price,
                amount=filled_amount,
                total_cost=filled_amount * avg_price,
                signal_reason=signal,
                order_id=order['id'],
                timestamp=time.time()
            )

            # Phase 3: Log order_done event for completed order
            order_timestamp = order.get('timestamp', 0) / 1000 if order.get('timestamp') else None
            latency_ms_total = int((time.time() - order_timestamp) * 1000) if order_timestamp else None

            with Trace(decision_id=decision_id, client_order_id=order.get('clientOrderId'), exchange_order_id=order.get('id')):
                log_event(
                    ORDER_LOG(),
                    "order_done",
                    symbol=symbol,
                    side="buy",
                    final_status="filled",
                    filled_qty=filled_amount,
                    avg_price=avg_price,
                    total_cost=filled_amount * avg_price,
                    fee_quote=fee_quote,
                    latency_ms_total=latency_ms_total
                )

            # Notify adaptive logger of trade completion
            notify_trade_completed(symbol, "buy")

            # Add to session digest
            self.engine.session_digest['buys'].append({
                'sym': symbol, 'px': avg_price, 'qty': filled_amount,
                't': int(time.time()), 'signal': signal
            })

            # Format unified BUY notification message
            total_cost = filled_amount * avg_price
            buy_msg = f"🛒 BUY {symbol} @{avg_price:.6f} x{filled_amount:.6f} [{signal}] Cost: ${total_cost:.2f}"

            # Console output for order fill visibility
            print(f"\n✅ {buy_msg}\n", flush=True)

            # Dashboard event
            try:
                from ui.dashboard import emit_dashboard_event
                emit_dashboard_event("BUY_FILLED", f"{symbol} @ ${avg_price:.4f} x{filled_amount:.4f} (Cost: ${total_cost:.2f})")
            except Exception:
                pass

            # Terminal output (already exists)
            logger.info(f"BUY FILLED {symbol} @{avg_price:.6f} x{filled_amount:.6f} [{signal}]")

            # Telegram notification (if available)
            if hasattr(self.engine, 'telegram') and self.engine.telegram:
                try:
                    self.engine.telegram.send_message(buy_msg)
                except Exception as e:
                    logger.warning(f"Telegram BUY notification failed: {e}")

        except Exception as e:
            logger.error(f"Buy fill handling error: {e}")
