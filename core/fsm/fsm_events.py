#!/usr/bin/env python3
"""
FSM Event System (Table-Driven)

Events that trigger FSM transitions.
Different from events.py (which is for logging), these are transition triggers.
"""

import time
from enum import Enum, auto
from typing import Any, Dict, Optional


class FSMEvent(Enum):
    """
    FSM Events - Triggers for state transitions

    Naming convention: WHAT_HAPPENED (past tense)
    """
    # Market data events
    TICK_RECEIVED = auto()           # New price data arrived

    # Entry evaluation events
    SIGNAL_DETECTED = auto()         # Buy signal triggered
    GUARDS_PASSED = auto()           # All guards allowed entry
    GUARDS_BLOCKED = auto()          # Guards blocked entry
    RISK_LIMITS_BLOCKED = auto()     # Risk limits blocked entry

    # Order lifecycle events (BUY)
    BUY_ORDER_PLACED = auto()        # Buy order sent to exchange
    BUY_ORDER_ACK = auto()           # Exchange acknowledged order
    BUY_ORDER_FILLED = auto()        # Buy order completely filled
    BUY_ORDER_PARTIAL = auto()       # Buy order partially filled
    BUY_ORDER_TIMEOUT = auto()       # Buy order timed out
    BUY_ORDER_REJECTED = auto()      # Exchange rejected buy order
    BUY_ORDER_CANCELLED = auto()     # Buy order was cancelled

    # Position lifecycle events
    POSITION_OPENED = auto()         # Position successfully opened
    POSITION_UPDATED = auto()        # Position state changed (PnL, etc)

    # Exit evaluation events
    EXIT_SIGNAL_TP = auto()          # Take profit triggered
    EXIT_SIGNAL_SL = auto()          # Stop loss triggered
    EXIT_SIGNAL_TIMEOUT = auto()     # Position timeout triggered
    EXIT_SIGNAL_TRAILING = auto()    # Trailing stop triggered

    # Order lifecycle events (SELL)
    SELL_ORDER_PLACED = auto()       # Sell order sent to exchange
    SELL_ORDER_ACK = auto()          # Exchange acknowledged order
    SELL_ORDER_FILLED = auto()       # Sell order completely filled
    SELL_ORDER_PARTIAL = auto()      # Sell order partially filled
    SELL_ORDER_TIMEOUT = auto()      # Sell order timed out
    SELL_ORDER_REJECTED = auto()     # Exchange rejected sell order
    SELL_ORDER_CANCELLED = auto()    # Sell order was cancelled

    # System events
    COOLDOWN_EXPIRED = auto()        # Cooldown period ended
    ERROR_OCCURRED = auto()          # Unhandled error
    MANUAL_HALT = auto()             # Manual intervention required


class EventContext:
    """
    Immutable context passed with each event.
    Contains all data needed for transition logic.
    """
    def __init__(
        self,
        event: FSMEvent,
        symbol: str,
        timestamp: Optional[float] = None,
        data: Optional[Dict[str, Any]] = None,
        order_id: Optional[str] = None,
        filled_qty: Optional[float] = None,
        avg_price: Optional[float] = None,
        error: Optional[Exception] = None
    ):
        self.event = event
        self.symbol = symbol
        self.timestamp = timestamp if timestamp is not None else time.time()
        self.data = data or {}
        self.order_id = order_id
        self.filled_qty = filled_qty
        self.avg_price = avg_price
        self.error = error

    def __repr__(self):
        return f"EventContext({self.event.name}, {self.symbol}, order_id={self.order_id})"

    def __str__(self):
        parts = [self.event.name, self.symbol]
        if self.order_id:
            parts.append(f"order={self.order_id}")
        if self.filled_qty:
            parts.append(f"qty={self.filled_qty:.6f}")
        return f"EventContext({', '.join(parts)})"
