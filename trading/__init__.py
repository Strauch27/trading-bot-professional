"""
Trading Operations Package

Refactored trading module with functional separation:
- orders.py: Order placement and execution
- portfolio_reset.py: Portfolio reset operations and cleanup
- orderbook.py: Orderbook analysis and depth sweep
- settlement.py: Settlement and balance management
- helpers.py: Helper functions for trading
- legacy.py: Deprecated/legacy functions
"""

# Main trading functions will be imported from submodules
from .orders import (
    place_limit_buy_with_coid,
    place_limit_ioc_buy_with_coid,
    place_limit_ioc_sell_with_coid,
    place_market_ioc_sell_with_coid,
    place_precise_limit_buy,
    place_limit_ioc_buy,
    place_limit_ioc_sell,
)

from .portfolio_reset import (
    full_portfolio_reset,
    cleanup_stale_orders,
)

from .orderbook import (
    fetch_top_of_book,
    compute_sweep_limit_price,
    compute_limit_buy_price_from_book,
)

from .settlement import (
    refresh_budget_from_exchange,
    refresh_budget_from_exchange_safe,
    wait_for_balance_settlement,
    sync_active_order_and_state,
)

from .helpers import (
    compute_min_cost,
    size_limit_sell,
    size_limit_buy,
    compute_safe_sell_amount,
)

__all__ = [
    # Orders
    'place_limit_buy_with_coid',
    'place_limit_ioc_buy_with_coid',
    'place_limit_ioc_sell_with_coid',
    'place_market_ioc_sell_with_coid',
    'place_precise_limit_buy',
    'place_limit_ioc_buy',
    'place_limit_ioc_sell',
    # Portfolio Reset
    'full_portfolio_reset',
    'cleanup_stale_orders',
    # Orderbook
    'fetch_top_of_book',
    'compute_sweep_limit_price',
    'compute_limit_buy_price_from_book',
    # Settlement
    'refresh_budget_from_exchange',
    'refresh_budget_from_exchange_safe',
    'wait_for_balance_settlement',
    'sync_active_order_and_state',
    # Helpers
    'compute_min_cost',
    'size_limit_sell',
    'size_limit_buy',
    'compute_safe_sell_amount',
]
