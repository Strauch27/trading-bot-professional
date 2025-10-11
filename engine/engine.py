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
from collections import deque
from datetime import datetime, timezone

# Core Dependencies
import config
import ccxt
from decimal import Decimal, ROUND_DOWN

# Service Imports (All Drops)
from services import (
    # Drop 1: PnL Service
    PnLService, TradeRecord, PnLSummary, PositionState,

    # Drop 2: Trailing & Signals
    TrailingStopController, TrailingStopManager,
    ExitSignalQueue, SignalManager,

    # Drop 3: Exchange Adapter & Orders
    OrderService, OrderCache,

    # Drop 4: Exit Management & Market Data
    ExitManager, ExitContext, ExitResult,
    MarketDataProvider, TickerData, fetch_ticker_cached,

    # Drop 5: Buy Signals & Market Guards
    BuySignalService, MarketGuards,
)

# Adapter Imports
from adapters.exchange import ExchangeAdapter, MockExchange

# Logging Imports
from logger import JsonlLogger, new_decision_id, new_client_order_id
from adaptive_logger import get_adaptive_logger, guard_stats_maybe_summarize

# Buy Flow Logger
from services.buy_flow_logger import get_buy_flow_logger, shutdown_buy_flow_logger

# Debug Tracing System
from debug_tracer import trace_function, trace_step, get_execution_summary

# Shutdown Coordinator
from services.shutdown_coordinator import get_shutdown_coordinator

# Drop-Trigger System
from signals.drop_trigger import DropTrigger
from signals.rolling_window import RollingWindow
from signals.confirm import Stabilizer

# PnL and Telemetry System
from pnl import PnLTracker
from order_flow import on_order_update
from telemetry import RollingStats, heartbeat_emit

# Refactored Modules
from .monitoring import EngineMonitoring
from .buy_decision import BuyDecisionHandler
from .position_manager import PositionManager
from .exit_handler import ExitHandler
from .engine_config import EngineConfig

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
        self.topcoins: Dict[str, Any] = watchlist or {}
        self.running = False
        self.main_thread = None
        self._lock = threading.RLock()

        # Ensure BTC/USDT is in watchlist for market conditions
        if self.topcoins and "BTC/USDT" not in self.topcoins:
            self.topcoins["BTC/USDT"] = {}

        # Statistics
        self.session_digest = {
            "start_time": time.time(),
            "buys": [], "sells": [],
            "errors": 0, "total_trades": 0
        }

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

        # Log configuration snapshot for audit trail
        self.monitoring.log_configuration_snapshot(self.config)

        # Start log management
        from log_manager import start_log_management
        start_log_management()

        # Subscribe to event bus for unified PnL handling
        try:
            from event_bus import subscribe
            subscribe("EXIT_FILLED", self.exit_handler.on_exit_filled_event)
            logger.info("Subscribed to EXIT_FILLED events for unified PnL handling")
        except Exception as e:
            logger.warning(f"Event bus subscription failed: {e}")

        # Engine State
        self.last_market_update = 0
        self.last_position_check = 0
        self.last_exit_processing = 0

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

        # Drop 4: Market Data Service
        self.market_data = MarketDataProvider(
            self.exchange_adapter,
            ticker_cache_ttl=self.config.ticker_cache_ttl,
            max_cache_size=1000
        )

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

        # Legacy Services - functionality moved to other services
        self.sizing_service = None
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
    # MAIN ENGINE LOOP - PURE ORCHESTRATION
    # =================================================================

    def start(self):
        """Start the trading engine"""
        if self.running:
            logger.warning("Engine already running")
            return

        logger.info("Starting Trading Engine...")
        self.running = True
        self.main_thread = threading.Thread(target=self._main_loop, daemon=False, name="TradingEngine-Main")
        self.main_thread.start()
        logger.info("Trading Engine initialized successfully")

    def stop(self):
        """Stop the trading engine"""
        self.running = False
        if self.main_thread and self.main_thread.is_alive():
            self.main_thread.join(timeout=5.0)

        # Print final statistics
        self.monitoring.log_final_statistics(self.session_digest, self.positions, self.pnl_service)

        # Shutdown buy flow logger
        shutdown_buy_flow_logger()

        # Stop log management
        from log_manager import stop_log_management
        stop_log_management()

        logger.info("Trading Engine stopped")

    def _main_loop(self):
        """Main engine loop - orchestrates all services via handlers"""
        try:
            logger.info("🚀 Main trading loop started", extra={'event_type': 'ENGINE_MAIN_LOOP_STARTED'})
            loop_counter = 0

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
                        print(f"💓 Engine läuft - Cycle #{loop_counter} - {len(self.positions)} Positionen, {len(self.topcoins)} Symbole")

                    # 1. Market Data Updates (every 5s)
                    if cycle_start - self.last_market_update > 5.0:
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
                        self.exit_handler.process_exit_signals()
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

                    # 6. Periodic Maintenance (every 30s)
                    if cycle_start % 30 < 1.0:
                        co.beat("before_maintenance")
                        self._periodic_maintenance()
                        co.beat("after_maintenance")

                    # 7. TopDrops Ticker (if enabled)
                    if self.config.enable_top_drops_ticker:
                        co.beat("before_topdrops_ticker")
                        self._emit_topdrops_ticker()
                        co.beat("after_topdrops_ticker")

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
                btc_history = self.market_data.get_ohlcv_history("BTC/USDT", limit=60)
                if btc_history and len(btc_history.bars) >= 2:
                    recent_bars = btc_history.bars[-60:]
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

                history = self.market_data.get_ohlcv_history(symbol, limit=60)
                if history and len(history.bars) >= 2:
                    recent_bars = history.bars[-60:]
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

    def get_current_price(self, symbol: str) -> Optional[float]:
        """Get current price via Market Data Service"""
        return self.market_data.get_price(symbol)

    # =================================================================
    # BUY OPPORTUNITY EVALUATION - Delegated to Handler
    # =================================================================

    @trace_function(include_args=False, include_result=False)
    def _evaluate_buy_opportunities(self):
        """Evaluate buy opportunities - delegates to BuyDecisionHandler"""
        try:
            trace_step("market_health_check", market_health="unknown")
            market_health = "unknown"
            allow_buy_eval = True

            # Max positions check
            if len(self.positions) >= self.config.max_positions:
                trace_step("max_positions_reached", current_positions=len(self.positions),
                          max_positions=self.config.max_positions)
                return

            trace_step("evaluating_symbols", symbols_count=len(self.topcoins),
                      positions_count=len(self.positions))

            # Evaluate each symbol
            for symbol, coin_data in self.topcoins.items():
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
