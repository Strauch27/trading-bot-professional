#!/usr/bin/env python3
"""
Trading Engine - Pure Orchestration Layer

All implementation details extracted to specialized modules:
- buy_decision: Buy signal evaluation and execution
- position_manager: Position management and trailing stops
- exit_handler: Exit signal processing and fills
- monitoring: Performance metrics and statistics
- engine_config: Configuration and factory functions

This file contains ONLY orchestration logic (~700 lines vs original 2011)
"""

import time
import threading
import logging
from typing import Dict, List, Optional, Any

# Core Dependencies
import config

# Service Imports (All Drops)
from services import (
    # Drop 1: PnL Service
    PnLService, PnLSummary,

    # Drop 2: Trailing & Signals
    TrailingStopManager, SignalManager,

    # Drop 3: Exchange Adapter & Orders
    OrderService, OrderCache,

    # Drop 4: Exit Management & Market Data
    ExitManager, MarketDataProvider,

    # Drop 5: Buy Signals & Market Guards
    BuySignalService, MarketGuards,
)

# Adapter Imports
from adapters.exchange import ExchangeAdapter

# Logging Imports
from core.logging.logger import JsonlLogger
from core.logging.adaptive_logger import get_adaptive_logger, guard_stats_maybe_summarize

# Buy Flow Logger
from services.buy_flow_logger import get_buy_flow_logger, shutdown_buy_flow_logger

# Debug Tracing System
from core.logging.debug_tracer import trace_function, trace_step

# Shutdown Coordinator
from services.shutdown_coordinator import get_shutdown_coordinator

# Drop-Trigger System
from signals.drop_trigger import DropTrigger
from signals.confirm import Stabilizer

# PnL and Telemetry System
from core.utils.pnl import PnLTracker
from core.utils.telemetry import RollingStats, heartbeat_emit

# Refactored Modules
from .monitoring import EngineMonitoring
from .buy_decision import BuyDecisionHandler
from .position_manager import PositionManager
from .exit_handler import ExitHandler
from .engine_config import EngineConfig

# NEW: Order Router & Reconciliation System
from services.order_router import OrderRouter, RouterConfig
from services.reconciler import Reconciler
from interfaces.exchange_wrapper import ExchangeWrapper
from telemetry.jsonl_writer import JsonlWriter

# NEW: Exit Engine for Prioritized Exit Rules
from .exit_engine import ExitEngine

# P1: Debounced State Writer for Intent Persistence
from core.state_writer import DebouncedStateWriter

logger = logging.getLogger(__name__)


