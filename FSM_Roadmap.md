# FSM (Finite State Machine) Implementation Roadmap

**Status**: Phase 0 - Planning Complete, Ready for Implementation
**Last Updated**: 2025-01-12
**Owner**: Trading Bot Professional v11+

---

## Overview

This roadmap transforms the current handler-based FSM into a **table-driven, event-based FSM** with proper idempotency, persistence, and recovery capabilities.

### Current State Analysis

**Existing Implementation** (`engine/fsm_engine.py`):
- ✅ Phase enum exists (12 states: WARMUP, IDLE, ENTRY_EVAL, PLACE_BUY, WAIT_FILL, POSITION, EXIT_EVAL, PLACE_SELL, WAIT_SELL_FILL, POST_TRADE, COOLDOWN, ERROR)
- ✅ CoinState class for per-symbol state tracking
- ✅ Handler-based registration via `_register_transitions()`
- ❌ No transition table (uses if/else logic in handlers)
- ❌ No event enum (handlers check conditions directly)
- ❌ No explicit idempotency mechanism
- ❌ No FSM snapshotting/persistence
- ❌ Timeouts scattered across multiple handlers
- ❌ Partial fill handling incomplete
- ❌ No centralized error recovery

### Goals

1. **Determinism**: Same inputs → same outputs, always
2. **Idempotency**: Repeated events don't cause duplicate actions
3. **Traceability**: Every state transition logged with reason
4. **Recovery**: Crash-safe with snapshot/restore capability
5. **Testability**: Pure functions, no hidden dependencies
6. **Maintainability**: Clear separation of concerns

### Architecture Shift

**FROM**: Handler-based FSM (implicit transitions)
```python
def handle_place_buy(self, symbol: str, coin_state: CoinState):
    if buy_order_filled:
        coin_state.phase = Phase.POSITION  # Implicit transition
```

**TO**: Table-driven FSM (explicit transitions)
```python
TRANSITIONS = {
    (Phase.PLACE_BUY, Event.ORDER_FILLED): (Phase.POSITION, action_on_fill),
    (Phase.PLACE_BUY, Event.ORDER_TIMEOUT): (Phase.IDLE, action_on_timeout),
}
```

---

## Phase 0: Foundation (PFLICHT)

**Goal**: Establish core FSM primitives - Enum states, Event system, Transition table, Idempotency

### TODO 0.1: Event Enum System ✅ (Prerequisite)

**File**: `core/fsm/events.py` (CREATE)

**Implementation**:
```python
from enum import Enum, auto

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
        timestamp: float,
        data: Dict[str, Any] = None,
        order_id: Optional[str] = None,
        filled_qty: Optional[float] = None,
        avg_price: Optional[float] = None,
        error: Optional[Exception] = None
    ):
        self.event = event
        self.symbol = symbol
        self.timestamp = timestamp
        self.data = data or {}
        self.order_id = order_id
        self.filled_qty = filled_qty
        self.avg_price = avg_price
        self.error = error

    def __repr__(self):
        return f"EventContext({self.event.name}, {self.symbol}, {self.order_id})"
```

**Why Important**: Events are first-class citizens. No more implicit state changes.

**Testing**: Unit tests for event creation and context immutability.

---

### TODO 0.2: Transition Table ✅ (Core)

**File**: `core/fsm/transitions.py` (CREATE)

**Implementation**:
```python
from typing import Dict, Tuple, Callable, Optional
from core.fsm.events import FSMEvent
from engine.fsm_engine import Phase

# Type aliases
State = Phase
Action = Callable[[EventContext, CoinState], None]
TransitionKey = Tuple[State, FSMEvent]
TransitionValue = Tuple[State, Action]

class TransitionTable:
    """
    Transition Table: (CurrentState, Event) → (NextState, Action)

    The single source of truth for all state transitions.
    """

    def __init__(self):
        self._table: Dict[TransitionKey, TransitionValue] = {}
        self._default_actions: Dict[State, Action] = {}

        # Build the table
        self._build_table()

    def _build_table(self):
        """Define all valid transitions"""

        # ===== WARMUP Phase =====
        self._add(Phase.WARMUP, FSMEvent.TICK_RECEIVED, Phase.IDLE, action_warmup_complete)

        # ===== IDLE Phase =====
        self._add(Phase.IDLE, FSMEvent.SIGNAL_DETECTED, Phase.ENTRY_EVAL, action_evaluate_entry)
        self._add(Phase.IDLE, FSMEvent.TICK_RECEIVED, Phase.IDLE, action_idle_tick)

        # ===== ENTRY_EVAL Phase =====
        self._add(Phase.ENTRY_EVAL, FSMEvent.GUARDS_PASSED, Phase.PLACE_BUY, action_prepare_buy)
        self._add(Phase.ENTRY_EVAL, FSMEvent.GUARDS_BLOCKED, Phase.IDLE, action_log_blocked)
        self._add(Phase.ENTRY_EVAL, FSMEvent.RISK_LIMITS_BLOCKED, Phase.IDLE, action_log_blocked)

        # ===== PLACE_BUY Phase =====
        self._add(Phase.PLACE_BUY, FSMEvent.BUY_ORDER_PLACED, Phase.WAIT_FILL, action_wait_for_fill)
        self._add(Phase.PLACE_BUY, FSMEvent.BUY_ORDER_REJECTED, Phase.IDLE, action_handle_reject)
        self._add(Phase.PLACE_BUY, FSMEvent.ERROR_OCCURRED, Phase.ERROR, action_log_error)

        # ===== WAIT_FILL Phase =====
        self._add(Phase.WAIT_FILL, FSMEvent.BUY_ORDER_FILLED, Phase.POSITION, action_open_position)
        self._add(Phase.WAIT_FILL, FSMEvent.BUY_ORDER_PARTIAL, Phase.WAIT_FILL, action_handle_partial_buy)
        self._add(Phase.WAIT_FILL, FSMEvent.BUY_ORDER_TIMEOUT, Phase.IDLE, action_cancel_and_cleanup)
        self._add(Phase.WAIT_FILL, FSMEvent.BUY_ORDER_CANCELLED, Phase.IDLE, action_cleanup_cancelled)

        # ===== POSITION Phase =====
        self._add(Phase.POSITION, FSMEvent.TICK_RECEIVED, Phase.EXIT_EVAL, action_check_exit)
        self._add(Phase.POSITION, FSMEvent.POSITION_UPDATED, Phase.POSITION, action_update_pnl)

        # ===== EXIT_EVAL Phase =====
        self._add(Phase.EXIT_EVAL, FSMEvent.EXIT_SIGNAL_TP, Phase.PLACE_SELL, action_prepare_sell)
        self._add(Phase.EXIT_EVAL, FSMEvent.EXIT_SIGNAL_SL, Phase.PLACE_SELL, action_prepare_sell)
        self._add(Phase.EXIT_EVAL, FSMEvent.EXIT_SIGNAL_TIMEOUT, Phase.PLACE_SELL, action_prepare_sell)
        self._add(Phase.EXIT_EVAL, FSMEvent.EXIT_SIGNAL_TRAILING, Phase.PLACE_SELL, action_prepare_sell)
        self._add(Phase.EXIT_EVAL, FSMEvent.TICK_RECEIVED, Phase.POSITION, action_continue_holding)

        # ===== PLACE_SELL Phase =====
        self._add(Phase.PLACE_SELL, FSMEvent.SELL_ORDER_PLACED, Phase.WAIT_SELL_FILL, action_wait_for_sell)
        self._add(Phase.PLACE_SELL, FSMEvent.SELL_ORDER_REJECTED, Phase.POSITION, action_retry_sell)
        self._add(Phase.PLACE_SELL, FSMEvent.ERROR_OCCURRED, Phase.ERROR, action_log_error)

        # ===== WAIT_SELL_FILL Phase =====
        self._add(Phase.WAIT_SELL_FILL, FSMEvent.SELL_ORDER_FILLED, Phase.POST_TRADE, action_close_position)
        self._add(Phase.WAIT_SELL_FILL, FSMEvent.SELL_ORDER_PARTIAL, Phase.WAIT_SELL_FILL, action_handle_partial_sell)
        self._add(Phase.WAIT_SELL_FILL, FSMEvent.SELL_ORDER_TIMEOUT, Phase.POSITION, action_retry_sell)

        # ===== POST_TRADE Phase =====
        self._add(Phase.POST_TRADE, FSMEvent.TICK_RECEIVED, Phase.COOLDOWN, action_start_cooldown)

        # ===== COOLDOWN Phase =====
        self._add(Phase.COOLDOWN, FSMEvent.COOLDOWN_EXPIRED, Phase.IDLE, action_reset_to_idle)
        self._add(Phase.COOLDOWN, FSMEvent.TICK_RECEIVED, Phase.COOLDOWN, action_check_cooldown)

        # ===== ERROR Phase =====
        self._add(Phase.ERROR, FSMEvent.MANUAL_HALT, Phase.ERROR, action_safe_halt)

    def _add(self, from_state: State, event: FSMEvent, to_state: State, action: Action):
        """Add a transition to the table"""
        key = (from_state, event)
        if key in self._table:
            raise ValueError(f"Duplicate transition: {key}")
        self._table[key] = (to_state, action)

    def get_transition(
        self,
        current_state: State,
        event: FSMEvent
    ) -> Optional[TransitionValue]:
        """
        Lookup transition for (state, event) pair.

        Returns:
            (next_state, action) or None if invalid transition
        """
        key = (current_state, event)
        return self._table.get(key)

    def is_valid_transition(self, current_state: State, event: FSMEvent) -> bool:
        """Check if transition is defined"""
        return (current_state, event) in self._table

    def get_valid_events(self, current_state: State) -> List[FSMEvent]:
        """Get all valid events for current state"""
        return [
            event for (state, event) in self._table.keys()
            if state == current_state
        ]


# Singleton instance
_transition_table = None

def get_transition_table() -> TransitionTable:
    """Get global transition table singleton"""
    global _transition_table
    if _transition_table is None:
        _transition_table = TransitionTable()
    return _transition_table
```

**Why Important**: Single source of truth for all transitions. Makes FSM behavior explicit and auditable.

**Testing**:
- Unit test for all defined transitions
- Test invalid transition rejection
- Test duplicate transition detection

