"""
Trading Engine Package

Refactored engine module with separation of concerns:
- engine.py: Main orchestration
- buy_decision.py: Buy signal evaluation and execution
- position_manager.py: Position management and trailing stops
- exit_handler.py: Exit signal processing
- monitoring.py: Performance metrics and statistics
- engine_config.py: Configuration and factory functions
"""

from .engine import TradingEngine
from .engine_config import EngineConfig, create_mock_trading_engine, create_trading_engine

__all__ = [
    'TradingEngine',
    'EngineConfig',
    'create_trading_engine',
    'create_mock_trading_engine',
]
