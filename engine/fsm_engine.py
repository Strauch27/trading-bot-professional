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

# Core FSM - Table-Driven Architecture
from core.fsm.state import CoinState
from core.fsm.phases import Phase
from core.fsm.fsm_machine import FSMMachine
from core.fsm.transitions import get_transition_table
from core.fsm.fsm_events import FSMEvent, EventContext
from core.fsm.state_data import StateData, OrderContext
from core.fsm.timeouts import TimeoutManager
from core.fsm.partial_fills import PartialFillHandler
from core.fsm.snapshot import SnapshotManager
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

        # Initialize Services (needed before FSM)
        self._initialize_services()

        # Table-Driven FSM Components
        self.fsm = FSMMachine(get_transition_table())
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

    # ========== EVENT-BASED SYMBOL PROCESSING ==========

    def _process_symbol(self, symbol: str, md: Dict):
        """
        Process symbol with event-based FSM dispatch.

        Args:
            symbol: Trading symbol
            md: Market data dict with price, bid, ask, volume
        """
        # Get or create state
        if symbol not in self.states:
            st = CoinState(symbol=symbol)
            st.fsm_data = StateData()
            st.phase = Phase.WARMUP
            self.states[symbol] = st
        else:
            st = self.states[symbol]

        price = md.get("price", 0.0)
        if price <= 0:
            return  # Skip invalid price data

        # Build event context
        ctx = EventContext(
            symbol=symbol,
            price=price,
            timestamp=time.time(),
            decision_id=st.decision_id or "",
            order_id=st.order_id or "",
            data={
                "bid": md.get("bid", 0.0),
                "ask": md.get("ask", 0.0),
                "volume": md.get("volume", 0.0)
            }
        )

        # Phase-specific event dispatch
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
                if self.fsm.process_event(st, event):
                    self.snapshot_manager.save_snapshot(symbol, st)
                    self._log_phase_transition(st, event)

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
            price=ctx.price,
            decision_id=ctx.decision_id,
            order_id=ctx.order_id,
            data=ctx.data
        )

        if self.fsm.process_event(st, event_ctx):
            # Save snapshot synchronously after state change
            self.snapshot_manager.save_snapshot(st.symbol, st)
            # Log phase transition asynchronously
            self._log_phase_transition(st, event_ctx)
            return True
        return False

    def _log_phase_transition(self, st: CoinState, ctx: EventContext):
        """Log phase transition to JSONL (async)."""
        try:
            self.phase_logger.log_phase_change({
                "symbol": st.symbol,
                "to": st.phase.value,
                "ts_ms": int(ctx.timestamp * 1000),
                "decision_id": st.decision_id or "",
                "order_id": st.order_id or "",
                "event": ctx.event.name if ctx.event else ""
            })
        except Exception as e:
            logger.debug(f"Phase logging failed: {e}")

    # ========== PHASE-SPECIFIC PROCESSORS ==========

    def _process_warmup(self, st: CoinState, ctx: EventContext):
        """WARMUP: Initialize symbol and transition to IDLE."""
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
            self._emit_event(st, FSMEvent.WARMUP_COMPLETED, ctx)

        except Exception as e:
            logger.error(f"Warmup error {st.symbol}: {e}")
            self._emit_event(st, FSMEvent.ERROR_OCCURRED, ctx)

    def _process_idle(self, st: CoinState, ctx: EventContext):
        """IDLE: Check for free slots and transition to ENTRY_EVAL."""
        if st.in_cooldown():
            return  # Still in cooldown

        # Check available slots
        max_trades = getattr(config, 'MAX_TRADES', 10)
        active_positions = sum(1 for s in self.states.values() if s.phase == Phase.POSITION)

        if active_positions >= max_trades:
            return  # No slots available

        # Assign decision_id for idempotency
        st.decision_id = new_decision_id()

        # Emit SLOT_AVAILABLE event
        self._emit_event(st, FSMEvent.SLOT_AVAILABLE, ctx)

    def _process_entry_eval(self, st: CoinState, ctx: EventContext):
        """ENTRY_EVAL: Evaluate guards and signals."""
        # Update services
        self.buy_signal_service.update_price(st.symbol, ctx.price)
        self.market_guards.update_price_data(st.symbol, ctx.price, ctx.data.get("volume", 0.0))

        if ctx.data.get("bid") and ctx.data.get("ask"):
            self.market_guards.update_orderbook(st.symbol, ctx.data["bid"], ctx.data["ask"])

        # Check guards
        passes_guards, failed_guards = self.market_guards.passes_all_guards(st.symbol, ctx.price)
        if not passes_guards:
            self._emit_event(st, FSMEvent.GUARDS_FAILED, ctx)
            return

        # Check signal
        buy_triggered, signal_context = self.buy_signal_service.evaluate_buy_signal(st.symbol, ctx.price)

        if buy_triggered:
            st.signal = f"DROP_MODE_{signal_context.get('mode', '?')}"
            if st.fsm_data:
                st.fsm_data.signal_detected_at = time.time()
                st.fsm_data.signal_type = st.signal
            self._emit_event(st, FSMEvent.SIGNAL_DETECTED, ctx)
        else:
            self._emit_event(st, FSMEvent.NO_SIGNAL, ctx)

    def _process_place_buy(self, st: CoinState, ctx: EventContext):
        """PLACE_BUY: Calculate size and place buy order."""
        try:
            # Calculate position size
            available_budget = self.portfolio.get_free_usdt()
            max_trades = getattr(config, 'MAX_TRADES', 10)
            per_trade = available_budget / max(1, max_trades)
            quote_budget = min(per_trade, getattr(config, 'POSITION_SIZE_USDT', 10.0))

            if quote_budget < getattr(config, 'MIN_SLOT_USDT', 5.0):
                self._emit_event(st, FSMEvent.ORDER_PLACEMENT_FAILED, ctx)
                return

            amount = quote_budget / ctx.price
            client_order_id = new_client_order_id(st.decision_id, "buy")

            # Place order
            order = self.order_service.place_limit_ioc(
                symbol=st.symbol,
                side="buy",
                amount=amount,
                price=ctx.price,
                client_order_id=client_order_id
            )

            if order and order.get("id"):
                st.order_id = order["id"]
                st.client_order_id = client_order_id
                st.order_placed_ts = time.time()

                if st.fsm_data:
                    st.fsm_data.buy_order = OrderContext(
                        order_id=order["id"],
                        client_order_id=client_order_id,
                        placed_at=time.time(),
                        target_qty=amount,
                        status="pending"
                    )

                self._emit_event(st, FSMEvent.BUY_ORDER_PLACED, ctx)
                self.stats["total_buys"] += 1
            else:
                self._emit_event(st, FSMEvent.ORDER_PLACEMENT_FAILED, ctx)

        except Exception as e:
            logger.error(f"Place buy error {st.symbol}: {e}")
            self._emit_event(st, FSMEvent.ERROR_OCCURRED, ctx)

    def _process_wait_fill(self, st: CoinState, ctx: EventContext):
        """WAIT_FILL: Poll order status and handle fills with PartialFillHandler."""
        # Note: Timeouts handled by _tick_timeouts
        try:
            order = self.exchange.fetch_order(st.order_id, st.symbol)

            if order.get("status") == "closed":
                filled = order.get("filled", 0)
                avg_price = order.get("average", 0)
                fee_quote = order.get('fee', {}).get('cost', 0) or 0

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

                    # Atomic portfolio update
                    with self.portfolio_tx.begin(st.symbol, st):
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

                        self.pnl_service.record_fill(
                            symbol=st.symbol,
                            side="buy",
                            quantity=final_qty,
                            avg_price=final_price,
                            fee_quote=final_fee,
                            order_id=st.order_id,
                            client_order_id=st.client_order_id
                        )

                        self._emit_event(st, FSMEvent.BUY_ORDER_FILLED, ctx)

                    # Telegram notification
                    if self.telegram:
                        try:
                            self.telegram.send_message(
                                f"🛒 BUY {st.symbol} @{final_price:.6f} x{final_qty:.6f}"
                            )
                        except Exception:
                            pass

            elif order.get("status") == "canceled":
                self._emit_event(st, FSMEvent.BUY_ORDER_CANCELED, ctx)

        except Exception as e:
            logger.error(f"Wait fill error {st.symbol}: {e}")
            self._emit_event(st, FSMEvent.ERROR_OCCURRED, ctx)

    def _process_position(self, st: CoinState, ctx: EventContext):
        """POSITION: Monitor position, update trailing, check exits periodically."""
        st.current_price = ctx.price

        # Update trailing stop
        if getattr(config, 'USE_TRAILING_STOP', False):
            if ctx.price > st.peak_price:
                st.peak_price = ctx.price
                st.trailing_trigger = st.peak_price * getattr(config, 'TRAILING_DISTANCE_PCT', 0.99)

        # Update unrealized PnL
        self.pnl_service.set_unrealized_position(
            symbol=st.symbol,
            quantity=st.amount,
            avg_entry_price=st.entry_price,
            current_price=ctx.price,
            entry_fee_per_unit=st.entry_fee_per_unit
        )

        # Check exits every 4 cycles (2s)
        if self.cycle_count % 4 == 0:
            self._emit_event(st, FSMEvent.TICK_RECEIVED, ctx)

    def _process_exit_eval(self, st: CoinState, ctx: EventContext):
        """EXIT_EVAL: Check TP, SL, trailing, timeout."""
        # Take Profit
        if ctx.price >= st.tp_px:
            st.exit_reason = "TAKE_PROFIT"
            if st.fsm_data:
                st.fsm_data.exit_signal = "TAKE_PROFIT"
                st.fsm_data.exit_detected_at = time.time()
            self._emit_event(st, FSMEvent.TAKE_PROFIT_HIT, ctx)
            return

        # Stop Loss
        if ctx.price <= st.sl_px:
            st.exit_reason = "STOP_LOSS"
            if st.fsm_data:
                st.fsm_data.exit_signal = "STOP_LOSS"
                st.fsm_data.exit_detected_at = time.time()
            self._emit_event(st, FSMEvent.STOP_LOSS_HIT, ctx)
            return

        # Trailing Stop
        if getattr(config, 'USE_TRAILING_STOP', False) and st.trailing_trigger > 0:
            if ctx.price <= st.trailing_trigger:
                st.exit_reason = "TRAILING_STOP"
                if st.fsm_data:
                    st.fsm_data.exit_signal = "TRAILING_STOP"
                    st.fsm_data.exit_detected_at = time.time()
                self._emit_event(st, FSMEvent.TRAILING_STOP_HIT, ctx)
                return

        # Position Timeout
        max_hold_minutes = getattr(config, 'MAX_POSITION_HOLD_MINUTES', 60)
        hold_time_minutes = (time.time() - st.entry_ts) / 60.0
        if hold_time_minutes > max_hold_minutes:
            st.exit_reason = "TIMEOUT"
            if st.fsm_data:
                st.fsm_data.exit_signal = "TIMEOUT"
                st.fsm_data.exit_detected_at = time.time()
            self._emit_event(st, FSMEvent.POSITION_TIMEOUT, ctx)
            return

        # No exit - return to POSITION
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

                if st.fsm_data:
                    st.fsm_data.sell_order = OrderContext(
                        order_id=order["id"],
                        client_order_id=client_order_id,
                        placed_at=time.time(),
                        target_qty=st.amount,
                        status="pending"
                    )

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
            self._emit_event(st, FSMEvent.ERROR_OCCURRED, ctx)

    def _process_wait_sell_fill(self, st: CoinState, ctx: EventContext):
        """WAIT_SELL_FILL: Poll sell order status."""
        # Note: Timeouts handled by _tick_timeouts
        try:
            order = self.exchange.fetch_order(st.order_id, st.symbol)

            if order.get("status") == "closed":
                filled = order.get("filled", 0)
                if filled >= st.amount * 0.95:
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
        try:
            order = self.exchange.fetch_order(st.order_id, st.symbol)
            filled = order.get("filled", 0)
            avg_exit_price = order.get("average", 0)
            exit_fee = order.get('fee', {}).get('cost', 0) or 0

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

            self.pnl_service.remove_unrealized_position(st.symbol)
            self.portfolio.remove_held_asset(st.symbol)

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
            logger.error(f"Post trade error {st.symbol}: {e}")
            st.amount = 0.0
            st.cooldown_until = time.time() + (15 * 60)
            self._emit_event(st, FSMEvent.TRADE_COMPLETE, ctx)

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

                # Tick timeouts first (cooldowns, order timeouts)
                self._tick_timeouts()

                # Heartbeat every 10 cycles
                if self.cycle_count % 10 == 0:
                    active = len([s for s in self.states.values() if s.phase not in [Phase.IDLE, Phase.WARMUP]])
                    positions = len([s for s in self.states.values() if s.phase == Phase.POSITION])
                    logger.info(f"💓 FSM Cycle #{self.cycle_count} - {len(self.states)} symbols | Active: {active} | Positions: {positions}")

                # Process all symbols with event-based FSM
                for symbol in self.watchlist.keys():
                    try:
                        md = self._build_context(symbol)
                        self._process_symbol(symbol, md)
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
        for st in self.states.values():
            if st.phase not in [Phase.IDLE, Phase.WARMUP, Phase.COOLDOWN]:
                update_stuck_metric(st)

    # ========== PUBLIC API ==========

    def get_states(self) -> Dict[str, CoinState]:
        """Get all states (for status table)."""
        return self.states

    def get_positions(self) -> Dict[str, CoinState]:
        """Get active positions."""
        return {symbol: st for symbol, st in self.states.items() if st.phase == Phase.POSITION}

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