---

### TODO 0.3: Action Functions ✅ (Implementation)

**File**: `core/fsm/actions.py` (CREATE)

**Implementation**:
```python
"""
Action functions executed during state transitions.

All actions must be:
1. Pure functions (no hidden state)
2. Idempotent (safe to call multiple times)
3. Fast (< 10ms)
4. Logged (structured events)
"""

from core.fsm.events import EventContext
from engine.fsm_engine import CoinState
from core.logger_factory import log_event, DECISION_LOG

def action_warmup_complete(ctx: EventContext, coin_state: CoinState) -> None:
    """Transition: WARMUP → IDLE"""
    log_event(DECISION_LOG(), "fsm_transition",
              symbol=ctx.symbol,
              from_phase="WARMUP",
              to_phase="IDLE",
              event=ctx.event.name,
              reason="warmup_period_complete")

def action_idle_tick(ctx: EventContext, coin_state: CoinState) -> None:
    """Transition: IDLE → IDLE (no-op)"""
    pass  # Tick processing happens in evaluator

def action_evaluate_entry(ctx: EventContext, coin_state: CoinState) -> None:
    """Transition: IDLE → ENTRY_EVAL"""
    coin_state.signal_detected_at = ctx.timestamp
    coin_state.signal_type = ctx.data.get('signal_type')

    log_event(DECISION_LOG(), "fsm_transition",
              symbol=ctx.symbol,
              from_phase="IDLE",
              to_phase="ENTRY_EVAL",
              event=ctx.event.name,
              signal_type=coin_state.signal_type)

def action_prepare_buy(ctx: EventContext, coin_state: CoinState) -> None:
    """Transition: ENTRY_EVAL → PLACE_BUY"""
    coin_state.buy_order_prepared_at = ctx.timestamp

    log_event(DECISION_LOG(), "fsm_transition",
              symbol=ctx.symbol,
              from_phase="ENTRY_EVAL",
              to_phase="PLACE_BUY",
              event=ctx.event.name,
              order_price=ctx.data.get('order_price'),
              order_qty=ctx.data.get('order_qty'))

def action_log_blocked(ctx: EventContext, coin_state: CoinState) -> None:
    """Transition: ENTRY_EVAL → IDLE (blocked)"""
    log_event(DECISION_LOG(), "fsm_transition",
              symbol=ctx.symbol,
              from_phase="ENTRY_EVAL",
              to_phase="IDLE",
              event=ctx.event.name,
              block_reason=ctx.data.get('block_reason'))

def action_wait_for_fill(ctx: EventContext, coin_state: CoinState) -> None:
    """Transition: PLACE_BUY → WAIT_FILL"""
    coin_state.buy_order_id = ctx.order_id
    coin_state.buy_order_placed_at = ctx.timestamp

    log_event(DECISION_LOG(), "fsm_transition",
              symbol=ctx.symbol,
              from_phase="PLACE_BUY",
              to_phase="WAIT_FILL",
              event=ctx.event.name,
              order_id=ctx.order_id)

def action_open_position(ctx: EventContext, coin_state: CoinState) -> None:
    """Transition: WAIT_FILL → POSITION"""
    coin_state.position_opened_at = ctx.timestamp
    coin_state.position_qty = ctx.filled_qty
    coin_state.position_entry_price = ctx.avg_price

    log_event(DECISION_LOG(), "position_opened",
              symbol=ctx.symbol,
              qty=ctx.filled_qty,
              avg_entry=ctx.avg_price,
              notional=ctx.filled_qty * ctx.avg_price,
              opened_at=ctx.timestamp)

    log_event(DECISION_LOG(), "fsm_transition",
              symbol=ctx.symbol,
              from_phase="WAIT_FILL",
              to_phase="POSITION",
              event=ctx.event.name)

def action_handle_partial_buy(ctx: EventContext, coin_state: CoinState) -> None:
    """Transition: WAIT_FILL → WAIT_FILL (partial fill)"""
    coin_state.partial_fill_qty = coin_state.partial_fill_qty or 0.0
    coin_state.partial_fill_qty += ctx.filled_qty

    log_event(DECISION_LOG(), "order_partial_fill",
              symbol=ctx.symbol,
              filled_qty=ctx.filled_qty,
              cumulative_qty=coin_state.partial_fill_qty,
              order_id=ctx.order_id)

def action_cancel_and_cleanup(ctx: EventContext, coin_state: CoinState) -> None:
    """Transition: WAIT_FILL → IDLE (timeout)"""
    log_event(DECISION_LOG(), "fsm_transition",
              symbol=ctx.symbol,
              from_phase="WAIT_FILL",
              to_phase="IDLE",
              event=ctx.event.name,
              reason="order_timeout",
              partial_fill_qty=coin_state.partial_fill_qty)

    # Cleanup state
    coin_state.buy_order_id = None
    coin_state.partial_fill_qty = 0.0

def action_check_exit(ctx: EventContext, coin_state: CoinState) -> None:
    """Transition: POSITION → EXIT_EVAL"""
    # Tick triggers exit evaluation
    pass

def action_update_pnl(ctx: EventContext, coin_state: CoinState) -> None:
    """Transition: POSITION → POSITION (PnL update)"""
    coin_state.unrealized_pnl = ctx.data.get('unrealized_pnl')
    coin_state.unrealized_pct = ctx.data.get('unrealized_pct')

def action_prepare_sell(ctx: EventContext, coin_state: CoinState) -> None:
    """Transition: EXIT_EVAL → PLACE_SELL"""
    coin_state.exit_signal = ctx.data.get('exit_signal')
    coin_state.exit_detected_at = ctx.timestamp

    log_event(DECISION_LOG(), "fsm_transition",
              symbol=ctx.symbol,
              from_phase="EXIT_EVAL",
              to_phase="PLACE_SELL",
              event=ctx.event.name,
              exit_signal=coin_state.exit_signal)

def action_continue_holding(ctx: EventContext, coin_state: CoinState) -> None:
    """Transition: EXIT_EVAL → POSITION (no exit signal)"""
    pass

def action_wait_for_sell(ctx: EventContext, coin_state: CoinState) -> None:
    """Transition: PLACE_SELL → WAIT_SELL_FILL"""
    coin_state.sell_order_id = ctx.order_id
    coin_state.sell_order_placed_at = ctx.timestamp

    log_event(DECISION_LOG(), "fsm_transition",
              symbol=ctx.symbol,
              from_phase="PLACE_SELL",
              to_phase="WAIT_SELL_FILL",
              event=ctx.event.name,
              order_id=ctx.order_id)

def action_close_position(ctx: EventContext, coin_state: CoinState) -> None:
    """Transition: WAIT_SELL_FILL → POST_TRADE"""
    realized_pnl = (ctx.avg_price - coin_state.position_entry_price) * ctx.filled_qty

    log_event(DECISION_LOG(), "position_closed",
              symbol=ctx.symbol,
              qty_closed=ctx.filled_qty,
              exit_price=ctx.avg_price,
              realized_pnl_usdt=realized_pnl,
              reason=coin_state.exit_signal)

    log_event(DECISION_LOG(), "fsm_transition",
              symbol=ctx.symbol,
              from_phase="WAIT_SELL_FILL",
              to_phase="POST_TRADE",
              event=ctx.event.name)

def action_handle_partial_sell(ctx: EventContext, coin_state: CoinState) -> None:
    """Transition: WAIT_SELL_FILL → WAIT_SELL_FILL (partial)"""
    coin_state.partial_sell_qty = coin_state.partial_sell_qty or 0.0
    coin_state.partial_sell_qty += ctx.filled_qty

    log_event(DECISION_LOG(), "order_partial_fill",
              symbol=ctx.symbol,
              filled_qty=ctx.filled_qty,
              cumulative_qty=coin_state.partial_sell_qty,
              order_id=ctx.order_id)

def action_retry_sell(ctx: EventContext, coin_state: CoinState) -> None:
    """Transition: PLACE_SELL/WAIT_SELL_FILL → POSITION (retry)"""
    coin_state.sell_retry_count = coin_state.sell_retry_count or 0
    coin_state.sell_retry_count += 1

    log_event(DECISION_LOG(), "fsm_transition",
              symbol=ctx.symbol,
              to_phase="POSITION",
              event=ctx.event.name,
              reason="sell_failed_retry",
              retry_count=coin_state.sell_retry_count)

def action_start_cooldown(ctx: EventContext, coin_state: CoinState) -> None:
    """Transition: POST_TRADE → COOLDOWN"""
    coin_state.cooldown_started_at = ctx.timestamp

    log_event(DECISION_LOG(), "fsm_transition",
              symbol=ctx.symbol,
              from_phase="POST_TRADE",
              to_phase="COOLDOWN",
              event=ctx.event.name)

def action_check_cooldown(ctx: EventContext, coin_state: CoinState) -> None:
    """Transition: COOLDOWN → COOLDOWN (waiting)"""
    pass

def action_reset_to_idle(ctx: EventContext, coin_state: CoinState) -> None:
    """Transition: COOLDOWN → IDLE"""
    # Reset state
    coin_state.buy_order_id = None
    coin_state.sell_order_id = None
    coin_state.position_qty = 0.0
    coin_state.partial_fill_qty = 0.0
    coin_state.partial_sell_qty = 0.0
    coin_state.sell_retry_count = 0

    log_event(DECISION_LOG(), "fsm_transition",
              symbol=ctx.symbol,
              from_phase="COOLDOWN",
              to_phase="IDLE",
              event=ctx.event.name,
              reason="cooldown_expired")

def action_log_error(ctx: EventContext, coin_state: CoinState) -> None:
    """Transition: * → ERROR"""
    log_event(DECISION_LOG(), "fsm_transition",
              symbol=ctx.symbol,
              to_phase="ERROR",
              event=ctx.event.name,
              error=str(ctx.error))

def action_safe_halt(ctx: EventContext, coin_state: CoinState) -> None:
    """Transition: ERROR → ERROR (halted)"""
    coin_state.halted = True
    log_event(DECISION_LOG(), "fsm_halted",
              symbol=ctx.symbol,
              reason="manual_halt")

def action_handle_reject(ctx: EventContext, coin_state: CoinState) -> None:
    """Transition: PLACE_BUY → IDLE (rejected)"""
    log_event(DECISION_LOG(), "fsm_transition",
              symbol=ctx.symbol,
              from_phase="PLACE_BUY",
              to_phase="IDLE",
              event=ctx.event.name,
              reject_reason=ctx.data.get('reject_reason'))

def action_cleanup_cancelled(ctx: EventContext, coin_state: CoinState) -> None:
    """Transition: WAIT_FILL → IDLE (cancelled)"""
    log_event(DECISION_LOG(), "fsm_transition",
              symbol=ctx.symbol,
              from_phase="WAIT_FILL",
              to_phase="IDLE",
              event=ctx.event.name,
              reason="order_cancelled")

    coin_state.buy_order_id = None
    coin_state.partial_fill_qty = 0.0
```

