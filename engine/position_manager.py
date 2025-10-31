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

import logging
import threading
import time
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

        # FIX C2: Position-level locks for atomic switching
        self._position_locks = {}
        self._locks_lock = threading.RLock()

    def _get_position_lock(self, symbol: str):
        """
        FIX C2: Get or create position-specific lock for atomic operations.

        Thread-safe lock acquisition per symbol.
        """
        with self._locks_lock:
            if symbol not in self._position_locks:
                self._position_locks[symbol] = threading.RLock()
            return self._position_locks[symbol]

    def _get_position_data(self, symbol: str) -> Dict:
        """
        FIX H3: Get position data with Portfolio.positions as primary source.

        Reads from Portfolio.positions.meta first, then falls back to engine.positions.
        Merges data from both sources for backwards compatibility.

        Returns:
            Merged position data dict
        """
        # Primary source: Portfolio.positions
        portfolio_position = self.engine.portfolio.positions.get(symbol)
        position_data = {}

        if portfolio_position:
            # Build position_data from Portfolio.positions
            position_data = {
                'symbol': symbol,
                'amount': float(portfolio_position.qty),
                'buying_price': float(portfolio_position.avg_price),
                'time': portfolio_position.opened_ts,
                'state': portfolio_position.state
            }
            # Merge meta data
            position_data.update(portfolio_position.meta)

        # Fallback/Merge: engine.positions (legacy)
        legacy_data = self.engine.positions.get(symbol, {})
        if legacy_data:
            # Merge legacy data, but Portfolio.positions takes precedence
            for key, value in legacy_data.items():
                if key not in position_data:
                    position_data[key] = value

        return position_data if position_data else None

    def manage_positions(self):
        """Manage all active positions"""
        # FIX H3: Get positions from Portfolio.positions (authoritative source)
        portfolio_symbols = set(self.engine.portfolio.positions.keys())
        legacy_symbols = set(self.engine.positions.keys())
        all_symbols = portfolio_symbols | legacy_symbols

        logger.info(
            f"[POSITION_MGMT] Starting position management - "
            f"Portfolio: {len(portfolio_symbols)}, Legacy: {len(legacy_symbols)}, Total: {len(all_symbols)}",
            extra={'event_type': 'POSITION_MGMT_START'}
        )

        with self.engine._lock:
            for symbol in list(all_symbols):
                data = self._get_position_data(symbol)
                if not data:
                    logger.warning(
                        f"[POSITION_MGMT] No position data found for {symbol}",
                        extra={'event_type': 'POSITION_MGMT_NO_DATA', 'symbol': symbol}
                    )
                    continue
                try:
                    logger.debug(f"[POSITION_MGMT] Processing {symbol}", extra={'event_type': 'POSITION_MGMT_SYMBOL'})

                    current_price = self.engine.get_current_price(symbol)
                    if not current_price:
                        logger.warning(f"[POSITION_MGMT] No price available for {symbol}",
                                     extra={'event_type': 'POSITION_MGMT_NO_PRICE', 'symbol': symbol})
                        continue

                    logger.debug(f"[POSITION_MGMT] {symbol} current_price={current_price}",
                               extra={'event_type': 'POSITION_MGMT_PRICE', 'symbol': symbol, 'price': current_price})

                    # 1. Update trailing stops
                    if self.engine.config.enable_trailing_stops:
                        logger.debug(f"[POSITION_MGMT] Updating trailing stops for {symbol}",
                                   extra={'event_type': 'POSITION_MGMT_TRAILING'})
                        self.update_trailing_stops(symbol, data, current_price)

                    # 2. Dynamic TP/SL Switching based on PnL
                    logger.debug(f"[POSITION_MGMT] Checking dynamic TP/SL switch for {symbol}",
                               extra={'event_type': 'POSITION_MGMT_DYNAMIC_SWITCH'})
                    self.manage_dynamic_tp_sl_switch(symbol, data, current_price)

                    # 3. Restore missing exit protections
                    logger.debug(f"[POSITION_MGMT] Restoring exit protections for {symbol}",
                               extra={'event_type': 'POSITION_MGMT_PROTECTIONS'})
                    self.restore_exit_protections(symbol, data, current_price)

                    # 4. Evaluate exit conditions
                    if self.engine.config.enable_auto_exits:
                        logger.info(f"[POSITION_MGMT] Evaluating exits for {symbol} (enable_auto_exits=True)",
                                  extra={'event_type': 'POSITION_MGMT_EXIT_EVAL_START', 'symbol': symbol})
                        self.evaluate_position_exits(symbol, data, current_price)
                    else:
                        logger.warning(f"[POSITION_MGMT] Exit evaluation SKIPPED for {symbol} (enable_auto_exits=False)",
                                     extra={'event_type': 'POSITION_MGMT_EXIT_EVAL_DISABLED', 'symbol': symbol})

                    # 4. Update unrealized PnL
                    logger.debug(f"[POSITION_MGMT] Updating PnL for {symbol}",
                               extra={'event_type': 'POSITION_MGMT_PNL'})
                    self.update_unrealized_pnl(symbol, data, current_price)

                except Exception as e:
                    logger.error(f"Position management error for {symbol}: {e}", exc_info=True)

    def manage_dynamic_tp_sl_switch(self, symbol: str, data: Dict, current_price: float):
        """
        Dynamically switch between TP and SL orders based on unrealized PnL.

        FIX C2: Uses position-level locking for atomic state transitions.

        Logic:
        - If PnL goes negative (< SWITCH_TO_SL_THRESHOLD): Cancel TP, place SL
        - If PnL goes positive (> SWITCH_TO_TP_THRESHOLD): Cancel SL, place TP
        - Respects cooldown period between switches
        """
        # FIX C2: Acquire position-level lock for atomic switching
        with self._get_position_lock(symbol):
            try:
                entry_price = data.get('buying_price', 0)
                if not entry_price or entry_price <= 0:
                    return

                # Calculate current PnL ratio
                pnl_ratio = current_price / entry_price
                pnl_pct = (pnl_ratio - 1.0) * 100

                # Get config thresholds
                switch_to_sl_threshold = getattr(config, 'SWITCH_TO_SL_THRESHOLD', 0.995)  # -0.5%
                switch_to_tp_threshold = getattr(config, 'SWITCH_TO_TP_THRESHOLD', 1.002)  # +0.2%
                switch_cooldown_s = getattr(config, 'SWITCH_COOLDOWN_S', 20)

                # Check cooldown
                last_switch_time = data.get('last_protection_switch_time', 0)
                time_since_last_switch = time.time() - last_switch_time

                if time_since_last_switch < switch_cooldown_s:
                    # Still in cooldown
                    logger.debug(
                        f"Switch cooldown active for {symbol}: "
                        f"{time_since_last_switch:.1f}s < {switch_cooldown_s}s | "
                        f"Skipping switch request",
                        extra={
                            'event_type': 'SWITCH_COOLDOWN_ACTIVE',
                            'symbol': symbol,
                            'time_since_last_s': time_since_last_switch,
                            'cooldown_s': switch_cooldown_s
                        }
                    )
                    return

                # Get current protection state (re-check inside lock!)
                current_protection = data.get('active_protection_type', 'TP')  # Default to TP

                # Check for intermediate states (switching in progress)
                if current_protection in ['SWITCHING_TO_SL', 'SWITCHING_TO_TP']:
                    logger.debug(
                        f"Switch already in progress for {symbol}: {current_protection}",
                        extra={'event_type': 'SWITCH_IN_PROGRESS', 'symbol': symbol}
                    )
                    return

                has_tp_order = bool(data.get('tp_order_id'))
                has_sl_order = bool(data.get('sl_order_id'))

                # Calculate TP and SL prices
                tp_threshold = getattr(config, 'TAKE_PROFIT_THRESHOLD', 1.005)
                sl_threshold = getattr(config, 'STOP_LOSS_THRESHOLD', 0.990)

                tp_price = entry_price * tp_threshold
                sl_price = entry_price * sl_threshold
                amount = data.get('amount', 0)

                # SWITCH TO SL: Price went negative
                if pnl_ratio < switch_to_sl_threshold and current_protection == 'TP':
                    # FIX C2: Set intermediate state FIRST (atomic transition)
                    data['active_protection_type'] = 'SWITCHING_TO_SL'

                    logger.warning(
                        f"PROTECTION SWITCH: {symbol} | TP → SL | "
                        f"Entry={entry_price:.8f} | Current={current_price:.8f} | "
                        f"PnL={pnl_pct:+.2f}% | Threshold={switch_to_sl_threshold}",
                        extra={
                            'event_type': 'PROTECTION_SWITCH_TO_SL',
                            'symbol': symbol,
                            'pnl_ratio': pnl_ratio,
                            'pnl_pct': pnl_pct
                        }
                    )

                    try:
                        # Cancel TP order if exists
                        if has_tp_order and data.get('tp_order_id'):
                            try:
                                self.engine.order_service.cancel_order(
                                    data['tp_order_id'], symbol, reason="switch_to_sl"
                                )
                                data['tp_order_id'] = None
                                data['tp_px'] = None
                            except Exception as cancel_err:
                                logger.error(f"Failed to cancel TP order for {symbol}: {cancel_err}")
                                # Rollback intermediate state
                                data['active_protection_type'] = 'TP'
                                return

                        # Place SL order
                        sl_order_id = self.engine.exit_manager.place_exit_protection(
                            symbol=symbol,
                            exit_type="STOP_LOSS",
                            amount=amount,
                            target_price=sl_price,
                            active_order_id=data.get('sl_order_id')
                        )

                        if sl_order_id:
                            # FIX H3: Update BOTH state locations
                            # FIX C2: Set final state only after success
                            data['sl_order_id'] = sl_order_id
                            data['sl_px'] = sl_price
                            data['active_protection_type'] = 'SL'  # Final state
                            data['last_protection_switch_time'] = time.time()

                            # Update Portfolio.positions.meta
                            portfolio_position = self.engine.portfolio.positions.get(symbol)
                            if portfolio_position:
                                portfolio_position.meta['sl_order_id'] = sl_order_id
                                portfolio_position.meta['sl_px'] = sl_price
                                portfolio_position.meta['active_protection_type'] = 'SL'
                                portfolio_position.meta['last_protection_switch_time'] = time.time()

                            logger.info(
                                f"SWITCH SUCCESS: SL order placed for {symbol} @ {sl_price:.8f} (Order ID: {sl_order_id})",
                                extra={'event_type': 'SL_ORDER_PLACED', 'symbol': symbol}
                            )
                        else:
                            # SL placement failed - rollback
                            logger.error(f"SL order placement failed for {symbol}, rolling back to TP")
                            data['active_protection_type'] = 'TP'

                    except Exception as switch_err:
                        # Rollback to previous state on any error
                        logger.error(f"Switch to SL failed for {symbol}: {switch_err}", exc_info=True)
                        data['active_protection_type'] = 'TP'

                # SWITCH TO TP: Price went positive again
                elif pnl_ratio > switch_to_tp_threshold and current_protection == 'SL':
                    # FIX C2: Set intermediate state FIRST (atomic transition)
                    data['active_protection_type'] = 'SWITCHING_TO_TP'

                    logger.info(
                        f"PROTECTION SWITCH: {symbol} | SL → TP | "
                        f"Entry={entry_price:.8f} | Current={current_price:.8f} | "
                        f"PnL={pnl_pct:+.2f}% | Threshold={switch_to_tp_threshold}",
                        extra={
                            'event_type': 'PROTECTION_SWITCH_TO_TP',
                            'symbol': symbol,
                            'pnl_ratio': pnl_ratio,
                            'pnl_pct': pnl_pct
                        }
                    )

                    try:
                        # Cancel SL order if exists
                        if has_sl_order and data.get('sl_order_id'):
                            try:
                                self.engine.order_service.cancel_order(
                                    data['sl_order_id'], symbol, reason="switch_to_tp"
                                )
                                data['sl_order_id'] = None
                                data['sl_px'] = None
                            except Exception as cancel_err:
                                logger.error(f"Failed to cancel SL order for {symbol}: {cancel_err}")
                                # Rollback intermediate state
                                data['active_protection_type'] = 'SL'
                                return

                        # Place TP order
                        tp_order_id = self.engine.exit_manager.place_exit_protection(
                            symbol=symbol,
                            exit_type="TAKE_PROFIT",
                            amount=amount,
                            target_price=tp_price,
                            active_order_id=data.get('tp_order_id')
                        )

                        if tp_order_id:
                            # FIX H3: Update BOTH state locations
                            # FIX C2: Set final state only after success
                            data['tp_order_id'] = tp_order_id
                            data['tp_px'] = tp_price
                            data['active_protection_type'] = 'TP'  # Final state
                            data['last_protection_switch_time'] = time.time()

                            # Update Portfolio.positions.meta
                            portfolio_position = self.engine.portfolio.positions.get(symbol)
                            if portfolio_position:
                                portfolio_position.meta['tp_order_id'] = tp_order_id
                                portfolio_position.meta['tp_px'] = tp_price
                                portfolio_position.meta['active_protection_type'] = 'TP'
                                portfolio_position.meta['last_protection_switch_time'] = time.time()

                            logger.info(
                                f"SWITCH SUCCESS: TP order placed for {symbol} @ {tp_price:.8f} (Order ID: {tp_order_id})",
                                extra={'event_type': 'TP_ORDER_PLACED', 'symbol': symbol}
                            )
                        else:
                            # TP placement failed - rollback
                            logger.error(f"TP order placement failed for {symbol}, rolling back to SL")
                            data['active_protection_type'] = 'SL'

                    except Exception as switch_err:
                        # Rollback to previous state on any error
                        logger.error(f"Switch to TP failed for {symbol}: {switch_err}", exc_info=True)
                        data['active_protection_type'] = 'SL'

            except Exception as e:
                logger.error(f"Dynamic TP/SL switch error for {symbol}: {e}", exc_info=True)

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
                        reason=info.get('reason') if hit else "atr_stop_not_hit"
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
