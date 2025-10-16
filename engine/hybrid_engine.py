"""
Hybrid Engine - Parallel Operation Mode

Allows running legacy TradingEngine and FSMTradingEngine side-by-side for:
- Safe migration validation
- A/B testing
- Gradual rollout

Modes:
- "legacy": Only legacy engine (default, safe)
- "fsm": Only FSM engine (full migration)
- "both": Both engines in parallel (validation mode)
"""

import logging
import threading
from typing import Dict, Any, Optional

from .engine import TradingEngine as LegacyEngine
from .fsm_engine import FSMTradingEngine
from core.fsm.state import CoinState
from core.fsm.phases import Phase

logger = logging.getLogger(__name__)


class HybridEngine:
    """
    Hybrid Engine for parallel operation of legacy and FSM engines.

    Use Cases:
    1. Validation: Run both, compare results
    2. Migration: Gradual shift from legacy to FSM
    3. Rollback: Quick fallback to legacy if issues
    """

    def __init__(self, exchange, portfolio, orderbookprovider, telegram=None,
                 watchlist=None, mode: str = "legacy", engine_config=None):
        """
        Initialize Hybrid Engine.

        Args:
            exchange: CCXT exchange instance
            portfolio: PortfolioManager instance
            orderbookprovider: Orderbook provider
            telegram: Telegram notifier (optional)
            watchlist: Dict of symbols to track
            mode: "legacy", "fsm", or "both"
            engine_config: Legacy engine config
        """
        logger.info(f"Initializing Hybrid Engine in mode: {mode}")

        self.mode = mode.lower()
        self.exchange = exchange
        self.portfolio = portfolio
        self.orderbookprovider = orderbookprovider
        self.telegram = telegram
        self.watchlist = watchlist or {}
        self.engine_config = engine_config

        # Engines
        self.legacy_engine: Optional[LegacyEngine] = None
        self.fsm_engine: Optional[FSMTradingEngine] = None

        # Validation tracking (for "both" mode)
        self.validation_stats = {
            "trades_legacy": 0,
            "trades_fsm": 0,
            "errors_legacy": 0,
            "errors_fsm": 0,
            "divergences": 0,
        }

        # Initialize engines based on mode
        self._initialize_engines()

        logger.info(f"Hybrid Engine initialized: mode={self.mode}")

    def _initialize_engines(self):
        """Initialize engines based on mode."""
        if self.mode in ["legacy", "both"]:
            logger.info("Initializing Legacy Engine...")
            try:
                self.legacy_engine = LegacyEngine(
                    exchange=self.exchange,
                    portfolio=self.portfolio,
                    orderbookprovider=self.orderbookprovider,
                    telegram=self.telegram,
                    mock_mode=False,
                    engine_config=self.engine_config,
                    watchlist=self.watchlist
                )
                logger.info("Legacy Engine initialized successfully")
            except Exception as e:
                logger.error(f"Legacy Engine initialization failed: {e}")
                if self.mode == "legacy":
                    raise  # Fatal for legacy-only mode

        if self.mode in ["fsm", "both"]:
            logger.info("Initializing FSM Engine...")
            try:
                self.fsm_engine = FSMTradingEngine(
                    exchange=self.exchange,
                    portfolio=self.portfolio,
                    orderbookprovider=self.orderbookprovider,
                    telegram=self.telegram if self.mode == "fsm" else None,  # Only notify in FSM-only mode
                    watchlist=self.watchlist
                )
                logger.info("FSM Engine initialized successfully")
            except Exception as e:
                logger.error(f"FSM Engine initialization failed: {e}")
                if self.mode == "fsm":
                    raise  # Fatal for FSM-only mode

    def start(self):
        """Start active engines based on mode."""
        logger.info(f"Starting Hybrid Engine (mode={self.mode})...")

        if self.mode in ["legacy", "both"] and self.legacy_engine:
            logger.info("Starting Legacy Engine...")
            self.legacy_engine.start()

        if self.mode in ["fsm", "both"] and self.fsm_engine:
            logger.info("Starting FSM Engine...")
            self.fsm_engine.start()

        # Start validation thread if in "both" mode
        if self.mode == "both":
            self._start_validation_monitor()

        logger.info("Hybrid Engine started successfully")

    def stop(self):
        """Stop all active engines."""
        logger.info("Stopping Hybrid Engine...")

        if self.legacy_engine:
            logger.info("Stopping Legacy Engine...")
            self.legacy_engine.stop()

        if self.fsm_engine:
            logger.info("Stopping FSM Engine...")
            self.fsm_engine.stop()

        # Log final validation stats if in "both" mode
        if self.mode == "both":
            logger.info(f"Final Validation Stats: {self.validation_stats}")

        logger.info("Hybrid Engine stopped")

    def _start_validation_monitor(self):
        """Start background validation monitoring (for 'both' mode)."""
        def validation_loop():
            import time
            logger.info("Validation monitor started")

            while self.is_running():
                try:
                    time.sleep(60)  # Check every minute

                    # Compare states
                    legacy_positions = len(self.legacy_engine.positions) if self.legacy_engine else 0
                    fsm_positions = len(self.fsm_engine.get_positions()) if self.fsm_engine else 0

                    # Detect divergence
                    if abs(legacy_positions - fsm_positions) > 0:
                        self.validation_stats["divergences"] += 1
                        logger.warning(f"DIVERGENCE DETECTED: Legacy={legacy_positions} positions, FSM={fsm_positions} positions")

                    # Log validation status
                    logger.info(f"Validation Status: {self.validation_stats}")

                except Exception as e:
                    logger.error(f"Validation monitor error: {e}")

        validation_thread = threading.Thread(target=validation_loop, daemon=True, name="ValidationMonitor")
        validation_thread.start()

    # ========== State Access (mode-dependent) ==========

    def get_states(self) -> Dict[str, Any]:
        """
        Get current states from active engine.

        Returns:
            - FSM mode: Dict[str, CoinState]
            - Legacy mode: Bridged states from legacy positions
            - Both mode: FSM states (primary)
        """
        if self.mode == "fsm" or self.mode == "both":
            if self.fsm_engine:
                return self.fsm_engine.get_states()

        if self.mode == "legacy" and self.legacy_engine:
            # Bridge legacy positions to FSM-like states
            return self._bridge_legacy_to_fsm()

        return {}

    def _bridge_legacy_to_fsm(self) -> Dict[str, CoinState]:
        """
        Bridge legacy engine positions to FSM CoinState format.

        This allows status table and monitoring to work with legacy engine.
        """
        states = {}

        if not self.legacy_engine:
            return states

        try:
            # Convert legacy positions to CoinStates
            for symbol, position_data in self.legacy_engine.positions.items():
                st = CoinState(symbol=symbol)

                # Map legacy position to FSM state
                st.phase = Phase.POSITION  # Assume all positions are in POSITION phase
                st.amount = position_data.get('amount', 0.0)
                st.entry_price = position_data.get('buying_price', 0.0)
                st.entry_ts = position_data.get('time', 0.0)
                st.signal = position_data.get('signal', 'UNKNOWN')

                # Detect phase based on legacy state
                # (This is approximate - legacy doesn't have explicit phases)
                if symbol in getattr(self.legacy_engine, 'pending_exits', {}):
                    st.phase = Phase.EXIT_EVAL

                states[symbol] = st

            # Add symbols from watchlist that aren't in positions (assume IDLE)
            for symbol in self.watchlist.keys():
                if symbol not in states:
                    st = CoinState(symbol=symbol, phase=Phase.IDLE)
                    states[symbol] = st

        except Exception as e:
            logger.error(f"Legacy-to-FSM bridge error: {e}")

        return states

    def get_positions(self) -> Dict[str, Any]:
        """Get positions from active engine."""
        if self.mode == "fsm" or self.mode == "both":
            if self.fsm_engine:
                return self.fsm_engine.get_positions()

        if self.mode == "legacy" and self.legacy_engine:
            return self.legacy_engine.get_positions()

        return {}

    def get_statistics(self) -> Dict[str, Any]:
        """Get statistics from active engine(s)."""
        stats = {
            "mode": self.mode,
            "validation_stats": self.validation_stats if self.mode == "both" else None,
        }

        if self.legacy_engine:
            try:
                stats["legacy"] = self.legacy_engine.get_service_statistics()
            except Exception as e:
                logger.error(f"Failed to get legacy stats: {e}")

        if self.fsm_engine:
            try:
                stats["fsm"] = self.fsm_engine.get_statistics()
            except Exception as e:
                logger.error(f"Failed to get FSM stats: {e}")

        return stats

    def get_pnl_summary(self):
        """Get PnL summary from active engine."""
        if self.mode == "fsm" or self.mode == "both":
            if self.fsm_engine:
                return self.fsm_engine.get_pnl_summary()

        if self.mode == "legacy" and self.legacy_engine:
            return self.legacy_engine.get_pnl_summary()

        return None

    def is_running(self) -> bool:
        """Check if any engine is running."""
        legacy_running = self.legacy_engine.is_running() if self.legacy_engine else False
        fsm_running = self.fsm_engine.is_running() if self.fsm_engine else False

        return legacy_running or fsm_running

    # ========== Dashboard Compatibility (Delegation) ==========

    @property
    def topcoins(self) -> Dict[str, Any]:
        """
        Get topcoins/watchlist from active engine.

        Dashboard compatibility: Delegates to internal engine.
        """
        # Priority: legacy_engine > fsm_engine > watchlist
        if self.legacy_engine and hasattr(self.legacy_engine, 'topcoins'):
            return self.legacy_engine.topcoins

        if self.fsm_engine and hasattr(self.fsm_engine, 'watchlist'):
            return self.fsm_engine.watchlist

        # Fallback to watchlist passed during init
        return self.watchlist or {}

    @property
    def rolling_windows(self) -> Dict[str, Any]:
        """
        Get rolling windows from active engine.

        Dashboard compatibility: Delegates to internal engine.
        """
        if self.legacy_engine and hasattr(self.legacy_engine, 'rolling_windows'):
            return self.legacy_engine.rolling_windows

        if self.fsm_engine and hasattr(self.fsm_engine, 'rolling_windows'):
            return self.fsm_engine.rolling_windows

        return {}

    def get_current_price(self, symbol: str) -> Optional[float]:
        """
        Get current price from active engine.

        Dashboard compatibility: Delegates to internal engine's market data.
        """
        # Try legacy engine first
        if self.legacy_engine and hasattr(self.legacy_engine, 'get_current_price'):
            try:
                return self.legacy_engine.get_current_price(symbol)
            except Exception as e:
                logger.debug(f"Legacy engine price fetch failed for {symbol}: {e}")

        # Try FSM engine
        if self.fsm_engine and hasattr(self.fsm_engine, 'market_data'):
            try:
                return self.fsm_engine.market_data.get_price(symbol)
            except Exception as e:
                logger.debug(f"FSM engine price fetch failed for {symbol}: {e}")

        return None

    def get_drop_anchor(self, symbol: str) -> Optional[float]:
        """
        Get drop anchor from portfolio.

        Dashboard compatibility: Delegates to portfolio manager.
        """
        try:
            if self.portfolio and hasattr(self.portfolio, 'get_drop_anchor'):
                return self.portfolio.get_drop_anchor(symbol)
        except Exception as e:
            logger.debug(f"Drop anchor fetch failed for {symbol}: {e}")

        return None

    # ========== Manual Operations (delegate to active engine) ==========

    def add_manual_position(self, symbol: str, amount: float, entry_price: float, **kwargs) -> bool:
        """Add manual position to active engine."""
        if self.mode == "fsm" and self.fsm_engine:
            logger.warning("Manual position not yet implemented for FSM engine")
            return False

        if self.legacy_engine:
            return self.legacy_engine.add_manual_position(symbol, amount, entry_price, **kwargs)

        return False

    def exit_position(self, symbol: str, reason: str = "MANUAL_EXIT") -> bool:
        """Exit position in active engine."""
        if self.mode == "fsm" and self.fsm_engine:
            logger.warning("Manual exit not yet implemented for FSM engine")
            return False

        if self.legacy_engine:
            return self.legacy_engine.exit_position(symbol, reason)

        return False

    # ========== Validation & Comparison ==========

    def compare_engines(self) -> Dict[str, Any]:
        """
        Compare legacy and FSM engine states (for validation).

        Returns:
            Dict with comparison results
        """
        if self.mode != "both":
            return {"error": "Not in 'both' mode"}

        comparison = {
            "timestamp": __import__('time').time(),
            "positions": {
                "legacy": len(self.legacy_engine.positions) if self.legacy_engine else 0,
                "fsm": len(self.fsm_engine.get_positions()) if self.fsm_engine else 0,
                "match": False,
            },
            "symbols": {
                "legacy": set(self.legacy_engine.positions.keys()) if self.legacy_engine else set(),
                "fsm": set(self.fsm_engine.get_positions().keys()) if self.fsm_engine else set(),
            }
        }

        # Check if positions match
        comparison["positions"]["match"] = (
            comparison["positions"]["legacy"] == comparison["positions"]["fsm"]
        )

        # Symbol-level comparison
        comparison["symbols"]["both"] = comparison["symbols"]["legacy"] & comparison["symbols"]["fsm"]
        comparison["symbols"]["legacy_only"] = comparison["symbols"]["legacy"] - comparison["symbols"]["fsm"]
        comparison["symbols"]["fsm_only"] = comparison["symbols"]["fsm"] - comparison["symbols"]["legacy"]

        return comparison

    def get_validation_report(self) -> str:
        """
        Get human-readable validation report (for 'both' mode).

        Returns:
            Formatted validation report
        """
        if self.mode != "both":
            return "Validation not active (not in 'both' mode)"

        comparison = self.compare_engines()

        lines = [
            "=" * 60,
            "HYBRID ENGINE VALIDATION REPORT",
            "=" * 60,
            f"Mode: {self.mode}",
            "",
            "Position Count:",
            f"  Legacy: {comparison['positions']['legacy']}",
            f"  FSM:    {comparison['positions']['fsm']}",
            f"  Match:  {comparison['positions']['match']}",
            "",
            "Symbols:",
            f"  In Both:       {len(comparison['symbols']['both'])}",
            f"  Legacy Only:   {len(comparison['symbols']['legacy_only'])}",
            f"  FSM Only:      {len(comparison['symbols']['fsm_only'])}",
            "",
            "Statistics:",
            f"  Divergences:   {self.validation_stats['divergences']}",
            f"  Trades (L):    {self.validation_stats['trades_legacy']}",
            f"  Trades (F):    {self.validation_stats['trades_fsm']}",
            f"  Errors (L):    {self.validation_stats['errors_legacy']}",
            f"  Errors (F):    {self.validation_stats['errors_fsm']}",
            "=" * 60,
        ]

        return "\n".join(lines)

    def __repr__(self):
        return f"<HybridEngine: mode={self.mode}, legacy={self.legacy_engine is not None}, fsm={self.fsm_engine is not None}>"
