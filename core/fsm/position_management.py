#!/usr/bin/env python3
"""
FSM Position Management - Dynamic TP/SL Switching

Manages dynamic switching between TP and SL based on PnL:
- PnL < -0.5% → Prioritize SL (cancel TP, place SL)
- PnL > +0.2% → Prioritize TP (cancel SL, place TP)
- Cooldown: 20s between switches
"""

import logging
import time
from datetime import datetime
from typing import Dict, Optional

logger = logging.getLogger(__name__)


class DynamicTPSLManager:
    """
    Manages dynamic TP/SL switching based on PnL.

    Legacy behavior:
    - When PnL goes negative → Cancel TP order, place SL order on exchange
    - When PnL goes positive → Cancel SL order, place TP order on exchange
    - Cooldown period prevents rapid switching
    """

    def __init__(self, cooldown_seconds: int = 20):
        """
        Initialize manager.

        Args:
            cooldown_seconds: Minimum time between switches (default 20s)
        """
        self.last_switch: Dict[str, float] = {}  # symbol -> timestamp
        self.cooldown_seconds = cooldown_seconds
        self.logger = logger

    def rebalance_protection(self,
                            symbol: str,
                            coin_state,
                            current_price: float,
                            order_service) -> None:
        """
        Dynamically switch between TP and SL based on PnL.

        Logic:
        - If PnL < -0.5% → Prioritize SL (cancel TP, place SL)
        - If PnL > +0.2% → Prioritize TP (cancel SL, place TP)
        - Cooldown: 20s between switches

        Args:
            symbol: Trading symbol
            coin_state: CoinState with position data
            current_price: Current market price
            order_service: OrderService for placing/canceling orders
        """
        # Check if position is open
        if not hasattr(coin_state, 'entry_price') or coin_state.entry_price <= 0:
            return

        if not hasattr(coin_state, 'amount') or coin_state.amount <= 0:
            return

        # Check cooldown
        if symbol in self.last_switch:
            elapsed = time.time() - self.last_switch[symbol]
            if elapsed < self.cooldown_seconds:
                return

        # Calculate PnL percentage
        pnl_pct = ((current_price - coin_state.entry_price) / coin_state.entry_price) * 100

        # Decide which protection to activate
        if pnl_pct < -0.5:  # Negative PnL → SL priority
            if getattr(coin_state, 'tp_active', False) and not getattr(coin_state, 'sl_active', False):
                self._switch_to_sl(symbol, coin_state, current_price, order_service)
                self.last_switch[symbol] = time.time()

        elif pnl_pct > 0.2:  # Positive PnL → TP priority
            if getattr(coin_state, 'sl_active', False) and not getattr(coin_state, 'tp_active', False):
                self._switch_to_tp(symbol, coin_state, current_price, order_service)
                self.last_switch[symbol] = time.time()

    def _switch_to_sl(self, symbol: str, coin_state, current_price: float, order_service) -> None:
        """
        Cancel TP, place SL order.

        Args:
            symbol: Trading symbol
            coin_state: CoinState
            current_price: Current price (for logging)
            order_service: OrderService
        """
        pnl_pct = ((current_price - coin_state.entry_price) / coin_state.entry_price) * 100

        self.logger.info(f"{symbol}: Switching to SL priority (PnL: {pnl_pct:.2f}%)")

        # Cancel existing TP order if any
        if hasattr(coin_state, 'tp_order_id') and coin_state.tp_order_id:
            try:
                order_service.cancel_order(symbol, coin_state.tp_order_id)
                self.logger.debug(f"{symbol}: Cancelled TP order {coin_state.tp_order_id}")
            except Exception as e:
                self.logger.warning(f"{symbol}: Failed to cancel TP: {e}")

        # Place SL order on exchange
        if hasattr(coin_state, 'sl_px') and coin_state.sl_px > 0:
            try:
                sl_order = order_service.place_order(
                    symbol=symbol,
                    side="sell",
                    amount=coin_state.amount,
                    price=coin_state.sl_px,
                    order_type="stop_market",
                    stop_price=coin_state.sl_px
                )

                if sl_order and sl_order.get("id"):
                    coin_state.sl_order_id = sl_order["id"]
                    coin_state.sl_active = True
                    coin_state.tp_active = False
                    self.logger.info(f"{symbol}: SL order placed at {coin_state.sl_px:.8f} (order_id: {sl_order['id']})")
            except Exception as e:
                self.logger.error(f"{symbol}: Failed to place SL: {e}")

    def _switch_to_tp(self, symbol: str, coin_state, current_price: float, order_service) -> None:
        """
        Cancel SL, place TP order.

        Args:
            symbol: Trading symbol
            coin_state: CoinState
            current_price: Current price (for logging)
            order_service: OrderService
        """
        pnl_pct = ((current_price - coin_state.entry_price) / coin_state.entry_price) * 100

        self.logger.info(f"{symbol}: Switching to TP priority (PnL: {pnl_pct:.2f}%)")

        # Cancel existing SL order
        if hasattr(coin_state, 'sl_order_id') and coin_state.sl_order_id:
            try:
                order_service.cancel_order(symbol, coin_state.sl_order_id)
                self.logger.debug(f"{symbol}: Cancelled SL order {coin_state.sl_order_id}")
            except Exception as e:
                self.logger.warning(f"{symbol}: Failed to cancel SL: {e}")

        # Place TP order on exchange
        if hasattr(coin_state, 'tp_px') and coin_state.tp_px > 0:
            try:
                tp_order = order_service.place_order(
                    symbol=symbol,
                    side="sell",
                    amount=coin_state.amount,
                    price=coin_state.tp_px,
                    order_type="limit",
                    time_in_force="GTC"
                )

                if tp_order and tp_order.get("id"):
                    coin_state.tp_order_id = tp_order["id"]
                    coin_state.tp_active = True
                    coin_state.sl_active = False
                    self.logger.info(f"{symbol}: TP order placed at {coin_state.tp_px:.8f} (order_id: {tp_order['id']})")
            except Exception as e:
                self.logger.error(f"{symbol}: Failed to place TP: {e}")


# Global instance for convenience
_global_manager: Optional[DynamicTPSLManager] = None


def get_position_manager() -> DynamicTPSLManager:
    """Get or create global DynamicTPSLManager instance."""
    global _global_manager
    if _global_manager is None:
        _global_manager = DynamicTPSLManager()
    return _global_manager
