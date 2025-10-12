#!/usr/bin/env python3
"""
Trace Context - Unified Correlation IDs using ContextVars

Provides thread-safe correlation IDs that propagate automatically through
async and sync call chains without explicit passing.

Usage:
    with Trace(decision_id="dec_123") as tr:
        # All logs within this context see decision_id automatically
        do_work()

        tr.set(order_req_id="ord_456")
        # Now order_req_id is also visible
        place_order()
"""

from contextvars import ContextVar
import uuid
from typing import Optional, Dict, Any


# Context Variables for Correlation IDs
session_id_var: ContextVar[Optional[str]] = ContextVar("session_id", default=None)
decision_id_var: ContextVar[Optional[str]] = ContextVar("decision_id", default=None)
order_req_id_var: ContextVar[Optional[str]] = ContextVar("order_req_id", default=None)
client_order_id_var: ContextVar[Optional[str]] = ContextVar("client_order_id", default=None)
exchange_order_id_var: ContextVar[Optional[str]] = ContextVar("exchange_order_id", default=None)


def get_correlation_ids() -> Dict[str, Optional[str]]:
    """Get all current correlation IDs as dict."""
    return {
        "session_id": session_id_var.get(),
        "decision_id": decision_id_var.get(),
        "order_req_id": order_req_id_var.get(),
        "client_order_id": client_order_id_var.get(),
        "exchange_order_id": exchange_order_id_var.get(),
    }


def get_context_var(name: str) -> Optional[str]:
    """Get specific context variable value by name."""
    context_vars = {
        'session_id': session_id_var,
        'decision_id': decision_id_var,
        'order_req_id': order_req_id_var,
        'client_order_id': client_order_id_var,
        'exchange_order_id': exchange_order_id_var,
    }
    var = context_vars.get(name)
    return var.get() if var else None


def set_session_id(session_id: str) -> None:
    """Set global session ID (call once at bot startup)."""
    session_id_var.set(session_id)


def new_decision_id(prefix: str = "dec") -> str:
    """Generate new decision ID."""
    return f"{prefix}_{uuid.uuid4().hex[:12]}"


def new_order_req_id(prefix: str = "oreq") -> str:
    """Generate new order request ID."""
    return f"{prefix}_{uuid.uuid4().hex[:12]}"


class Trace:
    """
    Context manager for correlation ID propagation.

    Automatically sets and resets correlation IDs within a context.

    Example:
        with Trace(decision_id="dec_abc123") as tr:
            # decision_id is now visible to all logs
            evaluate_signal()

            tr.set(order_req_id="oreq_xyz789")
            # Now order_req_id is also visible
            place_order()
    """

    def __init__(
        self,
        session_id: Optional[str] = None,
        decision_id: Optional[str] = None,
        order_req_id: Optional[str] = None,
        client_order_id: Optional[str] = None,
        exchange_order_id: Optional[str] = None,
    ):
        """
        Initialize trace context.

        Args:
            session_id: Session ID (usually set globally, not per-context)
            decision_id: Decision/evaluation ID
            order_req_id: Order request ID
            client_order_id: Client-side order ID sent to exchange
            exchange_order_id: Exchange-assigned order ID
        """
        self.tokens = []
        self.values = {}

        # Preserve existing session_id if not explicitly set
        if session_id is None:
            session_id = session_id_var.get()

        # Build values dict (only set non-None values)
        if session_id is not None:
            self.values[session_id_var] = session_id
        if decision_id is not None:
            self.values[decision_id_var] = decision_id
        if order_req_id is not None:
            self.values[order_req_id_var] = order_req_id
        if client_order_id is not None:
            self.values[client_order_id_var] = client_order_id
        if exchange_order_id is not None:
            self.values[exchange_order_id_var] = exchange_order_id

    def __enter__(self):
        """Enter context - set all correlation IDs."""
        for var, val in self.values.items():
            token = var.set(val)
            self.tokens.append((var, token))
        return self

    def set(
        self,
        order_req_id: Optional[str] = None,
        client_order_id: Optional[str] = None,
        exchange_order_id: Optional[str] = None,
    ) -> 'Trace':
        """
        Set additional correlation IDs within the context.

        Useful for setting order IDs after decision context is already established.

        Args:
            order_req_id: Order request ID
            client_order_id: Client order ID
            exchange_order_id: Exchange order ID

        Returns:
            Self for chaining
        """
        updates = [
            (order_req_id_var, order_req_id),
            (client_order_id_var, client_order_id),
            (exchange_order_id_var, exchange_order_id),
        ]

        for var, val in updates:
            if val is not None:
                token = var.set(val)
                self.tokens.append((var, token))

        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        """Exit context - restore previous correlation IDs."""
        # Reset in reverse order (LIFO)
        while self.tokens:
            var, token = self.tokens.pop()
            var.reset(token)

        # Don't suppress exceptions
        return False


# Convenience functions for common patterns
def trace_decision(decision_id: Optional[str] = None) -> Trace:
    """Create trace context for decision evaluation."""
    if decision_id is None:
        decision_id = new_decision_id()
    return Trace(decision_id=decision_id)


def trace_order(
    decision_id: Optional[str] = None,
    order_req_id: Optional[str] = None,
    client_order_id: Optional[str] = None,
) -> Trace:
    """Create trace context for order execution."""
    if order_req_id is None:
        order_req_id = new_order_req_id()
    return Trace(
        decision_id=decision_id,
        order_req_id=order_req_id,
        client_order_id=client_order_id,
    )