**Why Important**: All business logic is isolated in pure, testable functions.

**Testing**: Unit test each action function with mock EventContext and CoinState.

---

### TODO 0.4: Idempotency Store ✅ (Critical)

**File**: `core/fsm/idempotency.py` (CREATE)

**Implementation**:
```python
"""
Idempotency Store - Prevents duplicate event processing

Uses event fingerprints (symbol + event + order_id + timestamp) to detect duplicates.
"""

import threading
from typing import Set, Tuple
from core.fsm.events import FSMEvent, EventContext
import time

class IdempotencyStore:
    """
    Thread-safe store for detecting duplicate events.

    Stores (symbol, event, order_id, timestamp_bucket) tuples.
    Auto-expires entries after 5 minutes.
    """

    def __init__(self, expiry_seconds: int = 300):
        self._store: Set[Tuple] = set()
        self._expiry_map: Dict[Tuple, float] = {}  # fingerprint → expiry_time
        self._lock = threading.RLock()
        self._expiry_seconds = expiry_seconds

    def _make_fingerprint(self, ctx: EventContext) -> Tuple:
        """
        Create event fingerprint.

        Timestamp is bucketed to 1-second intervals to handle slight timing variations.
        """
        timestamp_bucket = int(ctx.timestamp)
        return (
            ctx.symbol,
            ctx.event,
            ctx.order_id or "",
            timestamp_bucket
        )

    def is_duplicate(self, ctx: EventContext) -> bool:
        """
        Check if event has already been processed.

        Returns:
            True if duplicate, False if first occurrence
        """
        with self._lock:
            self._cleanup_expired()

            fingerprint = self._make_fingerprint(ctx)
            return fingerprint in self._store

    def mark_processed(self, ctx: EventContext) -> None:
        """Mark event as processed"""
        with self._lock:
            fingerprint = self._make_fingerprint(ctx)
            expiry_time = time.time() + self._expiry_seconds

            self._store.add(fingerprint)
            self._expiry_map[fingerprint] = expiry_time

    def _cleanup_expired(self):
        """Remove expired entries (called automatically)"""
        now = time.time()
        expired = [
            fp for fp, expiry in self._expiry_map.items()
            if expiry < now
        ]

        for fp in expired:
            self._store.discard(fp)
            del self._expiry_map[fp]

    def get_size(self) -> int:
        """Get current store size (for monitoring)"""
        with self._lock:
            return len(self._store)

    def clear(self) -> None:
        """Clear all entries (for testing)"""
        with self._lock:
            self._store.clear()
            self._expiry_map.clear()


# Global singleton
_idempotency_store = None

def get_idempotency_store() -> IdempotencyStore:
    """Get global idempotency store singleton"""
    global _idempotency_store
    if _idempotency_store is None:
        _idempotency_store = IdempotencyStore()
    return _idempotency_store
```

**Why Important**: Prevents duplicate order placements on exchange reconnects or retries.

**Testing**:
- Test duplicate detection within time window
- Test expiry cleanup
- Test thread safety under concurrent access

---

### TODO 0.5: FSM Core Engine ✅ (Integration)

**File**: `core/fsm/machine.py` (CREATE)

**Implementation**:
```python
"""
FSM Core Engine - Table-driven state machine

Orchestrates transitions, actions, and logging.
"""

import logging
from typing import Optional
from core.fsm.events import FSMEvent, EventContext
from core.fsm.transitions import get_transition_table
from core.fsm.idempotency import get_idempotency_store
from engine.fsm_engine import Phase, CoinState
from core.logger_factory import log_event, DECISION_LOG

logger = logging.getLogger(__name__)

class FSMachine:
    """
    Finite State Machine - Table-driven core

    Responsibilities:
    1. Process events via transition table
    2. Execute actions atomically
    3. Log all transitions
    4. Enforce idempotency
    5. Handle invalid transitions gracefully
    """

    def __init__(self):
        self.transition_table = get_transition_table()
        self.idempotency_store = get_idempotency_store()
        self._transition_count = 0

    def process_event(
        self,
        coin_state: CoinState,
        ctx: EventContext
    ) -> bool:
        """
        Process an event and execute transition if valid.

        Args:
            coin_state: Current coin state (mutable)
            ctx: Event context (immutable)

        Returns:
            True if transition executed, False if invalid/duplicate
        """
        # Step 1: Check idempotency
        if self.idempotency_store.is_duplicate(ctx):
            logger.debug(
                f"Duplicate event ignored: {ctx.symbol} {ctx.event.name} "
                f"(order_id={ctx.order_id})"
            )
            return False

        # Step 2: Lookup transition
        current_phase = coin_state.phase
        transition = self.transition_table.get_transition(current_phase, ctx.event)

        if transition is None:
            logger.warning(
                f"Invalid transition: {current_phase.name} + {ctx.event.name} "
                f"for {ctx.symbol}"
            )
            self._log_invalid_transition(coin_state, ctx)
            return False

        next_phase, action = transition

        # Step 3: Execute action
        try:
            action(ctx, coin_state)
        except Exception as e:
            logger.error(
                f"Action failed during {current_phase.name} → {next_phase.name}: {e}",
                exc_info=True
            )
            # Transition to ERROR state
            coin_state.phase = Phase.ERROR
            coin_state.error_message = str(e)
            return False

        # Step 4: Update phase
        coin_state.phase = next_phase

        # Step 5: Mark as processed
        self.idempotency_store.mark_processed(ctx)

        # Step 6: Log transition
        self._log_transition(coin_state, ctx, current_phase, next_phase)

        self._transition_count += 1

        return True

    def _log_transition(
        self,
        coin_state: CoinState,
        ctx: EventContext,
        from_phase: Phase,
        to_phase: Phase
    ):
        """Log successful transition"""
        log_event(
            DECISION_LOG(),
            "fsm_transition",
            symbol=ctx.symbol,
            from_phase=from_phase.name,
            to_phase=to_phase.name,
            event=ctx.event.name,
            order_id=ctx.order_id,
            transition_count=self._transition_count
        )

    def _log_invalid_transition(self, coin_state: CoinState, ctx: EventContext):
        """Log invalid transition attempt"""
        log_event(
            DECISION_LOG(),
            "fsm_invalid_transition",
            symbol=ctx.symbol,
            current_phase=coin_state.phase.name,
            event=ctx.event.name,
            order_id=ctx.order_id,
            valid_events=[e.name for e in self.transition_table.get_valid_events(coin_state.phase)]
        )

    def get_valid_events(self, coin_state: CoinState) -> List[FSMEvent]:
        """Get valid events for current state"""
        return self.transition_table.get_valid_events(coin_state.phase)

    def get_stats(self) -> Dict:
        """Get FSM statistics"""
        return {
            'total_transitions': self._transition_count,
            'idempotency_store_size': self.idempotency_store.get_size()
        }


# Global singleton
_fsm = None

def get_fsm() -> FSMachine:
    """Get global FSM singleton"""
    global _fsm
    if _fsm is None:
        _fsm = FSMachine()
    return _fsm
```

**Why Important**: This is the core orchestrator that makes everything work together.

**Testing**:
- Integration tests with real transition sequences
- Test idempotency enforcement
- Test error handling and ERROR state transitions

---

## Phase 1: Order Lifecycle Management (PFLICHT)

**Goal**: Robust order tracking with timeouts, partial fills, and retry logic

### TODO 1.1: StateData Class for Order Context ✅

**File**: `core/fsm/state_data.py` (CREATE)

**Implementation**:
```python
"""
StateData - Rich context for FSM state

Extends CoinState with order-specific tracking fields.
"""

from dataclasses import dataclass, field
from typing import Optional, List, Dict
from datetime import datetime

@dataclass
class OrderContext:
    """Order lifecycle tracking"""
    order_id: Optional[str] = None
    client_order_id: Optional[str] = None
    placed_at: Optional[float] = None
    ack_at: Optional[float] = None
    filled_at: Optional[float] = None
    cancelled_at: Optional[float] = None

    # Fill tracking
    cumulative_qty: float = 0.0
    target_qty: float = 0.0
    avg_price: float = 0.0
    total_fees: float = 0.0

    # Status
    status: str = "pending"  # pending, ack, filled, cancelled, timeout
    fill_trades: List[Dict] = field(default_factory=list)

    # Retry tracking
    retry_count: int = 0
    last_retry_at: Optional[float] = None

@dataclass
class StateData:
    """
    Extended state data for FSM.
    Attached to CoinState.fsm_data
    """
    # Order contexts
    buy_order: Optional[OrderContext] = None
    sell_order: Optional[OrderContext] = None

    # Entry evaluation
    signal_detected_at: Optional[float] = None
    signal_type: Optional[str] = None
    guards_eval_result: Optional[Dict] = None
    risk_limits_eval_result: Optional[Dict] = None

    # Position tracking
    position_opened_at: Optional[float] = None
    position_entry_price: float = 0.0
    position_qty: float = 0.0
    position_fees_accum: float = 0.0

    # Exit evaluation
    exit_signal: Optional[str] = None
    exit_detected_at: Optional[float] = None
    peak_price: float = 0.0
    trailing_stop: Optional[float] = None

    # Cooldown
    cooldown_started_at: Optional[float] = None

    # Error tracking
    error_count: int = 0
    last_error: Optional[str] = None
    last_error_at: Optional[float] = None
```

**Why Important**: Centralizes all order state instead of scattering it across handler functions.

---

