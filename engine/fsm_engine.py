"""
FSM Trading Engine - Complete Implementation

Full rewrite with explicit Finite State Machine pattern.
All 12 phase handlers fully implemented.
"""

import time
import threading
import logging
from typing import Dict, Any, Optional
from datetime import datetime, timezone

# Core FSM (legacy handler-based)
from core.fsm.machine import StateMachine
from core.fsm.state import CoinState, set_phase
from core.fsm.phases import Phase

# New table-driven FSM components
from core.fsm.fsm_machine import get_fsm as get_table_driven_fsm
from core.fsm.fsm_events import FSMEvent, EventContext
from core.fsm.state_data import StateData, OrderContext
from core.fsm.timeouts import get_timeout_manager
from core.fsm.partial_fills import get_partial_fill_handler
from core.fsm.snapshot import get_snapshot_manager
from core.fsm.recovery import recover_fsm_states_on_startup
from core.fsm.portfolio_transaction import init_portfolio_transaction, get_portfolio_transaction
from core.fsm.retry import with_order_retry

# Logging & Metrics
from core.logging.phase_events import PhaseEventLogger
from core.logging.logger import new_decision_id, new_client_order_id
from telemetry.phase_metrics import (
    phase_changes, phase_code, PHASE_MAP,
    start_metrics_server, update_stuck_metric
)

# Services (from existing architecture)
from services import (
    BuySignalService, MarketGuards, OrderService, OrderCache,
    ExitManager, PnLService, MarketDataProvider, ExchangeAdapter
)
from adapters.exchange import ExchangeAdapter as ExchangeAdapterClass

