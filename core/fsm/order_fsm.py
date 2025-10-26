#!/usr/bin/env python3
"""
Order Finite State Machine - Explicit state tracking for order lifecycle

Provides explicit state machine for order lifecycle with:
- State validation (PENDING → PARTIAL → FILLED/CANCELED/EXPIRED)
- Transition guards
- State history tracking
- Integration with COID Manager and Fill Telemetry
"""

import logging
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


class OrderState(Enum):
    """
    Order lifecycle states.

    Terminal states: FILLED, CANCELED, EXPIRED
    Non-terminal states: PENDING, PARTIAL
    """
    PENDING = "pending"      # Order submitted, no fills yet
    PARTIAL = "partial"      # Partially filled
    FILLED = "filled"        # Fully filled (terminal)
    CANCELED = "canceled"    # Canceled by user/system (terminal)
    EXPIRED = "expired"      # Expired (IOC timeout) (terminal)
    FAILED = "failed"        # Failed to submit (terminal)

    def is_terminal(self) -> bool:
        """Check if this is a terminal state"""
        return self in (OrderState.FILLED, OrderState.CANCELED, OrderState.EXPIRED, OrderState.FAILED)

    def can_transition_to(self, new_state: 'OrderState') -> bool:
        """Check if transition to new state is valid"""
        # Terminal states cannot transition
        if self.is_terminal():
            return False

        # Valid transitions
        valid_transitions = {
            OrderState.PENDING: {OrderState.PARTIAL, OrderState.FILLED, OrderState.CANCELED, OrderState.EXPIRED, OrderState.FAILED},
            OrderState.PARTIAL: {OrderState.FILLED, OrderState.CANCELED, OrderState.EXPIRED},
        }

        return new_state in valid_transitions.get(self, set())


@dataclass
class StateTransition:
    """Record of a state transition"""
    from_state: OrderState
    to_state: OrderState
    timestamp: float
    reason: Optional[str] = None
    metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass
class OrderFSM:
    """
    Finite State Machine for Order Lifecycle.

    Tracks order state with explicit state machine semantics:
    - State validation
    - Transition guards
    - State history
    - Fill tracking
    """

    # Core identifiers
    order_id: str
    symbol: str
    side: str  # 'buy' or 'sell'

    # State tracking
    state: OrderState = OrderState.PENDING

    # Quantity tracking
    filled_qty: float = 0.0
    total_qty: float = 0.0

    # Price tracking
    avg_fill_price: float = 0.0
    limit_price: Optional[float] = None

    # Timing
    created_ts: float = field(default_factory=time.time)
    first_fill_ts: Optional[float] = None
    completed_ts: Optional[float] = None

    # Fees
    total_fees: float = 0.0

    # State history
    state_history: List[StateTransition] = field(default_factory=list)

    # Metadata
    metadata: Dict[str, Any] = field(default_factory=dict)

    def __post_init__(self):
        """Initialize with PENDING state in history"""
        if not self.state_history:
            self.state_history.append(StateTransition(
                from_state=OrderState.PENDING,
                to_state=OrderState.PENDING,
                timestamp=self.created_ts,
                reason="Order created"
            ))

    # ========================================================================
    # State Transition Methods
    # ========================================================================

    def transition(
        self,
        new_state: OrderState,
        reason: Optional[str] = None,
        **metadata
    ) -> bool:
        """
        Transition to new state with validation.

        Args:
            new_state: Target state
            reason: Reason for transition (for logging)
            **metadata: Additional metadata for transition

        Returns:
            True if transition succeeded, False if invalid

        Raises:
            ValueError: If trying to transition from terminal state
        """
        # Check if current state is terminal
        if self.state.is_terminal():
            logger.error(
                f"Cannot transition from terminal state {self.state.value} to {new_state.value} "
                f"for order {self.order_id}"
            )
            raise ValueError(f"Cannot transition from terminal state {self.state.value}")

        # Validate transition
        if not self.state.can_transition_to(new_state):
            logger.warning(
                f"Invalid transition from {self.state.value} to {new_state.value} "
                f"for order {self.order_id}"
            )
            return False

        # Record transition
        transition = StateTransition(
            from_state=self.state,
            to_state=new_state,
            timestamp=time.time(),
            reason=reason or f"Transition to {new_state.value}",
            metadata=metadata
        )
        self.state_history.append(transition)

        # Update state
        old_state = self.state
        self.state = new_state

        # Update timestamps for terminal states
        if new_state.is_terminal():
            self.completed_ts = time.time()

        logger.info(
            f"Order {self.order_id} ({self.symbol} {self.side.upper()}): "
            f"{old_state.value} → {new_state.value} | {reason or 'No reason'}"
        )

        return True

    def record_fill(
        self,
        fill_qty: float,
        fill_price: float,
        fill_fee: float = 0.0,
        auto_transition: bool = True
    ) -> bool:
        """
        Record a fill and optionally transition state.

        Args:
            fill_qty: Quantity filled in this trade
            fill_price: Price of this fill
            fill_fee: Fee for this fill
            auto_transition: Automatically transition to PARTIAL or FILLED

        Returns:
            True if fill recorded successfully
        """
        # Update first fill timestamp
        if self.first_fill_ts is None:
            self.first_fill_ts = time.time()

        # Update weighted average price
        previous_qty = self.filled_qty
        new_qty = previous_qty + fill_qty

        if previous_qty > 0:
            # Weighted average
            self.avg_fill_price = (
                (previous_qty * self.avg_fill_price + fill_qty * fill_price) / new_qty
            )
        else:
            self.avg_fill_price = fill_price

        # Update quantities and fees
        self.filled_qty = new_qty
        self.total_fees += fill_fee

        logger.debug(
            f"Fill recorded for {self.order_id}: {fill_qty:.6f} @ {fill_price:.6f}, "
            f"cumulative: {new_qty:.6f}/{self.total_qty:.6f} ({self.fill_rate:.1%})"
        )

        # Auto-transition if enabled
        if auto_transition:
            if self.is_fully_filled():
                self.transition(OrderState.FILLED, reason=f"Fully filled: {self.filled_qty:.6f}/{self.total_qty:.6f}")
            elif self.filled_qty > 0 and self.state == OrderState.PENDING:
                self.transition(OrderState.PARTIAL, reason=f"Partial fill: {self.filled_qty:.6f}/{self.total_qty:.6f}")

        return True

    def cancel(self, reason: Optional[str] = None) -> bool:
        """
        Cancel the order.

        Args:
            reason: Cancellation reason

        Returns:
            True if cancellation recorded
        """
        return self.transition(
            OrderState.CANCELED,
            reason=reason or "Order canceled",
            filled_qty=self.filled_qty,
            fill_rate=self.fill_rate
        )

    def expire(self, reason: Optional[str] = None) -> bool:
        """
        Mark order as expired (IOC timeout).

        Args:
            reason: Expiration reason

        Returns:
            True if expiration recorded
        """
        return self.transition(
            OrderState.EXPIRED,
            reason=reason or "Order expired (IOC timeout)",
            filled_qty=self.filled_qty,
            fill_rate=self.fill_rate
        )

    def fail(self, reason: Optional[str] = None, error: Optional[str] = None) -> bool:
        """
        Mark order as failed.

        Args:
            reason: Failure reason
            error: Error message

        Returns:
            True if failure recorded
        """
        return self.transition(
            OrderState.FAILED,
            reason=reason or "Order failed",
            error=error
        )

    # ========================================================================
    # State Query Methods
    # ========================================================================

    def is_terminal(self) -> bool:
        """Check if order is in terminal state"""
        return self.state.is_terminal()

    def is_pending(self) -> bool:
        """Check if order is pending"""
        return self.state == OrderState.PENDING

    def is_partial(self) -> bool:
        """Check if order is partially filled"""
        return self.state == OrderState.PARTIAL

    def is_filled(self) -> bool:
        """Check if order is fully filled"""
        return self.state == OrderState.FILLED

    def is_canceled(self) -> bool:
        """Check if order is canceled"""
        return self.state == OrderState.CANCELED

    def is_expired(self) -> bool:
        """Check if order is expired"""
        return self.state == OrderState.EXPIRED

    def is_failed(self) -> bool:
        """Check if order failed"""
        return self.state == OrderState.FAILED

    def is_fully_filled(self, tolerance: float = 0.001) -> bool:
        """
        Check if order is fully filled within tolerance.

        Args:
            tolerance: Fill tolerance (0.001 = 99.9% counts as filled)

        Returns:
            True if fill_rate >= (1.0 - tolerance)
        """
        if self.total_qty == 0:
            return False
        return self.fill_rate >= (1.0 - tolerance)

    # ========================================================================
    # Metrics & Statistics
    # ========================================================================

    @property
    def fill_rate(self) -> float:
        """Get fill rate (0.0 to 1.0)"""
        if self.total_qty == 0:
            return 0.0
        return self.filled_qty / self.total_qty

    @property
    def remaining_qty(self) -> float:
        """Get remaining quantity to fill"""
        return max(0.0, self.total_qty - self.filled_qty)

    @property
    def age_seconds(self) -> float:
        """Get order age in seconds"""
        if self.completed_ts:
            return self.completed_ts - self.created_ts
        return time.time() - self.created_ts

    @property
    def fill_age_seconds(self) -> Optional[float]:
        """Get time since first fill (None if no fills)"""
        if self.first_fill_ts is None:
            return None
        if self.completed_ts:
            return self.completed_ts - self.first_fill_ts
        return time.time() - self.first_fill_ts

    @property
    def num_transitions(self) -> int:
        """Get number of state transitions"""
        return len(self.state_history)

    def get_statistics(self) -> Dict[str, Any]:
        """Get comprehensive order statistics"""
        return {
            'order_id': self.order_id,
            'symbol': self.symbol,
            'side': self.side,
            'state': self.state.value,
            'is_terminal': self.is_terminal(),
            'filled_qty': self.filled_qty,
            'total_qty': self.total_qty,
            'fill_rate': self.fill_rate,
            'remaining_qty': self.remaining_qty,
            'avg_fill_price': self.avg_fill_price,
            'limit_price': self.limit_price,
            'total_fees': self.total_fees,
            'age_seconds': self.age_seconds,
            'fill_age_seconds': self.fill_age_seconds,
            'num_transitions': self.num_transitions,
            'created_ts': self.created_ts,
            'first_fill_ts': self.first_fill_ts,
            'completed_ts': self.completed_ts
        }

    def get_state_history_summary(self) -> List[Dict[str, Any]]:
        """Get summary of state history"""
        return [
            {
                'from': t.from_state.value,
                'to': t.to_state.value,
                'timestamp': t.timestamp,
                'reason': t.reason,
                'metadata': t.metadata
            }
            for t in self.state_history
        ]

    # ========================================================================
    # Serialization
    # ========================================================================

    def to_dict(self) -> Dict[str, Any]:
        """Serialize FSM to dictionary"""
        return {
            'order_id': self.order_id,
            'symbol': self.symbol,
            'side': self.side,
            'state': self.state.value,
            'filled_qty': self.filled_qty,
            'total_qty': self.total_qty,
            'avg_fill_price': self.avg_fill_price,
            'limit_price': self.limit_price,
            'created_ts': self.created_ts,
            'first_fill_ts': self.first_fill_ts,
            'completed_ts': self.completed_ts,
            'total_fees': self.total_fees,
            'state_history': self.get_state_history_summary(),
            'metadata': self.metadata
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> 'OrderFSM':
        """Deserialize FSM from dictionary"""
        # Parse state
        state = OrderState(data.get('state', 'pending'))

        # Reconstruct state history
        state_history = []
        for h in data.get('state_history', []):
            state_history.append(StateTransition(
                from_state=OrderState(h['from']),
                to_state=OrderState(h['to']),
                timestamp=h['timestamp'],
                reason=h.get('reason'),
                metadata=h.get('metadata', {})
            ))

        return cls(
            order_id=data['order_id'],
            symbol=data['symbol'],
            side=data['side'],
            state=state,
            filled_qty=data.get('filled_qty', 0.0),
            total_qty=data.get('total_qty', 0.0),
            avg_fill_price=data.get('avg_fill_price', 0.0),
            limit_price=data.get('limit_price'),
            created_ts=data.get('created_ts', time.time()),
            first_fill_ts=data.get('first_fill_ts'),
            completed_ts=data.get('completed_ts'),
            total_fees=data.get('total_fees', 0.0),
            state_history=state_history,
            metadata=data.get('metadata', {})
        )


# ============================================================================
# FSM Manager for tracking multiple orders
# ============================================================================

class OrderFSMManager:
    """
    Manager for multiple order FSMs.

    Provides:
    - Order FSM creation and lookup
    - State queries across orders
    - Statistics aggregation
    """

    def __init__(self):
        self._fsms: Dict[str, OrderFSM] = {}
        self._by_symbol: Dict[str, List[str]] = {}  # symbol -> [order_ids]
        logger.info("OrderFSMManager initialized")

    def create_order(
        self,
        order_id: str,
        symbol: str,
        side: str,
        total_qty: float,
        limit_price: Optional[float] = None,
        **metadata
    ) -> OrderFSM:
        """
        Create new order FSM.

        Args:
            order_id: Unique order identifier
            symbol: Trading symbol
            side: 'buy' or 'sell'
            total_qty: Total quantity to fill
            limit_price: Limit price (optional)
            **metadata: Additional metadata

        Returns:
            OrderFSM instance
        """
        if order_id in self._fsms:
            logger.warning(f"Order FSM {order_id} already exists")
            return self._fsms[order_id]

        fsm = OrderFSM(
            order_id=order_id,
            symbol=symbol,
            side=side,
            total_qty=total_qty,
            limit_price=limit_price,
            metadata=metadata
        )

        self._fsms[order_id] = fsm

        # Index by symbol
        if symbol not in self._by_symbol:
            self._by_symbol[symbol] = []
        self._by_symbol[symbol].append(order_id)

        logger.info(f"Created Order FSM: {order_id} ({symbol} {side.upper()}, qty={total_qty:.6f})")
        return fsm

    def get_order(self, order_id: str) -> Optional[OrderFSM]:
        """Get order FSM by ID"""
        return self._fsms.get(order_id)

    def get_orders_by_symbol(self, symbol: str) -> List[OrderFSM]:
        """Get all order FSMs for symbol"""
        order_ids = self._by_symbol.get(symbol, [])
        return [self._fsms[oid] for oid in order_ids if oid in self._fsms]

    def get_active_orders(self) -> List[OrderFSM]:
        """Get all non-terminal orders"""
        return [fsm for fsm in self._fsms.values() if not fsm.is_terminal()]

    def get_terminal_orders(self) -> List[OrderFSM]:
        """Get all terminal orders"""
        return [fsm for fsm in self._fsms.values() if fsm.is_terminal()]

    def cleanup_terminal_orders(self, age_threshold_seconds: float = 3600) -> int:
        """
        Cleanup old terminal orders.

        Args:
            age_threshold_seconds: Remove terminal orders older than this

        Returns:
            Number of orders cleaned up
        """
        to_remove = []
        now = time.time()

        for order_id, fsm in self._fsms.items():
            if fsm.is_terminal() and fsm.completed_ts:
                age = now - fsm.completed_ts
                if age > age_threshold_seconds:
                    to_remove.append(order_id)

        for order_id in to_remove:
            fsm = self._fsms.pop(order_id)
            # Remove from symbol index
            if fsm.symbol in self._by_symbol:
                self._by_symbol[fsm.symbol] = [
                    oid for oid in self._by_symbol[fsm.symbol] if oid != order_id
                ]

        if to_remove:
            logger.info(f"Cleaned up {len(to_remove)} terminal orders (age > {age_threshold_seconds}s)")

        return len(to_remove)

    def get_statistics(self) -> Dict[str, Any]:
        """Get aggregate statistics"""
        total = len(self._fsms)
        active = len(self.get_active_orders())
        terminal = len(self.get_terminal_orders())

        # Count by state
        state_counts = {}
        for fsm in self._fsms.values():
            state = fsm.state.value
            state_counts[state] = state_counts.get(state, 0) + 1

        return {
            'total_orders': total,
            'active_orders': active,
            'terminal_orders': terminal,
            'state_counts': state_counts,
            'symbols_tracked': len(self._by_symbol)
        }


# ============================================================================
# Global singleton
# ============================================================================

_order_fsm_manager = None


def get_order_fsm_manager() -> OrderFSMManager:
    """Get global OrderFSMManager singleton"""
    global _order_fsm_manager
    if _order_fsm_manager is None:
        _order_fsm_manager = OrderFSMManager()
        logger.info("Global OrderFSMManager initialized")
    return _order_fsm_manager
