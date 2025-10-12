#!/usr/bin/env python3
"""
FSM Transition Table

(CurrentState, Event) → (NextState, Action)

The single source of truth for all state transitions.
"""

from typing import Dict, Tuple, Callable, Optional, List
from core.fsm.fsm_events import FSMEvent, EventContext
from core.fsm.phases import Phase

# Type aliases
State = Phase
Action = Callable[[EventContext, 'CoinState'], None]
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

        # Import actions here to avoid circular imports
        from core.fsm.actions import (
            action_warmup_complete,
            action_idle_tick,
            action_evaluate_entry,
            action_prepare_buy,
            action_log_blocked,
            action_wait_for_fill,
            action_open_position,
            action_handle_partial_buy,
            action_cancel_and_cleanup,
            action_cleanup_cancelled,
            action_handle_reject,
            action_check_exit,
            action_update_pnl,
            action_prepare_sell,
            action_continue_holding,
            action_wait_for_sell,
            action_close_position,
            action_handle_partial_sell,
            action_retry_sell,
            action_start_cooldown,
            action_check_cooldown,
            action_reset_to_idle,
            action_log_error,
            action_safe_halt,
        )

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

    def get_all_transitions(self) -> Dict[TransitionKey, TransitionValue]:
        """Get all transitions (for debugging/testing)"""
        return self._table.copy()

    def get_transition_count(self) -> int:
        """Get total number of defined transitions"""
        return len(self._table)


# Singleton instance
_transition_table: Optional[TransitionTable] = None


def get_transition_table() -> TransitionTable:
    """Get global transition table singleton"""
    global _transition_table
    if _transition_table is None:
        _transition_table = TransitionTable()
    return _transition_table
