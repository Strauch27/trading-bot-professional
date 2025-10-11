"""
FSM (Finite State Machine) Framework for Trading Bot

Provides explicit state management for each symbol, replacing implicit states
scattered across held_assets, open_buy_orders, and pending_exits.

Components:
- phases.py: Phase Enum definitions
- state.py: CoinState dataclass and transition helpers
- machine.py: StateMachine orchestrator
- events.py: Event definitions for logging
"""

from .phases import Phase
from .state import CoinState, set_phase
from .machine import StateMachine

__all__ = [
    'Phase',
    'CoinState',
    'set_phase',
    'StateMachine',
]
