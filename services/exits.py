"""
Exit Management Service (Drop 4)
Centralized exit logic extraction from engine.py
"""

import time
import logging
from typing import Dict, List, Optional, Any, Tuple
from threading import RLock
from dataclasses import dataclass, asdict

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

            # Emergency Sell (TTL Timeout)
            if context.elapsed_minutes >= self.trade_ttl_min:
                reasons.append("TTL_EMERGENCY")

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
            try:
                self._statistics['exits_placed'] += 1

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
                    return result

                # Escalation strategy if limit IOC fails
                if not self.never_market_sells:
                    result = self._try_market_exit(context, reason)
                    if result.success:
                        self._statistics['market_fallbacks'] += 1
                        self._statistics['exits_filled'] += 1
                        return result
                else:
                    # Use IOC fallback instead of market
                    result = self._try_ioc_fallback(context, reason)
                    if result.success:
                        self._statistics['ioc_fallbacks'] += 1
                        self._statistics['exits_filled'] += 1
                        return result

                self._statistics['exits_failed'] += 1
                return ExitResult(
                    success=False,
                    reason=reason,
                    error="All exit strategies failed"
                )

            except Exception as e:
                self._statistics['exits_failed'] += 1
                logger.error(f"Exit execution error for {context.symbol}: {e}")
                return ExitResult(
                    success=False,
                    reason=reason,
                    error=str(e)
                )

    def _try_limit_ioc_exit(self, context: ExitContext, reason: str) -> ExitResult:
        """Try limit IOC exit order with aggressive BID-based pricing"""
        try:
            # Get BID price from ticker for aggressive SELL pricing
            # (consistent with BUY using ASK + premium)
            bid = context.current_price  # Default fallback

            try:
                # Fetch ticker to get real BID price
                ticker = self.exchange_adapter.fetch_ticker(context.symbol)
                if ticker and 'bid' in ticker and ticker['bid']:
                    bid = ticker['bid']
                    logger.debug(f"Using BID price for exit: {bid} (current: {context.current_price})")
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
            return ExitResult(success=False, reason=reason, error=str(e))

    def _try_market_exit(self, context: ExitContext, reason: str) -> ExitResult:
        """Try market exit order"""
        try:
            order = self.order_service.place_market_ioc(
                symbol=context.symbol,
                side="sell",
                amount=context.amount,
                client_order_id=f"market_exit_{reason}_{int(time.time())}"
            )

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

    def place_or_replace_exit_protection(self, symbol: str, exit_type: str,
                                       amount: float, target_price: float,
                                       active_order_id: Optional[str] = None) -> Optional[str]:
        """Place or replace exit protection order (TP/SL)"""
        with self._lock:
            try:
                # Cancel existing order if present
                if active_order_id:
                    self.order_service.cancel_order(active_order_id, symbol)

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

    def process_exit_signals(self, max_per_cycle: int = 5) -> int:
        """Process pending exit signals from signal queue"""
        with self._lock:
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
                    result = self.order_manager.execute_exit_order(context, signal['signal_type'])

                    if result.success:
                        logger.info(f"Exit executed for {signal['symbol']}: {signal['signal_type']}")
                    else:
                        logger.warning(f"Exit failed for {signal['symbol']}: {result.error}")

                    processed_count += 1

                except Exception as e:
                    logger.error(f"Exit signal processing error: {e}")
                    processed_count += 1

            return processed_count

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
            # Use the SignalManager's add_exit_signal method
            self.signal_manager.add_exit_signal(
                symbol=symbol,
                signal_type=reason,
                reason=reason,
                current_price=current_price,
                **position_data
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