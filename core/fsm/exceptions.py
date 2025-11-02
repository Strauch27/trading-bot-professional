#!/usr/bin/env python3
"""
FSM Exceptions

Custom exceptions for FSM transition control and error handling.
"""


class FSMTransitionAbort(Exception):
    """
    Raised by action to abort current transition and return to IDLE.

    This allows actions to perform validation and abort the transition
    if preconditions are not met (e.g., preflight validation fails).

    Attributes:
        reason: Human-readable reason for aborting
        should_cooldown: Whether to set cooldown on return to IDLE
        cooldown_seconds: Optional cooldown duration (uses config default if None)
    """

    def __init__(
        self,
        reason: str,
        should_cooldown: bool = True,
        cooldown_seconds: float = None
    ):
        self.reason = reason
        self.should_cooldown = should_cooldown
        self.cooldown_seconds = cooldown_seconds
        super().__init__(reason)


class FSMValidationError(Exception):
    """
    Raised when FSM state validation fails.

    Used for state consistency checks and recovery scenarios.
    """
    pass
