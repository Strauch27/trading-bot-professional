# Services package (sizing, sell, pnl, …)

# Import existing services
from .buy_service import BuyPlan, BuyService

# Import new Buy Signals & Market Guards services (Drop 5)
from .buy_signals import BuySignalService

# Import new Exit & Market Data services (Drop 4)
from .exits import ExitContext, ExitEvaluator, ExitManager, ExitOrderManager, ExitResult, ExitSignal
from .market_data import (
    MarketDataProvider,
    OHLCVBar,
    OHLCVHistory,
    TickerCache,
    TickerData,
    fetch_ticker_cached,
    fetch_ticker_with_retry,
)
from .market_guards import MarketGuards

# Import new Order services (Drop 3)
from .orders import OrderService
from .orders_cache import OrderCache

# Import new PnL service (Drop 1)
from .pnl import PnLService, PnLSummary, PositionState, TradeRecord
from .sell_service import SellService
from .signals import ExitSignalQueue, SignalManager
from .sizing_service import SizingService

# Import new Trailing & Signals services (Drop 2)
from .trailing import TrailingStopController, TrailingStopManager

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