### TODO 1.2: Centralized Timeout Handling ✅

**File**: `core/fsm/timeouts.py` (CREATE)

**Implementation**:
```python
"""
Centralized timeout handling for orders
"""

import time
from typing import Dict, List, Optional
from core.fsm.events import FSMEvent, EventContext
from core.fsm.state_data import StateData
import config

class TimeoutManager:
    """
    Manages order timeouts and emits timeout events.

    Timeouts:
    - Buy order fill: 30s (config.BUY_FILL_TIMEOUT_SECS)
    - Sell order fill: 30s (config.SELL_FILL_TIMEOUT_SECS)
    - Cooldown: 60s (config.COOLDOWN_SECS)
    """

    def __init__(self):
        self.buy_timeout_secs = getattr(config, 'BUY_FILL_TIMEOUT_SECS', 30)
        self.sell_timeout_secs = getattr(config, 'SELL_FILL_TIMEOUT_SECS', 30)
        self.cooldown_secs = getattr(config, 'COOLDOWN_SECS', 60)

    def check_buy_timeout(
        self,
        symbol: str,
        state_data: StateData
    ) -> Optional[EventContext]:
        """
        Check if buy order has timed out.

        Returns:
            EventContext with BUY_ORDER_TIMEOUT event, or None
        """
        if not state_data.buy_order or not state_data.buy_order.placed_at:
            return None

        elapsed = time.time() - state_data.buy_order.placed_at
        if elapsed > self.buy_timeout_secs:
            return EventContext(
                event=FSMEvent.BUY_ORDER_TIMEOUT,
                symbol=symbol,
                timestamp=time.time(),
                order_id=state_data.buy_order.order_id,
                data={'elapsed_seconds': elapsed}
            )

        return None

    def check_sell_timeout(
        self,
        symbol: str,
        state_data: StateData
    ) -> Optional[EventContext]:
        """Check if sell order has timed out"""
        if not state_data.sell_order or not state_data.sell_order.placed_at:
            return None

        elapsed = time.time() - state_data.sell_order.placed_at
        if elapsed > self.sell_timeout_secs:
            return EventContext(
                event=FSMEvent.SELL_ORDER_TIMEOUT,
                symbol=symbol,
                timestamp=time.time(),
                order_id=state_data.sell_order.order_id,
                data={'elapsed_seconds': elapsed}
            )

        return None

    def check_cooldown_expired(
        self,
        symbol: str,
        state_data: StateData
    ) -> Optional[EventContext]:
        """Check if cooldown period has expired"""
        if not state_data.cooldown_started_at:
            return None

        elapsed = time.time() - state_data.cooldown_started_at
        if elapsed > self.cooldown_secs:
            return EventContext(
                event=FSMEvent.COOLDOWN_EXPIRED,
                symbol=symbol,
                timestamp=time.time(),
                data={'cooldown_duration': elapsed}
            )

        return None

    def check_all_timeouts(
        self,
        symbol: str,
        coin_state
    ) -> List[EventContext]:
        """
        Check all timeout conditions.

        Returns:
            List of timeout events (0-1 events typically)
        """
        state_data = coin_state.fsm_data
        if not state_data:
            return []

        events = []

        # Check buy timeout (WAIT_FILL phase)
        if coin_state.phase.name == 'WAIT_FILL':
            event = self.check_buy_timeout(symbol, state_data)
            if event:
                events.append(event)

        # Check sell timeout (WAIT_SELL_FILL phase)
        elif coin_state.phase.name == 'WAIT_SELL_FILL':
            event = self.check_sell_timeout(symbol, state_data)
            if event:
                events.append(event)

        # Check cooldown expiry (COOLDOWN phase)
        elif coin_state.phase.name == 'COOLDOWN':
            event = self.check_cooldown_expired(symbol, state_data)
            if event:
                events.append(event)

        return events


# Global singleton
_timeout_manager = None

def get_timeout_manager() -> TimeoutManager:
    """Get global timeout manager singleton"""
    global _timeout_manager
    if _timeout_manager is None:
        _timeout_manager = TimeoutManager()
    return _timeout_manager
```

**Why Important**: Removes timeout logic scattered across handlers. Single place to tune timeouts.

---

### TODO 1.3: Partial Fill Accumulation ✅

**File**: `core/fsm/partial_fills.py` (CREATE)

**Implementation**:
```python
"""
Partial fill handling and accumulation
"""

from core.fsm.state_data import OrderContext
from core.fsm.events import FSMEvent, EventContext
import logging

logger = logging.getLogger(__name__)

class PartialFillHandler:
    """
    Handles partial fill accumulation.

    Tracks:
    - Cumulative quantity filled
    - Average fill price (weighted)
    - Total fees
    - Fill trades
    """

    def accumulate_fill(
        self,
        order_ctx: OrderContext,
        fill_qty: float,
        fill_price: float,
        fill_fee: float,
        trade_id: str
    ) -> None:
        """
        Accumulate a partial fill into order context.

        Updates:
        - cumulative_qty
        - avg_price (weighted average)
        - total_fees
        - fill_trades list
        """
        # Update cumulative quantity
        previous_qty = order_ctx.cumulative_qty
        new_qty = previous_qty + fill_qty

        # Update weighted average price
        if previous_qty > 0:
            # Weighted average: (old_qty * old_price + new_qty * new_price) / total_qty
            order_ctx.avg_price = (
                (previous_qty * order_ctx.avg_price + fill_qty * fill_price) / new_qty
            )
        else:
            order_ctx.avg_price = fill_price

        order_ctx.cumulative_qty = new_qty
        order_ctx.total_fees += fill_fee

        # Record trade
        order_ctx.fill_trades.append({
            'trade_id': trade_id,
            'qty': fill_qty,
            'price': fill_price,
            'fee': fill_fee
        })

        logger.debug(
            f"Partial fill accumulated: {fill_qty:.6f} @ {fill_price:.6f}, "
            f"cumulative: {new_qty:.6f} @ {order_ctx.avg_price:.6f}"
        )

    def is_fully_filled(self, order_ctx: OrderContext) -> bool:
        """
        Check if order is fully filled.

        Considers floating point tolerance (0.1% threshold).
        """
        if order_ctx.target_qty == 0:
            return False

        fill_ratio = order_ctx.cumulative_qty / order_ctx.target_qty
        return fill_ratio >= 0.999  # 99.9% filled = fully filled

    def get_remaining_qty(self, order_ctx: OrderContext) -> float:
        """Get remaining quantity to be filled"""
        return max(0.0, order_ctx.target_qty - order_ctx.cumulative_qty)

    def create_fill_event(
        self,
        symbol: str,
        order_ctx: OrderContext,
        is_buy: bool
    ) -> EventContext:
        """
        Create appropriate fill event based on completion status.

        Returns:
            BUY_ORDER_FILLED/SELL_ORDER_FILLED if fully filled,
            BUY_ORDER_PARTIAL/SELL_ORDER_PARTIAL if partial
        """
        is_fully_filled = self.is_fully_filled(order_ctx)

        if is_fully_filled:
            event_type = FSMEvent.BUY_ORDER_FILLED if is_buy else FSMEvent.SELL_ORDER_FILLED
        else:
            event_type = FSMEvent.BUY_ORDER_PARTIAL if is_buy else FSMEvent.SELL_ORDER_PARTIAL

        return EventContext(
            event=event_type,
            symbol=symbol,
            timestamp=time.time(),
            order_id=order_ctx.order_id,
            filled_qty=order_ctx.cumulative_qty,
            avg_price=order_ctx.avg_price,
            data={
                'fill_ratio': order_ctx.cumulative_qty / order_ctx.target_qty if order_ctx.target_qty > 0 else 0,
                'total_fees': order_ctx.total_fees,
                'num_trades': len(order_ctx.fill_trades)
            }
        )


# Global singleton
_partial_fill_handler = None

def get_partial_fill_handler() -> PartialFillHandler:
    """Get global partial fill handler singleton"""
    global _partial_fill_handler
    if _partial_fill_handler is None:
        _partial_fill_handler = PartialFillHandler()
    return _partial_fill_handler
```

**Why Important**: Correctly handles multi-trade fills with proper average price calculation.

---

### TODO 1.4: Integration into FSM Engine ✅

**File**: `engine/fsm_engine.py` (MODIFY)

**Changes**:
1. Add `fsm_data: Optional[StateData]` field to `CoinState`
2. Initialize `StateData()` on warmup
3. Replace timeout checks with `TimeoutManager.check_all_timeouts()`
4. Replace fill accumulation with `PartialFillHandler`
5. Use `FSMachine.process_event()` for all transitions

**Example**:
```python
# OLD (handler-based)
def handle_wait_fill(self, symbol: str, coin_state: CoinState):
    if order_filled:
        coin_state.phase = Phase.POSITION  # Implicit transition

# NEW (event-based)
def handle_wait_fill(self, symbol: str, coin_state: CoinState):
    order_ctx = coin_state.fsm_data.buy_order

    # Check for fills
    if order_filled:
        event = EventContext(
            event=FSMEvent.BUY_ORDER_FILLED,
            symbol=symbol,
            timestamp=time.time(),
            order_id=order_ctx.order_id,
            filled_qty=order_ctx.cumulative_qty,
            avg_price=order_ctx.avg_price
        )

        fsm = get_fsm()
        fsm.process_event(coin_state, event)  # Explicit transition via table
```

**Testing**: Integration tests covering full order lifecycle with partials and timeouts.

---

## Phase 2: Error Handling & Observability (PFLICHT)

**Goal**: Retry logic, structured logging, safe ERROR state

### TODO 2.1: Retry Decorators with Exponential Backoff ✅

**File**: `core/fsm/retry.py` (CREATE)

