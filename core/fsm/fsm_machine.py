#!/usr/bin/env python3
"""
FSM Core Engine - Table-driven state machine

Orchestrates transitions, actions, and logging.
"""

import logging
from typing import Optional, Dict, List
from core.fsm.fsm_events import FSMEvent, EventContext
from core.fsm.transitions import get_transition_table
from core.fsm.idempotency import get_idempotency_store
from core.fsm.phases import Phase
from core.fsm.state import CoinState

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
        self._invalid_transition_count = 0
        self._duplicate_event_count = 0

        logger.info(
            f"FSMachine initialized with {self.transition_table.get_transition_count()} transitions"
        )

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
            self._duplicate_event_count += 1
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
            self._invalid_transition_count += 1
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
            self._handle_action_error(coin_state, ctx, e)
            return False

        # Step 4: Update phase
        coin_state.phase = next_phase

        # Step 5: Mark as processed
        self.idempotency_store.mark_processed(ctx)

        # Step 6: Log transition
        self._log_transition(coin_state, ctx, current_phase, next_phase)

        self._transition_count += 1

        logger.debug(
            f"Transition executed: {ctx.symbol} {current_phase.name} → {next_phase.name} "
            f"(event: {ctx.event.name})"
        )

        return True

    def _log_transition(
        self,
        coin_state: CoinState,
        ctx: EventContext,
        from_phase: Phase,
        to_phase: Phase
    ):
        """Log successful transition"""
        try:
            from core.logger_factory import log_event, DECISION_LOG
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
        except Exception as e:
            logger.debug(f"Failed to log transition: {e}")

    def _log_invalid_transition(self, coin_state: CoinState, ctx: EventContext):
        """Log invalid transition attempt"""
        valid_events = self.transition_table.get_valid_events(coin_state.phase)

        try:
            from core.logger_factory import log_event, DECISION_LOG
            log_event(
                DECISION_LOG(),
                "fsm_invalid_transition",
                symbol=ctx.symbol,
                current_phase=coin_state.phase.name,
                event=ctx.event.name,
                order_id=ctx.order_id,
                valid_events=[e.name for e in valid_events]
            )
        except Exception as e:
            logger.debug(f"Failed to log invalid transition: {e}")

    def _handle_action_error(
        self,
        coin_state: CoinState,
        ctx: EventContext,
        error: Exception
    ):
        """Handle error during action execution"""
        # Transition to ERROR state
        coin_state.phase = Phase.ERROR
        coin_state.error_count += 1
        coin_state.last_error = str(error)

        # Create error event
        error_ctx = EventContext(
            event=FSMEvent.ERROR_OCCURRED,
            symbol=ctx.symbol,
            timestamp=ctx.timestamp,
            error=error,
            data={'original_event': ctx.event.name}
        )

        # Log error
        try:
            from core.logger_factory import log_event, DECISION_LOG
            log_event(
                DECISION_LOG(),
                "fsm_action_error",
                symbol=ctx.symbol,
                phase=coin_state.phase.name,
                event=ctx.event.name,
                error=str(error),
                error_count=coin_state.error_count
            )
        except Exception as e:
            logger.debug(f"Failed to log action error: {e}")

    def get_valid_events(self, coin_state: CoinState) -> List[FSMEvent]:
        """Get valid events for current state"""
        return self.transition_table.get_valid_events(coin_state.phase)

    def get_stats(self) -> Dict:
        """Get FSM statistics"""
        idempotency_stats = self.idempotency_store.get_stats()

        return {
            'total_transitions': self._transition_count,
            'invalid_transitions': self._invalid_transition_count,
            'duplicate_events': self._duplicate_event_count,
            'idempotency_store_size': idempotency_stats['store_size'],
            'idempotency_duplicate_rate': idempotency_stats['duplicate_rate'],
            'transition_table_size': self.transition_table.get_transition_count()
        }

    def reset_stats(self):
        """Reset statistics (for testing)"""
        self._transition_count = 0
        self._invalid_transition_count = 0
        self._duplicate_event_count = 0
        logger.info("FSMachine statistics reset")

    def __repr__(self):
        return (
            f"<FSMachine: {self._transition_count} transitions, "
            f"{self._invalid_transition_count} invalid, "
            f"{self._duplicate_event_count} duplicates>"
        )


# Global singleton
_fsm: Optional[FSMachine] = None


def get_fsm() -> FSMachine:
    """Get global FSM singleton"""
    global _fsm
    if _fsm is None:
        _fsm = FSMachine()
        logger.info("Global FSMachine instance created")
    return _fsm