# Config
import config

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

        # Phase Event Logger
        phase_log_file = config.SESSION_DIR + "/logs/phase_events.jsonl"
        self.phase_logger = PhaseEventLogger(phase_log_file)

        # Metrics
        self.metrics = type('Metrics', (), {
            'phase_changes': phase_changes,
            'phase_code': phase_code,
            'PHASE_MAP': PHASE_MAP
        })()

        # State Machine (legacy handler-based)
        self.fsm = StateMachine(log=self.phase_logger, metrics=self.metrics)

        # New table-driven FSM components
        self.table_driven_fsm = get_table_driven_fsm()
        self.timeout_manager = get_timeout_manager()
        self.partial_fill_handler = get_partial_fill_handler()
        self.snapshot_manager = get_snapshot_manager()

        # Initialize Services
        self._initialize_services()

        # Initialize portfolio transaction (needs portfolio and pnl_service)
        init_portfolio_transaction(self.portfolio, self.pnl_service, self.snapshot_manager)
        self.portfolio_tx = get_portfolio_transaction()

        # Register Phase Handlers
        self._register_transitions()

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
                    # Add to legacy FSM
                    self.fsm.states[symbol] = coin_state
                    logger.info(f"Recovered state for {symbol}: {coin_state.phase.name}")

                if recovered_states:
                    logger.info(f"Crash recovery: {len(recovered_states)} states restored")
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

        # Market Data Service
        self.market_data = MarketDataProvider(
            self.exchange_adapter,
            ticker_cache_ttl=getattr(config, 'TICKER_CACHE_TTL', 5.0),
            max_cache_size=1000
        )

        # Buy Signal Service
        self.buy_signal_service = BuySignalService(
            drop_trigger_value=config.DROP_TRIGGER_VALUE,
            drop_trigger_mode=config.DROP_TRIGGER_MODE,
            drop_trigger_lookback_min=config.DROP_TRIGGER_LOOKBACK_MIN,
            enable_minutely_audit=getattr(config, 'ENABLE_DROP_TRIGGER_MINUTELY', False)
        )

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

    def _register_transitions(self):
        """Register all phase handlers."""
        self.fsm.register_transition(Phase.WARMUP, self._handle_warmup)
        self.fsm.register_transition(Phase.IDLE, self._handle_idle)
        self.fsm.register_transition(Phase.ENTRY_EVAL, self._handle_entry_eval)
        self.fsm.register_transition(Phase.PLACE_BUY, self._handle_place_buy)
        self.fsm.register_transition(Phase.WAIT_FILL, self._handle_wait_fill)
        self.fsm.register_transition(Phase.POSITION, self._handle_position)
        self.fsm.register_transition(Phase.EXIT_EVAL, self._handle_exit_eval)
        self.fsm.register_transition(Phase.PLACE_SELL, self._handle_place_sell)
        self.fsm.register_transition(Phase.WAIT_SELL_FILL, self._handle_wait_sell_fill)
        self.fsm.register_transition(Phase.POST_TRADE, self._handle_post_trade)
        self.fsm.register_transition(Phase.COOLDOWN, self._handle_cooldown)
        self.fsm.register_transition(Phase.ERROR, self._handle_error)

    # ========== PHASE HANDLERS - COMPLETE IMPLEMENTATION ==========

    def _handle_warmup(self, st: CoinState, ctx: Dict):
        """
        WARMUP → IDLE

        Initialize symbol: backfill market data, set drop anchor.
        """
        symbol = st.symbol

        # Initialize StateData for table-driven FSM
        if not hasattr(st, 'fsm_data') or st.fsm_data is None:
            st.fsm_data = StateData()
            logger.debug(f"Initialized StateData for {symbol}")

        try:
            # Backfill market history if configured
            if getattr(config, 'BACKFILL_MINUTES', 0) > 0:
                self.market_data.backfill_history(
                    [symbol],
                    timeframe='1m',
                    minutes=config.BACKFILL_MINUTES
                )

            # Initialize drop anchor if using drop anchor system
            if getattr(config, 'USE_DROP_ANCHOR', False):
                price = ctx.get("price", 0.0)
                if price > 0:
                    st.anchor_price = price
                    st.anchor_ts = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
                    self.portfolio.set_drop_anchor(symbol, price, st.anchor_ts)

            # Event-based transition to IDLE
            event = EventContext(
                event=FSMEvent.WARMUP_COMPLETED,
                symbol=symbol,
                timestamp=ctx.get("timestamp", time.time()),
                data={'note': 'warmup complete'}
            )
            if self.table_driven_fsm.process_event(st, event):
                # Save snapshot after transition
                self.snapshot_manager.save_snapshot(symbol, st)
            else:
                # Fallback to legacy transition
                set_phase(st, Phase.IDLE, note="warmup complete",
                         log=self.phase_logger, metrics=self.metrics)

        except Exception as e:
            logger.error(f"Warmup error for {symbol}: {e}")
            # Event-based error transition
            event = EventContext(
                event=FSMEvent.ERROR_OCCURRED,
                symbol=symbol,
                timestamp=time.time(),
                error=e,
                data={'context': 'warmup_error', 'note': str(e)[:50]}
            )
            if self.table_driven_fsm.process_event(st, event):
                self.snapshot_manager.save_snapshot(symbol, st)
            else:
                # Fallback to legacy transition
                set_phase(st, Phase.ERROR, note=f"warmup_error: {str(e)[:50]}",
                         log=self.phase_logger, metrics=self.metrics)

    def _handle_idle(self, st: CoinState, ctx: Dict):
        """
        IDLE → ENTRY_EVAL (if slots available)

        Wait for free trading slot, then evaluate entry.
        """
        # Check if in cooldown
        if st.in_cooldown():
            return  # Stay in IDLE until cooldown expires

        # Check available slots
        max_trades = getattr(config, 'MAX_TRADES', 10)
        active_positions = len(self.fsm.get_active_positions())

        if active_positions >= max_trades:
            # No slots available - stay in IDLE
            return

        # Check if already have position for this symbol
        if st.has_position():
            # Shouldn't happen, but safety check
            logger.warning(f"{st.symbol} in IDLE but has position - resetting")
            st.amount = 0.0
            st.entry_price = 0.0
            return

        # Slots available - move to entry evaluation
        decision_id = new_decision_id()
        st.decision_id = decision_id

        # Event-based transition to ENTRY_EVAL
        event = EventContext(
            event=FSMEvent.SLOT_AVAILABLE,
            symbol=st.symbol,
            timestamp=ctx.get("timestamp", time.time()),
            data={'decision_id': decision_id, 'note': 'checking entry'}
        )
        if self.table_driven_fsm.process_event(st, event):
            self.snapshot_manager.save_snapshot(st.symbol, st)
        else:
            # Fallback to legacy transition
            set_phase(st, Phase.ENTRY_EVAL, note="checking entry",
                     decision_id=decision_id,
                     log=self.phase_logger, metrics=self.metrics)

    def _handle_entry_eval(self, st: CoinState, ctx: Dict):
        """
        ENTRY_EVAL → PLACE_BUY (if signal) or IDLE (if no signal/guards fail)

        Evaluate market guards and buy signals.
        """
        symbol = st.symbol
        price = ctx.get("price", 0.0)

        if price <= 0:
            set_phase(st, Phase.IDLE, note="no price data",
                     log=self.phase_logger, metrics=self.metrics)
            return

        # Update services with current price
        self.buy_signal_service.update_price(symbol, price)
        volume = ctx.get("volume", 0.0)
        self.market_guards.update_price_data(symbol, price, volume)

        # Update orderbook if available
        if "bid" in ctx and "ask" in ctx:
            self.market_guards.update_orderbook(symbol, ctx["bid"], ctx["ask"])

        # Check guards (fail fast)
        passes_guards, failed_guards = self.market_guards.passes_all_guards(symbol, price)

        if not passes_guards:
            note = f"guards_failed: {','.join(failed_guards[:3])}"
            # Event-based transition to IDLE
            event = EventContext(
                event=FSMEvent.GUARDS_FAILED,
                symbol=symbol,
                timestamp=ctx.get("timestamp", time.time()),
                data={'note': note, 'failed_guards': failed_guards}
            )
            if self.table_driven_fsm.process_event(st, event):
                self.snapshot_manager.save_snapshot(symbol, st)
            else:
                # Fallback to legacy transition
                set_phase(st, Phase.IDLE, note=note,
                         log=self.phase_logger, metrics=self.metrics)
            return

        # Check minimum sizing
        if hasattr(self.exchange_adapter, 'meets_minimums'):
            position_size = getattr(config, 'POSITION_SIZE_USDT', 10.0)
            sizing_ok, why = self.exchange_adapter.meets_minimums(symbol, price, position_size)
            if not sizing_ok:
                set_phase(st, Phase.IDLE, note=f"sizing_fail: {why}",
                         log=self.phase_logger, metrics=self.metrics)
                return

        # Evaluate buy signal
        buy_triggered, signal_context = self.buy_signal_service.evaluate_buy_signal(symbol, price)

        if buy_triggered:
            st.signal = f"DROP_MODE_{signal_context.get('mode', '?')}"
            # Store signal in fsm_data
            if hasattr(st, 'fsm_data') and st.fsm_data:
                st.fsm_data.signal_detected_at = time.time()
                st.fsm_data.signal_type = st.signal

            # Event-based transition to PLACE_BUY
            event = EventContext(
                event=FSMEvent.SIGNAL_DETECTED,
                symbol=symbol,
                timestamp=ctx.get("timestamp", time.time()),
                data={'signal_type': st.signal, 'signal_context': signal_context}
            )
            if self.table_driven_fsm.process_event(st, event):
                self.snapshot_manager.save_snapshot(symbol, st)
            else:
                # Fallback to legacy transition
                set_phase(st, Phase.PLACE_BUY, note=f"signal={st.signal}",
                         log=self.phase_logger, metrics=self.metrics)
        else:
            # Event-based transition to IDLE
            event = EventContext(
                event=FSMEvent.NO_SIGNAL,
                symbol=symbol,
                timestamp=ctx.get("timestamp", time.time()),
                data={'note': 'no_signal'}
            )
            if self.table_driven_fsm.process_event(st, event):
                self.snapshot_manager.save_snapshot(symbol, st)
            else:
                # Fallback to legacy transition
                set_phase(st, Phase.IDLE, note="no_signal",
                         log=self.phase_logger, metrics=self.metrics)

    def _handle_place_buy(self, st: CoinState, ctx: Dict):
        """
        PLACE_BUY → WAIT_FILL (if order placed) or IDLE (if placement fails)

        Calculate position size and place buy order.
        """
        symbol = st.symbol
        price = ctx.get("price", 0.0)

        try:
            # Calculate position size
            available_budget = self.portfolio.get_free_usdt()
            max_trades = getattr(config, 'MAX_TRADES', 10)
            per_trade = available_budget / max(1, max_trades)
            quote_budget = min(per_trade, getattr(config, 'POSITION_SIZE_USDT', 10.0))

            # Check minimum
            min_slot = getattr(config, 'MIN_SLOT_USDT', 5.0)
            if quote_budget < min_slot:
                set_phase(st, Phase.IDLE, note=f"insufficient_budget: {quote_budget:.2f}",
                         log=self.phase_logger, metrics=self.metrics)
                return

            # Calculate amount
            amount = quote_budget / price

            # Generate client order ID
            client_order_id = new_client_order_id(st.decision_id, "buy")

            # Place order (IOC or GTC based on config)
            use_ioc = getattr(config, 'USE_IOC_FOR_MODE2', False) and config.MODE == 2

            if use_ioc:
                order = self.order_service.place_limit_ioc(
                    symbol=symbol,
                    side="buy",
                    amount=amount,
                    price=price,
                    client_order_id=client_order_id
                )
            else:
                # GTC limit order
                order = self.exchange_adapter.exchange.create_limit_buy_order(
                    symbol, amount, price,
                    params={"clientOrderId": client_order_id}
                )

            if order and order.get("id"):
                st.order_id = order["id"]
                st.client_order_id = client_order_id
                st.order_placed_ts = time.time()
                st.current_price = price

                # Store order context in fsm_data
                if hasattr(st, 'fsm_data') and st.fsm_data:
                    st.fsm_data.buy_order = OrderContext(
                        order_id=order["id"],
                        client_order_id=client_order_id,
                        placed_at=time.time(),
                        target_qty=amount,
                        status="pending"
                    )

                # Event-based transition to WAIT_FILL
                event = EventContext(
                    event=FSMEvent.BUY_ORDER_PLACED,
                    symbol=symbol,
                    timestamp=time.time(),
                    order_id=st.order_id,
                    data={'amount': amount, 'price': price, 'note': f"order_placed: {st.order_id[:12]}"}
                )
                if self.table_driven_fsm.process_event(st, event):
                    self.snapshot_manager.save_snapshot(symbol, st)
                else:
                    # Fallback to legacy transition
                    set_phase(st, Phase.WAIT_FILL, note=f"order_placed: {st.order_id[:12]}",
                             order_id=st.order_id,
                             log=self.phase_logger, metrics=self.metrics)

                self.stats["total_buys"] += 1
            else:
                # Event-based transition to IDLE (placement failed)
                event = EventContext(
                    event=FSMEvent.ORDER_PLACEMENT_FAILED,
                    symbol=symbol,
                    timestamp=time.time(),
                    data={'note': 'order_placement_failed'}
                )
                if self.table_driven_fsm.process_event(st, event):
                    self.snapshot_manager.save_snapshot(symbol, st)
                else:
                    # Fallback to legacy transition
                    set_phase(st, Phase.IDLE, note="order_placement_failed",
                             log=self.phase_logger, metrics=self.metrics)

        except Exception as e:
            logger.error(f"Place buy error for {symbol}: {e}")
            # Event-based error transition
            event = EventContext(
                event=FSMEvent.ERROR_OCCURRED,
                symbol=symbol,
                timestamp=time.time(),
                error=e,
                data={'context': 'place_buy_error', 'note': str(e)[:50]}
            )
            if self.table_driven_fsm.process_event(st, event):
                self.snapshot_manager.save_snapshot(symbol, st)
            else:
                # Fallback to legacy transition
                set_phase(st, Phase.ERROR, note=f"place_buy_error: {str(e)[:50]}",
                         log=self.phase_logger, metrics=self.metrics)

    def _handle_wait_fill(self, st: CoinState, ctx: Dict):
        """
        WAIT_FILL → POSITION (if filled) or IDLE (if timeout/canceled)

        Poll order status and handle fill using TimeoutManager and PartialFillHandler.
        """
        symbol = st.symbol

        try:
            # Check timeout using TimeoutManager
            timeout_events = self.timeout_manager.check_all_timeouts(symbol, st)
            for timeout_event in timeout_events:
                if timeout_event.event == FSMEvent.BUY_ORDER_TIMEOUT:
                    # Cancel order
                    try:
                        self.exchange.cancel_order(st.order_id, symbol)
                    except Exception:
                        pass  # Order may already be filled/canceled

                    # Event-based transition to IDLE
                    if self.table_driven_fsm.process_event(st, timeout_event):
                        self.snapshot_manager.save_snapshot(symbol, st)
                    else:
                        # Fallback to legacy transition
                        set_phase(st, Phase.IDLE, note="buy_timeout",
                                 log=self.phase_logger, metrics=self.metrics)
                    return

            # Fetch order status
            order = self.exchange.fetch_order(st.order_id, symbol)

            if order.get("status") == "closed":
                # Order filled - use PartialFillHandler to accumulate
                filled = order.get("filled", 0)
                avg_price = order.get("average", 0)
                fee_quote = order.get('fee', {}).get('cost', 0) or 0

                if filled > 0 and avg_price > 0:
                    # Update order context with fill data
                    if hasattr(st, 'fsm_data') and st.fsm_data and st.fsm_data.buy_order:
                        order_ctx = st.fsm_data.buy_order
                        # Accumulate fill using PartialFillHandler
                        self.partial_fill_handler.accumulate_fill(
                            order_ctx=order_ctx,
                            fill_qty=filled,
                            fill_price=avg_price,
                            fill_fee=fee_quote,
                            trade_id=st.order_id
                        )
                        order_ctx.status = "filled"

                        # Use weighted average from PartialFillHandler
                        final_qty = order_ctx.cumulative_qty
                        final_avg_price = order_ctx.avg_price
                        final_fee = order_ctx.total_fees
                    else:
                        # Fallback to direct values
                        final_qty = filled
                        final_avg_price = avg_price
                        final_fee = fee_quote

                    # Update state
                    st.amount = final_qty
                    st.entry_price = final_avg_price
                    st.entry_ts = time.time()
                    st.current_price = final_avg_price
                    st.peak_price = final_avg_price  # Initialize trailing

                    # Calculate TP/SL
                    st.tp_px = final_avg_price * getattr(config, 'TAKE_PROFIT_THRESHOLD', 1.005)
                    st.sl_px = final_avg_price * getattr(config, 'STOP_LOSS_THRESHOLD', 0.990)

                    # Calculate fee per unit
                    st.entry_fee_per_unit = (final_fee / final_qty) if final_qty > 0 else 0

                    # Use PortfolioTransaction for atomic update
                    with self.portfolio_tx.begin(symbol, st):
                        # Add to portfolio
                        self.portfolio.add_held_asset(symbol, {
                            "amount": final_qty,
                            "entry_price": final_avg_price,
                            "buy_fee_quote_per_unit": st.entry_fee_per_unit,
                            "buy_price": final_avg_price
                        })

                        # Record in PnL service
                        self.pnl_service.record_fill(
                            symbol=symbol,
                            side="buy",
                            quantity=final_qty,
                            avg_price=final_avg_price,
                            fee_quote=final_fee,
                            order_id=st.order_id,
                            client_order_id=st.client_order_id
                        )

                        # Event-based transition to POSITION
                        event = EventContext(
                            event=FSMEvent.BUY_ORDER_FILLED,
                            symbol=symbol,
                            timestamp=time.time(),
                            filled_qty=final_qty,
                            avg_price=final_avg_price,
                            data={'note': f"filled: {final_qty:.6f}@{final_avg_price:.6f}"}
                        )
                        if not self.table_driven_fsm.process_event(st, event):
                            # Fallback to legacy transition
                            set_phase(st, Phase.POSITION,
                                     note=f"filled: {final_qty:.6f}@{final_avg_price:.6f}",
                                     log=self.phase_logger, metrics=self.metrics)

                    # Telegram notification
                    if self.telegram:
                        try:
                            self.telegram.send_message(
                                f"🛒 BUY {symbol} @{final_avg_price:.6f} x{final_qty:.6f} [{st.signal}]"
                            )
                        except Exception:
                            pass

                else:
                    # Filled but invalid data
                    event = EventContext(
                        event=FSMEvent.ORDER_INVALID_DATA,
                        symbol=symbol,
                        timestamp=time.time(),
                        data={'note': 'invalid_fill_data'}
                    )
                    if self.table_driven_fsm.process_event(st, event):
                        self.snapshot_manager.save_snapshot(symbol, st)
                    else:
                        set_phase(st, Phase.IDLE, note="invalid_fill_data",
                                 log=self.phase_logger, metrics=self.metrics)

            elif order.get("status") == "canceled":
                event = EventContext(
                    event=FSMEvent.BUY_ORDER_CANCELED,
                    symbol=symbol,
                    timestamp=time.time(),
                    data={'note': 'order_canceled'}
                )
                if self.table_driven_fsm.process_event(st, event):
                    self.snapshot_manager.save_snapshot(symbol, st)
                else:
                    set_phase(st, Phase.IDLE, note="order_canceled",
                             log=self.phase_logger, metrics=self.metrics)

            # else: order still open, stay in WAIT_FILL

        except Exception as e:
            logger.error(f"Wait fill error for {symbol}: {e}")
            # Event-based error transition
            event = EventContext(
                event=FSMEvent.ERROR_OCCURRED,
                symbol=symbol,
                timestamp=time.time(),
                error=e,
                data={'context': 'wait_fill_error', 'note': str(e)[:50]}
            )
            if self.table_driven_fsm.process_event(st, event):
                self.snapshot_manager.save_snapshot(symbol, st)
            else:
                set_phase(st, Phase.ERROR, note=f"wait_fill_error: {str(e)[:50]}",
                         log=self.phase_logger, metrics=self.metrics)

    def _handle_position(self, st: CoinState, ctx: Dict):
        """
        POSITION → EXIT_EVAL (periodically check exits)

        Monitor position, update trailing stops, calculate unrealized PnL.
        """
        symbol = st.symbol
        price = ctx.get("price", 0.0)

        if price <= 0:
            return  # Wait for valid price

        # Update current price
        st.current_price = price

        # Update trailing stop
        if getattr(config, 'USE_TRAILING_STOP', False):
            if price > st.peak_price:
                st.peak_price = price
                # Update trailing trigger
                trailing_pct = getattr(config, 'TRAILING_DISTANCE_PCT', 0.99)
                st.trailing_trigger = st.peak_price * trailing_pct

        # Update unrealized PnL
        self.pnl_service.set_unrealized_position(
            symbol=symbol,
            quantity=st.amount,
            avg_entry_price=st.entry_price,
            current_price=price,
            entry_fee_per_unit=st.entry_fee_per_unit
        )

        # Periodically check exit conditions (every 2s)
        if self.cycle_count % 4 == 0:  # 4 cycles * 0.5s = 2s
            # Event-based transition to EXIT_EVAL
            event = EventContext(
                event=FSMEvent.TICK_RECEIVED,
                symbol=symbol,
                timestamp=time.time(),
                data={'note': 'checking_exits', 'price': price}
            )
            if self.table_driven_fsm.process_event(st, event):
                self.snapshot_manager.save_snapshot(symbol, st)
            else:
                set_phase(st, Phase.EXIT_EVAL, note="checking_exits",
                         log=self.phase_logger, metrics=self.metrics)

    def _handle_exit_eval(self, st: CoinState, ctx: Dict):
        """
        EXIT_EVAL → PLACE_SELL (if exit condition met) or POSITION (if no exit)

        Check TP, SL, trailing stop, timeout.
        """
        symbol = st.symbol
        price = ctx.get("price", 0.0)

        if price <= 0:
            set_phase(st, Phase.POSITION, note="no_price",
                     log=self.phase_logger, metrics=self.metrics)
            return

        # Take Profit Check
        if price >= st.tp_px:
            st.exit_reason = "TAKE_PROFIT"
            if hasattr(st, 'fsm_data') and st.fsm_data:
                st.fsm_data.exit_signal = "TAKE_PROFIT"
                st.fsm_data.exit_detected_at = time.time()

            event = EventContext(
                event=FSMEvent.TAKE_PROFIT_HIT,
                symbol=symbol,
                timestamp=time.time(),
                data={'note': f"tp_hit: {price:.6f}>={st.tp_px:.6f}", 'price': price}
            )
            if self.table_driven_fsm.process_event(st, event):
                self.snapshot_manager.save_snapshot(symbol, st)
            else:
                set_phase(st, Phase.PLACE_SELL, note=f"tp_hit: {price:.6f}>={st.tp_px:.6f}",
                         log=self.phase_logger, metrics=self.metrics)
            return

        # Stop Loss Check
        if price <= st.sl_px:
            st.exit_reason = "STOP_LOSS"
            if hasattr(st, 'fsm_data') and st.fsm_data:
                st.fsm_data.exit_signal = "STOP_LOSS"
                st.fsm_data.exit_detected_at = time.time()

            event = EventContext(
                event=FSMEvent.STOP_LOSS_HIT,
                symbol=symbol,
                timestamp=time.time(),
                data={'note': f"sl_hit: {price:.6f}<={st.sl_px:.6f}", 'price': price}
            )
            if self.table_driven_fsm.process_event(st, event):
                self.snapshot_manager.save_snapshot(symbol, st)
            else:
                set_phase(st, Phase.PLACE_SELL, note=f"sl_hit: {price:.6f}<={st.sl_px:.6f}",
                         log=self.phase_logger, metrics=self.metrics)
            return

        # Trailing Stop Check
        if getattr(config, 'USE_TRAILING_STOP', False) and st.trailing_trigger > 0:
            if price <= st.trailing_trigger:
                st.exit_reason = "TRAILING_STOP"
                if hasattr(st, 'fsm_data') and st.fsm_data:
                    st.fsm_data.exit_signal = "TRAILING_STOP"
                    st.fsm_data.exit_detected_at = time.time()

                event = EventContext(
                    event=FSMEvent.TRAILING_STOP_HIT,
                    symbol=symbol,
                    timestamp=time.time(),
                    data={'note': f"trailing: {price:.6f}<={st.trailing_trigger:.6f}", 'price': price}
                )
                if self.table_driven_fsm.process_event(st, event):
                    self.snapshot_manager.save_snapshot(symbol, st)
                else:
                    set_phase(st, Phase.PLACE_SELL, note=f"trailing: {price:.6f}<={st.trailing_trigger:.6f}",
                             log=self.phase_logger, metrics=self.metrics)
                return

        # Timeout Check
        max_hold_minutes = getattr(config, 'MAX_POSITION_HOLD_MINUTES', 60)
        hold_time_minutes = (time.time() - st.entry_ts) / 60.0
        if hold_time_minutes > max_hold_minutes:
            st.exit_reason = "TIMEOUT"
            if hasattr(st, 'fsm_data') and st.fsm_data:
                st.fsm_data.exit_signal = "TIMEOUT"
                st.fsm_data.exit_detected_at = time.time()

            event = EventContext(
                event=FSMEvent.POSITION_TIMEOUT,
                symbol=symbol,
                timestamp=time.time(),
                data={'note': f"timeout: {hold_time_minutes:.1f}min", 'hold_time_minutes': hold_time_minutes}
            )
            if self.table_driven_fsm.process_event(st, event):
                self.snapshot_manager.save_snapshot(symbol, st)
            else:
                set_phase(st, Phase.PLACE_SELL, note=f"timeout: {hold_time_minutes:.1f}min",
                         log=self.phase_logger, metrics=self.metrics)
            return

        # No exit condition - return to POSITION
        event = EventContext(
            event=FSMEvent.NO_EXIT_SIGNAL,
            symbol=symbol,
            timestamp=time.time(),
            data={'note': 'no_exit_signal', 'price': price}
        )
        if self.table_driven_fsm.process_event(st, event):
            self.snapshot_manager.save_snapshot(symbol, st)
        else:
            set_phase(st, Phase.POSITION, note="no_exit_signal",
                     log=self.phase_logger, metrics=self.metrics)

    def _handle_place_sell(self, st: CoinState, ctx: Dict):
        """
        PLACE_SELL → WAIT_SELL_FILL (if order placed) or EXIT_EVAL (retry)

        Place sell order (IOC ladder or market).
        """
        symbol = st.symbol
        price = ctx.get("price", 0.0)

        try:
            # Generate client order ID
            client_order_id = new_client_order_id(st.decision_id, "sell")

            # Place sell order (IOC recommended for exits)
            order = self.order_service.place_limit_ioc(
                symbol=symbol,
                side="sell",
                amount=st.amount,
                price=price,
                client_order_id=client_order_id
            )

            if order and order.get("id"):
                st.order_id = order["id"]
                st.client_order_id = client_order_id
                st.order_placed_ts = time.time()

                # Store sell order context in fsm_data
                if hasattr(st, 'fsm_data') and st.fsm_data:
                    st.fsm_data.sell_order = OrderContext(
                        order_id=order["id"],
                        client_order_id=client_order_id,
                        placed_at=time.time(),
                        target_qty=st.amount,
                        status="pending"
                    )

                # Event-based transition to WAIT_SELL_FILL
                event = EventContext(
                    event=FSMEvent.SELL_ORDER_PLACED,
                    symbol=symbol,
                    timestamp=time.time(),
                    order_id=st.order_id,
                    data={'amount': st.amount, 'price': price, 'note': f"sell_order: {st.order_id[:12]}"}
                )
                if self.table_driven_fsm.process_event(st, event):
                    self.snapshot_manager.save_snapshot(symbol, st)
                else:
                    set_phase(st, Phase.WAIT_SELL_FILL, note=f"sell_order: {st.order_id[:12]}",
                             order_id=st.order_id,
                             log=self.phase_logger, metrics=self.metrics)

                self.stats["total_sells"] += 1
            else:
                # Retry
                st.retry_count += 1
                if st.retry_count < 3:
                    event = EventContext(
                        event=FSMEvent.ORDER_PLACEMENT_FAILED,
                        symbol=symbol,
                        timestamp=time.time(),
                        data={'note': f"retry_{st.retry_count}", 'retry_count': st.retry_count}
                    )
                    if self.table_driven_fsm.process_event(st, event):
                        self.snapshot_manager.save_snapshot(symbol, st)
                    else:
                        set_phase(st, Phase.PLACE_SELL, note=f"retry_{st.retry_count}",
                                 log=self.phase_logger, metrics=self.metrics)
                else:
                    event = EventContext(
                        event=FSMEvent.ERROR_OCCURRED,
                        symbol=symbol,
                        timestamp=time.time(),
                        data={'context': 'sell_placement_failed_max_retries'}
                    )
                    if self.table_driven_fsm.process_event(st, event):
                        self.snapshot_manager.save_snapshot(symbol, st)
                    else:
                        set_phase(st, Phase.ERROR, note="sell_placement_failed_max_retries",
                                 log=self.phase_logger, metrics=self.metrics)

        except Exception as e:
            logger.error(f"Place sell error for {symbol}: {e}")
            # Event-based error transition
            event = EventContext(
                event=FSMEvent.ERROR_OCCURRED,
                symbol=symbol,
                timestamp=time.time(),
                error=e,
                data={'context': 'place_sell_error', 'note': str(e)[:50]}
            )
            if self.table_driven_fsm.process_event(st, event):
                self.snapshot_manager.save_snapshot(symbol, st)
            else:
                set_phase(st, Phase.ERROR, note=f"place_sell_error: {str(e)[:50]}",
                         log=self.phase_logger, metrics=self.metrics)

    def _handle_wait_sell_fill(self, st: CoinState, ctx: Dict):
        """
        WAIT_SELL_FILL → POST_TRADE (if filled) or PLACE_SELL (retry with market)

        Poll sell order and handle partial fills.
        """
        symbol = st.symbol

        try:
            # Check timeout
            timeout_seconds = getattr(config, 'SELL_ORDER_TIMEOUT_SECONDS', 20)
            age = time.time() - st.order_placed_ts

            # Fetch order
            order = self.exchange.fetch_order(st.order_id, symbol)

            if order.get("status") == "closed":
                # Order filled
                filled = order.get("filled", 0)
                avg_price = order.get("average", 0)

                if filled >= st.amount * 0.95:  # 95% filled threshold
                    # Event-based transition to POST_TRADE
                    event = EventContext(
                        event=FSMEvent.SELL_ORDER_FILLED,
                        symbol=symbol,
                        timestamp=time.time(),
                        filled_qty=filled,
                        avg_price=avg_price,
                        data={'note': f"filled: {filled:.6f}@{avg_price:.6f}"}
                    )
                    if self.table_driven_fsm.process_event(st, event):
                        self.snapshot_manager.save_snapshot(symbol, st)
                    else:
                        set_phase(st, Phase.POST_TRADE,
                                 note=f"filled: {filled:.6f}@{avg_price:.6f}",
                                 log=self.phase_logger, metrics=self.metrics)
                else:
                    # Partial fill - retry with market order
                    remaining = st.amount - filled
                    if remaining > 0.0001:
                        st.retry_count += 1
                        st.amount = remaining  # Update remaining amount
                        event = EventContext(
                            event=FSMEvent.PARTIAL_FILL_RETRY,
                            symbol=symbol,
                            timestamp=time.time(),
                            data={'note': f"partial_fill_retry: {remaining:.6f}", 'remaining': remaining}
                        )
                        if self.table_driven_fsm.process_event(st, event):
                            self.snapshot_manager.save_snapshot(symbol, st)
                        else:
                            set_phase(st, Phase.PLACE_SELL, note=f"partial_fill_retry: {remaining:.6f}",
                                     log=self.phase_logger, metrics=self.metrics)
                    else:
                        # Small remainder, consider filled
                        event = EventContext(
                            event=FSMEvent.SELL_ORDER_FILLED,
                            symbol=symbol,
                            timestamp=time.time(),
                            data={'note': 'partial_accepted'}
                        )
                        if self.table_driven_fsm.process_event(st, event):
                            self.snapshot_manager.save_snapshot(symbol, st)
                        else:
                            set_phase(st, Phase.POST_TRADE, note="partial_accepted",
                                     log=self.phase_logger, metrics=self.metrics)

            elif age > timeout_seconds:
                # Timeout - retry with market order
                try:
                    self.exchange.cancel_order(st.order_id, symbol)
                except Exception:
                    pass

                st.retry_count += 1
                if st.retry_count < 3:
                    event = EventContext(
                        event=FSMEvent.SELL_ORDER_TIMEOUT,
                        symbol=symbol,
                        timestamp=time.time(),
                        data={'note': f"timeout_retry_{st.retry_count}", 'retry_count': st.retry_count}
                    )
                    if self.table_driven_fsm.process_event(st, event):
                        self.snapshot_manager.save_snapshot(symbol, st)
                    else:
                        set_phase(st, Phase.PLACE_SELL, note=f"timeout_retry_{st.retry_count}",
                                 log=self.phase_logger, metrics=self.metrics)
                else:
                    # Max retries - force to POST_TRADE
                    event = EventContext(
                        event=FSMEvent.MAX_RETRIES_EXCEEDED,
                        symbol=symbol,
                        timestamp=time.time(),
                        data={'note': 'timeout_max_retries'}
                    )
                    if self.table_driven_fsm.process_event(st, event):
                        self.snapshot_manager.save_snapshot(symbol, st)
                    else:
                        set_phase(st, Phase.POST_TRADE, note="timeout_max_retries",
                                 log=self.phase_logger, metrics=self.metrics)

            # else: order still open, stay in WAIT_SELL_FILL

        except Exception as e:
            logger.error(f"Wait sell fill error for {symbol}: {e}")
            # Event-based error transition
            event = EventContext(
                event=FSMEvent.ERROR_OCCURRED,
                symbol=symbol,
                timestamp=time.time(),
                error=e,
                data={'context': 'wait_sell_error', 'note': str(e)[:50]}
            )
            if self.table_driven_fsm.process_event(st, event):
                self.snapshot_manager.save_snapshot(symbol, st)
            else:
                set_phase(st, Phase.ERROR, note=f"wait_sell_error: {str(e)[:50]}",
                         log=self.phase_logger, metrics=self.metrics)

    def _handle_post_trade(self, st: CoinState, ctx: Dict):
        """
        POST_TRADE → COOLDOWN

        Record PnL, cleanup position, notify.
        """
        symbol = st.symbol

        try:
            # Fetch final order for PnL calculation
            order = self.exchange.fetch_order(st.order_id, symbol)
            filled = order.get("filled", 0)
            avg_exit_price = order.get("average", 0)
            exit_fee = order.get('fee', {}).get('cost', 0) or 0

            # Record in PnL service
            realized_pnl = self.pnl_service.record_fill(
                symbol=symbol,
                side="sell",
                quantity=filled,
                avg_price=avg_exit_price,
                fee_quote=exit_fee,
                entry_price=st.entry_price,
                order_id=st.order_id,
                client_order_id=st.client_order_id,
                reason=st.exit_reason
            )

            # Remove unrealized position
            self.pnl_service.remove_unrealized_position(symbol)

            # Remove from portfolio
            self.portfolio.remove_held_asset(symbol)

            # Telegram notification
            if self.telegram and realized_pnl is not None:
                try:
                    pnl_emoji = "✅" if realized_pnl > 0 else "❌"
                    self.telegram.send_message(
                        f"{pnl_emoji} SELL {symbol} @{avg_exit_price:.6f} x{filled:.6f} "
                        f"[{st.exit_reason}] PnL: {realized_pnl:+.2f}"
                    )
                except Exception:
                    pass

            # Set cooldown
            cooldown_minutes = getattr(config, 'SYMBOL_COOLDOWN_MINUTES', 15)
            st.cooldown_until = time.time() + (cooldown_minutes * 60)

            # Store cooldown in fsm_data
            if hasattr(st, 'fsm_data') and st.fsm_data:
                st.fsm_data.cooldown_started_at = time.time()

            # Clear position data
            st.amount = 0.0
            st.entry_price = 0.0
            st.order_id = None
            st.retry_count = 0

            # Event-based transition to COOLDOWN
            event = EventContext(
                event=FSMEvent.TRADE_COMPLETE,
                symbol=symbol,
                timestamp=time.time(),
                data={
                    'note': f"pnl={realized_pnl:+.2f} cooldown={cooldown_minutes}min",
                    'pnl': realized_pnl,
                    'cooldown_minutes': cooldown_minutes
                }
            )
            if self.table_driven_fsm.process_event(st, event):
                self.snapshot_manager.save_snapshot(symbol, st)
            else:
                set_phase(st, Phase.COOLDOWN, note=f"pnl={realized_pnl:+.2f} cooldown={cooldown_minutes}min",
                         log=self.phase_logger, metrics=self.metrics)

        except Exception as e:
            logger.error(f"Post trade error for {symbol}: {e}")
            # Still transition to COOLDOWN even on error
            st.amount = 0.0
            st.cooldown_until = time.time() + (15 * 60)

            event = EventContext(
                event=FSMEvent.TRADE_COMPLETE,
                symbol=symbol,
                timestamp=time.time(),
                data={'note': 'error_but_cleanup'}
            )
            if self.table_driven_fsm.process_event(st, event):
                self.snapshot_manager.save_snapshot(symbol, st)
            else:
                set_phase(st, Phase.COOLDOWN, note=f"error_but_cleanup",
                         log=self.phase_logger, metrics=self.metrics)

    def _handle_cooldown(self, st: CoinState, ctx: Dict):
        """
        COOLDOWN → IDLE (when cooldown expires)

        Wait for cooldown period using TimeoutManager.
        """
        # Check cooldown expiry using TimeoutManager
        timeout_events = self.timeout_manager.check_all_timeouts(st.symbol, st)
        for timeout_event in timeout_events:
            if timeout_event.event == FSMEvent.COOLDOWN_EXPIRED:
                # Event-based transition to IDLE
                if self.table_driven_fsm.process_event(st, timeout_event):
                    self.snapshot_manager.save_snapshot(st.symbol, st)
                else:
                    set_phase(st, Phase.IDLE, note="cooldown_expired",
                             log=self.phase_logger, metrics=self.metrics)
                return

        # Fallback to legacy check
        if not st.in_cooldown():
            event = EventContext(
                event=FSMEvent.COOLDOWN_EXPIRED,
                symbol=st.symbol,
                timestamp=time.time(),
                data={'note': 'cooldown_expired'}
            )
            if self.table_driven_fsm.process_event(st, event):
                self.snapshot_manager.save_snapshot(st.symbol, st)
            else:
                set_phase(st, Phase.IDLE, note="cooldown_expired",
                         log=self.phase_logger, metrics=self.metrics)

    def _handle_error(self, st: CoinState, ctx: Dict):
        """
        ERROR → IDLE (with exponential backoff)

        Recover from errors with backoff.
        """
        # Exponential backoff
        backoff_seconds = min(300, 10 * (2 ** min(st.error_count, 5)))  # Max 5min

        if st.ts_ms == 0 or (time.time() - st.ts_ms / 1000.0) > backoff_seconds:
            # Cleanup any position data
            if st.has_position():
                try:
                    self.portfolio.remove_held_asset(st.symbol)
                    self.pnl_service.remove_unrealized_position(st.symbol)
                except Exception:
                    pass

            st.amount = 0.0
            st.entry_price = 0.0
            st.order_id = None

            set_phase(st, Phase.IDLE, note=f"recovered after {backoff_seconds}s (errors={st.error_count})",
                     log=self.phase_logger, metrics=self.metrics)

    # ========== MAIN LOOP ==========

    def start(self):
        """Start FSM Trading Engine."""
        if self.running:
            logger.warning("Engine already running")
            return

        logger.info("Starting FSM Trading Engine...")
        self.running = True
        self.main_thread = threading.Thread(target=self._main_loop, daemon=False, name="FSM-Engine-Main")
        self.main_thread.start()
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
        logger.info("FSM main loop started")

        try:
            while self.running:
                cycle_start = time.time()
                self.cycle_count += 1

                # Heartbeat every 10 cycles
                if self.cycle_count % 10 == 0:
                    active = len([s for s in self.fsm.states.values() if s.phase not in [Phase.IDLE, Phase.WARMUP]])
                    positions = len(self.fsm.get_active_positions())
                    logger.info(f"💓 FSM Cycle #{self.cycle_count} - {len(self.fsm.states)} symbols | Active: {active} | Positions: {positions}")

                # Process all symbols
                for symbol in self.watchlist.keys():
                    try:
                        ctx = self._build_context(symbol)
                        self.fsm.process_symbol(symbol, ctx)
                    except Exception as e:
                        logger.error(f"Error processing {symbol}: {e}")
                        self.stats["total_errors"] += 1

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
        ctx = {"symbol": symbol, "timestamp": time.time()}

        try:
            # Get current price from market data service
            price = self.market_data.get_price(symbol)
            ctx["price"] = price if price else 0.0

            # Get orderbook data
            ticker = self.market_data.get_ticker(symbol)
            if ticker:
                ctx["bid"] = ticker.get("bid", 0.0)
                ctx["ask"] = ticker.get("ask", 0.0)
                ctx["volume"] = ticker.get("volume", 0.0)

        except Exception as e:
            logger.debug(f"Context build error for {symbol}: {e}")
            ctx["price"] = 0.0

        return ctx

    def _update_stuck_metrics(self):
        """Update Prometheus stuck metrics."""
        for st in self.fsm.states.values():
            if st.phase not in [Phase.IDLE, Phase.WARMUP, Phase.COOLDOWN]:
                update_stuck_metric(st)

    # ========== PUBLIC API ==========

    def get_states(self) -> Dict[str, CoinState]:
        """Get all states (for status table)."""
        return self.fsm.get_all_states()

    def get_positions(self) -> Dict[str, CoinState]:
        """Get active positions."""
        return {st.symbol: st for st in self.fsm.get_active_positions()}

    def get_statistics(self) -> Dict[str, Any]:
        """Get engine statistics."""
        return {
            **self.stats,
            "fsm_stats": self.fsm.get_statistics(),
            "uptime_seconds": time.time() - self.stats["start_time"],
        }

    def is_running(self) -> bool:
        """Check if engine is running."""
        return self.running

    def reset_symbol(self, symbol: str):
        """Reset symbol to IDLE."""
        self.fsm.reset_symbol(symbol)

    def get_stuck_symbols(self, threshold_seconds: float = 60.0) -> list:
        """Get stuck symbols."""
        return self.fsm.get_stuck_states(threshold_seconds)

    def get_pnl_summary(self):
        """Get PnL summary from service."""
        return self.pnl_service.get_summary()
