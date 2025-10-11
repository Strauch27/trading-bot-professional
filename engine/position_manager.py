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
from logger import new_decision_id
from risk_guards import atr_stop_hit, trailing_stop_hit

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
            # 1. Enhanced Exit Guards with clear reasons
            position_mock = type('Position', (), {
                'entry_price': data.get('buying_price', 0),
                'last_price': current_price,
                'entry_time': data.get('entry_time', time.time()),
                'avg_entry': data.get('buying_price', 0)
            })()

            # ATR Stop Check
            atr = data.get('atr', 0.0)
            if atr > 0:
                hit, info = atr_stop_hit(position_mock, atr, getattr(config, 'ATR_SL_MULTIPLIER', 2.0))
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

        except Exception as e:
            logger.error(f"PnL update error for {symbol}: {e}")
