#!/usr/bin/env python3
"""
Engine Configuration Module

Contains:
- EngineConfig: Configuration dataclass for trading engine
- Factory functions for creating engine instances
- Mock classes for testing
"""

import logging
import os
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

import config

logger = logging.getLogger(__name__)


@dataclass
class EngineConfig:
    """Configuration for Trading Engine"""
    trade_ttl_min: int = 60
    max_positions: int = 10
    settlement_tolerance: float = 0.001
    settlement_timeout: int = 30
    never_market_sells: bool = False
    exit_escalation_bps: List[int] = None
    ticker_cache_ttl: float = 5.0
    md_update_interval_s: float = 5.0
    enable_auto_exits: bool = True
    enable_trailing_stops: bool = True
    enable_top_drops_ticker: bool = True

    def __post_init__(self):
        if self.exit_escalation_bps is None:
            self.exit_escalation_bps = [50, 100, 200, 500]


# =================================================================
# MOCK CLASSES FOR TESTING
# =================================================================

class MockPortfolioManager:
    """Mock portfolio manager for testing"""
    def __init__(self, balances: Dict[str, float] = None):
        self.balances = balances or {"USDT": 10000.0}

    def get_balance(self, asset: str) -> float:
        return self.balances.get(asset, 0.0)


class MockOrderbookProvider:
    """Mock orderbook provider for testing"""
    def __init__(self):
        pass

    def _decision_bump(self, side: str, gate: str, reason: str):
        pass


# =================================================================
# FACTORY FUNCTIONS
# =================================================================

def create_trading_engine(exchange, portfolio, orderbookprovider,
                         telegram=None, mock_mode: bool = False,
                         watchlist: Optional[Dict[str, Any]] = None):
    """Factory function to create trading engine with default config"""

    # Import here to avoid circular dependency
    from .engine import TradingEngine

    engine_config = EngineConfig(
        trade_ttl_min=getattr(config, 'TRADE_TTL_MIN', 60),
        max_positions=getattr(config, 'MAX_POSITIONS', 10),
        never_market_sells=getattr(config, 'NEVER_MARKET_SELLS', False),
        exit_escalation_bps=getattr(config, 'EXIT_ESCALATION_BPS', [50, 100, 200, 500]),
        ticker_cache_ttl=float(os.getenv('TICKER_CACHE_TTL', getattr(config, 'TICKER_CACHE_TTL', 5.0))),
        md_update_interval_s=float(os.getenv('MD_UPDATE_INTERVAL_S', getattr(config, 'MD_UPDATE_INTERVAL_S', 5.0))),
        enable_auto_exits=True,
        enable_trailing_stops=True,
        enable_top_drops_ticker=getattr(config, 'ENABLE_TOP_DROPS_TICKER', False)
    )

    return TradingEngine(
        exchange=exchange,
        portfolio=portfolio,
        orderbookprovider=orderbookprovider,
        telegram=telegram,
        mock_mode=mock_mode,
        engine_config=engine_config,
        watchlist=watchlist
    )


def create_mock_trading_engine(initial_prices: Dict[str, float] = None):
    """Factory function to create mock trading engine for testing"""

    # Import here to avoid circular dependency
    from adapters.exchange import MockExchange

    if initial_prices is None:
        initial_prices = {"BTC/USDT": 50000.0, "ETH/USDT": 3000.0}

    mock_exchange = MockExchange(initial_prices)
    mock_portfolio = MockPortfolioManager({"USDT": 10000.0})
    mock_orderbook = MockOrderbookProvider()

    return create_trading_engine(
        exchange=mock_exchange,
        portfolio=mock_portfolio,
        orderbookprovider=mock_orderbook,
        mock_mode=True
    )
