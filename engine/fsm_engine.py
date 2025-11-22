"""
FSM Trading Engine - Complete Implementation

Full rewrite with explicit Finite State Machine pattern.
All 12 phase handlers fully implemented.
"""

import logging
import sys
import threading
import time
from datetime import datetime, timezone
from typing import Any, Dict, Optional

# Config
import config

# Debug helper - only writes if ENGINE_DEBUG_TRACE is enabled
def _debug_write(msg: str) -> None:
    """Write debug message to stdout only if ENGINE_DEBUG_TRACE is enabled."""
    if getattr(config, 'ENGINE_DEBUG_TRACE', False):
        _debug_write(msg)
        sys.stdout.flush()

# CRITICAL FIX (P2): Helper function to release budget with retry logic
def _release_budget_with_retry(portfolio, quote_amount: float, symbol: str, reason: str, max_retries: int = 3) -> bool:
    """
    Release budget with retry logic to handle transient failures.

    Args:
        portfolio: Portfolio instance
        quote_amount: Amount to release in quote currency
        symbol: Trading symbol
        reason: Reason for release
        max_retries: Maximum retry attempts (default: 3)

    Returns:
        True if release successful, False otherwise
    """
    for attempt in range(1, max_retries + 1):
        try:
            portfolio.release_budget(
                quote_amount=quote_amount,
                symbol=symbol,
                reason=reason
            )
            logging.getLogger(__name__).info(
                f"[BUDGET_RELEASED] {symbol}: {quote_amount:.2f} USDT released (reason: {reason}, attempt: {attempt})"
            )
            return True
        except Exception as e:
            if attempt < max_retries:
                logging.getLogger(__name__).warning(
                    f"[BUDGET_RELEASE_RETRY] {symbol}: Attempt {attempt}/{max_retries} failed: {e}, retrying..."
                )
                time.sleep(0.1 * attempt)  # 100ms, 200ms, 300ms backoff
            else:
                logging.getLogger(__name__).error(
                    f"[BUDGET_RELEASE_FAILED] {symbol}: All {max_retries} attempts failed: {e}"
                )
                return False
    return False

from adapters.exchange import ExchangeAdapter as ExchangeAdapterClass
from core.fsm.fsm_events import EventContext, FSMEvent
from core.fsm.fsm_machine import FSMachine
from core.fsm.partial_fills import PartialFillHandler
from core.fsm.phases import Phase
from core.fsm.portfolio_transaction import get_portfolio_transaction, init_portfolio_transaction
from core.fsm.recovery import recover_fsm_states_on_startup
from core.fsm.snapshot import SnapshotManager

# Core FSM - Table-Driven Architecture
from core.fsm.state import CoinState
from core.fsm.state_data import OrderContext, StateData
from core.fsm.timeouts import TimeoutManager
from core.fsm.exit_engine import ExitEngine
from core.fsm.order_router import FSMOrderRouter
from core.fsm.reconciler import FSMReconciler
from core.fsm.exchange_wrapper import FSMExchangeWrapper
from core.fsm.position_management import DynamicTPSLManager
from core.logging.logger import new_client_order_id, new_decision_id, JsonlLogger

# Exchange Compliance & Ghost Store
from core.exchange_compliance import quantize_and_validate, ComplianceResult
from core.ghost_store import GhostStore

# Logging & Metrics
from core.logging.phase_events import PhaseEventLogger
from core.logging.adaptive_logger import get_adaptive_logger, track_error, track_failed_trade, track_guard_block, notify_trade_completed
from services.buy_flow_logger import get_buy_flow_logger

# Services (from existing architecture)
from services import (
    BuySignalService,
    ExitManager,
    MarketDataProvider,
    MarketGuards,
    OrderCache,
    OrderService,
    PnLService,
)
from services.cooldown import get_cooldown_manager
from telemetry.phase_metrics import PHASE_MAP, phase_changes, phase_code, start_metrics_server, update_stuck_metric

logger = logging.getLogger(__name__)


