#!/usr/bin/env python3
"""
Position Management Module

Contains:
- Position management and updates
- Trailing stop updates
- Exit protection restoration
- Exit condition evaluation
- Unrealized PnL tracking
"""

import time
import logging
from typing import Dict

import config
from core.logging.logger import new_decision_id
from core.portfolio.risk_guards import atr_stop_hit, trailing_stop_hit

logger = logging.getLogger(__name__)


class PositionManager:
    """
    Handles all position management operations.

    Separates position management logic from main engine orchestration.
    """

    def __init__(self, engine):
        """
        Initialize with reference to main engine.

        Args:
            engine: Main TradingEngine instance for accessing services
        """
        self.engine = engine

    def manage_positions(self):
        """Manage all active positions"""
        with self.engine._lock:
            for symbol, data in list(self.engine.positions.items()):
                try:
                    current_price = self.engine.get_current_price(symbol)
                    if not current_price:
                        continue

                    # 1. Update trailing stops
                    if self.engine.config.enable_trailing_stops:
                        self.update_trailing_stops(symbol, data, current_price)

                    # 2. Restore missing exit protections
                    self.restore_exit_protections(symbol, data, current_price)

                    # 3. Evaluate exit conditions
                    if self.engine.config.enable_auto_exits:
                        self.evaluate_position_exits(symbol, data, current_price)

                    # 4. Update unrealized PnL
                    self.update_unrealized_pnl(symbol, data, current_price)

                except Exception as e:
                    logger.error(f"Position management error for {symbol}: {e}")

    def update_trailing_stops(self, symbol: str, data: Dict, current_price: float):
        """Update trailing stops via Trailing Manager"""
        try:
            if not data.get('enable_trailing', False):
                return

            # Update trailing stop through service
            updated_stop = self.engine.trailing_manager.update_trailing_stop(
                symbol, current_price, data
            )

            if updated_stop:
                # Place/update stop-loss order
                new_sl_order = self.engine.exit_manager.place_exit_protection(
                    symbol=symbol,
                    exit_type="STOP_LOSS",
                    amount=data.get('amount', 0),
                    target_price=updated_stop,
                    active_order_id=data.get('sl_order_id')
                )

                if new_sl_order:
                    data['sl_order_id'] = new_sl_order
                    data['sl_px'] = updated_stop

        except Exception as e:
            logger.error(f"Trailing stop update error for {symbol}: {e}")

    def restore_exit_protections(self, symbol: str, data: Dict, current_price: float):
        """Restore missing TP/SL orders via Exit Manager"""
        try:
            restored = self.engine.exit_manager.restore_exit_protections(
                symbol, data, current_price
            )
            if restored:
                logger.info(f"Exit protections restored for {symbol}")

        except Exception as e:
            logger.error(f"Exit protection restoration error for {symbol}: {e}")

    def evaluate_position_exits(self, symbol: str, data: Dict, current_price: float):
        """Evaluate exit conditions with deterministic guards and queue signals"""
        try:
            entry_price = data.get('buying_price', 0)
            if entry_price <= 0:
                return

            # Phase 1 (TODO 4): Percentage-based TP/SL Evaluation (HIGHEST PRIORITY)
            # Check these BEFORE ATR/Trailing stops as they are hard limits

            # Get TP/SL thresholds from config
            tp_threshold = getattr(config, 'TAKE_PROFIT_THRESHOLD', 1.005)  # Default: +0.5%
            sl_threshold = getattr(config, 'STOP_LOSS_THRESHOLD', 0.990)     # Default: -1.0%

            price_ratio = current_price / entry_price
            unrealized_pct = (price_ratio - 1.0) * 100

            # Check Take Profit
            if price_ratio >= tp_threshold:
                try:
                    from core.event_schemas import SellTriggerEval
                    from core.logger_factory import DECISION_LOG, log_event
                    from core.trace_context import Trace

                    tp_eval = SellTriggerEval(
                        symbol=symbol,
                        trigger="tp",
                        entry_price=entry_price,
                        current_price=current_price,
                        unrealized_pct=unrealized_pct,
                        threshold=tp_threshold,
                        hit=True,
                        reason="take_profit_hit"
                    )

                    decision_id = data.get('decision_id')
                    with Trace(decision_id=decision_id) if decision_id else Trace():
                        log_event(DECISION_LOG(), "sell_trigger_eval", **tp_eval.model_dump())

                except Exception as e:
                    logger.debug(f"Failed to log sell_trigger_eval (TP) for {symbol}: {e}")

                # Queue exit
                logger.info(f"[DECISION_END] {symbol} SELL | Take Profit Hit (+{unrealized_pct:.2f}%)")
                self.engine.jsonl_logger.decision_end(
                    decision_id=new_decision_id(),
                    symbol=symbol,
                    decision="sell",
                    reason="take_profit_hit",
                    unrealized_pct=unrealized_pct
                )
                success = self.engine.exit_manager.queue_exit_signal(
                    symbol=symbol,
                    reason="take_profit_hit",
                    position_data=data,
                    current_price=current_price
                )
                if success:
                    logger.info(f"Exit signal queued for {symbol}: take_profit_hit")
                return

            # Check Stop Loss
            if price_ratio <= sl_threshold:
                try:
                    from core.event_schemas import SellTriggerEval
                    from core.logger_factory import DECISION_LOG, log_event
                    from core.trace_context import Trace

                    sl_eval = SellTriggerEval(
                        symbol=symbol,
                        trigger="sl",
                        entry_price=entry_price,
                        current_price=current_price,
                        unrealized_pct=unrealized_pct,
                        threshold=sl_threshold,
                        hit=True,
                        reason="stop_loss_hit"
                    )

                    decision_id = data.get('decision_id')
                    with Trace(decision_id=decision_id) if decision_id else Trace():
                        log_event(DECISION_LOG(), "sell_trigger_eval", **sl_eval.model_dump())

                except Exception as e:
                    logger.debug(f"Failed to log sell_trigger_eval (SL) for {symbol}: {e}")

                # Queue exit
                logger.info(f"[DECISION_END] {symbol} SELL | Stop Loss Hit ({unrealized_pct:.2f}%)")
                self.engine.jsonl_logger.decision_end(
                    decision_id=new_decision_id(),
                    symbol=symbol,
                    decision="sell",
                    reason="stop_loss_hit",
                    unrealized_pct=unrealized_pct
                )
                success = self.engine.exit_manager.queue_exit_signal(
                    symbol=symbol,
                    reason="stop_loss_hit",
                    position_data=data,
                    current_price=current_price
                )
                if success:
                    logger.info(f"Exit signal queued for {symbol}: stop_loss_hit")
                return

            # 1. Enhanced Exit Guards with clear reasons (ATR/Trailing come after TP/SL)
            position_mock = type('Position', (), {
                'entry_price': entry_price,
                'last_price': current_price,
                'entry_time': data.get('entry_time', time.time()),
                'avg_entry': entry_price
            })()

            # ATR Stop Check
            atr = data.get('atr', 0.0)
            if atr > 0:
                hit, info = atr_stop_hit(position_mock, atr, getattr(config, 'ATR_SL_MULTIPLIER', 2.0))

                # Phase 3: Log sell_trigger_eval for ATR stop
                try:
                    from core.event_schemas import SellTriggerEval
                    from core.logger_factory import DECISION_LOG, log_event
                    from core.trace_context import Trace

                    entry_price = data.get('buying_price', 0)
                    unrealized_pct = ((current_price / entry_price) - 1.0) * 100 if entry_price > 0 else 0

                    atr_eval = SellTriggerEval(
                        symbol=symbol,
                        trigger="sl",
                        entry_price=entry_price,
                        current_price=current_price,
                        unrealized_pct=unrealized_pct,
                        threshold=info.get('stop_price') if hit else None,
                        hit=hit,
                        reason=info.get('reason') if hit else f"atr_stop_not_hit"
                    )

                    decision_id = data.get('decision_id')
                    with Trace(decision_id=decision_id) if decision_id else Trace():
                        log_event(DECISION_LOG(), "sell_trigger_eval", **atr_eval.model_dump())

                except Exception as e:
                    logger.debug(f"Failed to log sell_trigger_eval (ATR) for {symbol}: {e}")

                if hit:
                    logger.info(f"[DECISION_END] {symbol} SELL | {info['reason']}")
                    self.engine.jsonl_logger.decision_end(
                        decision_id=new_decision_id(),
                        symbol=symbol,
                        decision="sell",
                        reason=info["reason"],
                        exit_details=info
                    )
                    success = self.engine.exit_manager.queue_exit_signal(
                        symbol=symbol,
                        reason=info["reason"],
                        position_data=data,
                        current_price=current_price
                    )
                    if success:
                        logger.info(f"Exit signal queued for {symbol}: {info['reason']}")
                    return

            # Trailing Stop Check
            if data.get('peak_price'):
                trailing_state = type('TrailingState', (), {
                    'last_price': current_price,
                    'entry_price': data.get('buying_price', 0),
                    'activation_pct': getattr(config, 'TRAILING_ACTIVATION_PCT', 1.005),
                    'distance_pct': getattr(config, 'TRAILING_DISTANCE_PCT', 0.99),
                    'high_since_entry': data.get('peak_price', current_price)
                })()

                hit, info = trailing_stop_hit(trailing_state)

                # Phase 3: Log sell_trigger_eval for trailing stop
                try:
                    from core.event_schemas import SellTriggerEval
                    from core.logger_factory import DECISION_LOG, log_event
                    from core.trace_context import Trace

                    entry_price = data.get('buying_price', 0)
                    unrealized_pct = ((current_price / entry_price) - 1.0) * 100 if entry_price > 0 else 0

                    trailing_eval = SellTriggerEval(
                        symbol=symbol,
                        trigger="trailing",
                        entry_price=entry_price,
                        current_price=current_price,
                        unrealized_pct=unrealized_pct,
                        trailing_anchor=data.get('peak_price'),
                        threshold=info.get('stop_price') if hit else None,
                        hit=hit,
                        reason=info.get('reason') if hit else "trailing_not_hit"
                    )

                    decision_id = data.get('decision_id')
                    with Trace(decision_id=decision_id) if decision_id else Trace():
                        log_event(DECISION_LOG(), "sell_trigger_eval", **trailing_eval.model_dump())

                except Exception as e:
                    logger.debug(f"Failed to log sell_trigger_eval (Trailing) for {symbol}: {e}")

                if hit:
                    logger.info(f"[DECISION_END] {symbol} SELL | {info['reason']}")
                    self.engine.jsonl_logger.decision_end(
                        decision_id=new_decision_id(),
                        symbol=symbol,
                        decision="sell",
                        reason=info["reason"],
                        exit_details=info
                    )
                    success = self.engine.exit_manager.queue_exit_signal(
                        symbol=symbol,
                        reason=info["reason"],
                        position_data=data,
                        current_price=current_price
                    )
                    if success:
                        logger.info(f"Exit signal queued for {symbol}: {info['reason']}")
                    return

            # 2. Legacy Exit Manager evaluation
            exit_reasons = self.engine.exit_manager.evaluate_position_exits(
                symbol, data, current_price
            )

            # Queue signals for any triggered exits
            for reason in exit_reasons:
                success = self.engine.exit_manager.queue_exit_signal(
                    symbol=symbol,
                    reason=reason,
                    position_data=data,
                    current_price=current_price
                )

                if success:
                    logger.info(f"Exit signal queued for {symbol}: {reason}")

        except Exception as e:
            logger.error(f"Exit evaluation error for {symbol}: {e}")

    def update_unrealized_pnl(self, symbol: str, data: Dict, current_price: float):
        """Update unrealized PnL via PnL Service"""
        try:
            if not data.get('amount') or not data.get('buying_price'):
                return

            unrealized_pnl = self.engine.pnl_service.set_unrealized_position(
                symbol=symbol,
                quantity=data['amount'],
                avg_entry_price=data['buying_price'],
                current_price=current_price,
                entry_fee_per_unit=data.get('entry_fee_per_unit', 0)
            )

            data['unrealized_pnl'] = unrealized_pnl

            # Phase 1 (TODO 5): Log position_updated event
            try:
                from core.event_schemas import PositionUpdated
                from core.logger_factory import DECISION_LOG, log_event
                from core.trace_context import Trace

                entry_price = data['buying_price']
                unrealized_pct = ((current_price - entry_price) / entry_price) * 100 if entry_price > 0 else 0
                age_minutes = (time.time() - data.get('time', 0)) / 60 if data.get('time') else None

                position_updated = PositionUpdated(
                    symbol=symbol,
                    qty=data['amount'],
                    unrealized_pnl=unrealized_pnl,
                    unrealized_pct=unrealized_pct,
                    peak_price=data.get('peak_price'),
                    trailing_stop=data.get('trailing_stop'),
                    age_minutes=age_minutes
                )

                decision_id = data.get('decision_id')
                with Trace(decision_id=decision_id) if decision_id else Trace():
                    log_event(DECISION_LOG(), "position_updated", **position_updated.model_dump())

            except Exception as e:
                logger.debug(f"Failed to log position_updated for {symbol}: {e}")

        except Exception as e:
            logger.error(f"PnL update error for {symbol}: {e}")