**Implementation**:
```python
"""
Retry decorators with exponential backoff for exchange calls
"""

import time
import functools
import logging
from typing import Callable, Optional, Type
from ccxt.base.errors import NetworkError, ExchangeNotAvailable

logger = logging.getLogger(__name__)

def with_retry(
    max_attempts: int = 3,
    base_delay: float = 1.0,
    max_delay: float = 10.0,
    backoff_factor: float = 2.0,
    retryable_exceptions: tuple = (NetworkError, ExchangeNotAvailable)
):
    """
    Retry decorator with exponential backoff.

    Args:
        max_attempts: Maximum retry attempts
        base_delay: Initial delay in seconds
        max_delay: Maximum delay cap
        backoff_factor: Exponential backoff multiplier
        retryable_exceptions: Tuple of exception types to retry

    Example:
        @with_retry(max_attempts=3, base_delay=1.0)
        def place_order(exchange, symbol, side, amount, price):
            return exchange.create_limit_order(symbol, side, amount, price)
    """
    def decorator(func: Callable):
        @functools.wraps(func)
        def wrapper(*args, **kwargs):
            last_exception = None

            for attempt in range(1, max_attempts + 1):
                try:
                    return func(*args, **kwargs)

                except retryable_exceptions as e:
                    last_exception = e

                    if attempt == max_attempts:
                        logger.error(
                            f"{func.__name__} failed after {max_attempts} attempts: {e}"
                        )
                        raise

                    # Calculate delay with exponential backoff
                    delay = min(base_delay * (backoff_factor ** (attempt - 1)), max_delay)

                    logger.warning(
                        f"{func.__name__} attempt {attempt}/{max_attempts} failed: {e}. "
                        f"Retrying in {delay:.1f}s..."
                    )

                    time.sleep(delay)

                except Exception as e:
                    # Non-retryable exception, fail immediately
                    logger.error(f"{func.__name__} failed with non-retryable error: {e}")
                    raise

            # Should never reach here, but for safety
            raise last_exception

        return wrapper
    return decorator


# Pre-configured decorators for common use cases

def with_order_retry(func: Callable):
    """Retry decorator for order placement (3 attempts, 1s base delay)"""
    return with_retry(max_attempts=3, base_delay=1.0)(func)

def with_fetch_retry(func: Callable):
    """Retry decorator for data fetches (5 attempts, 0.5s base delay)"""
    return with_retry(max_attempts=5, base_delay=0.5, max_delay=5.0)(func)

def with_cancel_retry(func: Callable):
    """Retry decorator for order cancellations (2 attempts, 0.5s base delay)"""
    return with_retry(max_attempts=2, base_delay=0.5)(func)
```

**Usage Example**:
```python
from core.fsm.retry import with_order_retry

@with_order_retry
def place_buy_order(exchange, symbol, amount, price):
    return exchange.create_limit_order(symbol, 'buy', amount, price)
```

**Why Important**: Handles transient network errors gracefully without manual retry logic.

---

### TODO 2.2: Phase Audit Logging (Activate) ✅

**File**: `core/fsm/audit.py` (CREATE)

**Implementation**:
```python
"""
FSM Phase Audit Logging

Logs every phase transition with full context for debugging and compliance.
"""

from core.logger_factory import log_event, AUDIT_LOG
from core.fsm.events import EventContext
from engine.fsm_engine import Phase, CoinState

def log_phase_transition(
    symbol: str,
    from_phase: Phase,
    to_phase: Phase,
    event: str,
    coin_state: CoinState,
    ctx: EventContext
):
    """
    Log FSM phase transition to audit log.

    Captures:
    - Phase transition (from → to)
    - Triggering event
    - Order IDs
    - State snapshot
    - Timing information
    """
    state_data = coin_state.fsm_data

    # Build state snapshot
    state_snapshot = {
        'phase_from': from_phase.name,
        'phase_to': to_phase.name,
        'event': event,
        'symbol': symbol,
    }

    # Add order context if present
    if state_data:
        if state_data.buy_order:
            state_snapshot['buy_order_id'] = state_data.buy_order.order_id
            state_snapshot['buy_order_status'] = state_data.buy_order.status
            state_snapshot['buy_filled_qty'] = state_data.buy_order.cumulative_qty

        if state_data.sell_order:
            state_snapshot['sell_order_id'] = state_data.sell_order.order_id
            state_snapshot['sell_order_status'] = state_data.sell_order.status
            state_snapshot['sell_filled_qty'] = state_data.sell_order.cumulative_qty

        if state_data.position_opened_at:
            state_snapshot['position_age_secs'] = ctx.timestamp - state_data.position_opened_at
            state_snapshot['position_qty'] = state_data.position_qty
            state_snapshot['position_entry'] = state_data.position_entry_price

        if state_data.error_count > 0:
            state_snapshot['error_count'] = state_data.error_count
            state_snapshot['last_error'] = state_data.last_error

    # Log to audit log
    log_event(
        AUDIT_LOG(),
        "fsm_phase_transition",
        **state_snapshot
    )


def log_phase_entry(symbol: str, phase: Phase, coin_state: CoinState):
    """Log entry into a phase"""
    log_event(
        AUDIT_LOG(),
        "fsm_phase_entry",
        symbol=symbol,
        phase=phase.name,
        timestamp=time.time()
    )


def log_phase_exit(symbol: str, phase: Phase, coin_state: CoinState, reason: str):
    """Log exit from a phase"""
    log_event(
        AUDIT_LOG(),
        "fsm_phase_exit",
        symbol=symbol,
        phase=phase.name,
        reason=reason,
        timestamp=time.time()
    )
```

**Integration**: Call `log_phase_transition()` inside `FSMachine._log_transition()`.

**Why Important**: Complete audit trail for regulatory compliance and debugging.

---

### TODO 2.3: ERROR State Implementation ✅

**File**: `core/fsm/error_handling.py` (CREATE)

**Implementation**:
```python
"""
ERROR State Handling

Safe-halt mechanism for unrecoverable errors.
"""

from core.fsm.events import FSMEvent, EventContext
from engine.fsm_engine import Phase, CoinState
from core.logger_factory import log_event, DECISION_LOG
import logging

logger = logging.getLogger(__name__)

class ErrorHandler:
    """
    Handles transitions to ERROR state and recovery.
    """

    def __init__(self):
        self.error_symbols = set()  # Track symbols in ERROR state

    def transition_to_error(
        self,
        symbol: str,
        coin_state: CoinState,
        error: Exception,
        context: str
    ) -> EventContext:
        """
        Transition symbol to ERROR state.

        Args:
            symbol: Trading symbol
            coin_state: Current coin state
            error: Exception that triggered error
            context: Human-readable context (e.g., "order_placement_failed")

        Returns:
            EventContext for ERROR_OCCURRED event
        """
        # Update state data
        if coin_state.fsm_data:
            coin_state.fsm_data.error_count += 1
            coin_state.fsm_data.last_error = str(error)
            coin_state.fsm_data.last_error_at = time.time()

        # Track errored symbol
        self.error_symbols.add(symbol)

        # Log error transition
        logger.error(
            f"FSM ERROR for {symbol}: {context} - {error}",
            exc_info=True
        )

        log_event(
            DECISION_LOG(),
            "fsm_error",
            symbol=symbol,
            error_type=type(error).__name__,
            error_message=str(error),
            context=context,
            error_count=coin_state.fsm_data.error_count if coin_state.fsm_data else 1,
            current_phase=coin_state.phase.name
        )

        # Create error event
        return EventContext(
            event=FSMEvent.ERROR_OCCURRED,
            symbol=symbol,
            timestamp=time.time(),
            error=error,
            data={'context': context}
        )

    def is_in_error_state(self, symbol: str) -> bool:
        """Check if symbol is in ERROR state"""
        return symbol in self.error_symbols

    def manual_recover(self, symbol: str, coin_state: CoinState) -> bool:
        """
        Manually recover symbol from ERROR state.

        Returns:
            True if recovery successful
        """
        if symbol not in self.error_symbols:
            return False

        # Reset to IDLE
        coin_state.phase = Phase.IDLE

        # Clear error state
        if coin_state.fsm_data:
            coin_state.fsm_data.error_count = 0
            coin_state.fsm_data.last_error = None
            coin_state.fsm_data.last_error_at = None

        self.error_symbols.discard(symbol)

        logger.info(f"Manual recovery: {symbol} → IDLE")

        log_event(
            DECISION_LOG(),
            "fsm_manual_recovery",
            symbol=symbol,
            recovered_to="IDLE"
        )

        return True

    def get_error_summary(self) -> Dict:
        """Get summary of all symbols in ERROR state"""
        return {
            'error_count': len(self.error_symbols),
            'error_symbols': list(self.error_symbols)
        }


# Global singleton
_error_handler = None

def get_error_handler() -> ErrorHandler:
    """Get global error handler singleton"""
    global _error_handler
    if _error_handler is None:
        _error_handler = ErrorHandler()
    return _error_handler
```

**Why Important**: Prevents cascading failures. Symbol halts safely until manual intervention.

---

## Phase 3: Persistence & Recovery (PFLICHT)

**Goal**: Crash-safe FSM with snapshot/restore capability

### TODO 3.1: FSM Snapshot System ✅

**File**: `core/fsm/snapshot.py` (CREATE)

