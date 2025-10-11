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
from typing import Optional, Dict, Any

import config
from core.logging.logger import new_decision_id, new_client_order_id
from core.logging.debug_tracer import trace_function, trace_step, trace_error
from core.logging.adaptive_logger import track_error, track_guard_block, notify_trade_completed
from core.logging.adaptive_logger import guard_stats_record

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
                    return None
                else:
                    trace_step("sizing_passed", symbol=symbol)

            # 5a. Update Rolling Window and Drop Trigger
            trace_step("update_drop_trigger_system", symbol=symbol)

            # Initialize Rolling Window for symbol if needed
            if symbol not in self.engine.rolling_windows:
                from signals.rolling_window import RollingWindow
                lookback_s = getattr(config, 'DROP_TRIGGER_LOOKBACK_SECONDS', 60)
                self.engine.rolling_windows[symbol] = RollingWindow(lookback_s)

            # Add current price to rolling window
            now_ts = time.time()
            self.engine.rolling_windows[symbol].add(now_ts, current_price)

            # 5b. Signal Stabilization Check
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
                return None

            # 5c. Evaluate buy signal with DROP_TRIGGER logic
            trace_step("evaluate_buy_signal_start", symbol=symbol)
            buy_triggered, context = self.engine.buy_signal_service.evaluate_buy_signal(symbol, current_price)
            trace_step("evaluate_buy_signal_result", symbol=symbol, triggered=buy_triggered, context=context)

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

        try:
            # Calculate position size
            quote_budget = self._calculate_position_size(symbol, current_price, usdt_balance)
            if not quote_budget:
                return

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
            order = self._place_buy_order(symbol, amount, current_price, quote_budget, signal, decision_id, client_order_id)

            if order and order.get('status') == 'closed':
                self._handle_buy_fill(symbol, order, signal, decision_id)

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

        if self.engine.sizing_service:
            quote_budget = self.engine.sizing_service.calculate_position_size(
                symbol, current_price, usdt_balance
            )
            trace_step("position_size_calculated", symbol=symbol, quote_budget=quote_budget, method="sizing_service")
        else:
            # Simplified position sizing
            quote_budget = min(usdt_balance * 0.1, 100.0)  # 10% or $100 max
            trace_step("position_size_calculated", symbol=symbol, quote_budget=quote_budget, method="simplified")

        min_slot = getattr(config, 'MIN_SLOT_USDT', 10.0)
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
                        signal: str, decision_id: str, client_order_id: str):
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

        # Place buy order via Order Service - Mode-2 uses IOC, others use GTC
        from config import MODE, USE_IOC_FOR_MODE2
        use_ioc = (MODE == 2 and USE_IOC_FOR_MODE2)

        trace_step("placing_order", symbol=symbol, amount=amount, price=current_price,
                  client_order_id=client_order_id, use_ioc=use_ioc)

        if use_ioc:
            order = self.engine.order_service.place_limit_ioc(
                symbol=symbol,
                side="buy",
                amount=amount,
                price=current_price,
                client_order_id=client_order_id
            )
        else:
            # Use traditional GTC order for other modes
            order_cfg = {"tif": "GTC", "post_only": True}
            market_data = {"book": {"best_bid": current_price, "best_ask": current_price},
                          "filters": {"tickSize": 0.01, "stepSize": 0.001, "minNotional": 10.0}}
            order = self.engine.exchange_adapter.place_limit_buy(
                symbol, amount * current_price, order_cfg, market_data,
                meta={"decision_id": decision_id, "client_order_id": client_order_id}
            )

        trace_step("order_placed", symbol=symbol, order_id=order.get('id') if order else None,
                  status=order.get('status') if order else None)

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
            from order_flow import on_order_update
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