class FSMTradingEngine:
    """
    FSM-based Trading Engine - Complete Implementation.

    Each symbol flows through explicit phases:
    WARMUP → IDLE → ENTRY_EVAL → PLACE_BUY → WAIT_FILL → POSITION
         → EXIT_EVAL → PLACE_SELL → WAIT_SELL_FILL → POST_TRADE → COOLDOWN → IDLE
    """

    def __init__(self, exchange, portfolio, orderbookprovider, telegram=None, watchlist=None):
        """Initialize FSM Trading Engine with all services."""
        logger.info("Initializing FSM Trading Engine...")

        # Core Dependencies
        self.exchange = exchange
        self.portfolio = portfolio
        self.orderbookprovider = orderbookprovider
        self.telegram = telegram
        self.watchlist = watchlist or {}

        # Phase Event Logger (uses config parameters)
        self.phase_logger = PhaseEventLogger(
            log_file=config.PHASE_LOG_FILE,
            buffer_size=config.PHASE_LOG_BUFFER_SIZE
        )

        # P2-1: JsonlLogger for order/trade/decision events
        self.jsonl = JsonlLogger()

        # P2-2: AdaptiveLogger for context-aware logging
        self.adaptive_logger = get_adaptive_logger()

        # P2-3: BuyFlowLogger for step-by-step evaluation logging
        self.buy_flow = get_buy_flow_logger()

        # Symbol Cooldown Manager for failed order protection
        self.cooldown_manager = get_cooldown_manager()

        # Metrics
        self.metrics = type('Metrics', (), {
            'phase_changes': phase_changes,
            'phase_code': phase_code,
            'PHASE_MAP': PHASE_MAP
        })()

        # Initialize Services (needed before FSM modules that depend on them)
        self._initialize_services()

        # P0-4: ExitEngine for prioritized exit rule evaluation
        self.exit_engine = ExitEngine()

        # P0-5: DynamicTPSLManager for PnL-based TP/SL switching
        self.tp_sl_manager = DynamicTPSLManager()

        # P1-1: FSMOrderRouter for idempotent order placement
        self.order_router = FSMOrderRouter(self.order_service)

        # P1-2: FSMReconciler for exchange state synchronization
        self.reconciler = FSMReconciler(self.exchange, self.order_service)

        # P1-3: FSMExchangeWrapper for duplicate prevention
        self.exchange_wrapper = FSMExchangeWrapper(self.exchange)

        # Ghost Store for tracking rejected buy intents (24h TTL)
        self.ghost_store = GhostStore(ttl_sec=86400)

        # Table-Driven FSM Components
        self.fsm = FSMachine()
        self.timeout_manager = TimeoutManager()
        self.partial_fill_handler = PartialFillHandler()
        self.snapshot_manager = SnapshotManager()

        # Initialize portfolio transaction (needs portfolio and pnl_service)
        init_portfolio_transaction(self.portfolio, self.pnl_service, self.snapshot_manager)
        self.portfolio_tx = get_portfolio_transaction()

        # FSM state dictionary (symbol → CoinState)
        self.states: Dict[str, CoinState] = {}

        # Engine State
        self.running = False
        self.main_thread: Optional[threading.Thread] = None
        self.cycle_count = 0

        # Statistics
        self.stats = {
            "start_time": time.time(),
            "total_cycles": 0,
            "total_buys": 0,
            "total_sells": 0,
            "total_errors": 0,
        }

        # Start Prometheus
        if getattr(config, 'ENABLE_PROMETHEUS', False):
            port = getattr(config, 'PROMETHEUS_PORT', 8000)
            start_metrics_server(port)

        # Crash recovery - restore FSM states from snapshots
        if getattr(config, 'FSM_SNAPSHOT_ENABLED', True):
            try:
                recovered_states = recover_fsm_states_on_startup()
                for symbol, coin_state in recovered_states.items():
                    # Add to FSM state dict
                    self.states[symbol] = coin_state
                    logger.info(f"Recovered {symbol}: {coin_state.phase.name}")

                if recovered_states:
                    logger.info(f"✅ Crash recovery: {len(recovered_states)} states restored")
            except Exception as e:
                logger.error(f"Crash recovery failed: {e}", exc_info=True)

        logger.info(f"FSM Engine initialized: {len(self.watchlist)} symbols")

    def _initialize_services(self):
        """Initialize all trading services."""
        # Exchange Adapter
        self.exchange_adapter = ExchangeAdapterClass(self.exchange, max_retries=3)

        # Order Service
        self.order_cache = OrderCache(default_ttl=30.0, max_size=500)
        self.order_service = OrderService(self.exchange_adapter, self.order_cache)

        # EventBus (for market snapshot distribution)
        from core.events import get_event_bus
        self.event_bus = get_event_bus()

        # Market Data Service (with EventBus for snapshot publishing)
        self.market_data = MarketDataProvider(
            self.exchange_adapter,
            ticker_cache_ttl=getattr(config, 'TICKER_CACHE_TTL', 5.0),
            max_cache_size=1000,
            enable_drop_tracking=True,
            event_bus=self.event_bus
        )

        # Buy Signal Service
        self.buy_signal_service = BuySignalService(
            drop_trigger_value=config.DROP_TRIGGER_VALUE,
            drop_trigger_mode=config.DROP_TRIGGER_MODE,
            drop_trigger_lookback_min=config.DROP_TRIGGER_LOOKBACK_MIN,
            enable_minutely_audit=getattr(config, 'ENABLE_DROP_TRIGGER_MINUTELY', False)
        )

        # Drop Snapshot Store (for V9_3 integration)
        # Stores latest market snapshots with anchor data from MarketDataProvider
        self.drop_snapshot_store = {}  # symbol -> {snapshot: dict, ts: float}

        # Subscribe to market snapshots from EventBus
        self.event_bus.subscribe("market.snapshots", self._on_market_snapshots)

        # Market Guards
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

        # Exit Manager
        self.exit_manager = ExitManager(
            exchange_adapter=self.exchange_adapter,
            order_service=self.order_service,
            signal_manager=None,  # Not needed in FSM
            trade_ttl_min=getattr(config, 'TRADE_TTL_MIN', 60),
            never_market_sells=getattr(config, 'NEVER_MARKET_SELLS', False),
            max_slippage_bps=config.MAX_SLIPPAGE_BPS_EXIT
        )

        # PnL Service
        self.pnl_service = PnLService()

        logger.info("All trading services initialized")

    # ========== EVENT-BASED SYMBOL PROCESSING ==========

    def _process_symbol(self, symbol: str, md: Dict):
        """
        Process symbol with event-based FSM dispatch.

        Args:
            symbol: Trading symbol
            md: Market data dict with price, bid, ask, volume
        """
        import sys
        # Get or create state
        if symbol not in self.states:
            st = CoinState(symbol=symbol)
            st.fsm_data = StateData()
            st.phase = Phase.WARMUP
            self.states[symbol] = st
        else:
            st = self.states[symbol]

        price = md.get("price", 0.0)
        if symbol == list(self.watchlist.keys())[0]:  # Debug first symbol
            _debug_write(f"[DEBUG] {symbol}: price={price}, phase={st.phase.name}\n")
            sys.stdout.flush()
        if price <= 0:
            if symbol == list(self.watchlist.keys())[0]:
                _debug_write(f"[DEBUG] {symbol}: SKIPPING - invalid price\n")
                sys.stdout.flush()
            return  # Skip invalid price data

        # Build event context
        ctx = EventContext(
            event=None,  # Will be set by _emit_event
            symbol=symbol,
            timestamp=time.time(),
            order_id=st.order_id or "",
            decision_id=st.decision_id or "",
            data={
                "price": price,  # CRITICAL FIX: price goes in data dict
                "bid": md.get("bid", 0.0),
                "ask": md.get("ask", 0.0),
                "volume": md.get("volume", 0.0)
            }
        )
        if getattr(self, "exchange", None):
            ctx.data.setdefault("exchange", self.exchange)

        # Phase-specific event dispatch
        if symbol == list(self.watchlist.keys())[0]:
            _debug_write(f"[DEBUG] About to dispatch phase {st.phase.name}\n")
            sys.stdout.flush()
        if st.phase == Phase.WARMUP:
            self._process_warmup(st, ctx)

        elif st.phase == Phase.IDLE:
            self._process_idle(st, ctx)

        elif st.phase == Phase.ENTRY_EVAL:
            self._process_entry_eval(st, ctx)

        elif st.phase == Phase.PLACE_BUY:
            self._process_place_buy(st, ctx)

        elif st.phase == Phase.WAIT_FILL:
            self._process_wait_fill(st, ctx)

        elif st.phase == Phase.POSITION:
            self._process_position(st, ctx)

        elif st.phase == Phase.EXIT_EVAL:
            self._process_exit_eval(st, ctx)

        elif st.phase == Phase.PLACE_SELL:
            self._process_place_sell(st, ctx)

        elif st.phase == Phase.WAIT_SELL_FILL:
            self._process_wait_sell_fill(st, ctx)

        elif st.phase == Phase.POST_TRADE:
            self._process_post_trade(st, ctx)

        elif st.phase == Phase.COOLDOWN:
            pass  # Handled by _tick_timeouts

        elif st.phase == Phase.ERROR:
            self._process_error(st, ctx)

    def _tick_timeouts(self):
        """Check timeouts for all symbols and emit timeout events."""
        for symbol, st in list(self.states.items()):
            timeout_events = self.timeout_manager.check_all_timeouts(symbol, st)
            for event in timeout_events:
                prev_phase = st.phase  # Capture previous phase
                if self.fsm.process_event(st, event):
                    self.snapshot_manager.save_snapshot(symbol, st)
                    self._log_phase_transition(st, event, prev_phase)

    def _emit_event(self, st: CoinState, event: FSMEvent, ctx: EventContext) -> bool:
        """
        Emit FSM event and handle snapshot + logging.

        Returns:
            True if transition succeeded
        """
        event_ctx = EventContext(
            event=event,
            symbol=st.symbol,
            timestamp=ctx.timestamp,
            decision_id=ctx.decision_id,
            order_id=ctx.order_id,
            data=ctx.data  # price is in here!
        )

        prev_phase = st.phase  # Capture previous phase before transition

        if self.fsm.process_event(st, event_ctx):
            # Save snapshot synchronously after state change
            self.snapshot_manager.save_snapshot(st.symbol, st)
            # Log phase transition asynchronously (with from_state)
            self._log_phase_transition(st, event_ctx, prev_phase)
            return True
        return False

    def _log_phase_transition(self, st: CoinState, ctx: EventContext, prev_phase: Phase):
        """Log phase transition to JSONL (async)."""
        try:
            self.phase_logger.log_phase_change({
                "symbol": st.symbol,
                "from": prev_phase.value,  # Previous phase
                "to": st.phase.value,  # New phase
                "ts_ms": int(ctx.timestamp * 1000),
                "decision_id": st.decision_id or "",
                "order_id": st.order_id or "",
                "event": ctx.event.name if ctx.event else ""
            })
        except Exception as e:
            logger.debug(f"Phase logging failed: {e}")

        # Emit FSM event to dashboard
        try:
            from ui.dashboard import emit_fsm_event
            emit_fsm_event(
                symbol=st.symbol,
                from_phase=prev_phase.value,
                to_phase=st.phase.value,
                event=ctx.event.name if ctx.event else "UNKNOWN"
            )
        except Exception as e:
            logger.debug(f"Dashboard FSM event failed: {e}")

    def _on_market_snapshots(self, snapshots: list):
        """
        EventBus callback: Receive market snapshots from MarketDataProvider.
        Stores snapshots with anchor data for drop detection.
        """
        # DEBUGGING: Log that callback was called
        import sys
        _debug_write(f"[EVENT_BUS] _on_market_snapshots() called with {len(snapshots) if snapshots else 0} snapshots\n")
        sys.stdout.flush()

        if not snapshots:
            _debug_write(f"[EVENT_BUS] WARNING: Snapshots list is empty!\n")
            sys.stdout.flush()
            return

        import time
        now = time.time()

        symbols_stored = 0
        for snapshot in snapshots:
            if isinstance(snapshot, dict) and 'symbol' in snapshot:
                symbol = snapshot['symbol']
                self.drop_snapshot_store[symbol] = {
                    'snapshot': snapshot,
                    'ts': now
                }
                symbols_stored += 1

        _debug_write(f"[EVENT_BUS] Stored {symbols_stored} snapshots. Total store size: {len(self.drop_snapshot_store)}\n")
        sys.stdout.flush()

    # ========== PHASE-SPECIFIC PROCESSORS ==========

    def _process_warmup(self, st: CoinState, ctx: EventContext):
        """WARMUP: Initialize symbol and transition to IDLE."""
        import sys
        if st.symbol == list(self.watchlist.keys())[0]:
            _debug_write(f"[DEBUG] _process_warmup called for {st.symbol}\n")
            sys.stdout.flush()
        try:
            # Backfill history if configured
            if getattr(config, 'BACKFILL_MINUTES', 0) > 0:
                self.market_data.backfill_history(
                    [st.symbol],
                    timeframe='1m',
                    minutes=config.BACKFILL_MINUTES
                )

            # Initialize drop anchor if configured
            if getattr(config, 'USE_DROP_ANCHOR', False) and ctx.price > 0:
                st.anchor_price = ctx.price
                st.anchor_ts = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
                self.portfolio.set_drop_anchor(st.symbol, ctx.price, st.anchor_ts)

            # Emit WARMUP_COMPLETED event
            if st.symbol == list(self.watchlist.keys())[0]:
                _debug_write(f"[DEBUG] Emitting WARMUP_COMPLETED for {st.symbol}\n")
                sys.stdout.flush()
            result = self._emit_event(st, FSMEvent.WARMUP_COMPLETED, ctx)
            if st.symbol == list(self.watchlist.keys())[0]:
                _debug_write(f"[DEBUG] _emit_event returned: {result}, new phase: {st.phase.name}\n")
                sys.stdout.flush()

        except Exception as e:
            logger.error(f"Warmup error {st.symbol}: {e}")
            self._emit_event(st, FSMEvent.ERROR_OCCURRED, ctx)

    def _process_idle(self, st: CoinState, ctx: EventContext):
        """IDLE: Check for free slots and transition to ENTRY_EVAL."""
        if st.in_cooldown():
            return  # Still in cooldown

        # Check available slots
        max_trades = getattr(config, 'MAX_TRADES', 10)

        # CRITICAL FIX: Count ALL active phases, not just POSITION
        # A slot is occupied if a coin is in any of these phases:
        # - WAIT_FILL (buying), POSITION (holding), EXIT_EVAL (checking exit)
        # - PLACE_SELL (selling), WAIT_SELL_FILL (selling), POST_TRADE (cleanup)
        active_phases = {Phase.WAIT_FILL, Phase.POSITION, Phase.EXIT_EVAL,
                        Phase.PLACE_SELL, Phase.WAIT_SELL_FILL, Phase.POST_TRADE}
        active_positions = sum(1 for s in self.states.values() if s.phase in active_phases)

        if active_positions >= max_trades:
            return  # No slots available

        # Assign decision_id for idempotency
        st.decision_id = new_decision_id()

        # Emit SLOT_AVAILABLE event
        self._emit_event(st, FSMEvent.SLOT_AVAILABLE, ctx)

    def _process_entry_eval(self, st: CoinState, ctx: EventContext):
        """ENTRY_EVAL: Evaluate guards and signals."""
        # P2-3: Start buy flow logging
        try:
            self.buy_flow.start_evaluation(st.symbol)
        except Exception as e:
            logger.debug(f"Failed to start buy_flow logging: {e}")

        # P2-1: Log decision start
        try:
            self.jsonl.decision_start(
                symbol=st.symbol,
                decision_id=st.decision_id,
                price=ctx.price,
                phase="entry_eval"
            )
        except Exception as e:
            logger.debug(f"Failed to log decision_start: {e}")

        # FIX: Check symbol cooldown (failed order protection)
        if self.cooldown_manager.is_active(st.symbol):
            remaining_s = self.cooldown_manager.get_remaining(st.symbol)
            logger.info(f"[COOLDOWN] {st.symbol} in cooldown: {remaining_s:.0f}s remaining after failed order")

            # P2-3: Log cooldown check step (BLOCKED)
            try:
                self.buy_flow.step(0, "Symbol Cooldown Check", "BLOCKED",
                                  f"{remaining_s:.0f}s remaining after failed order")
            except Exception as e:
                logger.debug(f"Failed to log buy_flow step: {e}")

            # P2-1: Log decision end (cooldown blocked)
            try:
                self.jsonl.decision_end(
                    symbol=st.symbol,
                    decision_id=st.decision_id,
                    outcome="cooldown_blocked",
                    remaining_s=remaining_s
                )
            except Exception as e:
                logger.debug(f"Failed to log decision_end: {e}")

            # P2-3: End buy flow logging (BLOCKED)
            try:
                duration_ms = (time.time() - self.buy_flow.start_time) * 1000 if self.buy_flow.start_time else 0
                self.buy_flow.end_evaluation("BLOCKED", duration_ms,
                                            f"symbol_cooldown_{remaining_s:.0f}s")
            except Exception as e:
                logger.debug(f"Failed to end buy_flow logging: {e}")

            # Emit NO_SIGNAL event to return to IDLE
            self._emit_event(st, FSMEvent.NO_SIGNAL, ctx)
            return

        # Update services
        self.buy_signal_service.update_price(st.symbol, ctx.price)
        self.market_guards.update_price_data(st.symbol, ctx.price, ctx.data.get("volume", 0.0))

        if ctx.data.get("bid") and ctx.data.get("ask"):
            self.market_guards.update_orderbook(st.symbol, ctx.data["bid"], ctx.data["ask"])

        # Check guards
        passes_guards, failed_guards = self.market_guards.passes_all_guards(st.symbol, ctx.price)
        if not passes_guards:
            # CRITICAL FIX (DIFF 7): Enhanced guard logging with details
            # Log each failed guard with specific reason and values
            guard_details = []
            for guard_name in failed_guards:
                try:
                    # Get guard-specific details
                    if guard_name == "btc_filter":
                        guard_details.append(f"{guard_name} (BTC volatility too high)")
                    elif guard_name == "falling_coins":
                        guard_details.append(f"{guard_name} (too many coins falling)")
                    elif guard_name == "sma_guard":
                        guard_details.append(f"{guard_name} (price below SMA)")
                    elif guard_name == "volume_guard":
                        guard_details.append(f"{guard_name} (volume too low)")
                    elif guard_name == "spread_guard":
                        guard_details.append(f"{guard_name} (spread too wide)")
                    elif guard_name == "vol_sigma_guard":
                        guard_details.append(f"{guard_name} (volatility too low)")
                    else:
                        guard_details.append(guard_name)
                except Exception:
                    guard_details.append(guard_name)

            guard_summary = ", ".join(guard_details)
            logger.warning(f"[GUARD_BLOCK] {st.symbol} @ {ctx.price:.8f}: {guard_summary}")

            # P2-3: Log guard check step (BLOCKED)
            try:
                self.buy_flow.step(1, "Market Guards", "BLOCKED", f"Failed: {guard_summary}")
            except Exception as e:
                logger.debug(f"Failed to log buy_flow step: {e}")

            # P2-2: Track guard block for adaptive logging
            try:
                track_guard_block(st.symbol, failed_guards)
            except Exception as e:
                logger.debug(f"Failed to track guard block: {e}")

            # P2-1: Log decision end (guards failed)
            try:
                self.jsonl.decision_end(
                    symbol=st.symbol,
                    decision_id=st.decision_id,
                    outcome="guards_failed",
                    failed_guards=failed_guards
                )
            except Exception as e:
                logger.debug(f"Failed to log decision_end: {e}")

            # P2-3: End buy flow logging (BLOCKED)
            try:
                duration_ms = (time.time() - self.buy_flow.start_time) * 1000 if self.buy_flow.start_time else 0
                self.buy_flow.end_evaluation("BLOCKED", duration_ms, f"Guards: {', '.join(failed_guards)}")
            except Exception as e:
                logger.debug(f"Failed to end buy_flow logging: {e}")

            self._emit_event(st, FSMEvent.GUARDS_BLOCKED, ctx)
            return
        else:
            # P2-3: Log guard check step (PASS)
            try:
                self.buy_flow.step(1, "Market Guards", "PASS", "All guards passed")
            except Exception as e:
                logger.debug(f"Failed to log buy_flow step: {e}")

        # FSM PARITY FIX (LÜCKE 1): Check budget affordability before signal evaluation
        # This prevents evaluating signals when we can't afford min_notional/min_qty
        try:
            from services.market_guards import can_afford

            # Calculate available budget
            available_budget = self.portfolio.get_free_usdt()
            max_trades = getattr(config, 'MAX_TRADES', 10)
            per_trade = available_budget / max(1, max_trades)
            quote_budget = min(per_trade, getattr(config, 'POSITION_SIZE_USDT', 10.0))

            if not can_afford(self.exchange, st.symbol, ctx.price, quote_budget):
                logger.warning(f"[BUDGET_GUARD] {st.symbol} @ {ctx.price:.8f}: Insufficient budget ({quote_budget:.2f} USDT) for min_notional/min_qty")

                # P2-3: Log budget check step (BLOCKED)
                try:
                    self.buy_flow.step(1.5, "Budget Check", "BLOCKED", f"Insufficient budget: {quote_budget:.2f} USDT")
                except Exception as e:
                    logger.debug(f"Failed to log buy_flow step: {e}")

                # P2-1: Log decision end (budget blocked)
                try:
                    self.jsonl.decision_end(
                        symbol=st.symbol,
                        decision_id=st.decision_id,
                        outcome="budget_blocked",
                        budget=quote_budget
                    )
                except Exception as e:
                    logger.debug(f"Failed to log decision_end: {e}")

                # P2-3: End buy flow logging (BLOCKED)
                try:
                    duration_ms = (time.time() - self.buy_flow.start_time) * 1000 if self.buy_flow.start_time else 0
                    self.buy_flow.end_evaluation("BLOCKED", duration_ms, "Budget: Insufficient for min_notional")
                except Exception as e:
                    logger.debug(f"Failed to end buy_flow logging: {e}")

                self._emit_event(st, FSMEvent.RISK_LIMITS_BLOCKED, ctx)
                return
        except Exception as e:
            logger.debug(f"Budget check failed for {st.symbol}: {e}")

        # Check signal
        # DEBUGGING FIX (P3): Add snapshot store diagnostics
        snapshot_entry = self.drop_snapshot_store.get(st.symbol)
        logger.info(f"[DROP_CHECK] Symbol: {st.symbol}, Price: {ctx.price}, Snapshot exists: {snapshot_entry is not None}, Store size: {len(self.drop_snapshot_store)}")
        if not snapshot_entry:
            logger.warning(f"[DROP_CHECK] NO SNAPSHOT for {st.symbol} - Cannot evaluate buy signal!")

        logger.debug(f"[DROP_CHECK] Evaluating buy signal for {st.symbol} @ {ctx.price}")
        buy_triggered, signal_context = self.buy_signal_service.evaluate_buy_signal(
            st.symbol, ctx.price, self.drop_snapshot_store
        )

        if buy_triggered:
            logger.info(f"[DROP_DETECTED] {st.symbol}: Drop detected! Mode={signal_context.get('mode')}, Drop%={signal_context.get('drop_pct', 0)*100:.2f}%, Price={ctx.price}")
            st.signal = f"DROP_MODE_{signal_context.get('mode', '?')}"
            if st.fsm_data:
                st.fsm_data.signal_detected_at = time.time()
                st.fsm_data.signal_type = st.signal

            # P2-3: Log signal check step (TRIGGERED)
            try:
                self.buy_flow.step(2, "Buy Signal", "TRIGGERED", f"Signal: {st.signal}")
            except Exception as e:
                logger.debug(f"Failed to log buy_flow step: {e}")

            # P2-1: Log decision end (signal detected)
            try:
                self.jsonl.decision_end(
                    symbol=st.symbol,
                    decision_id=st.decision_id,
                    outcome="signal_detected",
                    signal_type=st.signal,
                    signal_context=signal_context
                )
            except Exception as e:
                logger.debug(f"Failed to log decision_end: {e}")

            self._emit_event(st, FSMEvent.SIGNAL_DETECTED, ctx)
        else:
            # P2-3: Log signal check step (NO TRIGGER)
            reason = signal_context.get('reason', 'unknown')
            logger.debug(f"[DROP_CHECK] {st.symbol}: No drop detected. Reason: {reason}")
            try:
                self.buy_flow.step(2, "Buy Signal", "NO TRIGGER", f"No drop: {reason}")
            except Exception as e:
                logger.debug(f"Failed to log buy_flow step: {e}")

            # P2-1: Log decision end (no signal)
            try:
                self.jsonl.decision_end(
                    symbol=st.symbol,
                    decision_id=st.decision_id,
                    outcome="no_signal"
                )
            except Exception as e:
                logger.debug(f"Failed to log decision_end: {e}")

            # P2-3: End buy flow logging (SKIPPED)
            try:
                duration_ms = (time.time() - self.buy_flow.start_time) * 1000 if self.buy_flow.start_time else 0
                self.buy_flow.end_evaluation("SKIPPED", duration_ms, "No buy signal")
            except Exception as e:
                logger.debug(f"Failed to end buy_flow logging: {e}")

            self._emit_event(st, FSMEvent.NO_SIGNAL, ctx)

    def _process_place_buy(self, st: CoinState, ctx: EventContext):
        """PLACE_BUY: Calculate size and place buy order."""
        try:
            # CRITICAL FIX (P0-1): FSM-State-based duplicate prevention
            # Check if THIS EXACT SYMBOL already has an active FSM state in buy/hold phases
            # This prevents race conditions where multiple signals for the same symbol execute simultaneously
            allow_duplicates = getattr(config, 'ALLOW_DUPLICATE_COINS', False)
            if not allow_duplicates:
                active_buy_phases = {Phase.PLACE_BUY, Phase.WAIT_FILL, Phase.POSITION,
                                    Phase.EXIT_EVAL, Phase.PLACE_SELL, Phase.WAIT_SELL_FILL, Phase.POST_TRADE}

                # Check if any OTHER FSM state has this symbol active
                for other_symbol, other_state in self.states.items():
                    if other_symbol == st.symbol and other_state.phase in active_buy_phases and other_state is not st:
                        logger.error(
                            f"[DUPLICATE_FSM_BLOCKED] {st.symbol} already active in phase {other_state.phase.name}! "
                            f"Aborting duplicate buy. (ALLOW_DUPLICATE_COINS={allow_duplicates})"
                        )
                        self._emit_event(st, FSMEvent.BUY_ABORTED, ctx)
                        return

            # CRITICAL FIX (P3): Re-check cooldown to close race window
            # Race condition: Coin passes cooldown check in ENTRY_EVAL, but another instance
            # of the same symbol might have entered cooldown before PLACE_BUY executes.
            # This prevents duplicate orders for the same symbol within cooldown period.
            if hasattr(st, 'cooldown_until') and st.cooldown_until:
                import time
                remaining_cooldown = st.cooldown_until - time.time()
                if remaining_cooldown > 0:
                    logger.warning(
                        f"[COOLDOWN_RACE_BLOCKED] {st.symbol} entered cooldown before PLACE_BUY "
                        f"(remaining: {remaining_cooldown:.1f}s). Aborting buy."
                    )
                    self._emit_event(st, FSMEvent.BUY_ABORTED, ctx)
                    return

            # Calculate position size
            available_budget = self.portfolio.get_free_usdt()
            max_trades = getattr(config, 'MAX_TRADES', 10)
            per_trade = available_budget / max(1, max_trades)
            quote_budget = min(per_trade, getattr(config, 'POSITION_SIZE_USDT', 10.0))
            min_slot = getattr(config, 'MIN_SLOT_USDT', 5.0)

            # DEBUGGING FIX (P4): Add budget diagnostics
            # CRITICAL FIX: Count ALL active phases (same as in _process_idle)
            active_phases = {Phase.WAIT_FILL, Phase.POSITION, Phase.EXIT_EVAL,
                            Phase.PLACE_SELL, Phase.WAIT_SELL_FILL, Phase.POST_TRADE}
            active_positions = sum(1 for s in self.states.values() if s.phase in active_phases)
            logger.info(f"[BUDGET_CHECK] Symbol: {st.symbol}, Available: {available_budget:.2f} USDT, Per-trade: {per_trade:.2f}, Quote budget: {quote_budget:.2f}, Min slot: {min_slot:.2f}, Active positions: {active_positions}/{max_trades}")

            if quote_budget < min_slot:
                logger.error(f"[BUDGET_CHECK] INSUFFICIENT BUDGET for {st.symbol}! Quote budget {quote_budget:.2f} < Min slot {min_slot:.2f}")
                # FSM PARITY FIX (LÜCKE 3): Use BUY_ABORTED for budget failures
                self._emit_event(st, FSMEvent.BUY_ABORTED, ctx)
                return

            # CRITICAL FIX: Duplicate purchase protection
            # Prevent buying the same coin twice (race condition prevention)
            if self.portfolio.is_holding(st.symbol):
                logger.warning(f"[DUPLICATE_BLOCKED] {st.symbol} already has an active position! Aborting duplicate buy.")
                self._emit_event(st, FSMEvent.BUY_ABORTED, ctx)
                return

            # Check if there's already a pending buy order for this symbol
            if self.portfolio.has_open_order(st.symbol):
                logger.warning(f"[DUPLICATE_BLOCKED] {st.symbol} already has a pending buy order! Aborting duplicate buy.")
                self._emit_event(st, FSMEvent.BUY_ABORTED, ctx)
                return

            # CRITICAL FIX (P0-2): MAX_PER_SYMBOL_USD enforcement
            # Prevent excessive concentration in a single symbol
            max_per_symbol = getattr(config, 'MAX_PER_SYMBOL_USD', 60.0)
            current_exposure = self.portfolio.get_symbol_exposure_usdt(st.symbol)
            new_total_exposure = current_exposure + quote_budget

            if new_total_exposure > max_per_symbol:
                logger.error(
                    f"[MAX_PER_SYMBOL_BLOCKED] {st.symbol}: Current exposure ${current_exposure:.2f} + "
                    f"new order ${quote_budget:.2f} = ${new_total_exposure:.2f} exceeds limit ${max_per_symbol:.2f}. "
                    f"Aborting buy."
                )
                self._emit_event(st, FSMEvent.BUY_ABORTED, ctx)
                return

            # CRITICAL FIX: Reserve budget ATOMICALLY before order placement
            # This prevents race conditions where multiple coins compete for the same budget
            # Budget must be reserved HERE (not later in OrderRouter) to prevent concurrent access
            budget_reserved = False  # Track reservation state for cleanup
            try:
                self.portfolio.reserve_budget(
                    quote_amount=quote_budget,
                    symbol=st.symbol,
                    order_info={
                        "intent": "buy",
                        "decision_id": st.decision_id,
                        "price": ctx.price,
                        "timestamp": time.time()
                    }
                )
                budget_reserved = True  # Mark as successfully reserved
                st.reserved_budget = quote_budget  # Track reserved amount for cleanup
                logger.info(f"[BUDGET_RESERVED] {st.symbol}: {quote_budget:.2f} USDT reserved (available: {self.portfolio.get_free_usdt():.2f})")
            except ValueError as e:
                logger.error(f"[BUDGET_RESERVE_FAILED] {st.symbol}: {e}")
                self._emit_event(st, FSMEvent.BUY_ABORTED, ctx)
                return
            except Exception as e:
                logger.error(f"[BUDGET_RESERVE_ERROR] {st.symbol}: Unexpected error during budget reservation: {e}")
                self._emit_event(st, FSMEvent.BUY_ABORTED, ctx)
                return

            # CRITICAL FIX: Use exchange compliance module for auto-quantization
            # Generate intent_id first (for logging and tracking)
            intent_id = f"buy:{st.symbol}:{st.decision_id}:buy"

            # Get market info for compliance validation
            try:
                market_info = self.exchange.market(st.symbol)
            except Exception as e:
                logger.error(f"[BUY_INTENT_ABORTED] {st.symbol} intent={intent_id[:8]} reason=market_info_unavailable error={e}")
                # CRITICAL FIX (P2): Release reserved budget with retry logic
                if budget_reserved:
                    _release_budget_with_retry(
                        self.portfolio,
                        quote_budget,
                        st.symbol,
                        "market_info_unavailable"
                    )
                # FSM PARITY FIX (LÜCKE 3): Use BUY_ABORTED for clean aborts
                self._emit_event(st, FSMEvent.BUY_ABORTED, ctx)
                return

            # FIX: Calculate raw price from ASK + PREMIUM (not mid price!)
            # Get ask price from context (set in _build_context via ticker)
            ask_price = ctx.data.get("ask", ctx.price)

            # Fallback if ask not available
            if not ask_price or ask_price <= 0:
                ask_price = ctx.price

            # Apply premium (BUY_ESCALATION_STEPS premium_bps)
            premium_bps = 20  # Default from BUY_ESCALATION_STEPS
            if hasattr(config, 'BUY_ESCALATION_STEPS') and config.BUY_ESCALATION_STEPS:
                premium_bps = config.BUY_ESCALATION_STEPS[0].get('premium_bps', 20)

            # Calculate aggressive buy price (ask + premium)
            raw_price = ask_price * (1.0 + premium_bps / 10000.0)
            raw_amount = quote_budget / raw_price

            logger.info(f"[PRICE_CALC] {st.symbol} ask={ask_price:.8f} premium={premium_bps}bps final={raw_price:.8f}")

            # Log BUY_INTENT
            logger.info(
                f"[BUY_INTENT] {st.symbol} intent={intent_id[:8]} "
                f"raw_price={raw_price:.8f} raw_amount={raw_amount:.6f} "
                f"budget={quote_budget:.2f}"
            )

            # Quantize and validate with auto-fix
            comp = quantize_and_validate(raw_price, raw_amount, market_info)

            # Log quantization result
            logger.info(
                f"[BUY_INTENT_QUANTIZED] {st.symbol} intent={intent_id[:8]} "
                f"q_price={comp.price:.8f} q_amount={comp.amount:.6f} "
                f"violations={comp.violations} auto_fixed={comp.auto_fixed}"
            )

            # Check if order is valid after quantization
            if not comp.is_valid():
                # Determine abort reason
                abort_reason = (
                    "precision_non_compliant"
                    if "invalid_amount_after_quantize" in comp.violations
                    else "min_cost_violation"
                )

                # Log abort
                logger.warning(
                    f"[BUY_INTENT_ABORTED] {st.symbol} intent={intent_id[:8]} "
                    f"reason={abort_reason} violations={comp.violations}"
                )

                # Create ghost position for UI tracking
                try:
                    self.ghost_store.create(
                        intent_id=intent_id,
                        symbol=st.symbol,
                        q_price=comp.price,
                        q_amount=comp.amount,
                        violations=comp.violations,
                        abort_reason=abort_reason,
                        raw_price=raw_price,
                        raw_amount=raw_amount,
                        market_precision={
                            "tick": market_info.get("precision", {}).get("price"),
                            "step": market_info.get("precision", {}).get("amount"),
                            "min_cost": market_info.get("limits", {}).get("cost", {}).get("min")
                        }
                    )
                except Exception as e:
                    logger.debug(f"Failed to create ghost position: {e}")

                # Track failed trade
                try:
                    track_failed_trade(st.symbol, f"compliance_{abort_reason}")
                except Exception:
                    pass

                # CRITICAL FIX (P2): Release reserved budget with retry logic
                if budget_reserved:
                    _release_budget_with_retry(
                        self.portfolio,
                        quote_budget,
                        st.symbol,
                        f"quantization_{abort_reason}"
                    )

                # FSM PARITY FIX (LÜCKE 3): Use BUY_ABORTED for clean aborts
                self._emit_event(st, FSMEvent.BUY_ABORTED, ctx)
                return

            # Use quantized values for order placement
            price = comp.price
            amount = comp.amount

            ctx.data["order_price"] = price
            ctx.data["order_qty"] = amount
            if getattr(self, "exchange", None):
                ctx.data.setdefault("exchange", self.exchange)

            logger.info(
                f"[PRE_FLIGHT] {st.symbol} PASSED "
                f"(q_price={price:.8f}, q_amount={amount:.6f}, "
                f"notional={price * amount:.2f})"
            )

            # P1-1: Use OrderRouter for idempotent order placement
            # intent_id already generated above for ghost tracking
            result = self.order_router.submit(
                intent_id=intent_id,
                symbol=st.symbol,
                side="buy",
                amount=amount,
                price=price,  # Use sized price from helpers, not raw ctx.price
                order_type="limit"  # OrderRouter will handle IOC via order_service
            )

            # CRITICAL FIX: Add defensive logging and validation
            logger.info(f"[PLACE_BUY] {st.symbol} OrderRouter result: success={result.success}, order_id={result.order_id!r}, error={result.error}")

            # CRITICAL FIX: Validate order_id is not None/empty before proceeding
            if result.success and result.order_id and str(result.order_id).strip():
                st.order_id = result.order_id
                st.client_order_id = intent_id  # Use intent_id as client order id
                st.order_placed_ts = time.time()

                # UI EVENT: ORDER_SUBMITTED (for dashboard)
                try:
                    from core.logging.events import emit
                    emit("ORDER_SUBMITTED",
                         symbol=st.symbol,
                         side="buy",
                         order_id=st.order_id,
                         price=price,
                         amount=amount,
                         decision_id=st.decision_id)
                except Exception as e:
                    logger.debug(f"Failed to emit ORDER_SUBMITTED: {e}")

                # CRITICAL FIX: Persist order state immediately after placement
                # This prevents order_id loss on crashes or phase transitions
                try:
                    self.portfolio.save_open_buy_order(st.symbol, {
                        "order_id": st.order_id,
                        "client_order_id": intent_id,
                        "price": price,  # Use sized price
                        "amount": amount,
                        "timestamp": st.order_placed_ts,
                        "phase": "WAIT_FILL"
                    })
                except Exception as e:
                    logger.warning(f"Failed to persist order state for {st.symbol}: {e}")

                if st.fsm_data:
                    st.fsm_data.buy_order = OrderContext(
                        order_id=result.order_id,
                        client_order_id=intent_id,
                        placed_at=time.time(),
                        target_qty=amount,
                        status="pending"
                    )

                ctx.order_id = st.order_id
                ctx.data["client_order_id"] = intent_id

                # P2-3: Log order placement step
                try:
                    self.buy_flow.step(3, "Place Buy Order", "SUCCESS", f"Order {result.order_id} placed (idempotent)")
                except Exception as e:
                    logger.debug(f"Failed to log buy_flow step: {e}")

                # P2-1: Log order placement
                try:
                    self.jsonl.order_sent(
                        symbol=st.symbol,
                        side="buy",
                        order_id=result.order_id,
                        client_order_id=intent_id,
                        amount=amount,
                        price=price,  # Use sized price
                        order_type="limit",
                        decision_id=st.decision_id
                    )
                except Exception as e:
                    logger.debug(f"Failed to log order_sent: {e}")

                # Remove ghost position if exists (edge case: retry after abort)
                try:
                    removed = self.ghost_store.remove_by_intent(intent_id)
                    if removed:
                        logger.debug(f"[GHOST_REMOVED] {st.symbol} intent={intent_id[:8]} (order placed successfully)")
                except Exception as e:
                    logger.debug(f"Failed to remove ghost: {e}")

                self._emit_event(st, FSMEvent.BUY_ORDER_PLACED, ctx)
                self.stats["total_buys"] += 1
            else:
                # CRITICAL FIX: Log detailed failure reason
                logger.error(f"[PLACE_BUY_FAILED] {st.symbol} Order placement failed: success={result.success}, order_id={result.order_id!r}, error={result.error}")

                # CRITICAL FIX (P2): Release reserved budget with retry logic
                if budget_reserved:
                    _release_budget_with_retry(
                        self.portfolio,
                        quote_budget,
                        st.symbol,
                        "order_placement_failed"
                    )

                # P2-2: Track failed trade (order placement failed)
                try:
                    track_failed_trade(st.symbol, "buy_order_placement_failed")
                except Exception as e:
                    logger.debug(f"Failed to track failed trade: {e}")

                self._emit_event(st, FSMEvent.ORDER_PLACEMENT_FAILED, ctx)

        except Exception as e:
            logger.error(f"Place buy error {st.symbol}: {e}")

            # CRITICAL FIX (P2): Release reserved budget with retry logic
            if budget_reserved:
                _release_budget_with_retry(
                    self.portfolio,
                    quote_budget,
                    st.symbol,
                    "buy_order_exception"
                )

            # P2-2: Track error for adaptive logging
            try:
                track_error("buy_order_error", symbol=st.symbol, details=str(e))
            except Exception:
                pass

            self._emit_event(st, FSMEvent.ERROR_OCCURRED, ctx)

    def _process_wait_fill(self, st: CoinState, ctx: EventContext):
        """WAIT_FILL: Poll order status and handle fills with PartialFillHandler."""
        # Note: Timeouts handled by _tick_timeouts
        try:
            # CRITICAL FIX: Multi-stage rehydration with retry logic
            if not st.order_id:
                logger.warning(f"[WAIT_FILL] {st.symbol} order_id is None - attempting rehydration")

                # Stage 1: Read from persistence
                try:
                    persisted = self.portfolio.get_open_buy_order(st.symbol)
                    if persisted and persisted.get("order_id"):
                        st.order_id = persisted["order_id"]
                        st.client_order_id = persisted.get("client_order_id")
                        logger.info(f"[WAIT_FILL] {st.symbol} order_id rehydrated from persistence: {st.order_id}")
                except Exception as e:
                    logger.debug(f"Persistence rehydration failed for {st.symbol}: {e}")

                # Stage 2: Lookup via clientOrderId if still missing
                # CRITICAL FIX (DIFF 5): Use exchange_wrapper with robust fallback
                if not st.order_id and st.client_order_id:
                    try:
                        order = self.exchange_wrapper.fetch_order_by_client_id(st.symbol, st.client_order_id)
                        if order and order.get("id"):
                            st.order_id = order["id"]
                            logger.info(f"[WAIT_FILL] {st.symbol} order_id rehydrated via clientOrderId: {st.order_id}")
                    except Exception as e:
                        logger.debug(f"clientOrderId lookup failed for {st.symbol}: {e}")

                # Stage 3: Short retry before giving up
                if not st.order_id:
                    if not hasattr(st, 'wait_fill_retry_count'):
                        st.wait_fill_retry_count = 0
                    st.wait_fill_retry_count += 1

                    if st.wait_fill_retry_count <= 3:
                        logger.warning(f"[WAIT_FILL_RETRY] {st.symbol} retry {st.wait_fill_retry_count}/3")
                        time.sleep(0.25)  # Brief pause before next tick
                        return
                    else:
                        # All retries exhausted - ABORT with BUY_ABORTED event
                        logger.error(f"[WAIT_FILL_ERROR] {st.symbol} order_id is None after rehydration+lookup+retries!")
                        self._emit_event(st, FSMEvent.BUY_ABORTED, ctx)
                        return
                else:
                    # Reset retry counter on success
                    st.wait_fill_retry_count = 0

            if not getattr(self, "exchange", None):
                logger.error(f"[WAIT_FILL] {st.symbol} missing exchange handle - aborting order {st.order_id}")
                self._emit_event(st, FSMEvent.BUY_ABORTED, ctx)
                return

            try:
                order = self.exchange.fetch_order(st.order_id, st.symbol)
            except Exception as fetch_error:
                retry_attr = "_fetch_retry_count"
                current_retry = getattr(st, retry_attr, 0) + 1
                setattr(st, retry_attr, current_retry)
                logger.warning(
                    f"[WAIT_FILL] {st.symbol} fetch_order failed (attempt {current_retry}): {fetch_error}"
                )
                if current_retry >= 3:
                    logger.error(f"[WAIT_FILL] {st.symbol} aborting after repeated fetch_order failures")
                    self._emit_event(st, FSMEvent.BUY_ABORTED, ctx)
                else:
                    time.sleep(0.1)
                return

            if not isinstance(order, dict):
                logger.error(
                    f"[WAIT_FILL] {st.symbol} received invalid order payload ({type(order).__name__}) for {st.order_id}: {order!r}"
                )
                self._emit_event(st, FSMEvent.BUY_ABORTED, ctx)
                return

            if not order:
                logger.error(f"[WAIT_FILL] {st.symbol} fetch_order returned empty payload for {st.order_id}")
                self._emit_event(st, FSMEvent.BUY_ABORTED, ctx)
                return

            if hasattr(st, "_fetch_retry_count"):
                setattr(st, "_fetch_retry_count", 0)

            # FSM PARITY FIX (LÜCKE 2): Implement wait_for_fill() timeout policy
            # - Total timeout: 30s since order placement
            # - Partial fill timeout: 10s stuck at same partial level
            WAIT_FILL_TIMEOUT_S = 30
            PARTIAL_MAX_AGE_S = 10

            # Check total timeout
            if st.order_placed_ts > 0:
                elapsed = time.time() - st.order_placed_ts
                if elapsed > WAIT_FILL_TIMEOUT_S:
                    logger.warning(f"[WAIT_FILL] {st.symbol} TIMEOUT after {elapsed:.1f}s - canceling order {st.order_id}")
                    try:
                        self.exchange.cancel_order(st.order_id, st.symbol)
                        logger.info(f"[WAIT_FILL] {st.symbol} order canceled successfully")
                    except Exception as e:
                        logger.warning(f"[WAIT_FILL] {st.symbol} cancel failed: {e}")

                    # CRITICAL FIX: Remove buy order from open_buy_orders.json after timeout
                    try:
                        self.portfolio.remove_buy_order(st.symbol)
                        logger.debug(f"[ORDER_CLEANUP] Removed {st.symbol} from open_buy_orders after timeout")
                    except Exception as cleanup_err:
                        logger.warning(f"[ORDER_CLEANUP_ERROR] {st.symbol}: Failed to remove buy order: {cleanup_err}")

                    self._emit_event(st, FSMEvent.BUY_ORDER_TIMEOUT, ctx)
                    return

            # Handle open/partial status (order still active)
            status = order.get("status")
            if status in ("open", "partial"):
                filled = order.get("filled", 0)
                amount = order.get("amount", 0)

                # Check partial fill timeout
                if status == "partial" and 0 < filled < amount:
                    # Track when partial fill started
                    if not hasattr(st, 'partial_fill_started_at'):
                        st.partial_fill_started_at = time.time()
                        st.partial_fill_qty = filled
                        logger.info(f"[WAIT_FILL] {st.symbol} PARTIAL: {filled:.6f}/{amount:.6f}")
                    else:
                        # Check if partial fill is stuck
                        partial_age = time.time() - st.partial_fill_started_at
                        if partial_age > PARTIAL_MAX_AGE_S:
                            logger.warning(f"[WAIT_FILL] {st.symbol} PARTIAL STUCK for {partial_age:.1f}s - canceling order {st.order_id}")
                            try:
                                self.exchange.cancel_order(st.order_id, st.symbol)
                                logger.info(f"[WAIT_FILL] {st.symbol} partial fill order canceled")
                            except Exception as e:
                                logger.warning(f"[WAIT_FILL] {st.symbol} cancel failed: {e}")
                            # Clear partial tracking
                            if hasattr(st, 'partial_fill_started_at'):
                                delattr(st, 'partial_fill_started_at')
                            if hasattr(st, 'partial_fill_qty'):
                                delattr(st, 'partial_fill_qty')
                            self._emit_event(st, FSMEvent.BUY_ORDER_TIMEOUT, ctx)
                            return

                # Track partial fills
                if status == "partial" and hasattr(self, 'partial_fill_handler'):
                    try:
                        self.partial_fill_handler.on_update(st.symbol, order)
                    except Exception as e:
                        logger.debug(f"Partial fill tracking failed: {e}")

                return  # Continue waiting

            if status == "closed":
                filled = order.get("filled", 0)
                avg_price = order.get("average", 0)
                # FIX: Handle None fee dict safely
                fee_dict = order.get('fee') or {}
                fee_quote = fee_dict.get('cost', 0) or 0

                if filled > 0 and avg_price > 0:
                    # Use PartialFillHandler
                    if st.fsm_data and st.fsm_data.buy_order:
                        self.partial_fill_handler.accumulate_fill(
                            order_ctx=st.fsm_data.buy_order,
                            fill_qty=filled,
                            fill_price=avg_price,
                            fill_fee=fee_quote,
                            trade_id=st.order_id
                        )
                        final_qty = st.fsm_data.buy_order.cumulative_qty
                        final_price = st.fsm_data.buy_order.avg_price
                        final_fee = st.fsm_data.buy_order.total_fees
                    else:
                        final_qty, final_price, final_fee = filled, avg_price, fee_quote

                    # P2-1: Log order fill
                    try:
                        self.jsonl.order_filled(
                            symbol=st.symbol,
                            side="buy",
                            order_id=st.order_id,
                            client_order_id=st.client_order_id,
                            filled_qty=final_qty,
                            avg_price=final_price,
                            fee=final_fee,
                            notional=final_qty * final_price,
                            decision_id=st.decision_id
                        )
                    except Exception as e:
                        logger.debug(f"Failed to log order_filled: {e}")

                    # UI EVENT: ORDER_FILLED (for dashboard)
                    try:
                        from core.logging.events import emit
                        emit("ORDER_FILLED",
                             symbol=st.symbol,
                             side="buy",
                             order_id=st.order_id,
                             filled=final_qty,
                             avg_price=final_price,
                             decision_id=st.decision_id)
                    except Exception as e:
                        logger.debug(f"Failed to emit ORDER_FILLED: {e}")

                    # CRITICAL FIX (P0): Removed portfolio_tx.begin() to prevent "Nested transactions" error
                    # Update portfolio and state directly - snapshot manager handles persistence
                    st.amount = final_qty
                    st.entry_price = final_price
                    st.entry_ts = time.time()
                    st.entry_fee_per_unit = (final_fee / final_qty) if final_qty > 0 else 0

                    self.portfolio.add_held_asset(st.symbol, {
                        "amount": final_qty,
                        "entry_price": final_price,
                        "buy_fee_quote_per_unit": st.entry_fee_per_unit,
                        "buy_price": final_price
                    })

                    # CRITICAL FIX: Commit reserved budget after successful fill
                    # Budget was reserved in _process_place_buy, now commit it
                    actual_cost = final_qty * final_price
                    try:
                        self.portfolio.commit_budget(
                            quote_amount=actual_cost,
                            symbol=st.symbol,
                            order_id=st.order_id,
                            intent_id=st.client_order_id
                        )
                        st.reserved_budget = 0.0  # Clear tracked reservation after commit
                        logger.info(f"[BUDGET_COMMITTED] {st.symbol}: {actual_cost:.2f} USDT committed (fill: {final_qty:.6f}@{final_price:.8f})")
                    except Exception as commit_error:
                        logger.error(f"[BUDGET_COMMIT_ERROR] {st.symbol}: Failed to commit budget: {commit_error}")

                    self.pnl_service.record_fill(
                        symbol=st.symbol,
                        side="buy",
                        quantity=final_qty,
                        avg_price=final_price,
                        fee_quote=final_fee,
                        order_id=st.order_id,
                        client_order_id=st.client_order_id
                    )

                    # Save snapshot after successful fill
                    try:
                        self.snapshot_manager.save_snapshot(st.symbol, st)
                    except Exception as snap_err:
                        logger.warning(f"[SNAPSHOT_ERROR] {st.symbol}: Failed to save snapshot: {snap_err}")

                    # P2-1: Log trade open
                    try:
                        self.jsonl.trade_open(
                            symbol=st.symbol,
                            side="buy",
                            amount=final_qty,
                            entry_price=final_price,
                            notional=final_qty * final_price,
                            fee=final_fee,
                            decision_id=st.decision_id,
                            entry_ts=st.entry_ts
                        )
                    except Exception as e:
                        logger.debug(f"Failed to log trade_open: {e}")

                    # P2-3: Log fill step and end buy flow
                    try:
                        self.buy_flow.step(4, "Wait for Fill", "SUCCESS", f"{final_qty:.6f} @ {final_price:.4f}")
                        duration_ms = (time.time() - self.buy_flow.start_time) * 1000 if self.buy_flow.start_time else 0
                        self.buy_flow.end_evaluation("BUY_COMPLETED", duration_ms)
                    except Exception as e:
                        logger.debug(f"Failed to log buy_flow completion: {e}")

                    # UI EVENT: POSITION_OPENED (for dashboard)
                    try:
                        from core.logging.events import emit
                        emit("POSITION_OPENED",
                             symbol=st.symbol,
                             qty=final_qty,
                             price=final_price,
                             notional=final_qty * final_price,
                             decision_id=st.decision_id)
                    except Exception as e:
                        logger.debug(f"Failed to emit POSITION_OPENED: {e}")

                    # DASHBOARD EVENT: Show successful buy in terminal
                    try:
                        from ui.dashboard import emit_dashboard_event
                        notional = final_qty * final_price
                        message = f"🟦 {st.symbol} BOUGHT @ ${final_price:.6f} | Qty: {final_qty:.4f} | Cost: ${notional:.2f}"
                        emit_dashboard_event("TRADE_ENTRY", message)
                    except Exception as e:
                        logger.debug(f"Failed to emit dashboard event: {e}")

                    self._emit_event(st, FSMEvent.BUY_ORDER_FILLED, ctx)

                    # CRITICAL FIX: Remove buy order from open_buy_orders.json
                    # This was causing "DUPLICATE_BLOCKED" errors after cooldown
                    # because the system thought there was still a pending order
                    try:
                        self.portfolio.remove_buy_order(st.symbol)
                        logger.debug(f"[ORDER_CLEANUP] Removed {st.symbol} from open_buy_orders after fill")
                    except Exception as cleanup_err:
                        logger.warning(f"[ORDER_CLEANUP_ERROR] {st.symbol}: Failed to remove buy order: {cleanup_err}")

                    # Telegram notification
                    if self.telegram:
                        try:
                            self.telegram.send_message(
                                f"🛒 BUY {st.symbol} @{final_price:.6f} x{final_qty:.6f}"
                            )
                        except Exception:
                            pass

            elif order.get("status") == "canceled":
                # UI EVENT: ORDER_CANCELED (for dashboard)
                try:
                    from core.logging.events import emit
                    emit("ORDER_CANCELED",
                         symbol=st.symbol,
                         side="buy",
                         order_id=st.order_id,
                         reason="exchange_canceled",
                         decision_id=st.decision_id)
                except Exception as e:
                    logger.debug(f"Failed to emit ORDER_CANCELED: {e}")

                # FIX: Set cooldown after canceled order to prevent immediate re-entry loop
                cooldown_duration = getattr(config, 'SYMBOL_COOLDOWN_AFTER_FAILED_ORDER_S', 60)
                self.cooldown_manager.set(st.symbol, cooldown_duration)
                logger.info(f"Symbol cooldown set for {st.symbol}: {cooldown_duration}s after order canceled")

                # CRITICAL FIX (P0): Release reserved budget on order cancellation
                if hasattr(st, 'reserved_budget') and st.reserved_budget:
                    _release_budget_with_retry(
                        self.portfolio,
                        st.reserved_budget,
                        st.symbol,
                        "order_canceled"
                    )
                    st.reserved_budget = 0.0

                # CRITICAL FIX: Remove buy order from open_buy_orders.json after cancellation
                try:
                    self.portfolio.remove_buy_order(st.symbol)
                    logger.debug(f"[ORDER_CLEANUP] Removed {st.symbol} from open_buy_orders after cancel")
                except Exception as cleanup_err:
                    logger.warning(f"[ORDER_CLEANUP_ERROR] {st.symbol}: Failed to remove buy order: {cleanup_err}")

                self._emit_event(st, FSMEvent.ORDER_CANCELED, ctx)

        except Exception as e:
            logger.error(f"Wait fill error {st.symbol}: {e}")

            # CRITICAL FIX (P0): Release reserved budget on error in WAIT_FILL
            # Budget was reserved in PLACE_BUY but not committed due to error
            if hasattr(st, 'reserved_budget') and st.reserved_budget:
                _release_budget_with_retry(
                    self.portfolio,
                    st.reserved_budget,
                    st.symbol,
                    "wait_fill_error"
                )
                st.reserved_budget = 0.0

            self._emit_event(st, FSMEvent.ERROR_OCCURRED, ctx)

    def _process_position(self, st: CoinState, ctx: EventContext):
        """POSITION: Monitor position, update trailing, check exits periodically."""
        st.current_price = ctx.price

        # Update trailing stop
        if getattr(config, 'USE_TRAILING_STOP', False):
            if ctx.price > st.peak_price:
                st.peak_price = ctx.price
                st.trailing_trigger = st.peak_price * getattr(config, 'TRAILING_DISTANCE_PCT', 0.99)

        # P0-5: Dynamic TP/SL switching based on PnL
        try:
            self.tp_sl_manager.rebalance_protection(
                symbol=st.symbol,
                coin_state=st,
                current_price=ctx.price,
                order_service=self.order_service
            )
        except Exception as e:
            logger.debug(f"Dynamic TP/SL rebalance failed for {st.symbol}: {e}")

        # Update unrealized PnL
        self.pnl_service.set_unrealized_position(
            symbol=st.symbol,
            quantity=st.amount,
            avg_entry_price=st.entry_price,
            current_price=ctx.price,
            entry_fee_per_unit=st.entry_fee_per_unit
        )

        # P1-4: Check exits every cycle (was: every 4 cycles) for faster detection
        self._emit_event(st, FSMEvent.TICK_RECEIVED, ctx)

    def _process_exit_eval(self, st: CoinState, ctx: EventContext):
        """EXIT_EVAL: Check TP, SL, trailing, timeout using ExitEngine priority logic."""
        # P0-4: Use ExitEngine for prioritized exit evaluation
        try:
            # CRITICAL FIX: Use correct FSMExitEngine API
            # choose_exit() evaluates all rules internally and returns highest priority
            exit_decision = self.exit_engine.choose_exit(
                coin_state=st,
                current_price=ctx.price,
                current_time=time.time()
            )

            if exit_decision:
                st.exit_reason = exit_decision.reason
                if st.fsm_data:
                    st.fsm_data.exit_signal = exit_decision.reason
                    st.fsm_data.exit_detected_at = time.time()
                    st.fsm_data.exit_price = exit_decision.price  # Fixed: was exit_price, should be price

                # Emit appropriate event based on exit rule
                if exit_decision.rule == "HARD_SL":
                    self._emit_event(st, FSMEvent.EXIT_SIGNAL_SL, ctx)
                elif exit_decision.rule == "HARD_TP":
                    self._emit_event(st, FSMEvent.EXIT_SIGNAL_TP, ctx)
                elif exit_decision.rule == "TRAILING":
                    self._emit_event(st, FSMEvent.EXIT_SIGNAL_TRAILING, ctx)
                elif exit_decision.rule == "TIME":  # Fixed: was TIME_EXIT, should be TIME
                    self._emit_event(st, FSMEvent.EXIT_SIGNAL_TIMEOUT, ctx)
                else:
                    logger.warning(f"Unknown exit rule: {exit_decision.rule}")
                    self._emit_event(st, FSMEvent.EXIT_SIGNAL_TIMEOUT, ctx)

                logger.info(f"{st.symbol}: Exit triggered - {exit_decision.rule} (priority={exit_decision.priority})")
                return

        except Exception as e:
            logger.error(f"ExitEngine evaluation failed for {st.symbol}: {e}")
            # Fallback to simple SL check
            if ctx.price <= st.sl_px:
                st.exit_reason = "STOP_LOSS_FALLBACK"
                self._emit_event(st, FSMEvent.EXIT_SIGNAL_SL, ctx)
                return

        # No exit - return to POSITION
        # DEBUG: Log why no exit was triggered
        logger.debug(
            f"{st.symbol}: No exit @ ${ctx.price:.6f} | "
            f"Entry: ${st.entry_price:.6f}, TP: ${st.tp_px:.6f}, SL: ${st.sl_px:.6f}, "
            f"Peak: ${st.peak_price:.6f}, Trailing Trigger: ${st.trailing_trigger:.6f}"
        )
        self._emit_event(st, FSMEvent.NO_EXIT_SIGNAL, ctx)

    def _process_place_sell(self, st: CoinState, ctx: EventContext):
        """PLACE_SELL: Place sell order (IOC)."""
        try:
            client_order_id = new_client_order_id(st.decision_id, "sell")

            order = self.order_service.place_limit_ioc(
                symbol=st.symbol,
                side="sell",
                amount=st.amount,
                price=ctx.price,
                client_order_id=client_order_id
            )

            if order and order.get("id"):
                st.order_id = order["id"]
                st.client_order_id = client_order_id
                st.order_placed_ts = time.time()

                # UI EVENT: ORDER_SUBMITTED (for dashboard - sell side)
                try:
                    from core.logging.events import emit
                    emit("ORDER_SUBMITTED",
                         symbol=st.symbol,
                         side="sell",
                         order_id=st.order_id,
                         price=ctx.price,
                         amount=st.amount,
                         decision_id=st.decision_id)
                except Exception as e:
                    logger.debug(f"Failed to emit ORDER_SUBMITTED (sell): {e}")

                if st.fsm_data:
                    st.fsm_data.sell_order = OrderContext(
                        order_id=order["id"],
                        client_order_id=client_order_id,
                        placed_at=time.time(),
                        target_qty=st.amount,
                        status="pending"
                    )

                # P2-1: Log sell order placement
                try:
                    self.jsonl.order_sent(
                        symbol=st.symbol,
                        side="sell",
                        order_id=order["id"],
                        client_order_id=client_order_id,
                        amount=st.amount,
                        price=ctx.price,
                        order_type="limit_ioc",
                        decision_id=st.decision_id,
                        exit_reason=ctx.data.get('exit_reason', 'unknown')
                    )
                except Exception as e:
                    logger.debug(f"Failed to log sell order_sent: {e}")

                self._emit_event(st, FSMEvent.SELL_ORDER_PLACED, ctx)
                self.stats["total_sells"] += 1
            else:
                st.retry_count += 1
                if st.retry_count < 3:
                    self._emit_event(st, FSMEvent.ORDER_PLACEMENT_FAILED, ctx)
                else:
                    self._emit_event(st, FSMEvent.ERROR_OCCURRED, ctx)

        except Exception as e:
            logger.error(f"Place sell error {st.symbol}: {e}")

            # P2-2: Track error for adaptive logging
            try:
                track_error("sell_order_error", symbol=st.symbol, details=str(e))
            except Exception:
                pass

            self._emit_event(st, FSMEvent.ERROR_OCCURRED, ctx)

    def _process_wait_sell_fill(self, st: CoinState, ctx: EventContext):
        """WAIT_SELL_FILL: Poll sell order status."""
        # Note: Timeouts handled by _tick_timeouts
        try:
            order = self.exchange.fetch_order(st.order_id, st.symbol)

            if order.get("status") == "closed":
                filled = order.get("filled", 0)
                if filled >= st.amount * 0.95:
                    # Extract order data
                    avg_exit_price = order.get("average", 0)
                    fee_dict = order.get('fee') or {}
                    exit_fee = fee_dict.get('cost', 0) or 0

                    # UI EVENT: ORDER_FILLED (for dashboard - sell side)
                    try:
                        from core.logging.events import emit
                        emit("ORDER_FILLED",
                             symbol=st.symbol,
                             side="sell",
                             order_id=st.order_id,
                             filled=filled,
                             avg_price=avg_exit_price,
                             decision_id=st.decision_id)
                    except Exception as e:
                        logger.debug(f"Failed to emit ORDER_FILLED (sell): {e}")

                    # CRITICAL FIX: Pass order data to action via ctx
                    # This ensures action_close_position has correct sell prices for fallback
                    ctx.filled_qty = filled
                    ctx.avg_price = avg_exit_price
                    ctx.data['fee'] = exit_fee  # Use ctx.data for fee (EventContext doesn't have fee attribute)

                    self._emit_event(st, FSMEvent.SELL_ORDER_FILLED, ctx)
                else:
                    # Partial fill - retry
                    remaining = st.amount - filled
                    if remaining > 0.0001:
                        st.retry_count += 1
                        st.amount = remaining
                        self._emit_event(st, FSMEvent.PARTIAL_FILL_RETRY, ctx)
                    else:
                        self._emit_event(st, FSMEvent.SELL_ORDER_FILLED, ctx)

        except Exception as e:
            logger.error(f"Wait sell fill error {st.symbol}: {e}")
            self._emit_event(st, FSMEvent.ERROR_OCCURRED, ctx)

    def _process_post_trade(self, st: CoinState, ctx: EventContext):
        """POST_TRADE: Record PnL, cleanup, transition to COOLDOWN."""

        # CRITICAL FIX: Prevent infinite retry loop when order not found
        # If we've tried 3 times and failed, use data from FSM state instead of Exchange
        use_fallback_data = st.retry_count >= 3

        try:
            if use_fallback_data:
                # Use sell_order data from FSM state (already filled in WAIT_SELL_FILL)
                logger.warning(f"{st.symbol}: Using fallback data after {st.retry_count} retries (order likely archived)")
                if st.fsm_data and st.fsm_data.sell_order:
                    filled = st.fsm_data.sell_order.cumulative_qty
                    avg_exit_price = st.fsm_data.sell_order.avg_price
                    exit_fee = st.fsm_data.sell_order.total_fees
                else:
                    # Ultimate fallback: use position data
                    filled = st.amount
                    avg_exit_price = st.current_price
                    exit_fee = 0.0
                    logger.warning(f"{st.symbol}: No sell_order data, using position data as fallback")
            else:
                # Normal path: fetch from Exchange
                order = self.exchange.fetch_order(st.order_id, st.symbol)
                filled = order.get("filled", 0)
                avg_exit_price = order.get("average", 0)
                # FIX: Handle None fee dict safely
                fee_dict = order.get('fee') or {}
                exit_fee = fee_dict.get('cost', 0) or 0

            # Record PnL
            realized_pnl = self.pnl_service.record_fill(
                symbol=st.symbol,
                side="sell",
                quantity=filled,
                avg_price=avg_exit_price,
                fee_quote=exit_fee,
                entry_price=st.entry_price,
                order_id=st.order_id,
                client_order_id=st.client_order_id,
                reason=st.exit_reason
            )

            # Immediately export closed positions to disk (crash-safe)
            try:
                import os
                import config as cfg
                filepath = os.path.join(cfg.BASE_DIR, "closed_positions.json")
                self.pnl_service.export_closed_positions(filepath)
            except Exception as e:
                self.logger.debug(f"Failed to export closed positions: {e}")

            # P2-1: Log sell order fill
            try:
                self.jsonl.order_filled(
                    symbol=st.symbol,
                    side="sell",
                    order_id=st.order_id,
                    client_order_id=st.client_order_id,
                    filled_qty=filled,
                    avg_price=avg_exit_price,
                    fee=exit_fee,
                    notional=filled * avg_exit_price,
                    decision_id=st.decision_id
                )
            except Exception as e:
                logger.debug(f"Failed to log sell order_filled: {e}")

            # P2-1: Log trade close
            try:
                self.jsonl.trade_close(
                    symbol=st.symbol,
                    side="sell",
                    amount=filled,
                    exit_price=avg_exit_price,
                    entry_price=st.entry_price,
                    pnl=realized_pnl if realized_pnl is not None else 0.0,
                    pnl_pct=((avg_exit_price - st.entry_price) / st.entry_price * 100) if st.entry_price > 0 else 0.0,
                    exit_reason=st.exit_reason,
                    decision_id=st.decision_id,
                    duration_seconds=time.time() - st.entry_ts if st.entry_ts > 0 else 0.0
                )
            except Exception as e:
                logger.debug(f"Failed to log trade_close: {e}")

            # P2-2: Notify trade completed for adaptive logging (enables enhanced logging period)
            try:
                notify_trade_completed(st.symbol, "sell")
            except Exception as e:
                logger.debug(f"Failed to notify trade completed: {e}")

            self.pnl_service.remove_unrealized_position(st.symbol)
            self.portfolio.remove_held_asset(st.symbol)

            # CRITICAL FIX: Release budget after successful sell
            # Return sale proceeds to available budget
            try:
                sale_proceeds = filled * avg_exit_price
                _release_budget_with_retry(
                    self.portfolio,
                    quote_amount=sale_proceeds,
                    symbol=st.symbol,
                    reason="position_sold"
                )
                logger.debug(f"{st.symbol}: Released {sale_proceeds:.2f} USDT after sell")
            except Exception as e:
                logger.error(f"{st.symbol}: Failed to release budget after sell: {e}")

            # UI EVENT: POSITION_CLOSED (for dashboard)
            try:
                from core.logging.events import emit
                emit("POSITION_CLOSED",
                     symbol=st.symbol,
                     qty_closed=filled,
                     exit_price=avg_exit_price,
                     entry_price=st.entry_price,
                     realized_pnl=realized_pnl if realized_pnl is not None else 0.0,
                     exit_reason=st.exit_reason,
                     decision_id=st.decision_id)
            except Exception as e:
                logger.debug(f"Failed to emit POSITION_CLOSED: {e}")

            # DASHBOARD EVENT: Show successful exit in terminal
            try:
                from ui.dashboard import emit_dashboard_event
                # Use net profit (realized_pnl already includes fees deducted)
                net_pnl = realized_pnl if realized_pnl is not None else 0.0

                # Calculate percentage based on net profit vs invested capital
                invested_capital = st.entry_price * filled if st.entry_price > 0 and filled > 0 else 0
                net_pnl_pct = (net_pnl / invested_capital * 100) if invested_capital > 0 else 0.0

                pnl_emoji = "🟢" if net_pnl >= 0 else "🔴"
                exit_reason_short = (st.exit_reason or "unknown")[:20]  # Truncate long reasons

                message = f"{pnl_emoji} {st.symbol} SOLD @ ${avg_exit_price:.6f} | Net PnL: {net_pnl:+.2f} ({net_pnl_pct:+.2f}%) | {exit_reason_short}"
                emit_dashboard_event("TRADE_EXIT", message)
            except Exception as e:
                logger.debug(f"Failed to emit dashboard event: {e}")

            # Telegram notification
            if self.telegram and realized_pnl is not None:
                try:
                    pnl_emoji = "✅" if realized_pnl > 0 else "❌"
                    self.telegram.send_message(
                        f"{pnl_emoji} SELL {st.symbol} @{avg_exit_price:.6f} PnL: {realized_pnl:+.2f}"
                    )
                except Exception:
                    pass

            # Set cooldown
            cooldown_minutes = getattr(config, 'SYMBOL_COOLDOWN_MINUTES', 15)
            st.cooldown_until = time.time() + (cooldown_minutes * 60)
            if st.fsm_data:
                st.fsm_data.cooldown_started_at = time.time()

            # Clear position data
            st.amount = 0.0
            st.entry_price = 0.0
            st.order_id = None
            st.retry_count = 0

            self._emit_event(st, FSMEvent.TRADE_COMPLETE, ctx)

        except Exception as e:
            error_msg = str(e)

            # CRITICAL FIX: Handle "Order does not exist" gracefully
            # This happens when Exchange archives orders quickly after fill
            if "does not exist" in error_msg.lower() or "-2013" in error_msg:
                st.retry_count += 1

                if st.retry_count >= 3:
                    # After 3 retries, force completion with cleanup
                    logger.warning(f"{st.symbol}: Order not found after {st.retry_count} attempts, forcing completion")
                    st.amount = 0.0
                    st.entry_price = 0.0
                    st.order_id = None
                    st.retry_count = 0
                    cooldown_minutes = getattr(config, 'SYMBOL_COOLDOWN_MINUTES', 15)
                    st.cooldown_until = time.time() + (cooldown_minutes * 60)

                    # Force transition to COOLDOWN (bypassing POST_TRADE -> TRADE_COMPLETE issue)
                    st.phase = Phase.COOLDOWN
                    if st.fsm_data:
                        st.fsm_data.cooldown_started_at = time.time()

                    logger.info(f"{st.symbol}: Forced transition to COOLDOWN after order fetch failures")
                    return  # Exit without emitting event to avoid "Invalid transition" error
                else:
                    # Retry on next cycle
                    logger.debug(f"{st.symbol}: Order not found (attempt {st.retry_count}/3), will retry")
                    return  # Will retry on next main loop iteration
            else:
                # Other errors: log and force cleanup
                logger.error(f"Post trade error {st.symbol}: {e}")
                st.amount = 0.0
                st.retry_count = 0
                st.cooldown_until = time.time() + (15 * 60)

                # Force transition to COOLDOWN
                st.phase = Phase.COOLDOWN
                if st.fsm_data:
                    st.fsm_data.cooldown_started_at = time.time()
                return

    def _process_error(self, st: CoinState, ctx: EventContext):
        """ERROR: Exponential backoff recovery."""
        backoff_seconds = min(300, 10 * (2 ** min(st.error_count, 5)))

        if st.ts_ms == 0 or (time.time() - st.ts_ms / 1000.0) > backoff_seconds:
            # Cleanup
            if st.has_position():
                try:
                    self.portfolio.remove_held_asset(st.symbol)
                    self.pnl_service.remove_unrealized_position(st.symbol)
                except Exception:
                    pass

            st.amount = 0.0
            st.entry_price = 0.0
            st.order_id = None
            st.phase = Phase.IDLE  # Direct assignment for error recovery

    # ========== MAIN LOOP ==========

    def start(self):
        """Start FSM Trading Engine."""
        import sys
        _debug_write(f"\n[FSM_ENGINE.START] ENTRY - running={self.running}\n")
        sys.stdout.flush()

        if self.running:
            logger.warning("Engine already running")
            _debug_write("[FSM_ENGINE.START] Already running, returning\n")
            sys.stdout.flush()
            return

        _debug_write("[FSM_ENGINE.START] Starting FSM Trading Engine...\n")
        sys.stdout.flush()
        logger.info("Starting FSM Trading Engine...")
        self.running = True

        # Start market data loop for snapshot generation
        _debug_write("[FSM_ENGINE.START] Starting market data...\n")
        sys.stdout.flush()
        self.market_data.start()

        # CRITICAL FIX: Wait for market data to populate cache before starting FSM loop
        # This prevents race condition where FSM tries to process symbols before prices are available
        # DEBUGGING FIX (P2): Increased from 3s to 10s to ensure cache is fully populated
        _debug_write("[FSM_ENGINE.START] Waiting for market data warmup (10s)...\n")
        sys.stdout.flush()
        time.sleep(10.0)
        _debug_write("[FSM_ENGINE.START] Market data warmup complete\n")
        sys.stdout.flush()

        _debug_write("[FSM_ENGINE.START] Starting main thread...\n")
        sys.stdout.flush()
        self.main_thread = threading.Thread(target=self._main_loop, daemon=False, name="FSM-Engine-Main")
        self.main_thread.start()

        _debug_write("[FSM_ENGINE.START] FSM Trading Engine started\n")
        sys.stdout.flush()
        logger.info("FSM Trading Engine started")

    def stop(self):
        """Stop FSM Trading Engine."""
        logger.info("Stopping FSM Trading Engine...")
        self.running = False

        if self.main_thread and self.main_thread.is_alive():
            self.main_thread.join(timeout=10.0)

        self.phase_logger.close()
        logger.info("FSM Trading Engine stopped")

    def _main_loop(self):
        """Main engine loop."""
        import sys
        _debug_write(f"\n[FSM_ENGINE._main_loop] ENTRY - running={self.running}\n")
        sys.stdout.flush()
        logger.info("FSM main loop started")

        try:
            _debug_write(f"[FSM_ENGINE._main_loop] Starting main loop (running={self.running})\n")
            sys.stdout.flush()
            while self.running:
                _debug_write(f"[FSM_ENGINE._main_loop] Cycle #{self.cycle_count + 1} starting...\n")
                sys.stdout.flush()
                cycle_start = time.time()
                self.cycle_count += 1

                # Tick timeouts first (cooldowns, order timeouts)
                _debug_write(f"[FSM_ENGINE._main_loop] Ticking timeouts...\n")
                sys.stdout.flush()
                self._tick_timeouts()

                # CRITICAL FIX: Active drop scanner (mirrors Legacy engine behavior)
                # Runs every 6 cycles (~3 seconds) to actively scan for buy signals
                # This is what Legacy engine does - FSM was missing this active scan!
                _debug_write(f"[DEBUG] Cycle {self.cycle_count} mod 6 = {self.cycle_count % 6}\n")
                sys.stdout.flush()
                if self.cycle_count % 6 == 0:
                    _debug_write(f"[FSM_ENGINE._main_loop] ⚡ ACTIVE SCANNER TRIGGERED (Cycle #{self.cycle_count})\n")
                    sys.stdout.flush()
                    try:
                        self._scan_for_drops()
                    except Exception as e:
                        logger.error(f"[ACTIVE_SCAN] Scanner failed: {e}", exc_info=True)
                        _debug_write(f"[ERROR] Scanner failed: {e}\n")
                        sys.stdout.flush()

                # P1-2: Reconciler sync every 60 cycles (~2 minutes)
                if self.cycle_count % 60 == 0:
                    try:
                        report = self.reconciler.sync(self.states)
                        if report.desyncs_found > 0:
                            logger.warning(f"Reconciler found {report.desyncs_found} desyncs, {report.corrections_made} corrections made")
                    except Exception as e:
                        logger.debug(f"Reconciler sync failed: {e}")

                # Heartbeat every 10 cycles
                if self.cycle_count % 10 == 0:
                    active = len([s for s in self.states.values() if s.phase not in [Phase.IDLE, Phase.WARMUP]])
                    positions = len([s for s in self.states.values() if s.phase == Phase.POSITION])
                    logger.info(f"💓 FSM Cycle #{self.cycle_count} - {len(self.states)} symbols | Active: {active} | Positions: {positions}")

                # Process all symbols with event-based FSM
                _debug_write(f"[FSM_ENGINE._main_loop] Processing {len(self.watchlist)} symbols...\n")
                sys.stdout.flush()
                for i, symbol in enumerate(self.watchlist.keys()):
                    try:
                        if i == 0:  # Only log first symbol to reduce noise
                            _debug_write(f"[FSM_ENGINE._main_loop] Building context for {symbol}...\n")
                            sys.stdout.flush()
                        md = self._build_context(symbol)
                        if i == 0:
                            _debug_write(f"[FSM_ENGINE._main_loop] Context built with price={md.get('price', 'MISSING')}, processing {symbol}...\n")
                            sys.stdout.flush()
                        self._process_symbol(symbol, md)
                    except Exception as e:
                        logger.error(f"Error processing {symbol}: {e}")
                        self.stats["total_errors"] += 1
                        _debug_write(f"[FSM_ENGINE._main_loop] ERROR processing {symbol}: {e}\n")
                        sys.stdout.flush()

                # Update stuck metrics every 5 cycles
                if self.cycle_count % 5 == 0:
                    self._update_stuck_metrics()

                self.stats["total_cycles"] += 1

                # Rate limiting (500ms target)
                cycle_time = time.time() - cycle_start
                sleep_time = max(0.0, 0.5 - cycle_time)
                if sleep_time > 0:
                    time.sleep(sleep_time)

        except Exception as fatal_e:
            logger.error(f"Fatal error in FSM main loop: {fatal_e}", exc_info=True)
            raise
        finally:
            logger.info("FSM main loop ended")

    def _build_context(self, symbol: str) -> Dict[str, Any]:
        """Build context dict with current market data."""
        import sys
        ctx = {"symbol": symbol, "timestamp": time.time()}

        try:
            # Get current price from market data service
            if symbol == list(self.watchlist.keys())[0]:  # Only log first symbol
                _debug_write(f"[FSM_ENGINE._build_context] Getting price for {symbol}...\n")
                sys.stdout.flush()
            price = self.market_data.get_price(symbol)
            if symbol == list(self.watchlist.keys())[0]:
                _debug_write(f"[FSM_ENGINE._build_context] get_price() returned: {price} (type: {type(price)})\n")
                sys.stdout.flush()

            # CRITICAL FIX: Fallback to direct exchange fetch if cache returns 0.0
            # This handles race conditions where MD loop hasn't populated cache yet
            if not price or price <= 0:
                if symbol == list(self.watchlist.keys())[0]:
                    _debug_write(f"[FSM_ENGINE._build_context] Price is 0, fetching directly from exchange...\n")
                    sys.stdout.flush()
                try:
                    ticker_direct = self.exchange_adapter.fetch_ticker(symbol)
                    if ticker_direct:
                        price = float(ticker_direct.get('last', 0) or 0)
                        if symbol == list(self.watchlist.keys())[0]:
                            _debug_write(f"[FSM_ENGINE._build_context] Direct fetch got price: {price}\n")
                            sys.stdout.flush()
                except Exception as ex:
                    if symbol == list(self.watchlist.keys())[0]:
                        _debug_write(f"[FSM_ENGINE._build_context] Direct fetch failed: {ex}\n")
                        sys.stdout.flush()
                    pass

            ctx["price"] = price if price else 0.0

            # Get orderbook data
            if symbol == list(self.watchlist.keys())[0]:
                _debug_write(f"[FSM_ENGINE._build_context] Getting ticker for {symbol}...\n")
                sys.stdout.flush()
            ticker = self.market_data.get_ticker(symbol)  # Returns TickerData directly, not tuple

            # STALENESS CHECK: Force fresh fetch if ticker is too old (> 10 seconds)
            if ticker and hasattr(ticker, 'timestamp') and ticker.timestamp:
                age_seconds = (time.time() * 1000 - ticker.timestamp) / 1000.0
                if age_seconds > 10.0:
                    logger.warning(
                        f"{symbol}: Ticker is {age_seconds:.1f}s old - may use stale data",
                        extra={'event_type': 'STALE_TICKER_DETECTED', 'age_s': age_seconds, 'symbol': symbol}
                    )
                    # Note: With Fix 1, get_price() now rejects STALE cache and fetches fresh

            if ticker:
                ctx["bid"] = ticker.bid if hasattr(ticker, 'bid') else 0.0
                ctx["ask"] = ticker.ask if hasattr(ticker, 'ask') else 0.0
                ctx["volume"] = ticker.volume if hasattr(ticker, 'volume') else 0.0

        except Exception as e:
            if symbol == list(self.watchlist.keys())[0]:
                _debug_write(f"[FSM_ENGINE._build_context] EXCEPTION: {e}\n")
                sys.stdout.flush()
            logger.debug(f"Context build error for {symbol}: {e}")
            # CRITICAL BUG FIX: Don't overwrite valid price on ticker fetch errors!
            # Only set price=0.0 if price wasn't already set
            if "price" not in ctx or ctx["price"] == 0:
                ctx["price"] = 0.0

        if symbol == list(self.watchlist.keys())[0]:
            _debug_write(f"[FSM_ENGINE._build_context] Returning ctx with price={ctx.get('price', 'MISSING')}\n")
            sys.stdout.flush()
        return ctx

    def _scan_for_drops(self):
        """
        CRITICAL FIX: Active drop scanner - mirrors Legacy engine behavior.

        Legacy engine calls _evaluate_buy_opportunities() every tick, which scans
        ALL symbols for drops regardless of slots/phases. FSM mode was missing this!

        This method actively scans all watchlist symbols for buy signals,
        independent of their current FSM phase. If a drop is detected, it forces
        the symbol into ENTRY_EVAL phase by emitting SLOT_AVAILABLE event.

        Called every 6 cycles (3 seconds) in main loop.
        """
        import sys
        _debug_write(f"[ACTIVE_SCAN] _scan_for_drops() ENTRY\n")
        sys.stdout.flush()

        # Check if we have slots available
        max_trades = getattr(config, 'MAX_TRADES', 10)

        # CRITICAL FIX: Count ALL active phases (same as in _process_idle)
        active_phases = {Phase.WAIT_FILL, Phase.POSITION, Phase.EXIT_EVAL,
                        Phase.PLACE_SELL, Phase.WAIT_SELL_FILL, Phase.POST_TRADE}
        active_positions = sum(1 for s in self.states.values() if s.phase in active_phases)

        _debug_write(f"[ACTIVE_SCAN] Slot check: active_positions={active_positions}, max_trades={max_trades}\n")
        sys.stdout.flush()

        if active_positions >= max_trades:
            _debug_write(f"[ACTIVE_SCAN] EARLY RETURN - max positions reached\n")
            sys.stdout.flush()
            logger.debug(f"[ACTIVE_SCAN] Skipping scan - max positions reached ({active_positions}/{max_trades})")
            return

        # Diagnostic: Log snapshot store status
        snapshot_count = len(self.drop_snapshot_store)
        _debug_write(f"[ACTIVE_SCAN] Snapshot store size: {snapshot_count}\n")
        sys.stdout.flush()

        if snapshot_count == 0:
            _debug_write(f"[ACTIVE_SCAN] EARLY RETURN - snapshot store EMPTY\n")
            sys.stdout.flush()
            logger.warning(f"[ACTIVE_SCAN] Snapshot store is EMPTY - no market data available!")
            return

        _debug_write(f"[ACTIVE_SCAN] Starting scan of {len(self.watchlist)} symbols\n")
        sys.stdout.flush()
        logger.debug(f"[ACTIVE_SCAN] Scanning {len(self.watchlist)} symbols (snapshots: {snapshot_count}, positions: {active_positions}/{max_trades})")

        drops_detected = 0
        symbols_scanned = 0

        # Scan all watchlist symbols
        for symbol in self.watchlist.keys():
            st = self.states.get(symbol)

            # Skip if already in position or actively evaluating
            if st and st.phase not in [Phase.IDLE, Phase.WARMUP, Phase.COOLDOWN]:
                continue

            # Skip if in cooldown
            if st and st.in_cooldown():
                continue

            # Get current price
            price = self.market_data.get_price(symbol)
            if not price or price <= 0:
                continue

            symbols_scanned += 1

            # ACTIVE DROP CHECK - this is what Legacy does every tick!
            try:
                buy_triggered, signal_context = self.buy_signal_service.evaluate_buy_signal(
                    symbol, price, self.drop_snapshot_store
                )

                if buy_triggered:
                    drops_detected += 1
                    mode = signal_context.get('mode', '?')
                    drop_pct = signal_context.get('drop_pct', 0) * 100

                    logger.info(f"[ACTIVE_SCAN] 🎯 DROP DETECTED: {symbol} @ {price} | Mode={mode}, Drop={drop_pct:.2f}%")

                    # Initialize state if needed
                    if not st:
                        st = CoinState(symbol=symbol)
                        st.phase = Phase.IDLE
                        self.states[symbol] = st

                    # Assign decision_id for idempotency
                    st.decision_id = new_decision_id()

                    # Store signal info
                    st.signal = f"DROP_MODE_{mode}"
                    if st.fsm_data:
                        st.fsm_data.signal_detected_at = time.time()
                        st.fsm_data.signal_type = st.signal

                    # Force transition to ENTRY_EVAL by emitting SLOT_AVAILABLE
                    ctx = EventContext(
                        event=FSMEvent.SLOT_AVAILABLE,
                        symbol=symbol,
                        timestamp=time.time(),
                        price=price,
                        data={
                            "price": price,
                            "signal_context": signal_context,
                            "triggered_by": "active_scan"
                        }
                    )
                    self._emit_event(st, FSMEvent.SLOT_AVAILABLE, ctx)

            except Exception as e:
                logger.debug(f"[ACTIVE_SCAN] Error scanning {symbol}: {e}")

        if drops_detected > 0:
            logger.info(f"[ACTIVE_SCAN] Scan complete: {drops_detected} drops detected (scanned {symbols_scanned} symbols)")

    def _update_stuck_metrics(self):
        """Update Prometheus stuck metrics."""
        for st in self.states.values():
            if st.phase not in [Phase.IDLE, Phase.WARMUP, Phase.COOLDOWN]:
                update_stuck_metric(st)

    # ========== PUBLIC API ==========

    def get_states(self) -> Dict[str, CoinState]:
        """Get all states (for status table)."""
        return self.states

    def get_positions(self) -> Dict[str, CoinState]:
        """Get active positions (POSITION or EXIT_EVAL phases)."""
        from core.fsm.phases import POSITION_PHASES
        return {symbol: st for symbol, st in self.states.items() if st.phase in POSITION_PHASES}

    def get_statistics(self) -> Dict[str, Any]:
        """Get engine statistics."""
        return {
            **self.stats,
            "total_symbols": len(self.states),
            "active_positions": len([s for s in self.states.values() if s.phase == Phase.POSITION]),
            "uptime_seconds": time.time() - self.stats["start_time"],
        }

    def is_running(self) -> bool:
        """Check if engine is running."""
        return self.running

    def reset_symbol(self, symbol: str):
        """Reset symbol to IDLE."""
        if symbol in self.states:
            st = self.states[symbol]
            st.phase = Phase.IDLE
            st.amount = 0.0
            st.entry_price = 0.0
            st.order_id = None
            st.retry_count = 0
            st.error_count = 0
            logger.info(f"Reset {symbol} to IDLE")

    def get_stuck_symbols(self, threshold_seconds: float = 60.0) -> list:
        """Get stuck symbols (in non-idle phases for too long)."""
        stuck = []
        now = time.time()
        for symbol, st in self.states.items():
            if st.phase not in [Phase.IDLE, Phase.WARMUP, Phase.COOLDOWN]:
                if st.ts_ms > 0:
                    age_seconds = now - (st.ts_ms / 1000.0)
                    if age_seconds > threshold_seconds:
                        stuck.append(symbol)
        return stuck

    def get_pnl_summary(self):
        """Get PnL summary from service."""
        return self.pnl_service.get_summary()
