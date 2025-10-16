#!/usr/bin/env python3
"""
Order Router - Deterministic FSM for Order Execution

Finite State Machine for order execution with:
- Idempotency via intent_id + clientOrderId
- Budget reservation before exchange calls
- Retry logic with exponential backoff
- Partial fill handling
- Complete audit trail via JSONL

FSM States:
  NEW → RESERVED → SENT → {FILLED | PARTIAL | CANCELED | RETRY} → FAILED/SUCCESS

This is the SINGLE execution path for all trading orders.
"""

import time
import math
import logging
from dataclasses import dataclass
from typing import Optional, Dict, Any

logger = logging.getLogger(__name__)


@dataclass
class RouterConfig:
    """
    Configuration for OrderRouter behavior.

    Attributes:
        max_retries: Maximum retry attempts for failed orders
        retry_backoff_ms: Initial backoff in milliseconds (exponential)
        tif: Time in force ("IOC", "GTC", "FOK")
        slippage_bps: Maximum allowed slippage in basis points
        min_notional: Minimum order notional in quote currency
    """
    max_retries: int = 3
    retry_backoff_ms: int = 400
    tif: str = "IOC"
    slippage_bps: int = 20
    min_notional: float = 5.0


class OrderRouter:
    """
    Deterministic order execution router with FSM.

    Responsibilities:
    - Budget reservation before exchange calls
    - Idempotent order placement
    - Retry logic with backoff
    - Partial fill handling
    - Position reconciliation
    - Complete JSONL audit trail

    NOT Responsible For:
    - Trading decisions (handled by Decision layer)
    - Signal generation (handled by SignalEngine)
    - Guard checks (handled by GuardSuite)
    """

    def __init__(
        self,
        exchange_wrapper,
        portfolio,
        telemetry,
        config: RouterConfig
    ):
        """
        Initialize order router.

        Args:
            exchange_wrapper: ExchangeWrapper instance for order execution
            portfolio: Portfolio instance for reservations and reconciliation
            telemetry: JsonlWriter instance for audit logging
            config: RouterConfig with execution parameters
        """
        self.ex = exchange_wrapper
        self.pf = portfolio
        self.tl = telemetry
        self.cfg = config

        # Idempotency: track seen intent IDs
        self._seen: set = set()

        logger.info(
            f"OrderRouter initialized: tif={config.tif}, "
            f"max_retries={config.max_retries}, slippage={config.slippage_bps}bp"
        )

    def _client_oid(self, intent_id: str) -> str:
        """
        Generate clientOrderId from intent_id for idempotency.

        Format: TBP-{intent_id}
        Example: TBP-1697123456789-BTCUSDT-a3f5c2d1e4b8f9a2

        Args:
            intent_id: Unique intent identifier

        Returns:
            clientOrderId for exchange
        """
        return f"TBP-{intent_id}"

    def _reserve_budget(
        self,
        symbol: str,
        side: str,
        qty: float,
        price: float
    ) -> bool:
        """
        Reserve budget before placing order.

        Prevents overbooking when multiple signals fire simultaneously.

        Args:
            symbol: Trading pair
            side: Order side ("buy" or "sell")
            qty: Quantity to reserve
            price: Estimated price for notional calculation

        Returns:
            True if reservation successful, False otherwise
        """
        # Check minimum notional
        notional = qty * price
        if notional < self.cfg.min_notional:
            logger.warning(
                f"Order below min notional: {notional:.2f} < {self.cfg.min_notional:.2f} "
                f"({symbol} {side} {qty}@{price})"
            )
            return False

        # Reserve via portfolio
        try:
            success = self.pf.reserve(symbol, side, qty, price)
            if not success:
                logger.warning(f"Budget reservation failed: {symbol} {side} {qty}@{price}")
            return success

        except Exception as e:
            logger.error(f"Budget reservation error: {symbol} {side} {qty}@{price}: {e}")
            return False

    def _release_budget(
        self,
        symbol: str,
        side: str,
        qty: float,
        price: float
    ) -> None:
        """
        Release reserved budget (on failure or partial cancel).

        Args:
            symbol: Trading pair
            side: Order side
            qty: Quantity to release
            price: Price used for reservation
        """
        try:
            self.pf.release(symbol, side, qty, price)
            logger.debug(f"Released budget: {symbol} {side} {qty}@{price}")

        except Exception as e:
            logger.error(f"Budget release error: {symbol} {side} {qty}@{price}: {e}")

    def _apply_slippage_guard(
        self,
        symbol: str,
        side: str,
        limit_px: Optional[float],
        last_price: float
    ) -> Optional[float]:
        """
        Apply slippage guard to limit price.

        Prevents execution at unfavorable prices due to market movement.

        Args:
            symbol: Trading pair
            side: Order side ("buy" or "sell")
            limit_px: Requested limit price (None for market)
            last_price: Current market price

        Returns:
            Capped limit price or None for market order
        """
        if limit_px is None or last_price <= 0:
            return limit_px

        # Calculate max allowed price based on slippage
        slippage_factor = self.cfg.slippage_bps / 10000.0

        if side == "buy":
            # Buy: cap at last_price * (1 + slippage)
            max_price = last_price * (1 + slippage_factor)
            capped_price = min(limit_px, max_price)

            if capped_price < limit_px:
                logger.info(
                    f"Slippage guard: BUY price capped from {limit_px:.8f} to {capped_price:.8f} "
                    f"(last={last_price:.8f}, max_slip={slippage_factor*100:.2f}%)"
                )

            return capped_price

        else:  # sell
            # Sell: cap at last_price * (1 - slippage)
            min_price = last_price * (1 - slippage_factor)
            capped_price = max(limit_px, min_price)

            if capped_price > limit_px:
                logger.info(
                    f"Slippage guard: SELL price capped from {limit_px:.8f} to {capped_price:.8f} "
                    f"(last={last_price:.8f}, max_slip={slippage_factor*100:.2f}%)"
                )

            return capped_price

    def _place_order(
        self,
        symbol: str,
        side: str,
        qty: float,
        limit_px: Optional[float],
        client_order_id: str
    ) -> Dict[str, Any]:
        """
        Place order via exchange wrapper.

        Args:
            symbol: Trading pair
            side: Order side
            qty: Quantity
            limit_px: Limit price (None for market)
            client_order_id: Client order ID for idempotency

        Returns:
            CCXT order object
        """
        params = {
            "clientOrderId": client_order_id,
            "timeInForce": self.cfg.tif
        }

        if limit_px is None:
            # Market order
            return self.ex.create_market_order(symbol, side, qty, params=params)
        else:
            # Limit order
            return self.ex.create_limit_order(symbol, side, qty, limit_px, params=params)

    def _reconcile(
        self,
        symbol: str,
        order_id: str
    ) -> None:
        """
        Reconcile position with exchange fills.

        Fetches all trades for the order and applies them to portfolio.
        This converts reservations into actual positions with accurate prices and fees.

        Args:
            symbol: Trading pair
            order_id: Exchange order ID
        """
        try:
            trades = self.ex.fetch_order_trades(symbol, order_id)

            if not trades:
                logger.warning(f"No trades found for order {order_id}")
                return

            # Apply fills to portfolio
            self.pf.apply_fills(symbol, trades)

            # Audit log
            self.tl.write("order_audit", {
                "symbol": symbol,
                "order_id": order_id,
                "state": "RECONCILED",
                "fills_count": len(trades),
                "timestamp": time.time()
            })

            logger.info(f"Reconciled {len(trades)} fills for order {order_id}")

        except Exception as e:
            logger.error(f"Reconciliation failed for {order_id}: {e}")

    def handle_intent(self, intent: Dict[str, Any]) -> None:
        """
        Handle a trading intent through the FSM.

        FSM Flow:
            NEW → RESERVED → SENT → {FILLED | PARTIAL | CANCELED | RETRY}
                                  → FAILED_FINAL or SUCCESS

        This is the SINGLE entry point for all order execution.

        Args:
            intent: Intent dict from decision.assembler with:
                - intent_id: Unique ID
                - symbol: Trading pair
                - side: "buy" or "sell"
                - qty: Quantity
                - limit_price: Optional limit price
                - reason: Signal reason

        Returns:
            None (async execution, results via audit log)
        """
        # Extract intent fields
        intent_id = intent.get("intent_id")
        symbol = intent.get("symbol")
        side = intent.get("side", "buy")
        qty = float(intent.get("qty", 0.0))
        limit_px = intent.get("limit_price")
        reason = intent.get("reason", "UNKNOWN")

        # Idempotency check
        if intent_id in self._seen:
            logger.debug(f"Intent {intent_id} already processed (idempotent)")
            return

        self._seen.add(intent_id)

        # Audit: NEW state
        self.tl.write("order_audit", {
            "intent_id": intent_id,
            "state": "NEW",
            "symbol": symbol,
            "side": side,
            "qty": qty,
            "limit_price": limit_px,
            "reason": reason,
            "timestamp": time.time()
        })

        logger.info(f"Processing intent {intent_id}: {symbol} {side} {qty} (reason={reason})")

        # Get last price for slippage guard and reservation
        last_price = self.pf.last_price(symbol) or limit_px or 0.0

        if last_price <= 0:
            logger.error(f"Cannot determine price for {symbol}")
            self.tl.write("order_audit", {
                "intent_id": intent_id,
                "state": "FAILED",
                "reason": "no_price",
                "timestamp": time.time()
            })
            return

        # State: RESERVE
        if not self._reserve_budget(symbol, side, qty, last_price):
            self.tl.write("order_audit", {
                "intent_id": intent_id,
                "state": "FAILED",
                "reason": "reserve_failed",
                "timestamp": time.time()
            })
            return

        self.tl.write("order_audit", {
            "intent_id": intent_id,
            "state": "RESERVED",
            "reserved_qty": qty,
            "reserved_price": last_price,
            "timestamp": time.time()
        })

        # Apply slippage guard to limit price
        if limit_px is not None:
            limit_px = self._apply_slippage_guard(symbol, side, limit_px, last_price)

        # Retry loop with exponential backoff
        attempt = 0
        filled_qty = 0.0
        order_id = None
        client_order_id = self._client_oid(intent_id)

        while attempt <= self.cfg.max_retries:
            attempt += 1
            remaining_qty = qty - filled_qty

            if remaining_qty <= 0:
                break

            try:
                # State: SENT
                order = self._place_order(symbol, side, remaining_qty, limit_px, client_order_id)
                order_id = order.get("id")

                self.tl.write("order_audit", {
                    "intent_id": intent_id,
                    "state": "SENT",
                    "order_id": order_id,
                    "attempt": attempt,
                    "qty": remaining_qty,
                    "price": limit_px,
                    "timestamp": time.time()
                })

                logger.info(f"Order sent: {order_id} (attempt {attempt}/{self.cfg.max_retries})")

                # Wait for fill
                status = self.ex.wait_for_fill(symbol, order_id, timeout_ms=2500)
                filled_qty += status.get("filled", 0.0)

                # Check terminal states
                if status["status"] == "closed":
                    # State: FILLED
                    self.tl.write("order_audit", {
                        "intent_id": intent_id,
                        "state": "FILLED",
                        "order_id": order_id,
                        "filled_qty": filled_qty,
                        "avg_price": status.get("average"),
                        "timestamp": time.time()
                    })

                    logger.info(f"Order filled: {order_id} ({filled_qty} {symbol})")

                    # Reconcile with exchange
                    self._reconcile(symbol, order_id)
                    return

                elif status["status"] == "canceled":
                    # State: CANCELED
                    self.tl.write("order_audit", {
                        "intent_id": intent_id,
                        "state": "CANCELED",
                        "order_id": order_id,
                        "filled_qty": filled_qty,
                        "timestamp": time.time()
                    })

                    logger.warning(f"Order canceled: {order_id} (filled {filled_qty}/{qty})")
                    break

                else:
                    # State: PARTIAL
                    self.tl.write("order_audit", {
                        "intent_id": intent_id,
                        "state": "PARTIAL",
                        "order_id": order_id,
                        "filled_qty": filled_qty,
                        "remaining_qty": qty - filled_qty,
                        "timestamp": time.time()
                    })

                    logger.info(f"Partial fill: {order_id} ({filled_qty}/{qty})")

            except Exception as e:
                # State: ERROR
                self.tl.write("order_audit", {
                    "intent_id": intent_id,
                    "state": "ERROR",
                    "error": str(e),
                    "error_type": type(e).__name__,
                    "attempt": attempt,
                    "timestamp": time.time()
                })

                logger.error(f"Order attempt {attempt} failed: {e}")

            # Exponential backoff before retry
            if attempt < self.cfg.max_retries:
                backoff_ms = self.cfg.retry_backoff_ms * (2 ** (attempt - 1))
                logger.info(f"Retrying in {backoff_ms}ms...")
                time.sleep(backoff_ms / 1000.0)

        # Final state: FAILED or PARTIAL_SUCCESS
        if filled_qty > 0:
            # Partial success - some fills occurred
            self._reconcile(symbol, order_id)

        # Release unfilled budget
        unfilled_qty = qty - filled_qty
        if unfilled_qty > 0:
            self._release_budget(symbol, side, unfilled_qty, last_price)

        # Final audit
        final_state = "PARTIAL_SUCCESS" if filled_qty > 0 else "FAILED_FINAL"
        self.tl.write("order_audit", {
            "intent_id": intent_id,
            "state": final_state,
            "order_id": order_id,
            "filled_qty": filled_qty,
            "unfilled_qty": unfilled_qty,
            "attempts": attempt,
            "timestamp": time.time()
        })

        logger.warning(
            f"Intent {intent_id} completed with {final_state}: "
            f"filled {filled_qty}/{qty} after {attempt} attempts"
        )
