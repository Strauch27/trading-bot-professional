#!/usr/bin/env python3
"""
Exit Handler Module

Contains:
- Exit signal processing
- Exit fill handling
- Position cleanup
- Manual exit operations
"""

import logging
import time

from services import ExitResult

logger = logging.getLogger(__name__)


class ExitHandler:
    """
    Handles all exit-related operations.

    Separates exit handling logic from main engine orchestration.
    """

    def __init__(self, engine):
        """
        Initialize with reference to main engine.

        Args:
            engine: Main TradingEngine instance for accessing services
        """
        self.engine = engine

    def process_exit_signals(self):
        """Process pending exit signals via Exit Manager"""
        try:
            results = self.engine.exit_manager.process_exit_signals(max_per_cycle=5)
            handled = 0

            for signal, result in results:
                if not result:
                    continue

                symbol = signal.get('symbol')
                if result.success and symbol:
                    reason = result.reason or signal.get('type') or signal.get('signal_type') or "EXIT"
                    self.handle_exit_fill(symbol, result, reason)
                    handled += 1

            if results:
                logger.debug(f"Processed {len(results)} exit signals (handled={handled})")

        except Exception as e:
            logger.error(f"Exit signal processing error: {e}")

    def exit_position(self, symbol: str, reason: str = "MANUAL_EXIT") -> bool:
        """Exit position manually via Exit Manager"""
        try:
            with self.engine._lock:
                if symbol not in self.engine.positions:
                    logger.warning(f"No position found for {symbol}")
                    return False

                position_data = self.engine.positions[symbol]
                current_price = self.engine.get_current_price(symbol)

                if not current_price:
                    logger.error(f"Cannot get current price for {symbol}")
                    return False

                # Execute immediate exit via Exit Manager
                result = self.engine.exit_manager.execute_immediate_exit(
                    symbol=symbol,
                    position_data=position_data,
                    current_price=current_price,
                    reason=reason
                )

                if result.success:
                    self.handle_exit_fill(symbol, result, reason)
                    return True
                else:
                    logger.error(f"Manual exit failed for {symbol}: {result.error}")
                    return False

        except Exception as e:
            logger.error(f"Manual exit error for {symbol}: {e}")
            return False

    def handle_exit_fill(self, symbol: str, result: ExitResult, reason: str):
        """Handle exit order fill - remove position"""
        try:
            if symbol not in self.engine.positions:
                return

            position_data = self.engine.positions[symbol]

            # Record exit in PnL Service with trade-based fee collection using cache
            exit_fee = 0.0
            if hasattr(result, 'order') and result.order:
                order = result.order
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
                exit_fee = sum(t.get("fee", {}).get("cost", 0) for t in (trades or []))

            realized_pnl = self.engine.pnl_service.record_fill(
                symbol=symbol,
                side="sell",
                quantity=result.filled_amount,
                avg_price=result.avg_price,
                fee_quote=exit_fee,
                entry_price=position_data.get('buying_price')
            )

            # Phase 3: Log position_closed event
            try:
                from core.event_schemas import PositionClosed
                from core.logger_factory import DECISION_LOG, ORDER_LOG, log_event
                from core.trace_context import Trace

                entry_price = position_data.get('buying_price', 0)
                entry_time = position_data.get('time', 0)
                duration_minutes = (time.time() - entry_time) / 60 if entry_time > 0 else None

                realized_pnl_pct = ((result.avg_price - entry_price) / entry_price) * 100 if entry_price > 0 else 0

                # Get buy fee from position if available
                buy_fee = position_data.get('buy_fee', 0) if position_data else 0
                total_fees = buy_fee + exit_fee

                position_closed = PositionClosed(
                    symbol=symbol,
                    qty_closed=result.filled_amount,
                    avg_entry=entry_price,
                    exit_price=result.avg_price,
                    realized_pnl_usdt=realized_pnl,
                    realized_pnl_pct=realized_pnl_pct,
                    fee_total=total_fees,
                    duration_minutes=duration_minutes,
                    reason=reason
                )

                decision_id = position_data.get('decision_id')
                with Trace(decision_id=decision_id) if decision_id else Trace():
                    log_event(DECISION_LOG(), "position_closed", **position_closed.model_dump())

            except Exception as e:
                logger.debug(f"Failed to log position_closed for {symbol}: {e}")

            # Phase 0: Log order_done event for sell order
            try:
                from core.logger_factory import ORDER_LOG, log_event
                from core.trace_context import Trace

                # Calculate order latency if order timestamp available
                order_timestamp = None
                exchange_order_id = None
                if hasattr(result, 'order') and result.order:
                    order_timestamp = result.order.get('timestamp', 0) / 1000 if result.order.get('timestamp') else None
                    exchange_order_id = result.order.get('id')
                elif result.order_id:
                    exchange_order_id = result.order_id

                latency_ms_total = int((time.time() - order_timestamp) * 1000) if order_timestamp else None

                decision_id = position_data.get('decision_id')
                with Trace(decision_id=decision_id, exchange_order_id=exchange_order_id) if decision_id or exchange_order_id else Trace():
                    log_event(
                        ORDER_LOG(),
                        "order_done",
                        symbol=symbol,
                        side="sell",
                        final_status="filled",
                        filled_qty=result.filled_amount,
                        avg_price=result.avg_price,
                        total_proceeds=result.filled_amount * result.avg_price if result.avg_price else 0,
                        fee_quote=exit_fee,
                        latency_ms_total=latency_ms_total,
                        reason=reason
                    )

            except Exception as e:
                logger.debug(f"Failed to log order_done for sell order {symbol}: {e}")

            # Remove/update position store
            del self.engine.positions[symbol]

            # Synchronize PortfolioManager holdings so UI/portfolio views update
            try:
                portfolio = getattr(self.engine, 'portfolio', None)
                if portfolio and hasattr(portfolio, 'held_assets'):
                    asset = portfolio.held_assets.get(symbol)
                    if asset:
                        remaining = float(asset.get('amount') or 0.0) - float(result.filled_amount or 0.0)
                        if remaining <= 1e-9:
                            portfolio.remove_held_asset(symbol)
                        else:
                            portfolio.update_held_asset(symbol, {"amount": max(0.0, remaining)})
            except Exception as portfolio_error:
                logger.debug(f"Failed to sync portfolio after exit for {symbol}: {portfolio_error}")

            # Notify BuySignalService of trade completion (for Mode 4 anchor reset)
            self.engine.buy_signal_service.on_trade_completed(symbol)

            # Add to session digest
            self.engine.session_digest['sells'].append({
                'sym': symbol, 'px': result.avg_price, 'qty': result.filled_amount,
                't': int(time.time()), 'reason': reason, 'pnl': realized_pnl
            })

            # Get decision_id from position_data for tracing
            decision_id = position_data.get('decision_id') if position_data else None
            logger.info(f"SELL FILLED {symbol} @{result.avg_price:.6f} x{result.filled_amount:.6f} "
                       f"[{reason}] PnL: {realized_pnl:.2f}",
                       extra={'decision_id': decision_id} if decision_id else {})

            # Dashboard event
            try:
                from ui.dashboard import emit_dashboard_event
                pnl_sign = "[+]" if realized_pnl >= 0 else "[-]"
                emit_dashboard_event("SELL_FILLED", f"{pnl_sign} {symbol} @ ${result.avg_price:.4f} | PnL: ${realized_pnl:.2f}")
            except Exception:
                pass

        except Exception as e:
            logger.error(f"Exit fill handling error: {e}")

    def on_exit_filled_event(self, **payload):
        """
        Event handler for EXIT_FILLED events from loggingx
        Provides unified PnL handling for Terminal and Telegram output
        """
        try:
            symbol = payload.get('symbol')
            if not symbol:
                return

            # Get position data for PnL calculation
            position_data = self.engine.positions.get(symbol)
            if not position_data:
                return

            # Extract exit data from payload (unified schema support)
            exit_price = payload.get('fill_price') or payload.get('avg_exit_price', 0)
            exit_qty = payload.get('fill_qty') or payload.get('qty', 0)
            exit_reason = payload.get('reason', 'UNKNOWN')

            if exit_price <= 0 or exit_qty <= 0:
                return

            # Calculate PnL via PnL Service
            entry_price = position_data.get('buying_price', 0)
            if entry_price > 0:
                # Extract fees from payload if available
                exit_fees = payload.get('fees', 0) or payload.get('sell_fees_quote', 0)
                entry_fees = payload.get('buy_fees_alloc_quote', 0)

                realized_pnl = self.engine.pnl_service.calculate_realized_pnl(
                    entry_price=entry_price,
                    exit_price=exit_price,
                    quantity=exit_qty,
                    entry_fees=entry_fees,
                    exit_fees=exit_fees
                )

                pnl_pct = ((exit_price - entry_price) / entry_price) * 100.0

                # Format unified message
                pnl_sign = "[+]" if realized_pnl >= 0 else "[-]"
                pnl_msg = (f"{pnl_sign} EXIT {symbol} @{exit_price:.6f} x{exit_qty:.6f} "
                          f"[{exit_reason}] PnL: ${realized_pnl:.2f} ({pnl_pct:+.2f}%)")

                # Terminal output (always)
                print(f"\n{pnl_msg}")
                logger.info(pnl_msg)

                # Telegram notification (if available)
                if hasattr(self.engine, 'telegram') and self.engine.telegram:
                    try:
                        self.engine.telegram.send_message(pnl_msg)
                    except Exception as e:
                        logger.warning(f"Telegram notification failed: {e}")

                # Update PnL service with the exit
                self.engine.pnl_service.record_fill(
                    symbol=symbol,
                    side="sell",
                    quantity=exit_qty,
                    avg_price=exit_price,
                    fee_quote=0.0,
                    entry_price=entry_price
                )

        except Exception as e:
            # Never crash on event handling
            logger.error(f"EXIT_FILLED event handler error: {e}")
