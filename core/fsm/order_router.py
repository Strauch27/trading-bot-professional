#!/usr/bin/env python3
"""
FSM Order Router - Simplified Idempotent Order Execution

Provides idempotent order placement for FSM engine:
- Intent ID tracking prevents duplicate orders
- Result caching for retry scenarios
- Basic retry logic with backoff
- Thread-safe operations

Simplified from services/order_router.py for FSM use.
"""

import hashlib
import logging
import threading
import time
from dataclasses import dataclass
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)


@dataclass
class OrderResult:
    """Result of order placement attempt."""
    success: bool
    order_id: Optional[str] = None
    filled_qty: float = 0.0
    avg_price: float = 0.0
    status: str = "unknown"
    error: Optional[str] = None
    timestamp: float = 0.0


class FSMOrderRouter:
    """
    Simplified order router for FSM with idempotency guarantee.

    Key Features:
    - Idempotent placement via intent_id
    - Result caching for retries
    - Thread-safe operations
    - TTL-based cleanup

    Guarantees:
    - Same intent_id → Same order (no duplicates)
    - Retry-safe: Returns cached result on retry
    - Thread-safe: Can be called from multiple FSM processors
    """

    def __init__(self, order_service, max_retries: int = 3):
        """
        Initialize FSM order router.

        Args:
            order_service: OrderService instance for actual order placement
            max_retries: Maximum retry attempts for failed orders
        """
        self.order_service = order_service
        self.max_retries = max_retries

        # Idempotency tracking
        self._lock = threading.RLock()
        self._intent_cache: Dict[str, OrderResult] = {}  # intent_id -> result
        self._last_cleanup = time.time()
        self._cleanup_interval = 3600  # Cleanup every hour
        self._result_ttl = 7200  # Keep results for 2 hours

        logger.info(f"FSMOrderRouter initialized (max_retries={max_retries})")

    def submit(self,
               intent_id: str,
               symbol: str,
               side: str,
               amount: float,
               price: float,
               order_type: str = "limit",
               **kwargs) -> OrderResult:
        """
        Submit order with idempotency guarantee.

        Guarantees:
        - Same intent_id → Returns cached result (no duplicate order)
        - Different intent_id → Places new order

        Args:
            intent_id: Unique intent identifier (hash of decision + params)
            symbol: Trading pair (e.g., "BTC/USDT")
            side: "buy" or "sell"
            amount: Order quantity
            price: Limit price
            order_type: "limit", "market", "ioc", etc.
            **kwargs: Additional order parameters

        Returns:
            OrderResult with order_id, status, fills

        Example:
            intent_id = f"buy_{symbol}_{timestamp}_{hash(params)}"
            result = router.submit(intent_id, "BTC/USDT", "buy", 0.01, 50000)
            if result.success:
                print(f"Order placed: {result.order_id}")
        """
        # Cleanup old entries periodically
        self._cleanup_old_entries()

        # Check cache first (idempotency)
        with self._lock:
            if intent_id in self._intent_cache:
                cached = self._intent_cache[intent_id]
                logger.debug(f"Idempotent hit: {intent_id} -> {cached.order_id}")
                return cached

        # Not in cache - place new order
        result = self._place_with_retry(
            intent_id=intent_id,
            symbol=symbol,
            side=side,
            amount=amount,
            price=price,
            order_type=order_type,
            **kwargs
        )

        # Cache result
        with self._lock:
            self._intent_cache[intent_id] = result

        return result

    def _place_with_retry(self,
                         intent_id: str,
                         symbol: str,
                         side: str,
                         amount: float,
                         price: float,
                         order_type: str,
                         **kwargs) -> OrderResult:
        """
        Place order with retry logic.

        Args:
            intent_id: Intent ID for logging
            symbol: Trading symbol
            side: buy/sell
            amount: Quantity
            price: Price
            order_type: Order type
            **kwargs: Additional parameters

        Returns:
            OrderResult
        """
        # Generate stable clientOrderId from intent_id
        client_order_id = self._gen_client_order_id(intent_id)

        # CRITICAL FIX: Check if order already exists via clientOrderId BEFORE placing
        # This prevents duplicates when cache was cleared or after bot restart
        try:
            exchange = self.order_service.exchange
            existing = exchange.fetch_order_by_client_id(client_order_id, symbol)
            if existing and existing.get("id"):
                logger.info(f"Found existing order via clientOrderId: {existing['id']} (intent={intent_id})")
                # Return existing order as success
                result = OrderResult(
                    success=True,
                    order_id=existing["id"],
                    filled_qty=float(existing.get("filled", 0)),
                    avg_price=float(existing.get("average", 0)),
                    status=existing.get("status", "unknown"),
                    timestamp=time.time()
                )
                # Cache this result for future lookups
                with self._lock:
                    self._intent_cache[intent_id] = result
                return result
        except Exception as e:
            # Order not found or exchange doesn't support fetch_order_by_client_id
            logger.debug(f"clientOrderId lookup failed (expected if order doesn't exist): {e}")

        last_error = None
        for attempt in range(self.max_retries):
            try:
                logger.debug(f"Placing order (attempt {attempt+1}/{self.max_retries}): {side} {amount} {symbol} @ {price}")

                # Place order via OrderService
                order = self.order_service.place_order(
                    symbol=symbol,
                    side=side,
                    amount=amount,
                    price=price,
                    order_type=order_type,
                    client_order_id=client_order_id,
                    **kwargs
                )

                # Parse result
                if order and order.get("id"):
                    # Success
                    return OrderResult(
                        success=True,
                        order_id=order["id"],
                        filled_qty=order.get("filled", 0.0),
                        avg_price=order.get("average", price),
                        status=order.get("status", "open"),
                        timestamp=time.time()
                    )
                else:
                    # No order ID - failed
                    last_error = "No order ID returned"

            except Exception as e:
                last_error = str(e)
                logger.warning(f"Order placement failed (attempt {attempt+1}): {e}")

                # Exponential backoff
                if attempt < self.max_retries - 1:
                    backoff = 0.4 * (2 ** attempt)  # 400ms, 800ms, 1600ms
                    time.sleep(backoff)

        # All retries failed
        logger.error(f"Order placement failed after {self.max_retries} attempts: {last_error}")
        return OrderResult(
            success=False,
            error=last_error,
            status="failed",
            timestamp=time.time()
        )

    def _gen_client_order_id(self, intent_id: str) -> str:
        """
        Generate stable clientOrderId from intent_id.

        Same intent_id → Same clientOrderId
        This prevents duplicates even on exchange side.

        Args:
            intent_id: Intent identifier

        Returns:
            Short client order ID (max 32 chars for exchange compatibility)
        """
        # Hash intent_id to short string
        hash_obj = hashlib.sha256(intent_id.encode())
        hash_hex = hash_obj.hexdigest()[:16]  # Take first 16 chars
        return f"fsm_{hash_hex}"

    def _cleanup_old_entries(self):
        """
        Cleanup old cached results to prevent memory leak.

        Removes entries older than _result_ttl.
        """
        current_time = time.time()

        # Only cleanup periodically
        if current_time - self._last_cleanup < self._cleanup_interval:
            return

        with self._lock:
            to_remove = []

            for intent_id, result in self._intent_cache.items():
                age = current_time - result.timestamp
                if age > self._result_ttl:
                    to_remove.append(intent_id)

            for intent_id in to_remove:
                del self._intent_cache[intent_id]

            if to_remove:
                logger.info(f"Cleaned up {len(to_remove)} old order results")

            self._last_cleanup = current_time

    def get_cached_result(self, intent_id: str) -> Optional[OrderResult]:
        """
        Get cached result for intent_id if exists.

        Args:
            intent_id: Intent identifier

        Returns:
            OrderResult if cached, None otherwise
        """
        with self._lock:
            return self._intent_cache.get(intent_id)

    def clear_cache(self):
        """Clear all cached results. Use for testing only."""
        with self._lock:
            self._intent_cache.clear()
            logger.warning("Order router cache cleared")


# Global instance for convenience
_global_router: Optional[FSMOrderRouter] = None


def get_order_router(order_service=None, max_retries: int = 3) -> FSMOrderRouter:
    """
    Get or create global FSMOrderRouter instance.

    Args:
        order_service: OrderService instance (required on first call)
        max_retries: Maximum retry attempts

    Returns:
        FSMOrderRouter instance
    """
    global _global_router
    if _global_router is None:
        if order_service is None:
            raise ValueError("order_service required on first call")
        _global_router = FSMOrderRouter(order_service, max_retries)
    return _global_router
