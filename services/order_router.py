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

import hashlib
import logging
import threading
import time
from dataclasses import dataclass
from typing import Any, Dict, Optional

import ccxt

import config

# P1: State Persistence
from core.state_writer import DebouncedStateWriter

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
        fetch_order_on_fill: P2 - Fetch full order details on fill (default: False for performance)
    """
    max_retries: int = 3
    retry_backoff_ms: int = 400
    tif: str = "IOC"
    slippage_bps: int = 20
    min_notional: float = 5.0
    fetch_order_on_fill: bool = False  # P2: Performance optimization


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
        config: RouterConfig,
        event_bus=None
    ):
        """
        Initialize order router.

        Args:
            exchange_wrapper: ExchangeWrapper instance for order execution
            portfolio: Portfolio instance for reservations and reconciliation
            telemetry: JsonlWriter instance for audit logging
            config: RouterConfig with execution parameters
            event_bus: Optional EventBus for publishing order.filled events
        """
        self.ex = exchange_wrapper
        self.pf = portfolio
        self.tl = telemetry
        self.cfg = config
        self.event_bus = event_bus

        # CRITICAL FIX (C-SERV-01): Add lock for thread-safe state persistence
        self._meta_lock = threading.RLock()

        # Idempotency: track seen intent IDs
        self._seen: set = set()
        self._order_meta: Dict[str, Dict[str, Any]] = {}

        # P1: Debounced state persistence for order metadata
        self._init_state_persistence()

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
        # Mexc allows only 32 chars [0-9a-zA-Z_-], so hash the intent_id
        digest = hashlib.sha1(intent_id.encode("utf-8")).hexdigest()[:18]
        return f"TBP{digest}"

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
        price: float,
        exchange_error: Optional[str] = None,
        intent_id: Optional[str] = None
    ) -> None:
        """
        Release reserved budget (on failure or partial cancel).

        Args:
            symbol: Trading pair
            side: Order side
            qty: Quantity to release
            price: Price used for reservation
            exchange_error: Exchange error message/code (if failure)
            intent_id: Intent ID for tracing
        """
        try:
            self.pf.release(symbol, side, qty, price, exchange_error=exchange_error, intent_id=intent_id)
            logger.debug(f"Released budget: {symbol} {side} {qty}@{price} (intent={intent_id}, error={exchange_error})")

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

    def _log_order_context(
        self,
        intent_id: str,
        symbol: str,
        side: str,
        qty: float,
        limit_px: Optional[float],
        quote_budget: float,
        attempt: int,
        attempt_start_ts: float
    ) -> None:
        """
        Log canonical ORDER_CONTEXT block with all execution constraints.

        This creates a structured log entry capturing intent metadata, quotes,
        slippage caps, and router config for each order attempt. Essential for
        post-mortem analysis of execution failures.

        Args:
            intent_id: Unique intent identifier
            symbol: Trading pair (e.g., "BTC/USDT")
            side: Order side ("buy" or "sell")
            qty: Order quantity
            limit_px: Limit price (None for market orders)
            quote_budget: Reserved quote currency budget
            attempt: Current attempt number (1-based)
            attempt_start_ts: Timestamp when attempt started
        """
        # Calculate notional value
        notional = qty * (limit_px or 0.0) if limit_px else quote_budget

        logger.info(
            "ORDER_CONTEXT",
            extra={
                "event_type": "ORDER_CONTEXT",
                "intent_id": intent_id,
                "symbol": symbol,
                "side": side,
                "qty": qty,
                "limit_px": limit_px,
                "quote_budget": quote_budget,
                "notional": notional,
                "tif": self.cfg.tif,
                "slippage_bps": self.cfg.slippage_bps,
                "min_notional": self.cfg.min_notional,
                "max_retries": self.cfg.max_retries,
                "retry_backoff_ms": self.cfg.retry_backoff_ms,
                "attempt": attempt,
                "attempt_start_ts": attempt_start_ts
            }
        )

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

        Checks ORDER_FLOW_ENABLED kill-switch before placing orders.

        Args:
            symbol: Trading pair
            side: Order side
            qty: Quantity
            limit_px: Limit price (None for market)
            client_order_id: Client order ID for idempotency

        Returns:
            CCXT order object

        Raises:
            RuntimeError: If ORDER_FLOW_ENABLED is False (kill-switch activated)
        """
        # Check ORDER_FLOW_ENABLED kill-switch
        order_flow_enabled = getattr(config, 'ORDER_FLOW_ENABLED', True)
        if not order_flow_enabled:
            error_msg = "ORDER_FLOW_ENABLED=False: Order placement disabled by kill-switch"
            logger.warning(error_msg, extra={'symbol': symbol, 'side': side, 'qty': qty})
            raise RuntimeError(error_msg)

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

    def _publish_filled(
        self,
        symbol: str,
        order_id: str,
        filled_qty: float,
        average_px: Optional[float] = None
    ) -> None:
        """
        Publish order.filled event for reconciler to handle.

        Separates order execution from reconciliation by publishing
        an event that the Reconciler will handle.

        Args:
            symbol: Trading pair
            order_id: Exchange order ID
        """
        # CRITICAL FIX (C-SERV-01): Protect metadata access
        with self._meta_lock:
            metadata = self._order_meta.pop(order_id, {})
        # P1: Persist metadata after removal (debounced)
        self._persist_meta_state()

        if self.event_bus:
            try:
                payload: Dict[str, Any] = {
                    "symbol": symbol,
                    "order_id": order_id,
                    "filled_qty": filled_qty,
                    "average_price": average_px,
                    "timestamp": time.time()
                }

                payload.update(metadata)

                # P2: Optionally fetch final order snapshot for downstream processing
                # Default: False for performance (saves ~30-50ms per fill)
                if self.cfg.fetch_order_on_fill:
                    try:
                        fetch_start = time.time()
                        order_details = self.ex.fetch_order(symbol, order_id)
                        if order_details:
                            payload["order"] = order_details
                            fetch_latency_ms = (time.time() - fetch_start) * 1000
                            payload["fetch_order_latency_ms"] = fetch_latency_ms
                            logger.debug(f"Fetched order details for {order_id} in {fetch_latency_ms:.1f}ms")
                    except Exception as fetch_error:
                        logger.debug(f"Failed to fetch order snapshot for {order_id}: {fetch_error}")

                self.event_bus.publish("order.filled", payload)
                logger.debug(f"Published order.filled event for {order_id}")
            except Exception as e:
                logger.error(f"Failed to publish order.filled event: {e}")

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

        # Enforce ORDER_FLOW kill-switch before doing anything costly
        if not getattr(config, 'ORDER_FLOW_ENABLED', True):
            error_msg = "ORDER_FLOW_ENABLED=False: Order placement disabled by kill-switch"
            logger.warning(
                error_msg,
                extra={'intent_id': intent_id, 'symbol': symbol, 'side': side}
            )
            self.tl.write("order_audit", {
                "intent_id": intent_id,
                "state": "FAILED",
                "reason": "order_flow_disabled",
                "timestamp": time.time()
            })
            if self.event_bus:
                try:
                    self.event_bus.publish("order.failed", {
                        "intent_id": intent_id,
                        "symbol": symbol,
                        "side": side,
                        "reason": "order_flow_disabled",
                        "error": error_msg
                    })
                except Exception as e:
                    logger.warning(f"Failed to publish order.failed event: {e}")
            return

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

        # Check ORDER_FLOW_ENABLED kill-switch early (before budget reservation)
        order_flow_enabled = getattr(config, 'ORDER_FLOW_ENABLED', True)
        if not order_flow_enabled:
            error_msg = "ORDER_FLOW_ENABLED=False: Order execution disabled by kill-switch"
            logger.warning(error_msg, extra={'intent_id': intent_id, 'symbol': symbol, 'side': side})
            self.tl.write("order_audit", {
                "intent_id": intent_id,
                "state": "FAILED",
                "reason": "kill_switch_disabled",
                "error": error_msg,
                "timestamp": time.time()
            })

            # Publish order.failed event to notify engine
            if self.event_bus:
                try:
                    self.event_bus.publish("order.failed", {
                        "intent_id": intent_id,
                        "symbol": symbol,
                        "side": side,
                        "reason": "kill_switch_disabled",
                        "error": error_msg
                    })
                except Exception as e:
                    logger.warning(f"Failed to publish order.failed event: {e}")
            return

        # Get last price for slippage guard and reservation
        last_price = self.pf.last_price(symbol) or limit_px or 0.0

        if last_price <= 0:
            error_msg = f"Cannot determine price for {symbol}"
            logger.error(error_msg, extra={'intent_id': intent_id})
            self.tl.write("order_audit", {
                "intent_id": intent_id,
                "state": "FAILED",
                "reason": "no_price",
                "timestamp": time.time()
            })

            # Publish order.failed event to notify engine
            if self.event_bus:
                try:
                    self.event_bus.publish("order.failed", {
                        "intent_id": intent_id,
                        "symbol": symbol,
                        "side": side,
                        "reason": "no_price",
                        "error": error_msg
                    })
                except Exception as e:
                    logger.warning(f"Failed to publish order.failed event: {e}")
            return

        # State: RESERVE
        if not self._reserve_budget(symbol, side, qty, last_price):
            error_msg = f"Budget reservation failed for {symbol}"
            logger.error(error_msg, extra={'intent_id': intent_id})
            self.tl.write("order_audit", {
                "intent_id": intent_id,
                "state": "FAILED",
                "reason": "reserve_failed",
                "timestamp": time.time()
            })

            # Publish order.failed event to notify engine
            if self.event_bus:
                try:
                    self.event_bus.publish("order.failed", {
                        "intent_id": intent_id,
                        "symbol": symbol,
                        "side": side,
                        "reason": "reserve_failed",
                        "error": error_msg
                    })
                except Exception as e:
                    logger.warning(f"Failed to publish order.failed event: {e}")
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
        last_status: Optional[Dict[str, Any]] = None
        last_exchange_error: Optional[str] = None  # Track last error for budget release
        client_order_id = self._client_oid(intent_id)
        base_meta = {
            "intent_id": intent_id,
            "side": side,
            "reason": reason,
            "qty": qty,
            "symbol": symbol,
            "client_order_id": client_order_id,
            "limit_price": limit_px
        }

        while attempt <= self.cfg.max_retries:
            attempt += 1
            attempt_start_ts = time.time()  # Track latency per attempt
            remaining_qty = qty - filled_qty

            if remaining_qty <= 0:
                break

            # Log canonical ORDER_CONTEXT for this attempt
            quote_budget = remaining_qty * (limit_px if limit_px else last_price)
            self._log_order_context(
                intent_id=intent_id,
                symbol=symbol,
                side=side,
                qty=remaining_qty,
                limit_px=limit_px,
                quote_budget=quote_budget,
                attempt=attempt,
                attempt_start_ts=attempt_start_ts
            )

            try:
                # State: SENT
                order = self._place_order(symbol, side, remaining_qty, limit_px, client_order_id)
                order_id = order.get("id")

                if order_id:
                    meta = dict(base_meta)
                    meta.update({"attempt": attempt, "filled_qty": filled_qty})
                    meta["start_ts"] = time.time()  # Fix: Set timestamp for recovery filtering
                    # CRITICAL FIX (C-SERV-01): Protect metadata write
                    with self._meta_lock:
                        self._order_meta[order_id] = meta
                    # P1: Persist metadata (debounced)
                    self._persist_meta_state()

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
                last_status = status
                filled_qty += status.get("filled", 0.0)

                if order_id in self._order_meta:
                    self._order_meta[order_id]["filled_qty"] = filled_qty
                    # P1: Persist metadata (debounced)
                    self._persist_meta_state()

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

                    # Publish filled event for reconciliation
                    self._publish_filled(
                        symbol,
                        order_id,
                        filled_qty=filled_qty,
                        average_px=status.get("average")
                    )
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
                    self._order_meta.pop(order_id, None)
                    self._persist_meta_state()  # P1
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
                # State: ERROR - Extract detailed exchange error information
                exchange_error_code = None
                exchange_error_msg = str(e)

                # Extract exchange-specific error codes from CCXT exceptions
                if isinstance(e, ccxt.BaseError):
                    if hasattr(e, 'code'):
                        exchange_error_code = e.code
                    # For MEXC/other exchanges, error messages often contain error codes
                    # Format: "mexc {"code":-1234,"msg":"...}"
                    import re
                    code_match = re.search(r'"code"\s*:\s*(-?\d+)', str(e))
                    if code_match:
                        exchange_error_code = code_match.group(1)

                # Store for budget release
                last_exchange_error = f"{type(e).__name__}:{exchange_error_code or 'N/A'}:{exchange_error_msg[:100]}"

                # Calculate latency since order attempt started
                attempt_latency_ms = (time.time() - attempt_start_ts) * 1000 if 'attempt_start_ts' in locals() else 0

                self.tl.write("order_audit", {
                    "intent_id": intent_id,
                    "state": "ERROR",
                    "error": exchange_error_msg,
                    "error_type": type(e).__name__,
                    "exchange_error_code": exchange_error_code,
                    "attempt": attempt,
                    "latency_ms": attempt_latency_ms,
                    "timestamp": time.time()
                })

                # CRITICAL: Log to ERROR level for visibility (not just order_audit)
                logger.error(
                    f"ORDER_FAILED intent={intent_id} symbol={symbol} side={side} qty={qty} "
                    f"attempt={attempt}/{self.cfg.max_retries} error_type={type(e).__name__} "
                    f"exchange_code={exchange_error_code} latency={attempt_latency_ms:.1f}ms "
                    f"error='{exchange_error_msg}'"
                )

                if order_id:
                    self._order_meta.pop(order_id, None)
                    self._persist_meta_state()  # P1

            # Exponential backoff before retry
            if attempt < self.cfg.max_retries:
                backoff_ms = self.cfg.retry_backoff_ms * (2 ** (attempt - 1))
                logger.info(f"Retrying in {backoff_ms}ms...")
                time.sleep(backoff_ms / 1000.0)

        # Final state: FAILED or PARTIAL_SUCCESS
        if filled_qty > 0 and order_id:
            # Partial success - some fills occurred
            self._publish_filled(
                symbol,
                order_id,
                filled_qty=filled_qty,
                average_px=last_status.get("average") if last_status else None
            )
        else:
            if order_id:
                self._order_meta.pop(order_id, None)
                self._persist_meta_state()  # P1

        # Release unfilled budget
        unfilled_qty = qty - filled_qty
        if unfilled_qty > 0:
            self._release_budget(
                symbol, side, unfilled_qty, last_price,
                exchange_error=last_exchange_error,
                intent_id=intent_id
            )

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

        # Write ORDER_FAILED event to orders.jsonl for complete failures
        if final_state == "FAILED_FINAL":
            self.tl.order_failed(
                intent_id=intent_id,
                symbol=symbol,
                side=side,
                qty=qty,
                order_id=order_id,
                filled_qty=filled_qty,
                attempts=attempt,
                exchange_error=last_exchange_error,
                timestamp=time.time()
            )

            # Publish order.failed event for engine to cleanup pending intent
            if self.event_bus:
                try:
                    self.event_bus.publish("order.failed", {
                        "intent_id": intent_id,
                        "symbol": symbol,
                        "side": side,
                        "qty": qty,
                        "order_id": order_id,
                        "exchange_error": last_exchange_error,
                        "attempts": attempt,
                        "timestamp": time.time()
                    })
                except Exception as e:
                    logger.error(f"Failed to publish order.failed event: {e}")

        logger.warning(
            f"Intent {intent_id} completed with {final_state}: "
            f"filled {filled_qty}/{qty} after {attempt} attempts"
        )

    # ==================================================================================
    # State Persistence (P1)
    # ==================================================================================

    def _init_state_persistence(self):
        """Initialize debounced state writer for order metadata"""
        try:
            self._meta_state_writer = DebouncedStateWriter(
                file_path=config.ORDER_ROUTER_META_FILE,
                interval_s=config.STATE_PERSIST_INTERVAL_S,
                auto_start=True
            )

            # Recover existing metadata
            self._recover_meta_state()

            logger.info(
                f"OrderRouter state persistence initialized: "
                f"file={config.ORDER_ROUTER_META_FILE}, "
                f"interval={config.STATE_PERSIST_INTERVAL_S}s"
            )

        except Exception as e:
            logger.error(f"Failed to initialize OrderRouter state persistence: {e}")
            self._meta_state_writer = None

    def _recover_meta_state(self):
        """Recover order metadata from previous session"""
        try:
            if not self._meta_state_writer:
                return

            state = self._meta_state_writer.get()
            if not state:
                logger.info("No previous order metadata to recover")
                return

            # Filter old metadata (>24h)
            cutoff = time.time() - config.ORDER_META_MAX_AGE_S
            recovered = 0
            filtered = 0

            for order_id, metadata in state.get("order_meta", {}).items():
                start_ts = metadata.get("start_ts", 0)
                if start_ts > cutoff:
                    self._order_meta[order_id] = metadata
                    recovered += 1
                else:
                    filtered += 1

            if recovered > 0:
                logger.info(
                    f"Recovered {recovered} order metadata entries "
                    f"(filtered {filtered} stale entries)"
                )
            elif filtered > 0:
                logger.info(f"All {filtered} previous metadata entries were stale")

        except Exception as e:
            logger.warning(f"Order metadata recovery failed: {e}")

    def _persist_meta_state(self):
        """Update persisted order metadata (debounced)"""
        try:
            if not self._meta_state_writer:
                return

            # CRITICAL FIX (C-SERV-01): Protect _order_meta access with lock
            with self._meta_lock:
                state = {
                    "order_meta": dict(self._order_meta),  # Create copy inside lock
                    "last_update": time.time()
                }

            # Update outside lock to avoid holding lock during I/O
            self._meta_state_writer.update(state)

        except Exception as e:
            logger.debug(f"Order metadata persist failed: {e}")

    def shutdown(self):
        """Shutdown router and persist final state"""
        try:
            if hasattr(self, '_meta_state_writer') and self._meta_state_writer:
                logger.info("Persisting final OrderRouter state...")
                self._persist_meta_state()
                self._meta_state_writer.shutdown()
                logger.info("OrderRouter state persisted successfully")
        except Exception as e:
            logger.error(f"Failed to persist OrderRouter state on shutdown: {e}")
