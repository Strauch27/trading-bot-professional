#!/usr/bin/env python3
"""
ERROR State Handling

Safe-halt mechanism for unrecoverable errors.
"""

from core.fsm.fsm_events import FSMEvent, EventContext
from core.fsm.phases import Phase
from core.fsm.state import CoinState
import time
import logging

logger = logging.getLogger(__name__)


class ErrorHandler:
    """
    Handles transitions to ERROR state and recovery.
    """

    def __init__(self):
        self.error_symbols = set()  # Track symbols in ERROR state
        self.error_history = []  # Track error events for analysis

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
        if hasattr(coin_state, 'fsm_data') and coin_state.fsm_data:
            coin_state.fsm_data.error_count += 1
            coin_state.fsm_data.last_error = str(error)
            coin_state.fsm_data.last_error_at = time.time()
        else:
            # Update coin_state directly if no fsm_data
            coin_state.error_count += 1
            coin_state.last_error = str(error)

        # Track errored symbol
        self.error_symbols.add(symbol)

        # Record error event
        error_event = {
            'timestamp': time.time(),
            'symbol': symbol,
            'error_type': type(error).__name__,
            'error_message': str(error),
            'context': context,
            'phase': coin_state.phase.name,
            'error_count': coin_state.error_count
        }
        self.error_history.append(error_event)

        # Keep history limited
        if len(self.error_history) > 1000:
            self.error_history = self.error_history[-1000:]

        # Log error transition
        logger.error(
            f"FSM ERROR for {symbol}: {context} - {error}",
            exc_info=True,
            extra={
                'event_type': 'FSM_ERROR',
                'symbol': symbol,
                'context': context,
                'error_count': coin_state.error_count
            }
        )

        # Log to structured log
        try:
            from core.logger_factory import log_event, DECISION_LOG
            log_event(
                DECISION_LOG(),
                "fsm_error",
                symbol=symbol,
                error_type=type(error).__name__,
                error_message=str(error),
                context=context,
                error_count=coin_state.error_count,
                current_phase=coin_state.phase.name
            )
        except Exception as e:
            logger.debug(f"Failed to log fsm_error event: {e}")

        # Create error event
        return EventContext(
            event=FSMEvent.ERROR_OCCURRED,
            symbol=symbol,
            timestamp=time.time(),
            error=error,
            data={'context': context, 'error_count': coin_state.error_count}
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
            logger.warning(f"Cannot recover {symbol}: not in ERROR state")
            return False

        # Reset to IDLE
        coin_state.phase = Phase.IDLE

        # Clear error state
        if hasattr(coin_state, 'fsm_data') and coin_state.fsm_data:
            coin_state.fsm_data.error_count = 0
            coin_state.fsm_data.last_error = None
            coin_state.fsm_data.last_error_at = None
        else:
            coin_state.error_count = 0
            coin_state.last_error = ""

        # Remove from error tracking
        self.error_symbols.discard(symbol)

        logger.info(f"Manual recovery: {symbol} → IDLE")

        # Log recovery event
        try:
            from core.logger_factory import log_event, DECISION_LOG
            log_event(
                DECISION_LOG(),
                "fsm_manual_recovery",
                symbol=symbol,
                recovered_to="IDLE",
                timestamp=time.time()
            )
        except Exception as e:
            logger.debug(f"Failed to log manual_recovery event: {e}")

        return True

    def auto_recover(self, symbol: str, coin_state: CoinState, max_error_count: int = 5) -> bool:
        """
        Attempt automatic recovery if error count is below threshold.

        Args:
            symbol: Trading symbol
            coin_state: Current coin state
            max_error_count: Maximum errors before requiring manual intervention

        Returns:
            True if auto-recovery successful
        """
        if symbol not in self.error_symbols:
            return False

        error_count = coin_state.error_count

        if error_count > max_error_count:
            logger.warning(
                f"Auto-recovery blocked for {symbol}: "
                f"error_count={error_count} exceeds max={max_error_count}"
            )
            return False

        # Reset to IDLE with backoff
        coin_state.phase = Phase.IDLE

        # Keep error count but clear last error
        if hasattr(coin_state, 'fsm_data') and coin_state.fsm_data:
            coin_state.fsm_data.last_error = None
            coin_state.fsm_data.last_error_at = None
        else:
            coin_state.last_error = ""

        # Remove from error tracking
        self.error_symbols.discard(symbol)

        logger.info(f"Auto-recovery: {symbol} → IDLE (error_count={error_count})")

        # Log recovery event
        try:
            from core.logger_factory import log_event, DECISION_LOG
            log_event(
                DECISION_LOG(),
                "fsm_auto_recovery",
                symbol=symbol,
                recovered_to="IDLE",
                error_count=error_count,
                timestamp=time.time()
            )
        except Exception as e:
            logger.debug(f"Failed to log auto_recovery event: {e}")

        return True

    def get_error_summary(self) -> dict:
        """Get summary of all symbols in ERROR state"""
        return {
            'error_count': len(self.error_symbols),
            'error_symbols': list(self.error_symbols),
            'recent_errors': self.error_history[-10:] if self.error_history else []
        }

    def get_error_stats(self) -> dict:
        """Get detailed error statistics"""
        if not self.error_history:
            return {
                'total_errors': 0,
                'errors_by_type': {},
                'errors_by_symbol': {},
                'errors_by_context': {}
            }

        # Count errors by type
        errors_by_type = {}
        errors_by_symbol = {}
        errors_by_context = {}

        for error_event in self.error_history:
            error_type = error_event['error_type']
            symbol = error_event['symbol']
            context = error_event['context']

            errors_by_type[error_type] = errors_by_type.get(error_type, 0) + 1
            errors_by_symbol[symbol] = errors_by_symbol.get(symbol, 0) + 1
            errors_by_context[context] = errors_by_context.get(context, 0) + 1

        return {
            'total_errors': len(self.error_history),
            'errors_by_type': errors_by_type,
            'errors_by_symbol': errors_by_symbol,
            'errors_by_context': errors_by_context,
            'current_error_symbols': len(self.error_symbols)
        }

    def clear_error_history(self):
        """Clear error history (for testing or periodic cleanup)"""
        self.error_history.clear()
        logger.info("Error history cleared")

    def reset_error_symbols(self):
        """Reset error symbols tracking (for testing)"""
        self.error_symbols.clear()
        logger.info("Error symbols tracking reset")


# Global singleton
_error_handler = None


def get_error_handler() -> ErrorHandler:
    """Get global error handler singleton"""
    global _error_handler
    if _error_handler is None:
        _error_handler = ErrorHandler()
        logger.info("ErrorHandler initialized")
    return _error_handler