**Implementation**:
```python
"""
FSM Snapshot System - Crash Recovery

Persists FSM state to disk after every transition.
Enables recovery from crashes without losing positions.
"""

import json
import logging
from pathlib import Path
from typing import Dict, Optional
from datetime import datetime
from engine.fsm_engine import Phase, CoinState
from core.fsm.state_data import StateData, OrderContext
import threading

logger = logging.getLogger(__name__)

class SnapshotManager:
    """
    Manages FSM state snapshots for crash recovery.

    Snapshots are written to:
    - sessions/current/fsm_snapshots/{symbol}.json

    Format:
    {
        "symbol": "BTC/USDT",
        "phase": "POSITION",
        "timestamp": 1234567890.123,
        "state_data": {...},
        "snapshot_version": 1
    }
    """

    def __init__(self, snapshot_dir: Optional[Path] = None):
        if snapshot_dir is None:
            # Default: sessions/current/fsm_snapshots/
            snapshot_dir = Path("sessions") / "current" / "fsm_snapshots"

        self.snapshot_dir = snapshot_dir
        self.snapshot_dir.mkdir(parents=True, exist_ok=True)

        self._lock = threading.RLock()
        self._write_count = 0

        logger.info(f"Snapshot manager initialized: {self.snapshot_dir}")

    def save_snapshot(self, symbol: str, coin_state: CoinState) -> bool:
        """
        Save current FSM state snapshot.

        Returns:
            True if successful
        """
        with self._lock:
            try:
                snapshot = self._serialize_state(symbol, coin_state)
                snapshot_path = self._get_snapshot_path(symbol)

                # Write atomically (write to temp, then rename)
                temp_path = snapshot_path.with_suffix('.tmp')
                with open(temp_path, 'w') as f:
                    json.dump(snapshot, f, indent=2)

                temp_path.rename(snapshot_path)

                self._write_count += 1

                logger.debug(f"Snapshot saved: {symbol} @ {coin_state.phase.name}")
                return True

            except Exception as e:
                logger.error(f"Failed to save snapshot for {symbol}: {e}")
                return False

    def load_snapshot(self, symbol: str) -> Optional[Dict]:
        """
        Load FSM state snapshot.

        Returns:
            Snapshot dict or None if not found
        """
        with self._lock:
            snapshot_path = self._get_snapshot_path(symbol)

            if not snapshot_path.exists():
                return None

            try:
                with open(snapshot_path) as f:
                    snapshot = json.load(f)

                logger.info(f"Snapshot loaded: {symbol} @ {snapshot['phase']}")
                return snapshot

            except Exception as e:
                logger.error(f"Failed to load snapshot for {symbol}: {e}")
                return None

    def restore_state(self, symbol: str, coin_state: CoinState) -> bool:
        """
        Restore coin state from snapshot.

        Returns:
            True if restored successfully
        """
        snapshot = self.load_snapshot(symbol)
        if not snapshot:
            return False

        try:
            # Restore phase
            coin_state.phase = Phase[snapshot['phase']]

            # Restore state data
            if snapshot.get('state_data'):
                coin_state.fsm_data = self._deserialize_state_data(snapshot['state_data'])

            logger.info(f"State restored: {symbol} → {coin_state.phase.name}")
            return True

        except Exception as e:
            logger.error(f"Failed to restore state for {symbol}: {e}")
            return False

    def delete_snapshot(self, symbol: str) -> bool:
        """Delete snapshot (after position closed)"""
        with self._lock:
            snapshot_path = self._get_snapshot_path(symbol)

            if snapshot_path.exists():
                try:
                    snapshot_path.unlink()
                    logger.debug(f"Snapshot deleted: {symbol}")
                    return True
                except Exception as e:
                    logger.error(f"Failed to delete snapshot for {symbol}: {e}")
                    return False

            return False

    def _serialize_state(self, symbol: str, coin_state: CoinState) -> Dict:
        """Serialize coin state to dict"""
        snapshot = {
            'symbol': symbol,
            'phase': coin_state.phase.name,
            'timestamp': time.time(),
            'snapshot_version': 1
        }

        # Serialize state data
        if coin_state.fsm_data:
            state_data = coin_state.fsm_data
            snapshot['state_data'] = {
                # Order contexts
                'buy_order': self._serialize_order_context(state_data.buy_order),
                'sell_order': self._serialize_order_context(state_data.sell_order),

                # Entry evaluation
                'signal_detected_at': state_data.signal_detected_at,
                'signal_type': state_data.signal_type,

                # Position
                'position_opened_at': state_data.position_opened_at,
                'position_entry_price': state_data.position_entry_price,
                'position_qty': state_data.position_qty,
                'position_fees_accum': state_data.position_fees_accum,

                # Exit
                'exit_signal': state_data.exit_signal,
                'exit_detected_at': state_data.exit_detected_at,
                'peak_price': state_data.peak_price,
                'trailing_stop': state_data.trailing_stop,

                # Cooldown
                'cooldown_started_at': state_data.cooldown_started_at,

                # Error
                'error_count': state_data.error_count,
                'last_error': state_data.last_error,
                'last_error_at': state_data.last_error_at
            }

        return snapshot

    def _serialize_order_context(self, order_ctx: Optional[OrderContext]) -> Optional[Dict]:
        """Serialize order context"""
        if not order_ctx:
            return None

        return {
            'order_id': order_ctx.order_id,
            'client_order_id': order_ctx.client_order_id,
            'placed_at': order_ctx.placed_at,
            'cumulative_qty': order_ctx.cumulative_qty,
            'target_qty': order_ctx.target_qty,
            'avg_price': order_ctx.avg_price,
            'total_fees': order_ctx.total_fees,
            'status': order_ctx.status,
            'retry_count': order_ctx.retry_count
        }

    def _deserialize_state_data(self, data: Dict) -> StateData:
        """Deserialize state data from dict"""
        state_data = StateData()

        # Restore order contexts
        if data.get('buy_order'):
            state_data.buy_order = self._deserialize_order_context(data['buy_order'])

        if data.get('sell_order'):
            state_data.sell_order = self._deserialize_order_context(data['sell_order'])

        # Restore other fields
        state_data.signal_detected_at = data.get('signal_detected_at')
        state_data.signal_type = data.get('signal_type')
        state_data.position_opened_at = data.get('position_opened_at')
        state_data.position_entry_price = data.get('position_entry_price', 0.0)
        state_data.position_qty = data.get('position_qty', 0.0)
        state_data.position_fees_accum = data.get('position_fees_accum', 0.0)
        state_data.exit_signal = data.get('exit_signal')
        state_data.exit_detected_at = data.get('exit_detected_at')
        state_data.peak_price = data.get('peak_price', 0.0)
        state_data.trailing_stop = data.get('trailing_stop')
        state_data.cooldown_started_at = data.get('cooldown_started_at')
        state_data.error_count = data.get('error_count', 0)
        state_data.last_error = data.get('last_error')
        state_data.last_error_at = data.get('last_error_at')

        return state_data

    def _deserialize_order_context(self, data: Dict) -> OrderContext:
        """Deserialize order context from dict"""
        return OrderContext(
            order_id=data.get('order_id'),
            client_order_id=data.get('client_order_id'),
            placed_at=data.get('placed_at'),
            cumulative_qty=data.get('cumulative_qty', 0.0),
            target_qty=data.get('target_qty', 0.0),
            avg_price=data.get('avg_price', 0.0),
            total_fees=data.get('total_fees', 0.0),
            status=data.get('status', 'pending'),
            retry_count=data.get('retry_count', 0)
        )

    def _get_snapshot_path(self, symbol: str) -> Path:
        """Get snapshot file path for symbol"""
        # Replace / with _ for filename
        safe_symbol = symbol.replace('/', '_')
        return self.snapshot_dir / f"{safe_symbol}.json"

    def get_stats(self) -> Dict:
        """Get snapshot statistics"""
        with self._lock:
            snapshots = list(self.snapshot_dir.glob("*.json"))
            return {
                'snapshot_count': len(snapshots),
                'write_count': self._write_count,
                'snapshot_dir': str(self.snapshot_dir)
            }


# Global singleton
_snapshot_manager = None

def get_snapshot_manager() -> SnapshotManager:
    """Get global snapshot manager singleton"""
    global _snapshot_manager
    if _snapshot_manager is None:
        _snapshot_manager = SnapshotManager()
    return _snapshot_manager
```

**Integration**: Call `snapshot_manager.save_snapshot()` after every FSM transition.

**Why Important**: Crash recovery without losing active positions.

---

### TODO 3.2: Transactional Portfolio Commits ✅

**File**: `core/fsm/portfolio_transaction.py` (CREATE)

**Implementation**:
```python
"""
Transactional Portfolio Updates

Ensures FSM state and portfolio state stay synchronized.
Uses 2-phase commit pattern.
"""

import logging
from typing import Optional, Dict
from contextlib import contextmanager

logger = logging.getLogger(__name__)

class PortfolioTransaction:
    """
    Transactional portfolio update.

    Pattern:
    1. Begin transaction (save old state)
    2. Update portfolio
    3. Update FSM state
    4. Commit (save snapshot) or Rollback (restore old state)
    """

    def __init__(self, portfolio, pnl_service, snapshot_manager):
        self.portfolio = portfolio
        self.pnl_service = pnl_service
        self.snapshot_manager = snapshot_manager

        self._in_transaction = False
        self._rollback_state = None

    @contextmanager
    def begin(self, symbol: str, coin_state):
        """
        Context manager for transactional update.

        Usage:
            with portfolio_tx.begin(symbol, coin_state) as tx:
                portfolio.add_position(...)
                coin_state.phase = Phase.POSITION
                # If exception: automatic rollback
                # If success: automatic commit + snapshot
        """
        if self._in_transaction:
            raise RuntimeError("Nested transactions not supported")

        self._in_transaction = True
        self._rollback_state = self._capture_state(symbol, coin_state)

        try:
            yield self
            # Success - commit
            self._commit(symbol, coin_state)

        except Exception as e:
            # Failure - rollback
            logger.error(f"Transaction failed for {symbol}, rolling back: {e}")
            self._rollback(symbol, coin_state)
            raise

        finally:
            self._in_transaction = False
            self._rollback_state = None

    def _capture_state(self, symbol: str, coin_state) -> Dict:
        """Capture current state for potential rollback"""
        return {
            'symbol': symbol,
            'phase': coin_state.phase,
            'portfolio_position': self.portfolio.get(symbol, {}).copy(),
            'fsm_data': coin_state.fsm_data  # Reference (not deep copy)
        }

    def _commit(self, symbol: str, coin_state):
        """Commit transaction and save snapshot"""
        # Save FSM snapshot
        success = self.snapshot_manager.save_snapshot(symbol, coin_state)
        if not success:
            logger.warning(f"Snapshot save failed for {symbol} (transaction committed)")

        logger.debug(f"Transaction committed: {symbol} @ {coin_state.phase.name}")

    def _rollback(self, symbol: str, coin_state):
        """Rollback transaction to previous state"""
        if not self._rollback_state:
            logger.error(f"Cannot rollback {symbol}: no saved state")
            return

        # Restore FSM phase
        coin_state.phase = self._rollback_state['phase']

        # Restore portfolio position
        old_position = self._rollback_state['portfolio_position']
        if old_position:
            self.portfolio[symbol] = old_position
        elif symbol in self.portfolio:
            del self.portfolio[symbol]

        logger.warning(f"Transaction rolled back: {symbol} → {coin_state.phase.name}")


# Global singleton (initialized in engine)
_portfolio_transaction = None

def get_portfolio_transaction() -> PortfolioTransaction:
    """Get global portfolio transaction manager"""
    global _portfolio_transaction
    if _portfolio_transaction is None:
        # Will be initialized by engine with real dependencies
        raise RuntimeError("PortfolioTransaction not initialized")
    return _portfolio_transaction

def init_portfolio_transaction(portfolio, pnl_service, snapshot_manager):
    """Initialize portfolio transaction manager (called by engine)"""
    global _portfolio_transaction
    _portfolio_transaction = PortfolioTransaction(portfolio, pnl_service, snapshot_manager)
```

