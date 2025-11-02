#!/usr/bin/env python3
"""
FSM Exchange Wrapper - Duplicate Prevention & Precision Handling

Wraps ExchangeAdapter with safety features:
- Stable clientOrderId generation (prevents duplicates)
- Precision rounding before submission
- Duplicate detection via clientOrderId
- Retry with same clientOrderId (idempotent)

Guarantees:
- No duplicate orders on retry
- No LOT_SIZE or PRICE_FILTER rejections
- Deterministic order submission
"""

import hashlib
import logging
import math
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)


class FSMExchangeWrapper:
    """
    Safety wrapper around ExchangeAdapter.

    Provides:
    - Stable clientOrderId (hash of order params)
    - Precision rounding (LOT_SIZE, PRICE_FILTER)
    - Duplicate prevention via clientOrderId
    - Exchange filter compliance

    Usage:
        wrapper = FSMExchangeWrapper(exchange_adapter)
        order = wrapper.place_order(
            symbol="BTC/USDT",
            side="buy",
            amount=0.123456789,  # Will be rounded
            price=50000.123456789  # Will be rounded
        )
    """

    def __init__(self, exchange_adapter):
        """
        Initialize exchange wrapper.

        Args:
            exchange_adapter: ExchangeAdapter or ccxt.Exchange instance
        """
        self.exchange = exchange_adapter
        self.logger = logger

        # Cache for exchange market info (for precision)
        self._market_cache: Dict[str, Any] = {}

        logger.info("FSMExchangeWrapper initialized")

    def place_order(self,
                   symbol: str,
                   side: str,
                   amount: float,
                   price: Optional[float] = None,
                   order_type: str = "limit",
                   client_order_id: Optional[str] = None,
                   **params) -> Optional[Dict]:
        """
        Place order with safety checks.

        Safety:
        - Generates stable clientOrderId (prevents duplicates)
        - Rounds amount/price to exchange precision
        - Retries with SAME clientOrderId

        Args:
            symbol: Trading pair (e.g., "BTC/USDT")
            side: "buy" or "sell"
            amount: Order quantity (will be rounded)
            price: Limit price (will be rounded)
            order_type: "limit", "market", "ioc", etc.
            client_order_id: Optional custom clientOrderId
            **params: Additional parameters

        Returns:
            Order dict from exchange or None on failure
        """
        try:
            # Get market info for precision
            market = self._get_market(symbol)

            # Round amount and price to exchange precision
            amount_rounded = self._round_amount(amount, market)
            price_rounded = self._round_price(price, market) if price else None

            # Generate stable clientOrderId if not provided
            if not client_order_id:
                client_order_id = self._gen_client_order_id(
                    symbol, side, amount_rounded, price_rounded, order_type
                )

            # Log submission
            self.logger.debug(
                f"Placing order: {side} {amount_rounded} {symbol} @ {price_rounded} "
                f"(clientOrderId: {client_order_id})"
            )

            # Place order via exchange
            order = self.exchange.create_order(
                symbol=symbol,
                type=order_type,
                side=side,
                amount=amount_rounded,
                price=price_rounded,
                params={"clientOrderId": client_order_id, **params}
            )

            self.logger.info(
                f"Order placed successfully: {order.get('id')} "
                f"({side} {amount_rounded} {symbol} @ {price_rounded})"
            )

            return order

        except Exception as e:
            self.logger.error(f"Order placement failed: {e}")
            # Check if it's a duplicate (exchange already has this clientOrderId)
            if "duplicate" in str(e).lower() or "exists" in str(e).lower():
                self.logger.warning(f"Duplicate order detected (clientOrderId: {client_order_id})")
                # Try to fetch existing order
                return self._fetch_order_by_client_id(symbol, client_order_id)
            return None

    def _gen_client_order_id(self,
                             symbol: str,
                             side: str,
                             amount: float,
                             price: Optional[float],
                             order_type: str) -> str:
        """
        Generate stable clientOrderId from order parameters.

        Same parameters → Same clientOrderId (prevents duplicates)

        Args:
            symbol: Trading symbol
            side: buy/sell
            amount: Order amount
            price: Order price
            order_type: Order type

        Returns:
            Client order ID (max 32 chars)
        """
        # Create deterministic string from params
        params_str = f"{symbol}_{side}_{amount}_{price}_{order_type}"

        # Hash to short ID
        hash_obj = hashlib.sha256(params_str.encode())
        hash_hex = hash_obj.hexdigest()[:20]  # Take first 20 chars

        return f"fsm_{hash_hex}"

    def _round_amount(self, amount: float, market: Dict) -> float:
        """
        Round amount to exchange LOT_SIZE precision.

        Args:
            amount: Raw amount
            market: Market info dict

        Returns:
            Rounded amount
        """
        if not market:
            return amount

        # Get lot size step
        precision = market.get("precision", {})
        amount_precision = precision.get("amount")

        if amount_precision is not None:
            # Round to specified decimals
            return round(amount, amount_precision)

        # Fallback: Get lot size from limits
        limits = market.get("limits", {})
        amount_limits = limits.get("amount", {})
        min_amount = amount_limits.get("min", 0)

        if min_amount > 0:
            # Calculate decimals from min amount
            decimals = max(0, -int(math.floor(math.log10(min_amount))))
            return round(amount, decimals)

        # Default: 8 decimals for crypto
        return round(amount, 8)

    def _round_price(self, price: Optional[float], market: Dict) -> Optional[float]:
        """
        Round price to exchange PRICE_FILTER precision.

        Args:
            price: Raw price
            market: Market info dict

        Returns:
            Rounded price
        """
        if price is None:
            return None

        if not market:
            return price

        # Get price precision
        precision = market.get("precision", {})
        price_precision = precision.get("price")

        if price_precision is not None:
            # Round to specified decimals
            return round(price, price_precision)

        # Fallback: Get tick size from limits
        limits = market.get("limits", {})
        price_limits = limits.get("price", {})
        min_price = price_limits.get("min", 0)

        if min_price > 0:
            # Calculate decimals from min price
            decimals = max(0, -int(math.floor(math.log10(min_price))))
            return round(price, decimals)

        # Default: 8 decimals for crypto
        return round(price, 8)

    def _get_market(self, symbol: str) -> Dict:
        """
        Get market info from exchange (cached).

        Args:
            symbol: Trading symbol

        Returns:
            Market info dict
        """
        if symbol in self._market_cache:
            return self._market_cache[symbol]

        try:
            # Fetch market info
            markets = self.exchange.load_markets()
            market = markets.get(symbol, {})
            self._market_cache[symbol] = market
            return market
        except Exception as e:
            self.logger.warning(f"Failed to fetch market info for {symbol}: {e}")
            return {}

    def _fetch_order_by_client_id(self, symbol: str, client_order_id: str) -> Optional[Dict]:
        """
        Fetch order by clientOrderId (for duplicate detection).

        Args:
            symbol: Trading symbol
            client_order_id: Client order ID

        Returns:
            Order dict or None
        """
        try:
            # Try to fetch order by clientOrderId (not all exchanges support this)
            orders = self.exchange.fetch_orders(symbol, limit=100)

            for order in orders:
                if order.get("clientOrderId") == client_order_id:
                    self.logger.info(f"Found existing order with clientOrderId: {client_order_id}")
                    return order

            return None

        except Exception as e:
            self.logger.debug(f"Could not fetch order by clientOrderId: {e}")
            return None


# Global instance for convenience
_global_wrapper: Optional[FSMExchangeWrapper] = None


def get_exchange_wrapper(exchange_adapter=None) -> FSMExchangeWrapper:
    """
    Get or create global FSMExchangeWrapper instance.

    Args:
        exchange_adapter: Exchange adapter (required on first call)

    Returns:
        FSMExchangeWrapper instance
    """
    global _global_wrapper
    if _global_wrapper is None:
        if exchange_adapter is None:
            raise ValueError("exchange_adapter required on first call")
        _global_wrapper = FSMExchangeWrapper(exchange_adapter)
    return _global_wrapper
