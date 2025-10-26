#!/usr/bin/env python3
"""
Partial fill handling and accumulation
"""

import logging
import time

from core.fsm.fsm_events import EventContext, FSMEvent
from core.fsm.state_data import OrderContext

logger = logging.getLogger(__name__)


class PartialFillHandler:
    """
    Handles partial fill accumulation.

    Tracks:
    - Cumulative quantity filled
    - Average fill price (weighted)
    - Total fees
    - Fill trades
    """

    def accumulate_fill(
        self,
        order_ctx: OrderContext,
        fill_qty: float,
        fill_price: float,
        fill_fee: float,
        trade_id: str
    ) -> None:
        """
        Accumulate a partial fill into order context.

        Updates:
        - cumulative_qty
        - avg_price (weighted average)
        - total_fees
        - fill_trades list
        """
        # Update cumulative quantity
        previous_qty = order_ctx.cumulative_qty
        new_qty = previous_qty + fill_qty

        # Update weighted average price
        if previous_qty > 0:
            # Weighted average: (old_qty * old_price + new_qty * new_price) / total_qty
            order_ctx.avg_price = (
                (previous_qty * order_ctx.avg_price + fill_qty * fill_price) / new_qty
            )
        else:
            order_ctx.avg_price = fill_price

        order_ctx.cumulative_qty = new_qty
        order_ctx.total_fees += fill_fee

        # Record trade
        order_ctx.fill_trades.append({
            'trade_id': trade_id,
            'qty': fill_qty,
            'price': fill_price,
            'fee': fill_fee,
            'timestamp': time.time()
        })

        logger.debug(
            f"Partial fill accumulated: {fill_qty:.6f} @ {fill_price:.6f}, "
            f"cumulative: {new_qty:.6f} @ {order_ctx.avg_price:.6f}, "
            f"fill_ratio: {order_ctx.get_fill_ratio():.2%}"
        )

    def is_fully_filled(self, order_ctx: OrderContext, tolerance: float = 0.001) -> bool:
        """
        Check if order is fully filled.

        Considers floating point tolerance (default 0.1% threshold).

        Args:
            order_ctx: Order context
            tolerance: Fill tolerance (0.001 = 99.9% filled counts as complete)

        Returns:
            True if order is considered fully filled
        """
        if order_ctx.target_qty == 0:
            return False

        fill_ratio = order_ctx.cumulative_qty / order_ctx.target_qty
        is_filled = fill_ratio >= (1.0 - tolerance)

        if is_filled:
            logger.debug(
                f"Order fully filled: {order_ctx.cumulative_qty:.6f}/{order_ctx.target_qty:.6f} "
                f"({fill_ratio:.2%})"
            )

        return is_filled

    def get_remaining_qty(self, order_ctx: OrderContext) -> float:
        """Get remaining quantity to be filled"""
        return max(0.0, order_ctx.target_qty - order_ctx.cumulative_qty)

    def get_fill_stats(self, order_ctx: OrderContext) -> dict:
        """Get detailed fill statistics"""
        if order_ctx.target_qty == 0:
            return {
                'fill_ratio': 0.0,
                'remaining_qty': 0.0,
                'num_trades': len(order_ctx.fill_trades),
                'avg_price': 0.0,
                'total_fees': 0.0
            }

        return {
            'fill_ratio': order_ctx.cumulative_qty / order_ctx.target_qty,
            'remaining_qty': self.get_remaining_qty(order_ctx),
            'cumulative_qty': order_ctx.cumulative_qty,
            'target_qty': order_ctx.target_qty,
            'num_trades': len(order_ctx.fill_trades),
            'avg_price': order_ctx.avg_price,
            'total_fees': order_ctx.total_fees,
            'is_fully_filled': self.is_fully_filled(order_ctx)
        }

    def create_fill_event(
        self,
        symbol: str,
        order_ctx: OrderContext,
        is_buy: bool,
        tolerance: float = 0.001
    ) -> EventContext:
        """
        Create appropriate fill event based on completion status.

        Returns:
            BUY_ORDER_FILLED/SELL_ORDER_FILLED if fully filled,
            BUY_ORDER_PARTIAL/SELL_ORDER_PARTIAL if partial
        """
        is_fully_filled = self.is_fully_filled(order_ctx, tolerance=tolerance)

        if is_fully_filled:
            event_type = FSMEvent.BUY_ORDER_FILLED if is_buy else FSMEvent.SELL_ORDER_FILLED
            logger.info(
                f"Order fully filled: {symbol} {'BUY' if is_buy else 'SELL'} "
                f"{order_ctx.cumulative_qty:.6f} @ {order_ctx.avg_price:.6f}"
            )
        else:
            event_type = FSMEvent.BUY_ORDER_PARTIAL if is_buy else FSMEvent.SELL_ORDER_PARTIAL
            logger.info(
                f"Order partially filled: {symbol} {'BUY' if is_buy else 'SELL'} "
                f"{order_ctx.cumulative_qty:.6f}/{order_ctx.target_qty:.6f} "
                f"({order_ctx.get_fill_ratio():.1%})"
            )

        fill_stats = self.get_fill_stats(order_ctx)

        return EventContext(
            event=event_type,
            symbol=symbol,
            timestamp=time.time(),
            order_id=order_ctx.order_id,
            filled_qty=order_ctx.cumulative_qty,
            avg_price=order_ctx.avg_price,
            data=fill_stats
        )

    def reset_order_context(self, order_ctx: OrderContext):
        """Reset order context for reuse"""
        order_ctx.cumulative_qty = 0.0
        order_ctx.target_qty = 0.0
        order_ctx.avg_price = 0.0
        order_ctx.total_fees = 0.0
        order_ctx.fill_trades.clear()
        order_ctx.status = "pending"
        order_ctx.retry_count = 0

        logger.debug("Order context reset")


# Global singleton
_partial_fill_handler = None


def get_partial_fill_handler() -> PartialFillHandler:
    """Get global partial fill handler singleton"""
    global _partial_fill_handler
    if _partial_fill_handler is None:
        _partial_fill_handler = PartialFillHandler()
        logger.info("PartialFillHandler initialized")
    return _partial_fill_handler
