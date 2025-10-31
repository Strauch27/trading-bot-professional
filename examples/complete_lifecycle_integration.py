#!/usr/bin/env python3
"""
Complete Trading Lifecycle Integration Example

Demonstrates the complete flow of the new trading architecture:
1. Decision → Intent (Entry signals)
2. OrderRouter → FSM Execution
3. Reconciler → Exchange Truth
4. Position Lifecycle → PnL Tracking
5. ExitEngine → Exit Signals
6. Exit Intent → OrderRouter (Close positions)

This serves as both documentation and a working integration example
for the complete trading lifecycle with FSM-based execution.
"""

import logging
import time
from typing import Dict

import config
from core.portfolio.portfolio import PortfolioManager
from decision.exit_assembler import assemble as assemble_exit_intent
from engine.exit_engine import ExitEngine
from interfaces.exchange_wrapper import ExchangeWrapper
from services.order_router import OrderRouter, RouterConfig
from services.reconciler import Reconciler
from telemetry.jsonl_writer import JsonlWriter

logger = logging.getLogger(__name__)


class MinimalTradingEngine:
    """
    Minimal trading engine demonstrating complete lifecycle integration.

    This is a simplified reference implementation showing how all
    components work together. NOT intended for production use -
    serves as integration documentation and test harness.
    """

    def __init__(self, exchange, portfolio: PortfolioManager):
        """
        Initialize minimal engine with all lifecycle components.

        Args:
            exchange: CCXT exchange instance
            portfolio: Portfolio manager with Position lifecycle support
        """
        self.portfolio = portfolio
        self.running = False

        # Market snapshots (simplified - production would use MarketDataProvider)
        self.snapshots: Dict[str, Dict] = {}

        # Telemetry/Audit
        self.telemetry = JsonlWriter()

        # Exchange wrapper with idempotency
        self.exchange_wrapper = ExchangeWrapper(exchange)

        # Reconciler for exchange truth
        self.reconciler = Reconciler(
            exchange=self.exchange_wrapper,
            portfolio=portfolio,
            telemetry=self.telemetry
        )

        # OrderRouter configuration
        router_config = RouterConfig(
            max_retries=getattr(config, 'ROUTER_MAX_RETRIES', 3),
            retry_backoff_ms=getattr(config, 'ROUTER_BACKOFF_MS', 400),
            tif=getattr(config, 'ROUTER_TIF', 'IOC'),
            slippage_bps=getattr(config, 'ROUTER_SLIPPAGE_BPS', 20),
            min_notional=getattr(config, 'ROUTER_MIN_NOTIONAL', 5.0)
        )

        # Event bus for order.filled events
        from core.events import get_event_bus
        self.event_bus = get_event_bus()

        # Subscribe reconciler to order.filled events
        self.event_bus.subscribe("order.filled", self._on_order_filled)

        # OrderRouter with FSM execution
        self.order_router = OrderRouter(
            exchange_wrapper=self.exchange_wrapper,
            portfolio=portfolio,
            telemetry=self.telemetry,
            config=router_config,
            event_bus=self.event_bus
        )

        # Exit Engine with prioritized rules
        self.exit_engine = ExitEngine(self, config)

        logger.info("Minimal Trading Engine initialized with complete lifecycle")

    def _on_order_filled(self, event_data: Dict):
        """
        Handle order.filled event from OrderRouter.

        Triggers reconciliation to convert reservations into positions
        using actual exchange fills.

        Args:
            event_data: Event payload with symbol and order_id
        """
        try:
            symbol = event_data.get("symbol")
            order_id = event_data.get("order_id")

            if not symbol or not order_id:
                logger.warning(f"Invalid order.filled event: {event_data}")
                return

            # Reconcile order to update position with exchange truth
            summary = self.reconciler.reconcile_order(symbol, order_id)

            if summary:
                logger.info(
                    f"Position reconciled: {symbol} - "
                    f"state={summary.get('state')}, "
                    f"qty_delta={summary.get('qty_delta')}"
                )

        except Exception as e:
            logger.error(f"Order filled event handler error: {e}")

    def _maybe_scan_exits(self):
        """
        Scan all open positions for exit signals.

        For each position:
        1. Check exit rules via ExitEngine (HARD_SL > HARD_TP > TRAIL_SL > TIME_EXIT)
        2. Generate exit signal if rule triggered
        3. Build ExitIntent with deterministic intent_id
        4. Route to OrderRouter for execution

        This is the CORE of the exit flow - runs every main loop iteration.
        """
        try:
            # Iterate over all open positions
            # FIX HIGH-3: Use thread-safe iteration
            positions = self.portfolio.get_all_positions()
            for symbol, pos in positions.items():

                # Skip positions with no qty (closed)
                if not pos or pos.qty == 0:
                    continue

                # Evaluate exit rules (prioritized)
                exit_signal = self.exit_engine.choose(symbol)

                if exit_signal:
                    # Log exit signal
                    logger.info(
                        f"Exit signal: {symbol} - "
                        f"rule={exit_signal.get('rule_code')}, "
                        f"reason={exit_signal.get('reason')}"
                    )

                    # Assemble ExitIntent with deterministic intent_id
                    exit_intent = assemble_exit_intent(exit_signal)

                    if exit_intent:
                        # Route to OrderRouter for FSM execution
                        # Convert frozen dataclass to dict for router
                        self.order_router.handle_intent(exit_intent.__dict__)

                        logger.info(
                            f"Exit intent routed: {exit_intent.intent_id} - "
                            f"{symbol} {exit_intent.side} {exit_intent.qty}"
                        )

        except Exception as e:
            logger.error(f"Exit scan error: {e}")

    def _update_snapshots(self):
        """
        Update market snapshots for all positions.

        In production, this would use MarketDataProvider.
        For this example, we create simplified snapshots.
        """
        try:
            # FIX HIGH-3: Use thread-safe iteration
            for symbol in list(self.portfolio.get_all_positions().keys()):
                # Fetch current ticker
                ticker = self.exchange_wrapper.fetch_ticker(symbol)

                if not ticker:
                    continue

                last_price = ticker.get("last")

                if not last_price:
                    continue

                # Build simplified snapshot
                # Production would include rolling windows, ATR, etc.
                snapshot = {
                    "symbol": symbol,
                    "price": {
                        "last": last_price,
                        "bid": ticker.get("bid"),
                        "ask": ticker.get("ask")
                    },
                    "windows": {
                        # For trailing stops - would come from RollingWindowManager
                        "peak": None,  # TODO: Implement rolling window tracking
                        "trough": None
                    },
                    "timestamp": time.time()
                }

                self.snapshots[symbol] = snapshot

                # Update portfolio's last_price for PnL calculation
                self.portfolio.mark_price(symbol, last_price)

        except Exception as e:
            logger.error(f"Snapshot update error: {e}")

    def _main_loop(self):
        """
        Main engine loop - orchestrates complete lifecycle.

        Flow:
        1. Update market snapshots (prices, rolling windows)
        2. Scan for exit signals (every iteration)
        3. [Entry signals would go here in full implementation]
        4. Sleep to prevent tight loop
        """
        logger.info("Main loop started")

        while self.running:
            try:
                cycle_start = time.time()

                # 1. Update market snapshots
                self._update_snapshots()

                # 2. Scan for exits (prioritized rules)
                self._maybe_scan_exits()

                # 3. Entry signals would be evaluated here
                # self._maybe_scan_entries()  # Not implemented in minimal example

                # Rate limiting
                cycle_time = time.time() - cycle_start
                sleep_time = max(0.1, 1.0 - cycle_time)
                time.sleep(sleep_time)

            except Exception as e:
                logger.error(f"Main loop error: {e}")
                time.sleep(1.0)

        logger.info("Main loop ended")

    def start(self):
        """Start the trading engine"""
        if self.running:
            logger.warning("Engine already running")
            return

        self.running = True

        logger.info("Starting minimal trading engine with complete lifecycle")

        # Run main loop in current thread (simplified - production uses threading)
        self._main_loop()

    def stop(self):
        """Stop the trading engine"""
        self.running = False
        logger.info("Engine stop requested")


