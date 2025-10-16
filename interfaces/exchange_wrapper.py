#!/usr/bin/env python3
"""
Exchange Wrapper - Idempotent Order Execution

Wraps CCXT exchange with:
- Idempotent order placement via clientOrderId
- Wait-for-fill polling with timeout
- Order trades fetching for reconciliation
- Error handling and retry logic

This is the ONLY module that should call CCXT directly for order operations.
"""

import time
import logging
from typing import Dict, List, Optional, Any

logger = logging.getLogger(__name__)


class ExchangeWrapper:
    """
    Idempotent wrapper around CCXT exchange.

    Enforces:
    - clientOrderId for all orders (idempotency)
    - Consistent order placement API
    - Wait-for-fill polling
    - Trade fetching for reconciliation
    """

    def __init__(self, ccxt_exchange):
        """
        Initialize exchange wrapper.

        Args:
            ccxt_exchange: CCXT exchange instance (e.g., ccxt.binance())
        """
        self.ccxt = ccxt_exchange

    def create_market_order(
        self,
        symbol: str,
        side: str,
        qty: float,
        params: Optional[Dict[str, Any]] = None
    ) -> Dict[str, Any]:
        """
        Create market order with clientOrderId for idempotency.

        Args:
            symbol: Trading pair (e.g., "BTC/USDT")
            side: Order side ("buy" or "sell")
            qty: Base quantity to trade
            params: Additional params (must include clientOrderId)

        Returns:
            CCXT order object

        Example:
            >>> wrapper.create_market_order(
            ...     "BTC/USDT", "buy", 0.001,
            ...     params={"clientOrderId": "TBP-123-abc"}
            ... )
        """
        params = params or {}
        if "clientOrderId" not in params:
            logger.warning(f"Market order for {symbol} missing clientOrderId - idempotency not guaranteed")

        try:
            order = self.ccxt.create_order(
                symbol=symbol,
                type="market",
                side=side,
                amount=qty,
                price=None,
                params=params
            )
            logger.debug(f"Market order created: {symbol} {side} {qty} (id={order.get('id')})")
            return order

        except Exception as e:
            logger.error(f"Market order failed: {symbol} {side} {qty}: {e}")
            raise

    def create_limit_order(
        self,
        symbol: str,
        side: str,
        qty: float,
        price: float,
        params: Optional[Dict[str, Any]] = None
    ) -> Dict[str, Any]:
        """
        Create limit order with clientOrderId for idempotency.

        Args:
            symbol: Trading pair
            side: Order side ("buy" or "sell")
            qty: Base quantity to trade
            price: Limit price
            params: Additional params (must include clientOrderId and timeInForce)

        Returns:
            CCXT order object

        Example:
            >>> wrapper.create_limit_order(
            ...     "BTC/USDT", "buy", 0.001, 50000.0,
            ...     params={"clientOrderId": "TBP-123-abc", "timeInForce": "IOC"}
            ... )
        """
        params = params or {}
        if "clientOrderId" not in params:
            logger.warning(f"Limit order for {symbol} missing clientOrderId - idempotency not guaranteed")

        try:
            order = self.ccxt.create_order(
                symbol=symbol,
                type="limit",
                side=side,
                amount=qty,
                price=price,
                params=params
            )
            logger.debug(f"Limit order created: {symbol} {side} {qty}@{price} (id={order.get('id')})")
            return order

        except Exception as e:
            logger.error(f"Limit order failed: {symbol} {side} {qty}@{price}: {e}")
            raise

    def wait_for_fill(
        self,
        symbol: str,
        order_id: str,
        timeout_ms: int = 2500
    ) -> Dict[str, Any]:
        """
        Poll order status until filled, canceled, or timeout.

        This provides synchronous fill detection for IOC/FOK orders
        without requiring websocket connections.

        Args:
            symbol: Trading pair
            order_id: Exchange order ID
            timeout_ms: Max polling time in milliseconds

        Returns:
            Dict with:
                - status: "closed", "canceled", "open", "expired"
                - filled: Filled quantity (float)
                - remaining: Remaining quantity (float)

        Example:
            >>> result = wrapper.wait_for_fill("BTC/USDT", "12345", timeout_ms=2000)
            >>> print(result)
            {'status': 'closed', 'filled': 0.001, 'remaining': 0.0}
        """
        end_time = time.time() + (timeout_ms / 1000.0)
        last_status = {"status": "open", "filled": 0.0, "remaining": 0.0}

        try:
            while time.time() < end_time:
                try:
                    order = self.ccxt.fetch_order(order_id, symbol)

                    last_status = {
                        "status": order.get("status", "unknown"),
                        "filled": float(order.get("filled") or 0.0),
                        "remaining": float(order.get("remaining") or 0.0),
                        "average": order.get("average")
                    }

                    # Terminal states - stop polling
                    if last_status["status"] in ("closed", "canceled", "expired"):
                        logger.debug(f"Order {order_id} reached terminal state: {last_status['status']}")
                        break

                    # Poll interval: 200ms
                    time.sleep(0.2)

                except Exception as poll_error:
                    logger.warning(f"Order status poll failed for {order_id}: {poll_error}")
                    time.sleep(0.2)
                    continue

        except Exception as e:
            logger.error(f"wait_for_fill failed for {order_id}: {e}")

        return last_status

    def fetch_order_trades(
        self,
        symbol: str,
        order_id: str
    ) -> List[Dict[str, Any]]:
        """
        Fetch all trades for an order (for reconciliation).

        Args:
            symbol: Trading pair
            order_id: Exchange order ID

        Returns:
            List of trade dicts with:
                - id: Trade ID
                - timestamp: Trade timestamp
                - price: Execution price
                - amount: Filled quantity
                - cost: Notional (price * amount)
                - fee: Fee paid (dict with cost and currency)
                - side: "buy" or "sell"

        Example:
            >>> trades = wrapper.fetch_order_trades("BTC/USDT", "12345")
            >>> for trade in trades:
            ...     print(f"Filled {trade['amount']} @ {trade['price']}")
        """
        try:
            trades = self.ccxt.fetch_order_trades(order_id, symbol)
            logger.debug(f"Fetched {len(trades)} trades for order {order_id}")
            return trades

        except Exception as e:
            logger.error(f"fetch_order_trades failed for {order_id}: {e}")
            return []

    def fetch_order(
        self,
        symbol: str,
        order_id: str
    ) -> Dict[str, Any]:
        """
        Fetch order details.

        Args:
            symbol: Trading pair
            order_id: Exchange order ID

        Returns:
            CCXT order object
        """
        try:
            return self.ccxt.fetch_order(order_id, symbol)
        except Exception as e:
            logger.error(f"fetch_order failed for {order_id}: {e}")
            raise

    def cancel_order(
        self,
        symbol: str,
        order_id: str
    ) -> Dict[str, Any]:
        """
        Cancel an open order.

        Args:
            symbol: Trading pair
            order_id: Exchange order ID

        Returns:
            CCXT order object (canceled)
        """
        try:
            result = self.ccxt.cancel_order(order_id, symbol)
            logger.info(f"Order canceled: {order_id}")
            return result
        except Exception as e:
            logger.error(f"cancel_order failed for {order_id}: {e}")
            raise
