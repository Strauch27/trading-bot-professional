"""
FSM Event Definitions

Standard event types emitted during phase transitions.
These events are logged to JSONL for audit trail and replay.
"""

from datetime import datetime, timezone
from enum import Enum
from typing import Any, Dict


class EventType(str, Enum):
    """FSM Event Types"""

    # Phase Events
    PHASE_CHANGE = "PHASE_CHANGE"
    """Phase transition occurred"""

    PHASE_TIMEOUT = "PHASE_TIMEOUT"
    """Phase exceeded timeout threshold"""

    # Order Events
    ORDER_PLACED = "ORDER_PLACED"
    """Order successfully placed"""

    ORDER_FILLED = "ORDER_FILLED"
    """Order fully filled"""

    ORDER_PARTIAL = "ORDER_PARTIAL"
    """Order partially filled"""

    ORDER_CANCELED = "ORDER_CANCELED"
    """Order canceled"""

    ORDER_FAILED = "ORDER_FAILED"
    """Order placement failed"""

    # Trade Events
    TRADE_OPENED = "TRADE_OPENED"
    """Position opened"""

    TRADE_CLOSED = "TRADE_CLOSED"
    """Position closed"""

    # Signal Events
    SIGNAL_TRIGGERED = "SIGNAL_TRIGGERED"
    """Buy signal triggered"""

    SIGNAL_BLOCKED = "SIGNAL_BLOCKED"
    """Buy signal blocked by guards"""

    EXIT_TRIGGERED = "EXIT_TRIGGERED"
    """Exit condition triggered"""

    # Error Events
    ERROR_OCCURRED = "ERROR_OCCURRED"
    """Error occurred during phase processing"""

    ERROR_RECOVERED = "ERROR_RECOVERED"
    """Successfully recovered from error"""

    # System Events
    SYMBOL_REGISTERED = "SYMBOL_REGISTERED"
    """Symbol registered in FSM"""

    COOLDOWN_STARTED = "COOLDOWN_STARTED"
    """Cooldown period started"""

    COOLDOWN_EXPIRED = "COOLDOWN_EXPIRED"
    """Cooldown period expired"""


def create_event(
    event_type: EventType,
    symbol: str,
    **kwargs
) -> Dict[str, Any]:
    """
    Create standardized event dict.

    Args:
        event_type: Type of event
        symbol: Trading symbol
        **kwargs: Additional event-specific fields

    Returns:
        Event dict with standard fields
    """
    event = {
        "ts_iso": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "ts_ms": int(datetime.now(timezone.utc).timestamp() * 1000),
        "event_type": event_type.value,
        "symbol": symbol,
    }

    # Add optional fields
    event.update(kwargs)

    return event


def create_phase_change_event(
    symbol: str,
    prev_phase: str,
    next_phase: str,
    note: str = "",
    **kwargs
) -> Dict[str, Any]:
    """Create PHASE_CHANGE event with standard fields."""
    return create_event(
        EventType.PHASE_CHANGE,
        symbol=symbol,
        prev=prev_phase,
        next=next_phase,
        note=note,
        **kwargs
    )


def create_order_event(
    event_type: EventType,
    symbol: str,
    order_id: str,
    side: str,
    amount: float,
    price: float,
    **kwargs
) -> Dict[str, Any]:
    """Create order event with standard fields."""
    return create_event(
        event_type,
        symbol=symbol,
        order_id=order_id,
        side=side,
        amount=amount,
        price=price,
        **kwargs
    )


def create_trade_event(
    event_type: EventType,
    symbol: str,
    side: str,
    amount: float,
    price: float,
    pnl: float = None,
    **kwargs
) -> Dict[str, Any]:
    """Create trade event with standard fields."""
    event = create_event(
        event_type,
        symbol=symbol,
        side=side,
        amount=amount,
        price=price,
        **kwargs
    )

    if pnl is not None:
        event["pnl"] = pnl

    return event


def create_error_event(
    symbol: str,
    error: str,
    phase: str,
    error_count: int,
    **kwargs
) -> Dict[str, Any]:
    """Create error event with standard fields."""
    return create_event(
        EventType.ERROR_OCCURRED,
        symbol=symbol,
        error=error,
        phase=phase,
        error_count=error_count,
        **kwargs
    )