**Usage Example**:
```python
from core.fsm.portfolio_transaction import get_portfolio_transaction

portfolio_tx = get_portfolio_transaction()

with portfolio_tx.begin(symbol, coin_state):
    # Update portfolio
    portfolio.add_position(symbol, qty, entry_price)

    # Update FSM state
    coin_state.phase = Phase.POSITION
    coin_state.fsm_data.position_opened_at = time.time()

    # If any exception: automatic rollback
    # If success: automatic commit + snapshot
```

**Why Important**: Prevents portfolio/FSM state divergence on errors.

---

### TODO 3.3: Crash Recovery on Startup ✅

**File**: `engine/fsm_engine.py` (MODIFY)

**Changes**: Add recovery logic to `__init__`:

```python
def __init__(self, ...):
    # ... existing init ...

    # Phase 3: Crash recovery
    self._recover_from_snapshots()

def _recover_from_snapshots(self):
    """
    Recover FSM state from snapshots on startup.

    Restores:
    - Active positions
    - Pending orders
    - Phase state
    """
    snapshot_manager = get_snapshot_manager()

    # Find all snapshots
    snapshots = list(snapshot_manager.snapshot_dir.glob("*.json"))

    if not snapshots:
        logger.info("No FSM snapshots found, starting fresh")
        return

    logger.info(f"Recovering from {len(snapshots)} FSM snapshots...")

    recovered_count = 0
    for snapshot_path in snapshots:
        try:
            symbol = snapshot_path.stem.replace('_', '/')  # BTC_USDT → BTC/USDT

            # Initialize coin state
            coin_state = CoinState(symbol)

            # Restore from snapshot
            if snapshot_manager.restore_state(symbol, coin_state):
                self.coin_states[symbol] = coin_state
                recovered_count += 1

                logger.info(
                    f"Recovered {symbol}: {coin_state.phase.name}"
                )

        except Exception as e:
            logger.error(f"Failed to recover snapshot {snapshot_path}: {e}")

    logger.info(f"FSM recovery complete: {recovered_count}/{len(snapshots)} restored")

    # Log recovery event
    log_event(
        DECISION_LOG(),
        "fsm_recovery",
        snapshots_found=len(snapshots),
        recovered_count=recovered_count,
        recovered_symbols=[s for s in self.coin_states.keys()]
    )
```

**Why Important**: Seamless recovery after crashes without manual intervention.

---

## Phase 4: Event Bus & Decoupling (Nice-to-Have)

**Goal**: Decouple components via in-process event bus

### TODO 4.1: In-Process Event Bus ✅

**File**: `core/events/event_bus.py` (CREATE)

**Implementation**:
```python
"""
In-Process Event Bus

Decouples FSM from exchange updates and market data.
"""

import threading
from typing import Callable, Dict, List
from collections import defaultdict
import logging

logger = logging.getLogger(__name__)

class EventBus:
    """
    Simple in-process event bus.

    Supports:
    - Subscribe to event types
    - Publish events
    - Wildcard subscriptions
    """

    def __init__(self):
        self._subscribers: Dict[str, List[Callable]] = defaultdict(list)
        self._lock = threading.RLock()
        self._publish_count = 0

    def subscribe(self, event_type: str, handler: Callable):
        """
        Subscribe to event type.

        Args:
            event_type: Event type string (e.g., "order.filled", "tick.received")
            handler: Callback function(event_data: Dict)
        """
        with self._lock:
            self._subscribers[event_type].append(handler)
            logger.debug(f"Subscribed to {event_type}: {handler.__name__}")

    def publish(self, event_type: str, event_data: Dict):
        """
        Publish event to all subscribers.

        Args:
            event_type: Event type
            event_data: Event payload dict
        """
        with self._lock:
            handlers = self._subscribers.get(event_type, [])

            if not handlers:
                logger.debug(f"No subscribers for {event_type}")
                return

            self._publish_count += 1

            for handler in handlers:
                try:
                    handler(event_data)
                except Exception as e:
                    logger.error(
                        f"Event handler {handler.__name__} failed for {event_type}: {e}",
                        exc_info=True
                    )

    def get_stats(self) -> Dict:
        """Get event bus statistics"""
        with self._lock:
            return {
                'subscriber_count': sum(len(handlers) for handlers in self._subscribers.values()),
                'event_types': list(self._subscribers.keys()),
                'publish_count': self._publish_count
            }


# Global singleton
_event_bus = None

def get_event_bus() -> EventBus:
    """Get global event bus singleton"""
    global _event_bus
    if _event_bus is None:
        _event_bus = EventBus()
    return _event_bus
```

**Usage Example**:
```python
from core.events.event_bus import get_event_bus

event_bus = get_event_bus()

# Subscribe
def on_order_filled(event_data):
    symbol = event_data['symbol']
    fsm.process_event(...)

event_bus.subscribe("order.filled", on_order_filled)

# Publish (from exchange update handler)
event_bus.publish("order.filled", {
    'symbol': 'BTC/USDT',
    'order_id': 'abc123',
    'filled_qty': 0.001
})
```

**Why Important**: Decouples FSM from direct exchange polling. Enables easier testing.

---

### TODO 4.2: Market Data → FSM Integration ✅

**File**: `engine/fsm_engine.py` (MODIFY)

**Changes**: Replace direct market data processing with event bus:

```python
def _process_market_tick(self, symbol: str, price: float, timestamp: float):
    """Process market tick (OLD: direct call, NEW: via event bus)"""
    event_bus = get_event_bus()

    # Publish tick event
    event_bus.publish("market.tick", {
        'symbol': symbol,
        'price': price,
        'timestamp': timestamp
    })

# Subscribe FSM to tick events
def _subscribe_to_market_events(self):
    """Subscribe FSM to market tick events"""
    event_bus = get_event_bus()

    def on_market_tick(event_data):
        symbol = event_data['symbol']
        price = event_data['price']

        coin_state = self.coin_states.get(symbol)
        if not coin_state:
            return

        # Create FSM event
        ctx = EventContext(
            event=FSMEvent.TICK_RECEIVED,
            symbol=symbol,
            timestamp=event_data['timestamp'],
            data={'price': price}
        )

        # Process via FSM
        fsm = get_fsm()
        fsm.process_event(coin_state, ctx)

    event_bus.subscribe("market.tick", on_market_tick)
```

**Why Important**: Clean separation between data ingestion and decision logic.

---

## Phase 5: Validation & Cleanup (Nice-to-Have)

**Goal**: Hybrid validator, remove legacy code

### TODO 5.1: Hybrid Validator ✅

**File**: `core/fsm/validator.py` (CREATE)

**Implementation**:
```python
"""
Hybrid Validator - Detects FSM/Portfolio Divergence

Runs in parallel to production FSM to detect state drift.
"""

import logging
from typing import Dict, List, Optional
from core.fsm.machine import FSMachine
from engine.fsm_engine import CoinState, Phase
from core.fsm.events import EventContext
import copy

logger = logging.getLogger(__name__)

class HybridValidator:
    """
    Runs shadow FSM for validation.

    Compares:
    - FSM phase state
    - Portfolio position quantities
    - Order statuses

    Reports divergences without affecting production.
    """

    def __init__(self, production_fsm: FSMachine):
        self.production_fsm = production_fsm
        self.shadow_fsm = FSMachine()  # Separate FSM instance
        self.shadow_states: Dict[str, CoinState] = {}

        self.divergence_count = 0
        self.divergences: List[Dict] = []

    def validate_transition(
        self,
        symbol: str,
        prod_state: CoinState,
        ctx: EventContext
    ) -> Optional[Dict]:
        """
        Validate transition by running shadow FSM in parallel.

        Returns:
            Divergence dict if states differ, None if match
        """
        # Get or create shadow state
        if symbol not in self.shadow_states:
            self.shadow_states[symbol] = copy.deepcopy(prod_state)

        shadow_state = self.shadow_states[symbol]

        # Process event in shadow FSM
        shadow_result = self.shadow_fsm.process_event(shadow_state, ctx)
        prod_result = True  # Production already processed

        # Compare results
        if shadow_state.phase != prod_state.phase:
            divergence = {
                'symbol': symbol,
                'event': ctx.event.name,
                'production_phase': prod_state.phase.name,
                'shadow_phase': shadow_state.phase.name,
                'timestamp': ctx.timestamp
            }

            self.divergence_count += 1
            self.divergences.append(divergence)

            logger.error(
                f"FSM DIVERGENCE: {symbol} | "
                f"Prod: {prod_state.phase.name} | "
                f"Shadow: {shadow_state.phase.name} | "
                f"Event: {ctx.event.name}"
            )

            return divergence

        return None

    def get_divergence_summary(self) -> Dict:
        """Get summary of detected divergences"""
        return {
            'total_divergences': self.divergence_count,
            'recent_divergences': self.divergences[-10:],  # Last 10
            'divergence_rate': self.divergence_count / max(1, self.production_fsm._transition_count)
        }


# Global singleton (optional, for monitoring)
_hybrid_validator = None

def get_hybrid_validator() -> HybridValidator:
    """Get hybrid validator (if enabled)"""
    global _hybrid_validator
    return _hybrid_validator

def init_hybrid_validator(production_fsm: FSMachine):
    """Initialize hybrid validator (if enabled in config)"""
    import config
    if not getattr(config, 'ENABLE_HYBRID_VALIDATOR', False):
        return

    global _hybrid_validator
    _hybrid_validator = HybridValidator(production_fsm)
    logger.info("Hybrid validator enabled")
```

**Why Important**: Catches FSM bugs in production without disrupting live trading.

---

### TODO 5.2: Legacy Code Cleanup ✅

**File**: `engine/fsm_engine.py` (MODIFY)

