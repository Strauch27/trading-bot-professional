"""
FSM State Machine Orchestrator

Manages all symbol states and coordinates phase transitions.
Central registry for all FSM operations.
"""

import logging
import time
from typing import Any, Callable, Dict, List, Optional

from .events import EventType, create_event
from .phases import Phase
from .state import CoinState, set_phase

logger = logging.getLogger(__name__)


class StateMachine:
    """
    FSM Orchestrator for all trading symbols.

    Responsibilities:
    - Symbol registration
    - State storage and retrieval
    - Phase transition coordination
    - Handler registration and invocation
    - Error handling and recovery
    """

    def __init__(self, phase_logger=None, metrics=None):
        """
        Initialize State Machine.

        Args:
            phase_logger: Logger instance for phase events (expects .log_phase_change method)
            metrics: Metrics instance for Prometheus (expects .phase_changes, .phase_code, .PHASE_MAP)
        """
        self.states: Dict[str, CoinState] = {}
        self.phase_logger = phase_logger
        self.metrics = metrics

        # Transition Handlers: phase -> handler_func
        self.transitions: Dict[Phase, Callable] = {}

        # Statistics
        self.stats = {
            "total_transitions": 0,
            "transitions_by_phase": {},
            "errors_by_symbol": {},
            "symbols_registered": 0,
        }

        logger.info("StateMachine initialized")

    def register_symbol(self, symbol: str, initial_phase: Phase = Phase.WARMUP) -> CoinState:
        """
        Register new symbol with initial phase.

        Args:
            symbol: Trading symbol (e.g. "BTC/USDT")
            initial_phase: Initial phase (default: WARMUP)

        Returns:
            Created CoinState
        """
        if symbol in self.states:
            logger.warning(f"Symbol {symbol} already registered, returning existing state")
            return self.states[symbol]

        # Create new state
        st = CoinState(symbol=symbol, phase=initial_phase)

        # Register
        self.states[symbol] = st
        self.stats["symbols_registered"] += 1

        # Emit initial phase
        set_phase(st, initial_phase, note="symbol_registered",
                 log=self.phase_logger, metrics=self.metrics)

        # Log registration event
        if self.phase_logger:
            event = create_event(
                EventType.SYMBOL_REGISTERED,
                symbol=symbol,
                initial_phase=initial_phase.value
            )
            self.phase_logger.log_phase_change(event)

        logger.info(f"Symbol registered: {symbol} → {initial_phase.value}")

        return st

    def register_transition(self, phase: Phase, handler: Callable):
        """
        Register phase transition handler.

        Handler signature: handler(st: CoinState, ctx: Dict) -> None

        Args:
            phase: Phase to handle
            handler: Handler function
        """
        if phase in self.transitions:
            logger.warning(f"Overwriting existing handler for phase: {phase.value}")

        self.transitions[phase] = handler
        logger.debug(f"Registered handler for phase: {phase.value}")

    def register_all_transitions(self, handlers: Dict[Phase, Callable]):
        """
        Register multiple transition handlers at once.

        Args:
            handlers: Dict mapping Phase -> handler function
        """
        for phase, handler in handlers.items():
            self.register_transition(phase, handler)

        logger.info(f"Registered {len(handlers)} transition handlers")

    def process_symbol(self, symbol: str, context: Dict[str, Any]):
        """
        Process one symbol through FSM.

        Looks up current phase, invokes handler, handles errors.

        Args:
            symbol: Trading symbol
            context: Context dict with price, orderbook, etc.
        """
        # Get or create state
        st = self.states.get(symbol)
        if not st:
            st = self.register_symbol(symbol)

        # Get handler
        handler = self.transitions.get(st.phase)
        if not handler:
            # No handler registered for this phase (may be intentional for terminal states)
            logger.debug(f"No handler for {symbol} in phase {st.phase.value}")
            return

        # Invoke handler with error handling
        try:
            handler(st, context)

            # Update stats
            self.stats["total_transitions"] += 1
            phase_key = st.phase.value
            self.stats["transitions_by_phase"][phase_key] = \
                self.stats["transitions_by_phase"].get(phase_key, 0) + 1

        except Exception as e:
            # Error handling
            logger.error(f"FSM error for {symbol} in {st.phase.value}: {e}", exc_info=True)

            # Update state
            st.error_count += 1
            st.last_error = str(e)

            # Update stats
            self.stats["errors_by_symbol"][symbol] = \
                self.stats["errors_by_symbol"].get(symbol, 0) + 1

            # Transition to ERROR phase
            set_phase(st, Phase.ERROR, note=str(e)[:100],
                     log=self.phase_logger, metrics=self.metrics)

            # Log error event
            if self.phase_logger:
                event = create_event(
                    EventType.ERROR_OCCURRED,
                    symbol=symbol,
                    phase=st.phase.value,
                    error=str(e),
                    error_count=st.error_count
                )
                self.phase_logger.log_phase_change(event)

    def process_all_symbols(self, get_context_func: Callable[[str], Dict]):
        """
        Process all registered symbols.

        Args:
            get_context_func: Function that takes symbol and returns context dict
        """
        for symbol in list(self.states.keys()):
            try:
                context = get_context_func(symbol)
                self.process_symbol(symbol, context)
            except Exception as e:
                logger.error(f"Error getting context for {symbol}: {e}")

    def get_state(self, symbol: str) -> Optional[CoinState]:
        """
        Get current state for symbol.

        Args:
            symbol: Trading symbol

        Returns:
            CoinState or None if not registered
        """
        return self.states.get(symbol)

    def get_all_states(self) -> Dict[str, CoinState]:
        """
        Get all states (for status table, monitoring).

        Returns:
            Dict mapping symbol -> CoinState
        """
        return self.states.copy()

    def get_states_by_phase(self, phase: Phase) -> List[CoinState]:
        """
        Get all symbols in a specific phase.

        Args:
            phase: Phase to filter by

        Returns:
            List of CoinStates in that phase
        """
        return [st for st in self.states.values() if st.phase == phase]

    def get_active_positions(self) -> List[CoinState]:
        """
        Get all states with active positions.

        Returns:
            List of CoinStates with amount > 0
        """
        return [st for st in self.states.values() if st.has_position()]

    def get_stuck_states(self, timeout_seconds: float = 60.0) -> List[CoinState]:
        """
        Get states that have been in current phase too long.

        Args:
            timeout_seconds: Threshold for "stuck"

        Returns:
            List of stuck CoinStates
        """
        return [st for st in self.states.values()
                if st.age_seconds() > timeout_seconds
                and st.phase not in [Phase.IDLE, Phase.COOLDOWN, Phase.WARMUP]]

    def reset_symbol(self, symbol: str, keep_history: bool = False):
        """
        Reset symbol to IDLE state.

        Args:
            symbol: Symbol to reset
            keep_history: If True, preserve phase history
        """
        st = self.states.get(symbol)
        if not st:
            logger.warning(f"Cannot reset {symbol}: not registered")
            return

        from .state import reset_state
        reset_state(st, keep_history=keep_history)

        set_phase(st, Phase.IDLE, note="manual_reset",
                 log=self.phase_logger, metrics=self.metrics)

        logger.info(f"Symbol reset: {symbol}")

    def unregister_symbol(self, symbol: str):
        """
        Remove symbol from FSM (cleanup).

        Args:
            symbol: Symbol to remove
        """
        if symbol in self.states:
            del self.states[symbol]
            logger.info(f"Symbol unregistered: {symbol}")

    def get_statistics(self) -> Dict[str, Any]:
        """
        Get FSM statistics.

        Returns:
            Dict with transition counts, error counts, etc.
        """
        return {
            **self.stats,
            "active_symbols": len(self.states),
            "active_positions": len(self.get_active_positions()),
            "phase_distribution": {
                phase.value: len(self.get_states_by_phase(phase))
                for phase in Phase
            },
        }

    def get_phase_distribution(self) -> Dict[str, int]:
        """
        Get count of symbols in each phase.

        Returns:
            Dict mapping phase_name -> count
        """
        dist = {}
        for st in self.states.values():
            phase_name = st.phase.value
            dist[phase_name] = dist.get(phase_name, 0) + 1
        return dist

    def export_states(self) -> Dict[str, Dict[str, Any]]:
        """
        Export all states as JSON-serializable dict (for state backup).

        Returns:
            Dict mapping symbol -> state dict
        """
        from dataclasses import asdict
        return {
            symbol: asdict(st)
            for symbol, st in self.states.items()
        }

    def import_states(self, states_dict: Dict[str, Dict[str, Any]]):
        """
        Import states from exported dict (for state restoration).

        Args:
            states_dict: Dict from export_states()
        """
        for symbol, state_data in states_dict.items():
            # Reconstruct CoinState
            st = CoinState(**state_data)
            self.states[symbol] = st

        logger.info(f"Imported {len(states_dict)} states")

    def cleanup_old_states(self, max_age_hours: float = 24.0):
        """
        Remove states that haven't been updated in a long time.

        Args:
            max_age_hours: Maximum age in hours before cleanup
        """
        max_age_seconds = max_age_hours * 3600
        current_time = time.time()

        to_remove = []
        for symbol, st in self.states.items():
            age = current_time - (st.ts_ms / 1000.0)
            if age > max_age_seconds and st.phase in [Phase.IDLE, Phase.WARMUP]:
                to_remove.append(symbol)

        for symbol in to_remove:
            del self.states[symbol]

        if to_remove:
            logger.info(f"Cleaned up {len(to_remove)} old states: {to_remove}")

    def __repr__(self):
        return (f"<StateMachine: {len(self.states)} symbols, "
                f"{len(self.transitions)} handlers, "
                f"{self.stats['total_transitions']} transitions>")