def demo_complete_lifecycle():
    """
    Demonstration of complete trading lifecycle.

    Shows how all components work together from entry to exit:
    - Entry: Signal → Intent → OrderRouter → Reconcile → Position
    - Exit: Position → ExitEngine → ExitIntent → OrderRouter → Position Close
    """
    import ccxt

    from core.utils import SettlementManager

    logger.info("=" * 80)
    logger.info("COMPLETE LIFECYCLE INTEGRATION DEMO")
    logger.info("=" * 80)

    # 1. Setup exchange (mock mode for demo)
    exchange = ccxt.mexc({
        'apiKey': 'demo',
        'secret': 'demo',
        'enableRateLimit': True
    })

    # 2. Setup portfolio with Position lifecycle
    settlement_manager = SettlementManager(None)
    portfolio = PortfolioManager(exchange, settlement_manager, None)

    # 3. Create minimal engine with all components
    engine = MinimalTradingEngine(exchange, portfolio)

    logger.info("Lifecycle components initialized:")
    logger.info(f"  - OrderRouter: {engine.order_router}")
    logger.info(f"  - Reconciler: {engine.reconciler}")
    logger.info(f"  - ExitEngine: {engine.exit_engine}")
    logger.info(f"  - Portfolio: {engine.portfolio}")

    logger.info("\nComponent Flow:")
    logger.info("  1. Entry: Signal → Intent → OrderRouter(FSM) → Reconcile → Position")
    logger.info("  2. Exit:  Position → ExitEngine → ExitIntent → OrderRouter(FSM) → Close")

    logger.info("\nExit Rule Priority:")
    logger.info("  1. HARD_SL   - Maximum loss protection (strength: 1.0)")
    logger.info("  2. HARD_TP   - Target profit reached (strength: 0.9)")
    logger.info("  3. TRAIL_SL  - Trailing stop loss (strength: 0.8)")
    logger.info("  4. TIME_EXIT - Maximum hold time (strength: 0.5)")

    logger.info("\n" + "=" * 80)

    # Engine is ready to start
    # engine.start()  # Uncomment to run in live mode


if __name__ == "__main__":
    # Configure logging
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
    )

    # Run demonstration
    demo_complete_lifecycle()