class TradingEngine:
    """
    Main Trading Engine - Pure Orchestration

    This engine coordinates all trading operations through specialized handlers:
    - BuyDecisionHandler: Buy signal evaluation and execution
    - PositionManager: Position management and trailing stops
    - ExitHandler: Exit signal processing and fills
    - EngineMonitoring: Performance metrics and statistics

    All business logic has been extracted to these handlers.
    """

    def __init__(self, exchange, portfolio, orderbookprovider, telegram=None,
                 mock_mode: bool = False, engine_config: EngineConfig = None,
                 watchlist: Optional[Dict[str, Any]] = None):
        """Initialize Trading Engine with service composition"""
        logger.info("DEBUG: TradingEngine.__init__ started")

        # Configuration
        logger.info("DEBUG: Initializing config")
        self.config = engine_config or EngineConfig()
        self.mock_mode = mock_mode

        # Core Dependencies
        self.portfolio = portfolio
        self.orderbookprovider = orderbookprovider
        self.telegram = telegram

        # Shutdown coordinator for heartbeat tracking
        self.shutdown_coordinator = get_shutdown_coordinator()

        # State Management
        self.positions: Dict[str, Dict] = {}
        self.pending_buy_intents: Dict[str, Dict[str, Any]] = {}
        self.topcoins: Dict[str, Any] = watchlist or {}
        self.running = False
        self.main_thread = None
        self._lock = threading.RLock()
        self._snap_recv = 0  # DEBUG_DROPS: Count received snapshot batches
        self._last_snap_recv_check = 0  # Health Monitoring: Last snapshot count for watchdog

        # Drop Snapshot Store (Long-term solution)
        self.drop_snapshot_store: Dict[str, Dict[str, Any]] = {}  # symbol -> {'snapshot': ..., 'ts': float}
        self._last_snapshot_ts: float = 0.0
        self._last_snapshot_cleanup_ts: float = 0.0  # Track last cleanup time for H-ENG-02 fix

        # Ensure BTC/USDT is in watchlist for market conditions
        if self.topcoins and "BTC/USDT" not in self.topcoins:
            self.topcoins["BTC/USDT"] = {}

        # Statistics
        self.session_digest = {
            "start_time": time.time(),
            "buys": [], "sells": [],
            "errors": 0, "total_trades": 0
        }
        self._last_market_data_stats: Dict[str, Any] = {}

        # Decision Trail Logging
        self.jsonl_logger = JsonlLogger()
        self.current_decision_id = None
        self.adaptive_logger = get_adaptive_logger()

        # Buy Flow Logger
        self.buy_flow_logger = get_buy_flow_logger()

        # Initialize Service Stack
        logger.info("DEBUG: About to initialize services")
        self._initialize_services(exchange)
        logger.info("DEBUG: Services initialized successfully")

        # Initialize Handlers (NEW - Refactored)
        self.monitoring = EngineMonitoring(self.jsonl_logger, self.adaptive_logger)
        self.buy_decision_handler = BuyDecisionHandler(self)
        self.position_manager = PositionManager(self)
        self.exit_handler = ExitHandler(self)

        # Subscribe to EXIT_FILLED events for unified PnL handling (use correct EventBus instance)
        self.event_bus.subscribe("EXIT_FILLED", self.exit_handler.on_exit_filled_event)
        logger.info("Subscribed to 'EXIT_FILLED' on unified EventBus")

        # Log configuration snapshot for audit trail
        self.monitoring.log_configuration_snapshot(self.config)

        # Start log management
        from core.logging.log_manager import start_log_management
        start_log_management()

        # Engine State
        self.last_market_update = 0
        self.last_position_check = 0
        self.last_exit_processing = 0
        self._md_started = False  # Track market data loop state

        # P1: Debounced State Persistence for Intents
        self._init_state_persistence()

        logger.info("Trading Engine initialized with service composition")

    def _initialize_services(self, exchange):
        """Initialize all trading services (Drops 1-5)"""
        logger.info("DEBUG: _initialize_services started")

        # Drop 3: Exchange Adapter & Order Services
        logger.info("DEBUG: Creating ExchangeAdapter")
        self.exchange_adapter = ExchangeAdapter(exchange, max_retries=3)
        logger.info("DEBUG: ExchangeAdapter created")
        self.order_cache = OrderCache(default_ttl=30.0, max_size=500)
        self.order_service = OrderService(self.exchange_adapter, self.order_cache)

        # EventBus for drop snapshots
        from core.events import get_event_bus
        self.event_bus = get_event_bus()

        # Drop 4: Market Data Service (with drop tracking)
        self.market_data = MarketDataProvider(
            self.exchange_adapter,
            ticker_cache_ttl=self.config.ticker_cache_ttl,
            max_cache_size=1000,
            enable_drop_tracking=True,
            event_bus=self.event_bus
        )

        # Subscribe to market snapshots (new pipeline)
        self.event_bus.subscribe("market.snapshots", self._on_drop_snapshots)

        # Drop 2: Signal Management
        self.signal_manager = SignalManager()

        # Drop 2: Trailing Stops
        self.trailing_manager = TrailingStopManager()

        # Drop 4: Exit Management
        self.exit_manager = ExitManager(
            exchange_adapter=self.exchange_adapter,
            order_service=self.order_service,
            signal_manager=self.signal_manager,
            trade_ttl_min=self.config.trade_ttl_min,
            never_market_sells=self.config.never_market_sells,
            max_slippage_bps=config.MAX_SLIPPAGE_BPS_EXIT
        )

        # Drop 1: PnL Service (Single Source of Truth)
        self.pnl_service = PnLService()

        # Enhanced PnL Tracker with Fill Processing
        self.pnl_tracker = PnLTracker(fee_rate=getattr(config, 'TRADING_FEE_RATE', 0.001))

        # Rolling Statistics for Telemetry
        self.rolling_stats = RollingStats()

        # Drop-Trigger System
        drop_threshold_bp = int((1.0 - config.DROP_TRIGGER_VALUE) * 10000)
        self.drop_trigger = DropTrigger(
            threshold_bp=drop_threshold_bp,
            hysteresis_bps=getattr(config, 'HYSTERESIS_BPS', 20),
            debounce_s=getattr(config, 'DEBOUNCE_S', 15)
        )

        # Rolling Windows per Symbol
        self.rolling_windows = {}  # symbol -> RollingWindow

        # Stabilizer for Signal Confirmation
        self.stabilizer = Stabilizer(confirm_ticks=getattr(config, 'CONFIRM_TICKS', 1))

        # Drop 5: Buy Signals & Market Guards
        self.buy_signal_service = BuySignalService(
            drop_trigger_value=config.DROP_TRIGGER_VALUE,
            drop_trigger_mode=config.DROP_TRIGGER_MODE,
            drop_trigger_lookback_min=config.DROP_TRIGGER_LOOKBACK_MIN,
            enable_minutely_audit=config.ENABLE_DROP_TRIGGER_MINUTELY
        )

        self.market_guards = MarketGuards(
            use_btc_filter=config.USE_BTC_FILTER,
            btc_change_threshold=config.BTC_CHANGE_THRESHOLD,
            use_falling_coins_filter=config.USE_FALLING_COINS_FILTER,
            falling_coins_threshold=config.FALLING_COINS_THRESHOLD,
            use_sma_guard=config.USE_SMA_GUARD,
            sma_guard_window=config.SMA_GUARD_WINDOW,
            sma_guard_min_ratio=config.SMA_GUARD_MIN_RATIO,
            use_volume_guard=config.USE_VOLUME_GUARD,
            volume_guard_window=config.VOLUME_GUARD_WINDOW,
            volume_guard_factor=config.VOLUME_GUARD_FACTOR,
            use_spread_guard=config.USE_SPREAD_GUARD,
            guard_max_spread_bps=config.GUARD_MAX_SPREAD_BPS,
            use_vol_sigma_guard=config.USE_VOL_SIGMA_GUARD,
            vol_sigma_window=config.VOL_SIGMA_WINDOW,
            require_vol_sigma_bps_min=config.REQUIRE_VOL_SIGMA_BPS_MIN,
            verbose=getattr(config, "VERBOSE_GUARD_LOGS", False)
        )

        # Sizing Service with Portfolio Management Integration
        from services.sizing_service import SizingService
        self.sizing_service = SizingService(
            exchange=exchange,
            portfolio_manager=self.portfolio
        )

        # NEW: Order Router & Reconciliation System
        logger.info("Initializing Order Router and Reconciliation System...")

        # Exchange Wrapper for Idempotency
        self.exchange_wrapper = ExchangeWrapper(exchange)

        # Telemetry for Order/Reconciliation Events
        self.order_telemetry = JsonlWriter()

        # Router Configuration
        router_config = RouterConfig(
            max_retries=getattr(config, 'ROUTER_MAX_RETRIES', 3),
            retry_backoff_ms=getattr(config, 'ROUTER_BACKOFF_MS', 400),
            tif=getattr(config, 'ROUTER_TIF', 'IOC'),
            slippage_bps=getattr(config, 'ROUTER_SLIPPAGE_BPS', 20),
            min_notional=getattr(config, 'ROUTER_MIN_NOTIONAL', 5.0),
            fetch_order_on_fill=getattr(config, 'ROUTER_FETCH_ORDER_ON_FILL', False)  # P2: Performance
        )

        # Order Router with FSM Execution
        self.order_router = OrderRouter(
            exchange_wrapper=self.exchange_wrapper,
            portfolio=self.portfolio,
            telemetry=self.order_telemetry,
            config=router_config,
            event_bus=self.event_bus
        )

        # Reconciler for Exchange-Truth Position Management
        if getattr(config, 'USE_RECONCILER', True):
            self.reconciler = Reconciler(
                exchange=self.exchange_wrapper,
                portfolio=self.portfolio,
                telemetry=self.order_telemetry
            )
        else:
            self.reconciler = None

        # Subscribe to order events
        self.event_bus.subscribe("order.intent", self._on_order_intent)
        if self.reconciler:
            self.event_bus.subscribe("order.filled", self._on_order_filled)
        self.event_bus.subscribe("order.failed", self._on_order_failed)

        # Exit Engine with Prioritized Rules
        self.exit_engine = ExitEngine(self, config)

        # Snapshots storage for exit engine
        self.snapshots: Dict[str, Dict] = {}  # symbol -> snapshot

        logger.info("Order Router and Reconciliation System initialized")

        # Legacy Services - functionality moved to other services
        self.buy_service = None
        self.sell_service = None

    def _initialize_startup_services(self):
        """Initialize services that need startup time (like market data connections)"""
        try:
            logger.info("Initializing startup services...")

            # Start market data service if it has async initialization
            if hasattr(self.market_data, 'start') and callable(getattr(self.market_data, 'start')):
                logger.info("Starting market data service...")
                start_time = time.time()
                try:
                    self.market_data.start()
                    duration = time.time() - start_time
                    logger.info(f"Market data service started in {duration:.2f}s")
                except Exception as md_error:
                    logger.warning(f"Market data service start failed: {md_error}")

            # Start any other services that need initialization
            if hasattr(self.buy_signal_service, 'start') and callable(getattr(self.buy_signal_service, 'start')):
                logger.info("Starting buy signal service...")
                try:
                    self.buy_signal_service.start()
                    logger.info("Buy signal service started")
                except Exception as bs_error:
                    logger.warning(f"Buy signal service start failed: {bs_error}")

        except Exception as e:
            logger.error(f"Startup services initialization failed: {e}")

    def _backfill_market_history(self):
        """Backfill market history for analysis"""
        try:
            symbols = list(self.positions.keys()) or ["BTC/USDT", "ETH/USDT"]
            results = self.market_data.backfill_history(
                symbols,
                timeframe='1m',
                minutes=config.BACKFILL_MINUTES
            )
            logger.info(f"Market data backfill completed: {results}")
        except Exception as e:
            logger.error(f"Market data backfill failed: {e}")

    # =================================================================
    # STATE PERSISTENCE (P1)
    # =================================================================

    def _init_state_persistence(self):
        """Initialize debounced state writers for transient engine state"""
        try:
            # Engine transient state (pending_buy_intents)
            self._engine_state_writer = DebouncedStateWriter(
                file_path=config.ENGINE_TRANSIENT_STATE_FILE,
                interval_s=config.STATE_PERSIST_INTERVAL_S,
                auto_start=True
            )

            # Recover existing state
            self._recover_transient_state()

            logger.info(
                f"State persistence initialized: "
                f"file={config.ENGINE_TRANSIENT_STATE_FILE}, "
                f"interval={config.STATE_PERSIST_INTERVAL_S}s"
            )

        except Exception as e:
            logger.error(f"Failed to initialize state persistence: {e}")
            # Create dummy writer that doesn't fail
            self._engine_state_writer = None

    def _recover_transient_state(self):
        """Recover transient state from previous session"""
        try:
            if not self._engine_state_writer:
                return

            state = self._engine_state_writer.get()
            if not state:
                logger.info("No previous transient state to recover")
                return

            # Filter stale intents (>1h old)
            cutoff = time.time() - config.INTENT_STALE_THRESHOLD_S
            recovered = 0
            filtered = 0

            for intent_id, metadata in state.get("pending_buy_intents", {}).items():
                start_ts = metadata.get("start_ts", 0)
                if start_ts > cutoff:
                    self.pending_buy_intents[intent_id] = metadata
                    recovered += 1
                else:
                    filtered += 1
                    logger.debug(
                        f"Filtered stale intent: {intent_id} "
                        f"(age: {time.time() - start_ts:.0f}s)"
                    )

            if recovered > 0:
                logger.info(
                    f"Recovered {recovered} pending buy intents from previous session "
                    f"(filtered {filtered} stale intents)"
                )
            elif filtered > 0:
                logger.info(f"All {filtered} previous intents were stale - none recovered")

        except Exception as e:
            logger.warning(f"Transient state recovery failed: {e}")

    def _persist_intent_state(self):
        """Update persisted intent state (debounced)"""
        try:
            if not self._engine_state_writer:
                return

            state = {
                "pending_buy_intents": self.pending_buy_intents,
                "last_update": time.time()
            }

            self._engine_state_writer.update(state)

        except Exception as e:
            logger.debug(f"Intent state persist failed: {e}")

    def clear_intent(self, intent_id: str, reason: str) -> bool:
        """
        Clear a pending buy intent and release its reserved budget.

        This is the canonical method for removing intents from pending_buy_intents.
        Called by:
        - Stale intent cleanup cron
        - Order router on failure (via order.filled event handler)
        - Manual cleanup operations

        Args:
            intent_id: Intent ID to remove
            reason: Reason for clearing (e.g., "stale_intent_cleanup", "order_router_release")

        Returns:
            True if intent was found and cleared, False if not found
        """
        metadata = self.pending_buy_intents.pop(intent_id, None)

        if not metadata:
            logger.debug(f"Intent {intent_id} not found in pending_buy_intents (already cleared or never existed)")
            return False

        # Extract metadata
        symbol = metadata.get("symbol")
        quote_budget = metadata.get("quote_budget", 0.0)
        age_s = time.time() - metadata.get("start_ts", time.time())

        # Release budget if reserved
        if symbol and quote_budget > 0:
            try:
                self.portfolio.release_budget(
                    quote_budget,
                    symbol,
                    reason=reason,
                    intent_id=intent_id
                )
                logger.info(
                    f"Cleared intent {intent_id}: symbol={symbol} budget=${quote_budget:.2f} "
                    f"age={age_s:.1f}s reason={reason}"
                )
            except Exception as e:
                logger.warning(f"Failed to release budget for intent {intent_id}: {e}")

        # Persist state
        self._persist_intent_state()

        return True

    def _check_stale_intents(self):
        """
        P4: Check for and cleanup stale intents (intents stuck without fill).

        Detects intents older than INTENT_STALE_THRESHOLD_S and:
        - Releases reserved budget
        - Removes from pending_buy_intents
        - Logs warning with details
        - Optionally sends Telegram alert
        """
        if not getattr(config, 'STALE_INTENT_CHECK_ENABLED', True):
            return

        if not self.pending_buy_intents:
            return

        stale_threshold_s = getattr(config, 'INTENT_STALE_THRESHOLD_S', 60)
        now = time.time()
        stale_intents = []

        for intent_id, metadata in list(self.pending_buy_intents.items()):
            age_s = now - metadata.get("start_ts", now)

            if age_s > stale_threshold_s:
                stale_intents.append({
                    "intent_id": intent_id,
                    "symbol": metadata.get("symbol"),
                    "age_s": age_s,
                    "quote_budget": metadata.get("quote_budget", 0.0),
                    "signal": metadata.get("signal"),
                    "decision_id": metadata.get("decision_id")
                })

                # Use canonical clear_intent helper
                self.clear_intent(intent_id, reason="stale_intent_cleanup")

        if stale_intents:
            # Log warning
            logger.warning(
                f"Cleaned up {len(stale_intents)} stale intents (age > {stale_threshold_s}s)",
                extra={
                    "event_type": "STALE_INTENT_CLEANUP",
                    "count": len(stale_intents),
                    "intents": stale_intents
                }
            )

            # Optional: Send Telegram alert
            if getattr(config, 'STALE_INTENT_TELEGRAM_ALERTS', False):
                if hasattr(self, 'telegram') and self.telegram:
                    try:
                        # Build alert message
                        msg_lines = [f"⚠️ Stale Intent Cleanup: {len(stale_intents)} intents"]
                        for intent in stale_intents[:5]:  # Show max 5
                            msg_lines.append(
                                f"• {intent['symbol']}: ${intent['quote_budget']:.2f} "
                                f"(age: {intent['age_s']:.0f}s)"
                            )

                        if len(stale_intents) > 5:
                            msg_lines.append(f"... and {len(stale_intents) - 5} more")

                        msg = "\n".join(msg_lines)
                        self.telegram.send_message(msg)

                    except Exception as telegram_error:
                        logger.debug(f"Telegram alert failed: {telegram_error}")

    def _cleanup_inactive_snapshots(self):
        """
        CRITICAL FIX (H-ENG-02): Periodic cleanup of drop_snapshot_store.

        Removes snapshots for symbols no longer in the watchlist to prevent
        unbounded memory growth when watchlist changes.
        """
        # Run cleanup every 5 minutes
        CLEANUP_INTERVAL_S = 300
        now = time.time()

        if now - self._last_snapshot_cleanup_ts < CLEANUP_INTERVAL_S:
            return

        self._last_snapshot_cleanup_ts = now

        with self._lock:
            # Get current watchlist symbols
            active_symbols = set(self.topcoins.keys()) if self.topcoins else set()

            # Find inactive symbols in snapshot store
            stored_symbols = set(self.drop_snapshot_store.keys())
            inactive_symbols = stored_symbols - active_symbols

            if inactive_symbols:
                for symbol in inactive_symbols:
                    del self.drop_snapshot_store[symbol]

                logger.info(
                    f"Cleaned up {len(inactive_symbols)} inactive symbols from drop_snapshot_store",
                    extra={
                        'event_type': 'SNAPSHOT_STORE_CLEANUP',
                        'removed_count': len(inactive_symbols),
                        'removed_symbols': list(inactive_symbols)[:10],  # Log max 10
                        'active_symbols_count': len(active_symbols),
                        'remaining_stored': len(self.drop_snapshot_store)
                    }
                )

    # =================================================================
    # MAIN ENGINE LOOP - PURE ORCHESTRATION
    # =================================================================

    def start(self):
        """Start the trading engine"""
        logger.info("=== ENGINE.START() CALLED ===")

        # Check for double-start and raise error to prevent race conditions
        if self.running:
            # Check if main thread is still alive
            thread_alive = self.main_thread and self.main_thread.is_alive()
            if thread_alive:
                error_msg = "Engine is already running. Use stop() before restarting."
                logger.error(error_msg, extra={'event_type': 'ENGINE_DOUBLE_START_ATTEMPT'})
                raise RuntimeError(error_msg)
            else:
                # Thread died but running flag still set - allow restart after cleanup
                logger.warning(
                    "Engine marked as running but main thread is dead. Allowing restart after cleanup.",
                    extra={'event_type': 'ENGINE_DEAD_THREAD_RESTART'}
                )
                self.running = False
                # Continue with normal startup

        logger.info("=== ENGINE.START() - Setting up market data ===")

        # Start market data loop (only once)
        logger.info(f"=== ENGINE.START() - _md_started={self._md_started}, has market_data={hasattr(self, 'market_data')} ===")
        if not self._md_started:
            if hasattr(self, 'market_data') and hasattr(self.market_data, 'start'):
                try:
                    self.market_data.start()
                    self._md_started = True

                    # Thread-Liveness prüfen
                    t = getattr(self.market_data, "_thread", None)
                    alive = t.is_alive() if t else None
                    logger.info("MD_THREAD_STATUS", extra={"is_alive": alive})
                    if not alive:
                        logger.error("MARKET_DATA_THREAD_NOT_ALIVE")

                    logger.info("Market data loop started successfully")
                except Exception as e:
                    logger.error(f"Failed to start market data loop: {e}")

        self.running = True
        self.main_thread = threading.Thread(target=self._main_loop, daemon=False, name="TradingEngine-Main")
        self.main_thread.start()

        # Optional: Start UI fallback feed
        self._start_ui_fallback_feed()

        logger.info("Trading Engine initialized successfully")

        # DEBUG_DROPS: Watchdog - check if snapshots arrive within 5s
        if getattr(config, "DEBUG_DROPS", False):
            import time
            from core.events import topic_counters

            t0 = time.time()
            while time.time() - t0 < 5.0:
                time.sleep(0.25)
                if self._snap_recv > 0:
                    logger.info("DEBUG_DROPS: Watchdog passed - snapshots received within 5s")
                    break

            if self._snap_recv == 0:
                pub = topic_counters["published"].get("market.snapshots", 0)
                symbols = getattr(config, "TOPCOINS_SYMBOLS", [])
                logger.critical(
                    f"DEBUG_DROPS: no snapshots received in 5s; published={pub} symbols={symbols[:5] if symbols else []}",
                    extra={"event_type": "DEBUG_DROPS_WATCHDOG_FAILED"}
                )

    def stop(self):
        """Stop the trading engine"""
        self.running = False

        # Signal UI fallback thread to stop (if exists)
        if hasattr(self, '_ui_fallback_shutdown_event') and self._ui_fallback_shutdown_event:
            logger.debug("Signaling UI fallback thread shutdown")
            self._ui_fallback_shutdown_event.set()

        # Join main thread
        if self.main_thread and self.main_thread.is_alive():
            self.main_thread.join(timeout=5.0)

        # Join UI fallback thread (non-daemon, needs explicit join)
        if hasattr(self, '_ui_fallback_thread') and self._ui_fallback_thread and self._ui_fallback_thread.is_alive():
            logger.debug("Waiting for UI fallback thread to stop...")
            self._ui_fallback_thread.join(timeout=3.0)
            if self._ui_fallback_thread.is_alive():
                logger.warning("UI fallback thread did not stop within timeout")
            else:
                logger.debug("UI fallback thread stopped cleanly")

        # P1: Shutdown state persistence (final flush)
        if config.STATE_PERSIST_ON_SHUTDOWN:
            try:
                # Engine transient state
                if hasattr(self, '_engine_state_writer') and self._engine_state_writer:
                    logger.info("Persisting final engine state...")
                    self._persist_intent_state()
                    self._engine_state_writer.shutdown()
                    logger.info("Engine state persisted successfully")

                # OrderRouter metadata state
                if hasattr(self, 'order_router') and self.order_router:
                    self.order_router.shutdown()

            except Exception as e:
                logger.error(f"Failed to persist state on shutdown: {e}")

        # Print final statistics
        self.monitoring.log_final_statistics(self.session_digest, self.positions, self.pnl_service)

        # Shutdown buy flow logger
        shutdown_buy_flow_logger()

        # Stop log management
        from core.logging.log_manager import stop_log_management
        stop_log_management()

        logger.info("Trading Engine stopped")

    def _main_loop(self):
        """Main engine loop - orchestrates all services via handlers"""
        try:
            logger.info("🚀 Main trading loop started", extra={'event_type': 'ENGINE_MAIN_LOOP_STARTED'})
            loop_counter = 0
            last_md_health_check = time.time()  # For periodic market data thread health checks
            last_snapshot_check = time.time()  # Track last snapshot delivery check

            while self.running:
                try:
                    cycle_start = time.time()
                    loop_counter += 1

                    # Record heartbeat at start of each cycle
                    co = self.shutdown_coordinator
                    co.beat("engine_cycle_start")

                    # Heartbeat every 10 cycles
                    if loop_counter % 10 == 0:
                        logger.info(f"💓 Engine heartbeat #{loop_counter} - Active: {len(self.positions)} positions, {len(self.topcoins)} symbols",
                                   extra={'event_type': 'ENGINE_HEARTBEAT', 'positions': len(self.positions),
                                          'symbols': len(self.topcoins), 'cycle': loop_counter})
                        # Deactivated - Dashboard shows status
                        # print(f"💓 Engine läuft - Cycle #{loop_counter} - {len(self.positions)} Positionen, {len(self.topcoins)} Symbole")

                    # Health Monitoring: Market Data Thread Liveness Check
                    health_check_interval = getattr(config, 'MD_HEALTH_CHECK_INTERVAL_S', 60)
                    if cycle_start - last_md_health_check > health_check_interval:
                        if hasattr(self, 'market_data') and hasattr(self.market_data, '_thread'):
                            thread = self.market_data._thread
                            if thread and not thread.is_alive():
                                logger.error(
                                    "🚨 CRITICAL: Market Data Thread is DEAD! Thread stopped unexpectedly.",
                                    extra={'event_type': 'MD_THREAD_DEAD'}
                                )
                                # Optional: Auto-restart (if enabled)
                                if getattr(config, 'MD_AUTO_RESTART_ON_CRASH', False):
                                    logger.warning("Attempting to restart Market Data Thread...")
                                    try:
                                        self.market_data.start()
                                        logger.info("Market Data Thread restarted successfully")
                                    except Exception as restart_error:
                                        logger.error(f"Failed to restart Market Data Thread: {restart_error}")
                            elif thread:
                                # Thread is alive, check heartbeat freshness
                                last_heartbeat = getattr(self.market_data, '_last_heartbeat', None)
                                if last_heartbeat:
                                    heartbeat_age = cycle_start - last_heartbeat
                                    if heartbeat_age > 120:  # No heartbeat for 2 minutes
                                        logger.warning(
                                            f"⚠️ Market Data Thread heartbeat is stale ({heartbeat_age:.1f}s old). Thread may be hung.",
                                            extra={'event_type': 'MD_HEARTBEAT_STALE', 'age_s': heartbeat_age}
                                        )
                        last_md_health_check = cycle_start

                    # Health Monitoring: Snapshot Delivery Watchdog
                    snapshot_timeout = getattr(config, 'MD_SNAPSHOT_TIMEOUT_S', 30)
                    if cycle_start - last_snapshot_check > snapshot_timeout:
                        # Check if snapshots are still being received
                        last_snap_count = getattr(self, '_last_snap_recv_check', 0)
                        current_snap_count = self._snap_recv

                        if current_snap_count == last_snap_count:
                            # No new snapshots in the last timeout period
                            logger.warning(
                                f"⚠️ No new market snapshots received for {snapshot_timeout}s. "
                                f"Snapshot count stuck at {current_snap_count}.",
                                extra={'event_type': 'MD_NO_SNAPSHOTS', 'timeout_s': snapshot_timeout}
                            )

                        self._last_snap_recv_check = current_snap_count
                        last_snapshot_check = cycle_start

                    # 1. Market Data Updates (configurable interval)
                    interval = getattr(self.config, 'md_update_interval_s', 5.0) or 5.0
                    if cycle_start - self.last_market_update > float(interval):
                        md_start = time.time()
                        logger.info("📊 Updating market data...", extra={'event_type': 'MARKET_DATA_UPDATE'})
                        try:
                            self._update_market_data()
                            logger.info("📊 Market data updated successfully", extra={'event_type': 'MARKET_DATA_UPDATED'})
                        except Exception as md_error:
                            logger.warning(f"Market data update failed: {md_error}", extra={'event_type': 'MARKET_DATA_UPDATE_ERROR'})
                        finally:
                            co.beat("after_update_market_data")
                            md_latency = time.time() - md_start
                            self.monitoring.performance_metrics['market_data_latencies'].append(md_latency)
                            self.last_market_update = cycle_start
                            logger.info(f"📊 Market data updated in {md_latency:.3f}s", extra={'event_type': 'MARKET_DATA_UPDATED'})

                    # 2. Process Exit Signals (every 1s) - Delegated to ExitHandler
                    if cycle_start - self.last_exit_processing > 1.0:
                        co.beat("before_exit_signals")
                        # Legacy exit handler
                        self.exit_handler.process_exit_signals()
                        # NEW: FSM-based exit scanning via ExitEngine
                        self._maybe_scan_exits()
                        co.beat("after_exit_signals")
                        self.last_exit_processing = cycle_start

                    # 3. Position Management (every 2s) - Delegated to PositionManager
                    if cycle_start - self.last_position_check > 2.0:
                        co.beat("before_position_management")
                        self.position_manager.manage_positions()
                        co.beat("after_position_management")
                        self.last_position_check = cycle_start

                    # 4. Buy Opportunities (every 3s) - Delegated to BuyDecisionHandler
                    trace_step("buy_opportunities_eval_start", cycle=loop_counter, symbols_count=len(self.topcoins))
                    logger.info("🛒 Evaluating buy opportunities...", extra={'event_type': 'BUY_EVAL'})
                    self._evaluate_buy_opportunities()
                    co.beat("after_scan_and_trade")
                    trace_step("buy_opportunities_eval_end", cycle=loop_counter)

                    # 5. Enhanced Heartbeat with PnL/Telemetry (every 30s)
                    if cycle_start % 30 < 1.0:
                        co.beat("before_heartbeat_emit")

                        # P4: Check for stale intents (same interval as heartbeat)
                        try:
                            self._check_stale_intents()
                        except Exception as stale_error:
                            logger.error(f"Stale intent check failed: {stale_error}")

                        # H-ENG-02: Periodic cleanup of inactive snapshots
                        try:
                            self._cleanup_inactive_snapshots()
                        except Exception as cleanup_error:
                            logger.error(f"Snapshot cleanup failed: {cleanup_error}")

                        try:
                            equity = self._calculate_current_equity()
                            drawdown_peak_pct = getattr(self, '_session_drawdown_peak', 0.0)

                            heartbeat_emit(
                                pnl_tracker=self.pnl_tracker,
                                rolling_stats=self.rolling_stats,
                                equity=equity,
                                drawdown_peak_pct=drawdown_peak_pct
                            )
                        except Exception as e:
                            logger.warning(f"Heartbeat emission failed: {e}")
                        co.beat("after_heartbeat_emit")

                    # 5b. Portfolio Display (every 60s if enabled)
                    if cycle_start % 60 < 1.0 and self.config.enable_pnl_monitor:
                        co.beat("before_portfolio_display")
                        try:
                            from services.portfolio_display import display_portfolio
                            display_portfolio(
                                positions=self.positions,
                                portfolio_manager=self.portfolio,
                                market_data_provider=self.market_data,
                                pnl_tracker=self.pnl_tracker,
                                max_positions=self.config.max_positions
                            )
                        except Exception as e:
                            logger.warning(f"Portfolio display failed: {e}")
                        co.beat("after_portfolio_display")

                    # 6. Periodic Maintenance (every 30s)
                    if cycle_start % 30 < 1.0:
                        co.beat("before_maintenance")
                        self._periodic_maintenance()
                        co.beat("after_maintenance")

                    # 7. TopDrops Ticker (DISABLED - replaced by Live Dashboard)
                    # if cycle_start % self.config.top_drops_interval_s < 1.0 and self.config.enable_top_drops_ticker:
                    #     co.beat("before_topdrops_ticker")
                    #     self._emit_topdrops_ticker()
                    #     co.beat("after_topdrops_ticker")

                    # 8. Performance Metrics Logging (every 60s)
                    if cycle_start % 60 < 1.0:
                        co.beat("before_metrics_logging")
                        self.monitoring.log_performance_metrics()
                        self.monitoring.flush_adaptive_logger_metrics()
                        co.beat("after_metrics_logging")

                    # 9. Guard Rolling Stats (every 30s)
                    if cycle_start % 30 < 1.0:
                        co.beat("before_guard_stats")
                        guard_stats_maybe_summarize(force=False)
                        co.beat("after_guard_stats")

                    # Rate limiting
                    time.sleep(0.5)

                    # Track cycle time
                    cycle_time = time.time() - cycle_start
                    self.monitoring.performance_metrics['loop_cycle_times'].append(cycle_time)

                except Exception as e:
                    co.beat("engine_exception")
                    logger.error(f"Main loop error: {e}", exc_info=True)
                    time.sleep(1.0)

                # Beat at end of each iteration
                co.beat("engine_cycle_end")

        except Exception as fatal_e:
            logger.error(f"Fatal error in main loop: {fatal_e}", exc_info=True,
                        extra={'event_type': 'ENGINE_FATAL_ERROR'})
        finally:
            logger.info("Main trading loop ended", extra={'event_type': 'ENGINE_MAIN_LOOP_ENDED'})

    # =================================================================
    # MARKET DATA ORCHESTRATION
    # =================================================================

    def _update_market_data(self) -> bool:
        """Update market data for all tracked symbols"""
        try:
            symbols = list(self.positions.keys()) + list(self.topcoins.keys())
            if not symbols:
                logger.warning("❌ Keine Symbole für Market-Data-Updates konfiguriert.",
                              extra={'event_type': 'NO_SYMBOLS_FOR_MARKET_DATA'})
                return True

            # Ensure BTC/USDT is included
            if "BTC/USDT" not in symbols:
                symbols.append("BTC/USDT")

            # Remove duplicates
            unique_symbols = list(dict.fromkeys(symbols))

            # Batch update via Market Data Service
            try:
                results = self.market_data.update_market_data(unique_symbols)
            except Exception as e:
                logger.warning(f"Market data update failed: {e}", extra={'event_type': 'MARKET_DATA_ERROR'})
                results = {symbol: True for symbol in unique_symbols}
                self._last_market_data_stats = {
                    'timestamp': time.time(),
                    'requested': len(unique_symbols),
                    'fetched': 0,
                    'failed': len(unique_symbols),
                    'degraded': 0,
                    'retry_attempts': 0,
                    'failures': unique_symbols,
                    'degraded_symbols': []
                }
            else:
                try:
                    self._last_market_data_stats = self.market_data.get_last_cycle_stats()
                except Exception as stats_error:
                    logger.debug(f"Could not retrieve market data stats: {stats_error}")

                if getattr(config, 'MD_REFRESH_PORTFOLIO_BUDGET', False) and results:
                    try:
                        self.portfolio.refresh_budget()
                    except Exception as budget_error:
                        logger.debug(f"Budget refresh skipped: {budget_error}")

            # Update market conditions for guards
            self._update_market_conditions(unique_symbols)

            # Handle failed updates
            failed_symbols = [sym for sym, success in results.items() if not success]
            if failed_symbols:
                logger.warning(f"Market data update failed for: {failed_symbols}")

            return len(failed_symbols) == 0

        except Exception as e:
            logger.error(f"Market data update error: {e}")
            return False

    def _update_market_conditions(self, symbols: List[str]):
        """Update market conditions for MarketGuards service"""
        try:
            # Calculate BTC change factor
            btc_price = self.market_data.get_price("BTC/USDT")
            if btc_price:
                # FIX: Use fetch_ohlcv instead of get_ohlcv_history (method doesn't exist)
                btc_history = self.market_data.fetch_ohlcv("BTC/USDT", timeframe='1m', limit=60, store=True)
                if btc_history and len(btc_history) >= 2:
                    recent_bars = btc_history[-60:]
                    if len(recent_bars) >= 2:
                        btc_60m_ago = recent_bars[0].close
                        btc_change_factor = btc_price / btc_60m_ago
                        self.market_guards.update_market_conditions(btc_change_factor=btc_change_factor)

            # Calculate percentage of falling coins
            falling_count = 0
            total_count = 0

            for symbol in symbols:
                if symbol == "BTC/USDT":
                    continue

                current_price = self.market_data.get_price(symbol)
                if not current_price:
                    continue

                # FIX: Use fetch_ohlcv instead of get_ohlcv_history (method doesn't exist)
                history = self.market_data.fetch_ohlcv(symbol, timeframe='1m', limit=60, store=True)
                if history and len(history) >= 2:
                    recent_bars = history[-60:]
                    if len(recent_bars) >= 2:
                        price_60m_ago = recent_bars[0].close
                        if current_price < price_60m_ago:
                            falling_count += 1
                        total_count += 1

            if total_count > 0:
                percentage_falling = falling_count / total_count
                self.market_guards.update_market_conditions(percentage_falling=percentage_falling)

        except Exception as e:
            logger.warning(f"Market conditions update failed: {e}")

    # ------------------------------------------------------------------
    # Snapshot helpers
    # ------------------------------------------------------------------

    def get_snapshot_entry(self, symbol: str) -> tuple[Optional[Dict], Optional[float]]:
        """Return (snapshot, ts) tuple for symbol with backwards compatibility."""
        entry = self.drop_snapshot_store.get(symbol)
        if not entry:
            return None, None
        if isinstance(entry, dict) and 'snapshot' in entry:
            return entry.get('snapshot'), entry.get('ts')
        return entry, None

    def iter_snapshot_entries(self):
        """Iterate symbols with (snapshot, ts)."""
        for symbol, entry in self.drop_snapshot_store.items():
            snapshot, ts = self.get_snapshot_entry(symbol)
            yield symbol, snapshot, ts

    def _on_drop_snapshots(self, snapshots: list[Dict]):
        """
        Callback for market snapshot events from MarketDataService.

        Updates drop_snapshot_store with latest versioned MarketSnapshots (v:1)
        for Dashboard/UI and BuyDecision consumption.

        NEW: Also updates self.snapshots for ExitEngine and marks portfolio prices.

        MarketSnapshot format:
        {
          "v": 1,
          "ts": timestamp,
          "symbol": "BTC/USDT",
          "price": {"last": ..., "bid": ..., "ask": ..., "mid": ...},
          "windows": {"peak": ..., "trough": ..., "drop_pct": ..., "rise_pct": ...},
          "features": {"atr": ...},
          "state": {"trend": ..., "vol_regime": ..., "liq_grade": ...},
          "flags": {"anomaly": ..., "stale": ...}
        }
        """
        try:
            # DEBUG_DROPS: Count and log first reception
            self._snap_recv += 1

            # DEBUG_DROPS: Log first snapshot reception and update dashboard debug
            if self._snap_recv == 1 and snapshots and getattr(config, "DEBUG_DROPS", False):
                s = snapshots[0]
                last_price = s.get("price", {}).get("last", -1)
                logger.info(
                    f"SNAP_RX first symbol={s.get('symbol')} last={last_price:.8f}",
                    extra={"event_type": "SNAP_RX_FIRST"}
                )

            # Update dashboard debug info with latest snapshot
            if snapshots and getattr(config, "DEBUG_DROPS", False):
                from ui.dashboard import update_snapshot_debug
                s = snapshots[0]  # Take first snapshot as sample
                symbol = s.get("symbol")
                last_price = s.get("price", {}).get("last")
                if symbol and last_price:
                    update_snapshot_debug(symbol, last_price)

            with self._lock:
                before = len(self.drop_snapshot_store)

                for snap in snapshots:
                    # Validate snapshot version
                    if snap.get("v") != 1:
                        logger.warning(f"Unknown snapshot version: {snap.get('v')}")
                        continue

                    symbol = snap.get("symbol")
                    if symbol:
                        entry = {
                            'snapshot': snap,
                            'ts': time.time()
                        }
                        # Store for Dashboard/UI
                        self.drop_snapshot_store[symbol] = entry

                        # NEW: Store for ExitEngine
                        self.snapshots[symbol] = snap

                        # FIX: Update self.topcoins with snapshot data (Single Source of Truth)
                        # Convert snapshot to coin_data format expected by buy_decision.py
                        price_data = snap.get("price", {})
                        self.topcoins[symbol] = {
                            'bid': price_data.get('bid'),
                            'ask': price_data.get('ask'),
                            'last': price_data.get('last'),
                            'volume': price_data.get('vol_24h', 0),
                            'timestamp': entry['ts']
                        }

                        # Track last snapshot timestamp
                        self._last_snapshot_ts = entry['ts']

                        # NEW: Mark portfolio price for PnL calculation
                        last_price = snap.get("price", {}).get("last")
                        if last_price:
                            self.portfolio.mark_price(symbol, last_price)

                after = len(self.drop_snapshot_store)
                logger.debug("ON_SNAPSHOTS", extra={"recv": len(snapshots), "store_before": before, "store_after": after})

            # Debug log (sample every 20th emission)
            if len(snapshots) > 0 and len(self.drop_snapshot_store) % 20 == 0:
                logger.debug(
                    f"Market snapshot store updated: {len(snapshots)} snapshots, "
                    f"total tracked: {len(self.drop_snapshot_store)}"
                )

        except Exception as e:
            logger.error(f"Market snapshot callback error: {e}")

    def _on_order_intent(self, intent_data: Dict):
        """
        Handle order.intent events from decision layer.

        Routes intent to OrderRouter for FSM-based execution.
        This is the entry point for all order placements (buy and exit).

        Args:
            intent_data: Intent dict from assembler (entry or exit)
        """
        try:
            logger.debug(f"Order intent received: {intent_data.get('intent_id')}")
            self.order_router.handle_intent(intent_data)
        except Exception as e:
            logger.error(f"Order intent handler error: {e}", exc_info=True)

    def _on_order_filled(self, event_data: Dict):
        """
        Handle order.filled events from OrderRouter.

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
            summary = None

            if self.reconciler:
                summary = self.reconciler.reconcile_order(symbol, order_id)

                if summary:
                    logger.info(
                        f"Position reconciled: {symbol} - "
                        f"state={summary.get('state')}, "
                        f"qty_delta={summary.get('qty_delta')}"
                    )

            if event_data.get("side") == "buy" and event_data.get("intent_id"):
                self.buy_decision_handler.handle_router_fill(
                    event_data["intent_id"],
                    event_data,
                    summary
                )

        except Exception as e:
            logger.error(f"Order filled event handler error: {e}", exc_info=True)

    def _on_order_failed(self, event_data: Dict):
        """
        Handle order.failed events from OrderRouter.

        Clears the pending buy intent when an order completely fails.
        This prevents stale intents from accumulating when orders cannot be filled.

        Args:
            event_data: Event payload with intent_id, symbol, exchange_error, etc.
        """
        try:
            intent_id = event_data.get("intent_id")
            if not intent_id:
                logger.debug(f"order.failed event missing intent_id: {event_data}")
                return

            # Clear the pending intent using canonical helper
            symbol = event_data.get("symbol", "UNKNOWN")
            exchange_error = event_data.get("exchange_error", "")

            cleared = self.clear_intent(intent_id, reason="order_failed")

            if cleared:
                logger.info(
                    f"Cleared failed intent {intent_id}: symbol={symbol} "
                    f"error={exchange_error[:100]}"
                )
            else:
                logger.debug(
                    f"Intent {intent_id} already cleared (race condition or partial fill)"
                )

        except Exception as e:
            logger.error(f"Order failed event handler error: {e}", exc_info=True)

    def get_current_price(self, symbol: str) -> Optional[float]:
        """Get current price via Market Data Service"""
        return self.market_data.get_price(symbol)

    def _maybe_scan_exits(self):
        """
        Scan all open positions for exit signals via ExitEngine.

        For each position:
        1. Check exit rules via ExitEngine (HARD_SL > HARD_TP > TRAIL_SL > TIME_EXIT)
        2. Generate exit signal if rule triggered
        3. Build ExitIntent with deterministic intent_id
        4. Route to OrderRouter for execution via order.intent event

        This is the CORE of the exit flow - runs every main loop iteration.
        """
        try:
            # Iterate over all open positions
            for symbol in list(self.portfolio.positions.keys()):
                pos = self.portfolio.positions.get(symbol)

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
                    from decision.exit_assembler import assemble as assemble_exit_intent
                    exit_intent = assemble_exit_intent(exit_signal)

                    if exit_intent:
                        # Publish order.intent event for OrderRouter
                        self.event_bus.publish("order.intent", exit_intent.__dict__)

                        logger.info(
                            f"Exit intent published: {exit_intent.intent_id} - "
                            f"{symbol} {exit_intent.side} {exit_intent.qty}"
                        )

        except Exception as e:
            logger.error(f"Exit scan error: {e}", exc_info=True)

    # =================================================================
    # BUY OPPORTUNITY EVALUATION - Delegated to Handler
    # =================================================================

    @trace_function(include_args=False, include_result=False)
    def _evaluate_buy_opportunities(self):
        """Evaluate buy opportunities - delegates to BuyDecisionHandler"""
        try:
            trace_step("market_health_check", market_health="unknown")
            market_health = "unknown"

            # Max positions check
            if len(self.positions) >= self.config.max_positions:
                trace_step("max_positions_reached", current_positions=len(self.positions),
                          max_positions=self.config.max_positions)
                return

            trace_step("evaluating_symbols", symbols_count=len(self.topcoins),
                      positions_count=len(self.positions))

            # CRITICAL FIX (C-ENG-01): Create snapshot to prevent race condition
            # If topcoins dict is modified during iteration, RuntimeError occurs
            with self._lock:
                topcoins_snapshot = list(self.topcoins.items())

            # Evaluate each symbol (safe to iterate snapshot without lock)
            for symbol, coin_data in topcoins_snapshot:
                trace_step("evaluate_symbol_start", symbol=symbol)

                if symbol in self.positions:
                    trace_step("symbol_already_in_positions", symbol=symbol)
                    continue

                current_price = self.get_current_price(symbol)
                if not current_price:
                    trace_step("no_price_data", symbol=symbol)
                    continue

                trace_step("symbol_evaluation", symbol=symbol, current_price=current_price)

                # Delegate to BuyDecisionHandler
                try:
                    trace_step("calling_evaluate_buy_signal", symbol=symbol)
                    buy_signal = self.buy_decision_handler.evaluate_buy_signal(
                        symbol, coin_data, current_price, market_health
                    )
                    trace_step("evaluate_buy_signal_returned", symbol=symbol, buy_signal=buy_signal)

                    if buy_signal:
                        trace_step("buy_signal_triggered", symbol=symbol, signal_reason=buy_signal)
                        self.buy_decision_handler.execute_buy_order(symbol, coin_data, current_price, buy_signal)
                    else:
                        trace_step("no_buy_signal", symbol=symbol)
                except Exception as eval_error:
                    trace_step("evaluate_buy_signal_error", symbol=symbol, error=str(eval_error))
                    logger.error(f"Error in buy evaluation for {symbol}: {eval_error}")
                    continue

        except Exception as e:
            logger.error(f"Buy opportunity evaluation error: {e}")

    # =================================================================
    # MANUAL OPERATIONS - Delegated to Handlers
    # =================================================================

    def add_manual_position(self, symbol: str, amount: float, entry_price: float, **kwargs) -> bool:
        """Add manual position with proper service integration"""
        try:
            with self._lock:
                if symbol in self.positions:
                    logger.warning(f"Position {symbol} already exists")
                    return False

                # Create position data
                position_data = {
                    'symbol': symbol,
                    'amount': amount,
                    'buying_price': entry_price,
                    'time': time.time(),
                    'signal': 'MANUAL',
                    'manual': True,
                    **kwargs
                }

                # Register with PnL Service
                self.pnl_service.record_fill(
                    symbol=symbol,
                    side="buy",
                    quantity=amount,
                    avg_price=entry_price,
                    fee_quote=0.0
                )

                self.positions[symbol] = position_data

                logger.info(f"Manual position added: {symbol} @{entry_price} x{amount}")
                return True

        except Exception as e:
            logger.error(f"Manual position addition error: {e}")
            return False

    def exit_position(self, symbol: str, reason: str = "MANUAL_EXIT") -> bool:
        """Exit position manually - delegated to ExitHandler"""
        return self.exit_handler.exit_position(symbol, reason)

    # =================================================================
    # MAINTENANCE & MONITORING
    # =================================================================

    def _periodic_maintenance(self):
        """Periodic maintenance tasks"""
        try:
            # Cache cleanup
            expired_tickers = self.market_data.cleanup_expired_cache()
            expired_orders = self.order_cache.cleanup_expired()

            if expired_tickers + expired_orders > 0:
                logger.debug(f"Cache cleanup: {expired_tickers} tickers, {expired_orders} orders")

            # Service statistics - delegated to Monitoring
            self.monitoring.log_service_statistics(self.market_data, self.exit_manager, self.pnl_service)

            # Portfolio sync
            self._sync_portfolio_state()

        except Exception as e:
            logger.error(f"Periodic maintenance error: {e}")

    def _sync_portfolio_state(self):
        """Sync portfolio state with positions"""
        try:
            # Placeholder for portfolio sync logic
            pass
        except Exception as e:
            logger.error(f"Portfolio sync error: {e}")

    def _emit_topdrops_ticker(self):
        """Emit TopDrops ticker showing biggest drops since anchor"""
        try:
            if not config.ENABLE_TOP_DROPS_TICKER:
                return

            # Get top drops from BuySignalService
            top_drops = self.buy_signal_service.get_top_drops(limit=config.TOP_DROPS_LIMIT or 10)

            if not top_drops:
                return

            # Get BTC info for context
            btc_price = self.market_data.get_price("BTC/USDT")
            btc_info = ""
            if btc_price:
                btc_conditions = self.market_guards.get_guard_status("BTC/USDT")
                btc_change = btc_conditions.get('btc_filter', {}).get('current_value')
                if btc_change:
                    btc_change_pct = (btc_change - 1.0) * 100.0
                    btc_info = f" | BTC: ${btc_price:.0f} ({btc_change_pct:+.2f}%)"

            # Build header
            trigger_pct = (1.0 - config.DROP_TRIGGER_VALUE) * 100.0
            header = (f"📉 Top Drops seit Anchor | Mode={config.DROP_TRIGGER_MODE} "
                     f"LB={config.DROP_TRIGGER_LOOKBACK_MIN}m | Trigger={trigger_pct:.1f}%{btc_info}")

            # Build drop lines
            lines = [header, "=" * 80]

            for i, drop in enumerate(top_drops, 1):
                symbol = drop['symbol']
                drop_pct = drop['drop_pct']
                current_price = drop['current_price']
                restweg_bps = drop['restweg_bps']
                hit_trigger = drop['hit_trigger']

                # Status indicator
                status = "🔥" if hit_trigger else f"{restweg_bps:+4d}bp"

                line = (f"{i:2d}. {symbol:12s} {drop_pct:+6.2f}% | "
                       f"${current_price:>8.4f} | {status}")
                lines.append(line)

            # Terminal output
            output = "\n".join(lines)
            print(f"\n{output}\n")

        except Exception as e:
            logger.error(f"TopDrops ticker error: {e}")

    def _calculate_current_equity(self) -> float:
        """Calculate current portfolio equity"""
        try:
            usdt_balance = self.portfolio.get_balance("USDT")
            pnl_summary = self.pnl_tracker.get_total_pnl()
            return usdt_balance + pnl_summary.get("unrealized", 0.0)
        except Exception as e:
            logger.warning(f"Equity calculation failed: {e}")
            return self.portfolio.get_balance("USDT")

    # =================================================================
    # PUBLIC API
    # =================================================================

    def get_positions(self) -> Dict[str, Dict]:
        """Get current positions"""
        with self._lock:
            return self.positions.copy()

    def get_pnl_summary(self) -> PnLSummary:
        """Get PnL summary from service"""
        return self.pnl_service.get_summary()

    def get_service_statistics(self) -> Dict[str, Any]:
        """Get comprehensive service statistics"""
        return {
            'market_data': self.market_data.get_statistics(),
            'exit_manager': self.exit_manager.get_statistics(),
            'order_service': self.order_service.get_statistics(),
            'pnl_service': self.pnl_service.get_summary(),
            'signal_queue': self.signal_manager.queue.get_signal_count(),
            'positions': len(self.positions),
            'session_digest': self.session_digest
        }

    def is_running(self) -> bool:
        """Check if engine is running"""
        return self.running

    def _start_ui_fallback_feed(self):
        """
        Optional UI fallback feed - polls tickers directly when snapshot bus fails.

        This is a diagnostic tool to verify the UI can display drops even when
        the snapshot pipeline has issues. Activate via config.UI_FALLBACK_FEED = True
        """
        if not getattr(config, "UI_FALLBACK_FEED", False):
            return

        if getattr(self, "_ui_fallback_thread", None):
            return

        import threading
        import time

        symbols = getattr(config, "UI_FALLBACK_SYMBOLS", ["BTC/USDT", "ETH/USDT"])
        peaks = {}

        # Create shutdown event for responsive thread cleanup
        shutdown_event = threading.Event()
        self._ui_fallback_shutdown_event = shutdown_event

        def run():
            logger.info("UI_FALLBACK_FEED_STARTED", extra={"symbols": symbols})
            while self.running:
                try:
                    snaps = []
                    for s in symbols:
                        # Check for shutdown during iteration
                        if not self.running:
                            break

                        try:
                            tkr = self.exchange_adapter.fetch_ticker(s)
                            last = float(tkr.get("last") or tkr.get("close") or 0.0)
                            if last <= 0:
                                continue

                            # Track peak
                            p = peaks.get(s, last)
                            p = max(p, last)
                            peaks[s] = p

                            # Calculate drop
                            drop_pct = (last / p - 1.0) * 100.0

                            # Build snapshot-like dict
                            snaps.append({
                                "v": 1,
                                "symbol": s,
                                "ts": time.time(),
                                "price": {"last": last, "bid": 0, "ask": 0, "mid": last},
                                "windows": {"peak": p, "trough": None, "drop_pct": drop_pct},
                                "features": {},
                                "state": {},
                                "flags": {}
                            })
                        except Exception as e:
                            logger.debug(f"UI fallback fetch error for {s}: {e}")

                    if snaps:
                        # Feed into drop_snapshot_store directly
                        with self._lock:
                            for snap in snaps:
                                symbol = snap.get("symbol")
                                if symbol:
                                    now_ts = time.time()
                            self.drop_snapshot_store[symbol] = {'snapshot': snap, 'ts': now_ts}
                            self._last_snapshot_ts = now_ts

                        logger.debug(f"UI_FALLBACK_FEED fed {len(snaps)} snapshots")

                except Exception as e:
                    logger.exception("UI_FALLBACK_FEED_ERR")

                # Use event wait instead of sleep for responsive shutdown
                # Returns True if shutdown signaled, False on timeout
                if shutdown_event.wait(timeout=1.5):
                    logger.debug("UI fallback thread received shutdown signal")
                    break

            logger.info("UI_FALLBACK_FEED_STOPPED")

        th = threading.Thread(target=run, daemon=False, name="UIFallbackFeed")
        th.start()
        self._ui_fallback_thread = th
        logger.info("UI Fallback Feed started", extra={"symbols": symbols})
