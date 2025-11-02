#!/usr/bin/env python3
"""
OrderService - Centralized Order Submission

Provides unified interface for all order submissions with:
- Retry logic (exponential backoff, max 3 attempts)
- Error classification (Recoverable vs. Fatal)
- Idempotent COID reuse via COIDManager
- Consistent logging

All Buy/Sell operations MUST use this service instead of direct exchange calls.

Phase 2 Enhancement:
- Integrated with core.coid for idempotent COID generation
- COID status tracking (PENDING → TERMINAL)
- No duplicate orders on retry/crash
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional

# Phase 2: Import COID manager
try:
    from core.coid import COIDStatus, get_coid_manager
    COID_AVAILABLE = True
except ImportError:
    COID_AVAILABLE = False
    logging.warning("core.coid not available, COID management disabled")

logger = logging.getLogger(__name__)


class OrderErrorType(Enum):
    """Classification of order errors"""
    NETWORK_ERROR = "network_error"  # Transient - retry
    RATE_LIMIT = "rate_limit"  # Transient - retry with backoff
    INSUFFICIENT_FUNDS = "insufficient_funds"  # Fatal - don't retry
    INVALID_ORDER = "invalid_order"  # Fatal - don't retry
    DUPLICATE_ORDER = "duplicate_order"  # Fatal - don't retry
    UNKNOWN = "unknown"  # Unknown - retry once


@dataclass
class OrderResult:
    """
    Result of order submission.

    Attributes:
        success: True if order was submitted successfully
        status: Order status (FILLED, PARTIALLY_FILLED, CANCELED, etc.)
        order_id: Exchange order ID
        client_order_id: Client order ID (COID)
        filled_qty: Filled quantity
        avg_price: Average fill price
        fees: Total fees in quote currency
        trades: List of trade executions
        error: Error message if failed
        error_type: Classified error type
        attempts: Number of submission attempts
        raw_order: Raw exchange order response
    """
    success: bool
    status: Optional[str] = None
    order_id: Optional[str] = None
    client_order_id: Optional[str] = None
    filled_qty: float = 0.0
    avg_price: float = 0.0
    fees: float = 0.0
    trades: List[Dict[str, Any]] = field(default_factory=list)
    error: Optional[str] = None
    error_type: Optional[OrderErrorType] = None
    attempts: int = 1
    raw_order: Optional[Dict[str, Any]] = None

    @property
    def is_filled(self) -> bool:
        """Check if order is fully filled"""
        return self.status in ("FILLED", "CLOSED") if self.status else False

    @property
    def is_partially_filled(self) -> bool:
        """Check if order is partially filled"""
        return self.filled_qty > 0 and not self.is_filled


class OrderService:
    """
    Centralized order submission service.

    Features:
    - Retry logic for transient errors
    - Error classification
    - Consistent logging
    - COID management
    """

    def __init__(
        self,
        exchange,
        max_retries: int = 3,
        base_retry_delay: float = 0.25,
        max_retry_delay: float = 2.0,
        use_coid_manager: bool = True
    ):
        """
        Initialize OrderService.

        Args:
            exchange: CCXT exchange instance
            max_retries: Maximum number of retry attempts
            base_retry_delay: Base delay between retries (seconds)
            max_retry_delay: Maximum delay between retries (seconds)
            use_coid_manager: Use COIDManager for idempotent COIDs (Phase 2)
        """
        self.exchange = exchange
        self.max_retries = max_retries
        self.base_retry_delay = base_retry_delay
        self.max_retry_delay = max_retry_delay

        # Phase 2: COID manager
        self.use_coid_manager = use_coid_manager and COID_AVAILABLE
        self.coid_manager = get_coid_manager() if self.use_coid_manager else None

        if self.use_coid_manager:
            logger.info("OrderService initialized with COID manager (Phase 2)")
        else:
            logger.info("OrderService initialized without COID manager")

    def place_order(
        self,
        symbol: str,
        side: str,
        amount: float,
        price: float,
        order_type: str = "limit",
        client_order_id: Optional[str] = None,
        **kwargs
    ) -> Dict[str, Any]:
        """
        Place order with specified type (dispatcher for FSM compatibility).

        This method provides a unified interface for FSMOrderRouter to place orders.
        It dispatches to submit_limit() or submit_market() based on order_type.

        Args:
            symbol: Trading pair (e.g., "BTC/USDT")
            side: "buy" or "sell"
            amount: Order quantity
            price: Limit price (ignored for market orders)
            order_type: "limit" or "market" (default: "limit")
            client_order_id: Client order ID for idempotency
            **kwargs: Additional parameters (e.g., time_in_force, decision_id)

        Returns:
            CCXT order dict with keys: id, status, filled, average, etc.

        Raises:
            ValueError: If order_type is not supported
        """
        time_in_force = kwargs.get("time_in_force", "IOC")
        decision_id = kwargs.get("decision_id")

        if order_type in ("limit", "ioc"):
            # Use limit order
            result = self.submit_limit(
                symbol=symbol,
                side=side,
                qty=amount,
                price=price,
                coid=client_order_id,
                time_in_force=time_in_force,
                decision_id=decision_id
            )
        elif order_type == "market":
            # Use market order
            result = self.submit_market(
                symbol=symbol,
                side=side,
                qty=amount,
                coid=client_order_id,
                time_in_force=time_in_force,
                decision_id=decision_id
            )
        else:
            raise ValueError(f"Unsupported order_type: {order_type}")

        # Convert OrderResult to CCXT-like dict for FSMOrderRouter compatibility
        if result.success and result.order_id:
            return {
                "id": result.order_id,
                "status": "open" if result.filled_qty == 0 else ("closed" if result.filled_qty == amount else "partial"),
                "filled": result.filled_qty,
                "average": result.avg_price,
                "symbol": symbol,
                "side": side,
                "type": order_type,
                "timestamp": int(time.time() * 1000)
            }
        else:
            # Failed - raise exception for FSMOrderRouter to handle
            error_msg = result.error or "Unknown order placement error"
            raise Exception(f"Order placement failed: {error_msg}")

    def submit_limit(
        self,
        symbol: str,
        side: str,
        qty: float,
        price: float,
        coid: Optional[str] = None,
        time_in_force: str = "IOC",
        decision_id: Optional[str] = None,
        leg_idx: int = 0
    ) -> OrderResult:
        """
        Submit limit order with retry logic.

        Args:
            symbol: Trading pair (e.g., "BTC/USDT")
            side: "buy" or "sell"
            qty: Order quantity
            price: Limit price
            coid: Client order ID (auto-generated if None and decision_id provided)
            time_in_force: Time in force (default: "IOC")
            decision_id: Trading decision ID (for COID generation, Phase 2)
            leg_idx: Order leg index (for multi-leg orders, Phase 2)

        Returns:
            OrderResult with submission details
        """
        # Phase 2: Auto-generate COID if needed
        if coid is None and decision_id and self.use_coid_manager:
            coid = self.coid_manager.next_client_order_id(
                decision_id=decision_id,
                leg_idx=leg_idx,
                side=side,
                symbol=symbol
            )
            logger.info(f"Auto-generated COID: {coid} for {symbol} {side}")
        elif coid is None:
            # Fallback: generate simple timestamp-based COID
            coid = f"{symbol.replace('/', '')}_{side}_{int(time.time()*1000)}"
            logger.warning(f"Generated fallback COID (no decision_id): {coid}")
        # Quantize price and quantity
        try:
            price = float(self.exchange.price_to_precision(symbol, price))
            qty = float(self.exchange.amount_to_precision(symbol, qty))
        except Exception as e:
            logger.error(f"Quantization failed for {symbol}: {e}")
            return OrderResult(
                success=False,
                error=f"Quantization error: {e}",
                error_type=OrderErrorType.INVALID_ORDER
            )

        # Validate quantized values
        if qty <= 0 or price <= 0:
            logger.error(f"Invalid quantized values: qty={qty}, price={price}")
            return OrderResult(
                success=False,
                error=f"Invalid values after quantization: qty={qty}, price={price}",
                error_type=OrderErrorType.INVALID_ORDER
            )

        # Submit with retry
        return self._submit_with_retry(
            method="create_limit_order",
            symbol=symbol,
            side=side,
            qty=qty,
            price=price,
            time_in_force=time_in_force,
            coid=coid
        )

    def submit_market(
        self,
        symbol: str,
        side: str,
        qty: float,
        coid: Optional[str] = None,
        time_in_force: str = "IOC",
        decision_id: Optional[str] = None,
        leg_idx: int = 0
    ) -> OrderResult:
        """
        Submit market order with retry logic.

        Args:
            symbol: Trading pair (e.g., "BTC/USDT")
            side: "buy" or "sell"
            qty: Order quantity
            coid: Client order ID (auto-generated if None and decision_id provided)
            time_in_force: Time in force (default: "IOC")
            decision_id: Trading decision ID (for COID generation, Phase 2)
            leg_idx: Order leg index (for multi-leg orders, Phase 2)

        Returns:
            OrderResult with submission details
        """
        # Phase 2: Auto-generate COID if needed
        if coid is None and decision_id and self.use_coid_manager:
            coid = self.coid_manager.next_client_order_id(
                decision_id=decision_id,
                leg_idx=leg_idx,
                side=side,
                symbol=symbol
            )
            logger.info(f"Auto-generated COID: {coid} for {symbol} {side} MARKET")
        elif coid is None:
            # Fallback: generate simple timestamp-based COID
            coid = f"{symbol.replace('/', '')}_{side}_MKT_{int(time.time()*1000)}"
            logger.warning(f"Generated fallback COID (no decision_id): {coid}")
        # Quantize quantity
        try:
            qty = float(self.exchange.amount_to_precision(symbol, qty))
        except Exception as e:
            logger.error(f"Quantization failed for {symbol}: {e}")
            return OrderResult(
                success=False,
                error=f"Quantization error: {e}",
                error_type=OrderErrorType.INVALID_ORDER
            )

        # Validate quantized value
        if qty <= 0:
            logger.error(f"Invalid quantized qty: {qty}")
            return OrderResult(
                success=False,
                error=f"Invalid qty after quantization: {qty}",
                error_type=OrderErrorType.INVALID_ORDER
            )

        # Submit with retry
        return self._submit_with_retry(
            method="create_market_order",
            symbol=symbol,
            side=side,
            qty=qty,
            time_in_force=time_in_force,
            coid=coid
        )

    def _submit_with_retry(
        self,
        method: str,
        symbol: str,
        side: str,
        qty: float,
        time_in_force: str,
        coid: str,
        price: Optional[float] = None
    ) -> OrderResult:
        """
        Submit order with exponential backoff retry.

        Args:
            method: Exchange method name ("create_limit_order" or "create_market_order")
            symbol: Trading pair
            side: "buy" or "sell"
            qty: Order quantity
            time_in_force: Time in force
            coid: Client order ID
            price: Limit price (for limit orders)

        Returns:
            OrderResult with submission details
        """
        last_error = None
        last_error_type = None

        for attempt in range(1, self.max_retries + 1):
            try:
                # Call exchange method
                if method == "create_limit_order":
                    order = self.exchange.create_limit_order(
                        symbol, side, qty, price, time_in_force, coid, False
                    )
                elif method == "create_market_order":
                    order = self.exchange.create_market_order(
                        symbol, side, qty, time_in_force, coid
                    )
                else:
                    raise ValueError(f"Unknown method: {method}")

                # Success - parse order
                return self._parse_order_response(order, coid, attempts=attempt)

            except Exception as e:
                last_error = str(e)
                last_error_type = self._classify_error(e)

                logger.warning(
                    f"Order submission failed (attempt {attempt}/{self.max_retries}): "
                    f"{symbol} {side} {qty} @ {price or 'market'} - {last_error_type.value}: {last_error}"
                )

                # Check if error is retryable
                if not self._is_retryable(last_error_type):
                    logger.error(f"Fatal error, not retrying: {last_error_type.value}")
                    break

                # Retry with exponential backoff
                if attempt < self.max_retries:
                    delay = min(
                        self.base_retry_delay * (2 ** (attempt - 1)),
                        self.max_retry_delay
                    )
                    logger.info(f"Retrying in {delay:.2f}s...")
                    time.sleep(delay)

        # All retries exhausted
        return OrderResult(
            success=False,
            client_order_id=coid,
            error=last_error,
            error_type=last_error_type,
            attempts=attempt
        )

    def _parse_order_response(
        self,
        order: Dict[str, Any],
        coid: str,
        attempts: int
    ) -> OrderResult:
        """
        Parse exchange order response into OrderResult.

        Args:
            order: Raw exchange order response
            coid: Client order ID
            attempts: Number of attempts

        Returns:
            OrderResult
        """
        status = (order.get("status") or "").upper()
        filled_qty = float(order.get("filled") or 0.0)
        avg_price = float(order.get("average") or order.get("price") or 0.0)

        # Extract trades
        trades = order.get("trades") or []

        # Calculate total fees
        fees = 0.0
        if trades:
            for trade in trades:
                fee = trade.get("fee", {})
                if fee and fee.get("currency"):
                    # Assume fee in quote currency for now
                    fees += float(fee.get("cost") or 0.0)
        elif order.get("fee"):
            # Fee in order object
            fee = order.get("fee", {})
            fees = float(fee.get("cost") or 0.0)

        # Phase 2: Update COID status
        if self.use_coid_manager and coid:
            coid_status = self._map_order_status_to_coid(status)
            self.coid_manager.update_status(
                coid=coid,
                status=coid_status,
                order_id=order.get("id"),
                metadata={
                    'filled_qty': filled_qty,
                    'avg_price': avg_price,
                    'fees': fees,
                    'attempts': attempts
                }
            )

        return OrderResult(
            success=True,
            status=status,
            order_id=order.get("id"),
            client_order_id=coid,
            filled_qty=filled_qty,
            avg_price=avg_price,
            fees=fees,
            trades=trades,
            attempts=attempts,
            raw_order=order
        )

    def _map_order_status_to_coid(self, order_status: str) -> COIDStatus:
        """Map order status to COID status (Phase 2)"""
        status_upper = order_status.upper()
        if status_upper in ('FILLED', 'CLOSED'):
            return COIDStatus.FILLED
        elif status_upper == 'CANCELED':
            return COIDStatus.CANCELED
        elif status_upper == 'REJECTED':
            return COIDStatus.REJECTED
        elif status_upper == 'EXPIRED':
            return COIDStatus.EXPIRED
        elif status_upper in ('PARTIALLY_FILLED', 'PARTIAL'):
            return COIDStatus.PARTIALLY_FILLED
        else:
            return COIDStatus.PENDING

    def _classify_error(self, error: Exception) -> OrderErrorType:
        """
        Classify exception into error type.

        Args:
            error: Exception from exchange

        Returns:
            OrderErrorType
        """
        error_str = str(error).lower()

        # Network errors (retryable)
        if any(keyword in error_str for keyword in [
            "network", "timeout", "connection", "timed out",
            "502", "503", "504"
        ]):
            return OrderErrorType.NETWORK_ERROR

        # Rate limit (retryable with backoff)
        if any(keyword in error_str for keyword in [
            "rate limit", "429", "too many requests"
        ]):
            return OrderErrorType.RATE_LIMIT

        # Insufficient funds (fatal)
        if any(keyword in error_str for keyword in [
            "insufficient", "balance", "not enough"
        ]):
            return OrderErrorType.INSUFFICIENT_FUNDS

        # Invalid order (fatal)
        if any(keyword in error_str for keyword in [
            "invalid", "min notional", "lot size", "price filter"
        ]):
            return OrderErrorType.INVALID_ORDER

        # Duplicate order (fatal)
        if any(keyword in error_str for keyword in [
            "duplicate", "already exists"
        ]):
            return OrderErrorType.DUPLICATE_ORDER

        # Unknown (retry once)
        return OrderErrorType.UNKNOWN

    def _is_retryable(self, error_type: OrderErrorType) -> bool:
        """
        Check if error type is retryable.

        Args:
            error_type: Classified error type

        Returns:
            True if retryable, False otherwise
        """
        retryable_types = {
            OrderErrorType.NETWORK_ERROR,
            OrderErrorType.RATE_LIMIT,
            OrderErrorType.UNKNOWN
        }
        return error_type in retryable_types


# Utility function for backwards compatibility
def create_order_service(exchange, **kwargs) -> OrderService:
    """
    Factory function to create OrderService.

    Args:
        exchange: CCXT exchange instance
        **kwargs: Additional OrderService parameters

    Returns:
        OrderService instance
    """
    return OrderService(exchange, **kwargs)