**Changes**:
1. Remove unused phases (if any)
2. Remove legacy timeout code (replaced by TimeoutManager)
3. Remove inline retry logic (replaced by decorators)
4. Remove scattered logging (replaced by audit logging)

**Checklist**:
- [ ] Remove `_handle_buy_timeout()` (replaced by TimeoutManager)
- [ ] Remove `_handle_sell_timeout()` (replaced by TimeoutManager)
- [ ] Remove inline `try/except` retry loops (replaced by decorators)
- [ ] Remove direct `coin_state.phase = ...` assignments (use FSMachine.process_event)
- [ ] Remove unused config flags (`OLD_FSM_ENABLED`, etc.)

**Why Important**: Reduces technical debt, improves maintainability.

---

### TODO 5.3: Documentation & Testing ✅

**File**: `docs/FSM_Architecture.md` (CREATE)

**Content**:
```markdown
# FSM Architecture

## Overview

The Trading Bot FSM is a **table-driven, event-based state machine** with:
- Explicit state transitions via transition table
- Idempotency for duplicate event handling
- Crash recovery via snapshot persistence
- Comprehensive audit logging

## States (Phases)

1. **WARMUP**: Initial warmup period (30 seconds)
2. **IDLE**: Waiting for signals
3. **ENTRY_EVAL**: Evaluating entry guards/risk limits
4. **PLACE_BUY**: Placing buy order
5. **WAIT_FILL**: Waiting for buy order fill
6. **POSITION**: Holding position, monitoring exits
7. **EXIT_EVAL**: Evaluating exit conditions
8. **PLACE_SELL**: Placing sell order
9. **WAIT_SELL_FILL**: Waiting for sell order fill
10. **POST_TRADE**: Post-trade cleanup
11. **COOLDOWN**: Cooldown period before next trade
12. **ERROR**: Error state (requires manual recovery)

## Events

See `core/fsm/events.py` for complete event taxonomy.

Key events:
- `SIGNAL_DETECTED`: Buy signal triggered
- `BUY_ORDER_FILLED`: Buy order completely filled
- `EXIT_SIGNAL_TP`: Take profit triggered
- `SELL_ORDER_FILLED`: Sell order completely filled
- `ERROR_OCCURRED`: Unhandled error

## Transition Table

Defined in `core/fsm/transitions.py`.

Example transitions:
```
(IDLE, SIGNAL_DETECTED) → (ENTRY_EVAL, action_evaluate_entry)
(ENTRY_EVAL, GUARDS_PASSED) → (PLACE_BUY, action_prepare_buy)
(WAIT_FILL, BUY_ORDER_FILLED) → (POSITION, action_open_position)
(POSITION, EXIT_SIGNAL_TP) → (PLACE_SELL, action_prepare_sell)
```

## Recovery

On crash, the FSM recovers from snapshots:
1. Load all `fsm_snapshots/{symbol}.json` files
2. Restore phase and state data
3. Resume from last known state

## Testing

Run FSM tests:
```bash
pytest tests/test_fsm_machine.py
pytest tests/test_fsm_transitions.py
pytest tests/test_fsm_recovery.py
```
```

**Unit Test Files**:
- `tests/test_fsm_machine.py` - Core FSM engine tests
- `tests/test_fsm_transitions.py` - Transition table tests
- `tests/test_fsm_idempotency.py` - Idempotency tests
- `tests/test_fsm_snapshot.py` - Snapshot/recovery tests
- `tests/test_fsm_timeouts.py` - Timeout manager tests

**Why Important**: Documentation ensures maintainability. Tests ensure correctness.

---

## Configuration Requirements

**File**: `config.py` (ADD)

```python
# ============================================================================
# FSM Configuration
# ============================================================================

# Timeouts
BUY_FILL_TIMEOUT_SECS = 30       # Timeout for buy order fill
SELL_FILL_TIMEOUT_SECS = 30      # Timeout for sell order fill
COOLDOWN_SECS = 60               # Cooldown between trades

# Snapshot
FSM_SNAPSHOT_ENABLED = True      # Enable FSM snapshotting
FSM_SNAPSHOT_DIR = "sessions/current/fsm_snapshots"

# Retry
ORDER_RETRY_MAX_ATTEMPTS = 3     # Max retry attempts for order placement
ORDER_RETRY_BASE_DELAY = 1.0     # Base delay for exponential backoff

# Validation
ENABLE_HYBRID_VALIDATOR = False  # Enable shadow FSM validator (debug only)

# Idempotency
IDEMPOTENCY_EXPIRY_SECS = 300    # Event fingerprint expiry time
```

---

## Testing Plan

### Phase 0 Tests
- [ ] Test all events can be created
- [ ] Test transition table completeness (all states have exit paths)
- [ ] Test idempotency store (duplicate detection, expiry)
- [ ] Test FSM core engine (happy path, invalid transitions, error handling)

### Phase 1 Tests
- [ ] Test timeout detection (buy/sell/cooldown)
- [ ] Test partial fill accumulation (weighted average price)
- [ ] Test order lifecycle (place → ack → partial → filled)
- [ ] Test order cancellation and cleanup

### Phase 2 Tests
- [ ] Test retry decorators (success, failure, max attempts)
- [ ] Test ERROR state transitions
- [ ] Test audit logging completeness

### Phase 3 Tests
- [ ] Test snapshot save/load
- [ ] Test crash recovery (save snapshot → kill → restart → verify state)
- [ ] Test portfolio transaction rollback

### Phase 4 Tests
- [ ] Test event bus (pub/sub, handler exceptions)
- [ ] Test market tick → FSM event flow

### Phase 5 Tests
- [ ] Test hybrid validator divergence detection
- [ ] Integration tests with real market data replay

---

## Implementation Priority

**PFLICHT (Must-Have)**:
1. Phase 0: Foundation (Event system, Transition table, Idempotency, FSM core)
2. Phase 1: Order lifecycle (Timeouts, Partial fills)
3. Phase 2: Error handling (Retry, ERROR state, Audit logging)
4. Phase 3: Persistence (Snapshots, Recovery, Portfolio transactions)

**Nice-to-Have**:
5. Phase 4: Event bus (Decoupling)
6. Phase 5: Validation (Hybrid validator, Cleanup)

---

## Success Metrics

**Correctness**:
- ✅ 100% of transitions go through transition table
- ✅ No duplicate order placements (idempotency)
- ✅ No state divergence between FSM and portfolio

**Reliability**:
- ✅ Recovery from crashes within 1 second
- ✅ No lost positions on restart
- ✅ Transient network errors handled gracefully (retry logic)

**Observability**:
- ✅ Every transition logged to audit log
- ✅ Complete event trace for debugging
- ✅ Error state with safe-halt

---

## Rollout Plan

### Stage 1: Development (Weeks 1-2)
- Implement Phase 0 (Foundation)
- Implement Phase 1 (Order lifecycle)
- Unit tests for all components

### Stage 2: Integration (Week 3)
- Implement Phase 2 (Error handling)
- Implement Phase 3 (Persistence)
- Integration tests with testnet

### Stage 3: Testing (Week 4)
- Load testing (100+ symbols)
- Chaos testing (kill -9, network failures)
- Hybrid validator testing

### Stage 4: Production Rollout (Week 5)
- Shadow mode (hybrid validator active)
- Gradual rollout (10% → 50% → 100% of symbols)
- Monitor divergences and errors

### Stage 5: Cleanup (Week 6)
- Remove legacy FSM code
- Phase 4/5 implementation (optional)
- Documentation finalization

---

## Migration from Legacy FSM

**Current State**: Handler-based FSM with implicit transitions
**Target State**: Table-driven FSM with explicit transitions

**Migration Strategy**:
1. **Parallel Implementation**: Implement new FSM alongside old FSM
2. **Symbol-by-Symbol Rollout**: Migrate symbols gradually
3. **Hybrid Validator**: Run shadow FSM to detect divergences
4. **Feature Flag**: `config.USE_TABLE_DRIVEN_FSM = True/False`
5. **Fallback**: If divergence detected, fall back to legacy FSM

**Rollback Plan**:
- Keep legacy FSM code until 100% rollout complete
- Monitor error rates and divergence rates
- Automatic fallback if divergence rate > 1%

---

## Appendix: Event Flow Examples

### Example 1: Successful Trade

```
1. IDLE + SIGNAL_DETECTED → ENTRY_EVAL
2. ENTRY_EVAL + GUARDS_PASSED → PLACE_BUY
3. PLACE_BUY + BUY_ORDER_PLACED → WAIT_FILL
4. WAIT_FILL + BUY_ORDER_FILLED → POSITION
5. POSITION + EXIT_SIGNAL_TP → PLACE_SELL
6. PLACE_SELL + SELL_ORDER_PLACED → WAIT_SELL_FILL
7. WAIT_SELL_FILL + SELL_ORDER_FILLED → POST_TRADE
8. POST_TRADE + TICK_RECEIVED → COOLDOWN
9. COOLDOWN + COOLDOWN_EXPIRED → IDLE
```

### Example 2: Order Timeout

```
1. IDLE + SIGNAL_DETECTED → ENTRY_EVAL
2. ENTRY_EVAL + GUARDS_PASSED → PLACE_BUY
3. PLACE_BUY + BUY_ORDER_PLACED → WAIT_FILL
4. WAIT_FILL + BUY_ORDER_TIMEOUT → IDLE (timeout after 30s)
```

### Example 3: Partial Fill

```
1. PLACE_BUY + BUY_ORDER_PLACED → WAIT_FILL
2. WAIT_FILL + BUY_ORDER_PARTIAL → WAIT_FILL (50% filled)
3. WAIT_FILL + BUY_ORDER_PARTIAL → WAIT_FILL (75% filled)
4. WAIT_FILL + BUY_ORDER_FILLED → POSITION (100% filled)
```

### Example 4: Error Recovery

```
1. PLACE_BUY + ERROR_OCCURRED → ERROR (exchange unreachable)
2. ERROR + MANUAL_HALT → ERROR (halted, waiting for intervention)
3. (Manual recovery via dashboard)
4. ERROR → IDLE (manually recovered)
```

---

**End of FSM Roadmap**

This roadmap provides a complete, structured path to a production-grade FSM implementation with crash recovery, idempotency, and comprehensive observability.
