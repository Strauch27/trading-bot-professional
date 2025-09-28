# Services package (sizing, sell, pnl, …)

# Import existing services
from .sizing_service import SizingService
from .buy_service import BuyService, BuyPlan
from .sell_service import SellService

# Import new PnL service (Drop 1)
from .pnl import PnLService, TradeRecord, PnLSummary, PositionState

# Import new Trailing & Signals services (Drop 2)
from .trailing import TrailingStopController, TrailingStopManager
from .signals import ExitSignalQueue, SignalManager

# Import new Order services (Drop 3)
from .orders import OrderService
from .orders_cache import OrderCache

# Import new Exit & Market Data services (Drop 4)
from .exits import (
    ExitSignal, ExitContext, ExitResult, ExitEvaluator,
    ExitOrderManager, ExitManager
)
from .market_data import (
    TickerData, OHLCVBar, TickerCache, OHLCVHistory,
    MarketDataProvider, fetch_ticker_cached, fetch_ticker_with_retry
)

# Import new Buy Signals & Market Guards services (Drop 5)
from .buy_signals import BuySignalService
from .market_guards import MarketGuards

__all__ = [
    # Existing services
    'SizingService',
    'BuyService', 'BuyPlan',
    'SellService',

    # Drop 1: PnL service
    'PnLService',
    'TradeRecord',
    'PnLSummary',
    'PositionState',

    # Drop 2: Trailing & Signals
    'TrailingStopController',
    'TrailingStopManager',
    'ExitSignalQueue',
    'SignalManager',

    # Drop 3: Orders & Cache
    'OrderService',
    'OrderCache',

    # Drop 4: Exit Management & Market Data
    'ExitSignal', 'ExitContext', 'ExitResult', 'ExitEvaluator',
    'ExitOrderManager', 'ExitManager',
    'TickerData', 'OHLCVBar', 'TickerCache', 'OHLCVHistory',
    'MarketDataProvider', 'fetch_ticker_cached', 'fetch_ticker_with_retry',

    # Drop 5: Buy Signals & Market Guards
    'BuySignalService',
    'MarketGuards'
]