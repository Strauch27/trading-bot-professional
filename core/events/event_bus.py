#!/usr/bin/env python3
"""
Event Bus System for Trading Bot
================================

Lightweight pub/sub system for loose coupling between components.
Allows loggingx to emit events without hard dependencies to Engine/Telegram.

Features:
- Thread-safe event subscription and emission
- Fire-and-forget semantics (handlers cannot crash the system)
- No return values or error propagation
- Minimal overhead for production use

Usage:
    from event_bus import subscribe, emit

    # Subscribe to events
    subscribe("EXIT_FILLED", my_handler)

    # Emit events (fire-and-forget)
    emit("EXIT_FILLED", symbol="BTC/USDT", qty=0.1, price=45000.0)
"""

from __future__ import annotations
from typing import Callable, Dict, List, Any
from threading import RLock
import logging

# Global event registry - thread-safe
_subscribers: Dict[str, List[Callable]] = {}
_lock = RLock()

logger = logging.getLogger(__name__)


def subscribe(event_type: str, handler: Callable) -> None:
    """
    Subscribe a handler to an event type.

    Args:
        event_type: String identifier for the event (e.g., "EXIT_FILLED")
        handler: Callable that accepts **kwargs
    """
    with _lock:
        if event_type not in _subscribers:
            _subscribers[event_type] = []
        _subscribers[event_type].append(handler)

        logger.debug(f"Event handler registered: {event_type} -> {handler.__name__}")


def unsubscribe(event_type: str, handler: Callable) -> bool:
    """
    Remove a handler from an event type.

    Args:
        event_type: String identifier for the event
        handler: Previously registered handler

    Returns:
        True if handler was found and removed, False otherwise
    """
    with _lock:
        if event_type in _subscribers:
            try:
                _subscribers[event_type].remove(handler)
                logger.debug(f"Event handler removed: {event_type} -> {handler.__name__}")
                return True
            except ValueError:
                pass
    return False


def emit(event_type: str, **payload: Any) -> None:
    """
    Emit an event to all registered handlers.

    Fire-and-forget semantics: handlers are called but exceptions
    are caught and logged without stopping execution.

    Args:
        event_type: String identifier for the event
        **payload: Arbitrary keyword arguments passed to handlers
    """
    handlers = []
    with _lock:
        handlers = _subscribers.get(event_type, []).copy()

    if not handlers:
        return

    # Call handlers outside the lock to prevent deadlocks
    for handler in handlers:
        try:
            handler(**payload)
        except Exception as e:
            # Log but never crash - this is fire-and-forget
            logger.warning(f"Event handler {handler.__name__} failed for {event_type}: {e}")


def get_subscribers(event_type: str = None) -> Dict[str, int]:
    """
    Get count of subscribers for debugging.

    Args:
        event_type: Specific event type, or None for all

    Returns:
        Dict mapping event types to subscriber counts
    """
    with _lock:
        if event_type:
            return {event_type: len(_subscribers.get(event_type, []))}
        else:
            return {k: len(v) for k, v in _subscribers.items()}


def clear_all() -> None:
    """Clear all subscribers. Primarily for testing."""
    with _lock:
        _subscribers.clear()
        logger.debug("All event subscribers cleared")


# Convenience function for common events
def emit_trade_event(event_type: str, symbol: str, side: str, **kwargs) -> None:
    """
    Emit a trade-related event with standard fields.

    Args:
        event_type: Event identifier (e.g., "BUY_FILLED", "EXIT_FILLED")
        symbol: Trading symbol (e.g., "BTC/USDT")
        side: Trade side ("buy" or "sell")
        **kwargs: Additional event data
    """
    emit(event_type, symbol=symbol, side=side, **kwargs)


# Pre-defined event types for type safety
class EventTypes:
    """Standard event type constants"""
    BUY_FILLED = "BUY_FILLED"
    EXIT_FILLED = "EXIT_FILLED"
    POSITION_OPENED = "POSITION_OPENED"
    POSITION_CLOSED = "POSITION_CLOSED"
    ORDER_PLACED = "ORDER_PLACED"
    ORDER_CANCELLED = "ORDER_CANCELLED"
    PNL_UPDATED = "PNL_UPDATED"
    ERROR_OCCURRED = "ERROR_OCCURRED"