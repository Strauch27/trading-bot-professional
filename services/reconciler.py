#!/usr/bin/env python3
"""
Reconciler - Exchange Truth to Position Conversion

Separates IO (fetching trades from exchange) from logic (position updates).
Ensures positions always reflect exchange reality.

Responsibilities:
- Fetch trades for completed orders from exchange
- Pass trades to Portfolio for position reconciliation
- Log all reconciliation events for audit trail

NOT Responsible For:
- Trading decisions (handled by Decision layer)
- Order placement (handled by OrderRouter)
- Position lifecycle management (handled by Portfolio)
"""

import time
import logging
from typing import Dict, List, Optional, Any

logger = logging.getLogger(__name__)


class Reconciler:
    """
    Reconciles exchange fills into portfolio positions.

    Fetches trades for orders and applies them to portfolio,
    converting reservations into actual positions with accurate
    prices, quantities, and fees from exchange.
    """

    def __init__(self, exchange, portfolio, telemetry):
        """
        Initialize reconciler.

        Args:
            exchange: Exchange adapter with fetch_order_trades method
            portfolio: PortfolioManager with apply_fills method
            telemetry: JsonlWriter for audit logging
        """
        self.ex = exchange
        self.pf = portfolio
        self.tl = telemetry

        logger.info("Reconciler initialized")

    def reconcile_order(self, symbol: str, order_id: str) -> Optional[Dict[str, Any]]:
        """
        Reconcile a specific order by fetching its trades and applying fills.

        Process:
        1. Fetch all trades for the order from exchange
        2. Apply trades to portfolio (converts reservations → positions)
        3. Log reconciliation event

        Args:
            symbol: Trading pair
            order_id: Exchange order ID

        Returns:
            Summary dict with reconciliation stats or None if no trades
        """
        try:
            # Fetch trades from exchange
            trades = self.ex.fetch_order_trades(symbol, order_id)

            if not trades:
                # No trades found - log and return
                self.tl.write("reconcile", {
                    "symbol": symbol,
                    "order_id": order_id,
                    "event": "no_trades",
                    "timestamp": time.time()
                })
                logger.warning(f"No trades found for order {order_id} ({symbol})")
                return None

            # Apply fills to portfolio (this updates positions, fees, PnL)
            summary = self.pf.apply_fills(symbol, trades)

            # Log successful reconciliation
            self.tl.write("reconcile", {
                "symbol": symbol,
                "order_id": order_id,
                "event": "applied",
                "fills_count": len(trades),
                "summary": summary,
                "timestamp": time.time()
            })

            logger.info(
                f"Reconciled order {order_id}: {symbol} - {len(trades)} fills applied"
            )

            return summary

        except Exception as e:
            # Log reconciliation failure
            self.tl.write("reconcile", {
                "symbol": symbol,
                "order_id": order_id,
                "event": "error",
                "error": str(e),
                "error_type": type(e).__name__,
                "timestamp": time.time()
            })

            logger.error(
                f"Reconciliation failed for order {order_id} ({symbol}): {e}",
                exc_info=True
            )

            return None
