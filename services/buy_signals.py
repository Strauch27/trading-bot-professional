#!/usr/bin/env python3
"""
BuySignalService - Drop Trigger Implementation

Implements the complete DROP_TRIGGER_VALUE logic with all 4 trigger modes:
- Mode 1: Session-Peak (highest since bot start)
- Mode 2: Rolling-Window-Peak (last X minutes)
- Mode 3: Max of both Session and Rolling peaks
- Mode 4: Persistent anchor with reset after trades

Thread-safe implementation with comprehensive logging and audit trail.
"""

import logging
import threading
from collections import deque
from datetime import datetime, timedelta
from typing import Dict, Optional, Tuple, Any, List
import numpy as np

logger = logging.getLogger(__name__)


class BuySignalService:
    """
    Service for evaluating buy signals based on DROP_TRIGGER_VALUE logic.

    Provides thread-safe drop-trigger evaluation with configurable modes
    and comprehensive audit logging.
    """

    def __init__(self,
                 drop_trigger_value: float = 0.96,
                 drop_trigger_mode: int = 4,
                 drop_trigger_lookback_min: int = 60,
                 enable_minutely_audit: bool = True,
                 history_retention_min: int = 1440):  # 24 hours
        """
        Initialize BuySignalService.

        Args:
            drop_trigger_value: Trigger threshold (0.96 = -4% drop)
            drop_trigger_mode: Anchor calculation mode (1-4)
            drop_trigger_lookback_min: Rolling window size in minutes
            enable_minutely_audit: Enable detailed audit logging
            history_retention_min: Price history retention in minutes
        """
        self.drop_trigger_value = drop_trigger_value
        self.drop_trigger_mode = drop_trigger_mode
        self.drop_trigger_lookback_min = drop_trigger_lookback_min
        self.enable_minutely_audit = enable_minutely_audit
        self.history_retention_min = history_retention_min

        # Thread safety
        self._lock = threading.RLock()

        # Price history storage (symbol -> deque of (timestamp, price))
        self._price_history: Dict[str, deque] = {}

        # Session peaks for Mode 1 and 3 (symbol -> peak_price)
        self._session_peaks: Dict[str, float] = {}

        # Persistent anchors for Mode 4 (symbol -> (anchor_price, timestamp))
        self._persistent_anchors: Dict[str, Tuple[float, datetime]] = {}

        # Trade completion tracking for Mode 4 anchor resets
        self._last_trade_close: Dict[str, datetime] = {}

        logger.info(f"BuySignalService initialized: mode={drop_trigger_mode}, "
                   f"trigger={drop_trigger_value}, lookback={drop_trigger_lookback_min}m")

    def update_price(self, symbol: str, price: float, timestamp: Optional[datetime] = None) -> None:
        """
        Update price history for a symbol.

        Args:
            symbol: Trading symbol
            price: Current price
            timestamp: Price timestamp (uses current time if None)
        """
        if timestamp is None:
            timestamp = datetime.utcnow()

        with self._lock:
            # Initialize price history if needed
            if symbol not in self._price_history:
                self._price_history[symbol] = deque(maxlen=self.history_retention_min + 60)

            # Add new price point
            self._price_history[symbol].append((timestamp, price))

            # Update session peak
            if symbol not in self._session_peaks:
                self._session_peaks[symbol] = price
            else:
                self._session_peaks[symbol] = max(self._session_peaks[symbol], price)

            # Cleanup old data
            self._cleanup_old_data(symbol, timestamp)

    def _cleanup_old_data(self, symbol: str, current_time: datetime) -> None:
        """Remove price data older than retention period."""
        if symbol not in self._price_history:
            return

        cutoff_time = current_time - timedelta(minutes=self.history_retention_min)
        history = self._price_history[symbol]

        # Remove old entries from the left
        while history and history[0][0] < cutoff_time:
            history.popleft()

    def _calculate_anchor_price(self, symbol: str, current_price: float) -> Optional[float]:
        """
        Calculate anchor price based on configured mode.

        Args:
            symbol: Trading symbol
            current_price: Current market price

        Returns:
            Anchor price or None if insufficient data
        """
        with self._lock:
            if symbol not in self._price_history:
                logger.warning(f"No price history for {symbol}")
                return None

            history = self._price_history[symbol]
            if not history:
                return None

            prices = [price for _, price in history]

            if self.drop_trigger_mode == 1:
                # Mode 1: Session-Peak (highest since bot start)
                anchor = self._session_peaks.get(symbol, current_price)

            elif self.drop_trigger_mode == 2:
                # Mode 2: Rolling-Window-Peak (last X minutes)
                lookback = min(self.drop_trigger_lookback_min, len(prices))
                if lookback > 0:
                    anchor = max(prices[-lookback:])
                else:
                    anchor = current_price

            elif self.drop_trigger_mode == 3:
                # Mode 3: Max of Session-Peak and Rolling-Window-Peak
                session_peak = self._session_peaks.get(symbol, current_price)

                lookback = min(self.drop_trigger_lookback_min, len(prices))
                rolling_peak = max(prices[-lookback:]) if lookback > 0 else current_price

                anchor = max(session_peak, rolling_peak)

            elif self.drop_trigger_mode == 4:
                # Mode 4: Persistent anchor with clamps and reset after trades
                anchor = self._get_persistent_anchor(symbol, current_price, prices)

            else:
                logger.error(f"Invalid drop_trigger_mode: {self.drop_trigger_mode}")
                return None

            return anchor

    def _get_persistent_anchor(self, symbol: str, current_price: float, prices: List[float]) -> float:
        """
        Get persistent anchor for Mode 4 with trade-based reset logic.
        """
        current_time = datetime.utcnow()

        # Check if we have a persistent anchor
        if symbol in self._persistent_anchors:
            anchor_price, anchor_time = self._persistent_anchors[symbol]

            # Check if anchor needs reset due to completed trade
            last_close = self._last_trade_close.get(symbol)
            if last_close and last_close > anchor_time:
                # Reset anchor after trade completion
                logger.info(f"Resetting persistent anchor for {symbol} after trade completion")
                anchor_price = max(prices) if prices else current_price
                self._persistent_anchors[symbol] = (anchor_price, current_time)
            else:
                # Update anchor if current price is higher
                if current_price > anchor_price:
                    anchor_price = current_price
                    self._persistent_anchors[symbol] = (anchor_price, current_time)
        else:
            # Initialize persistent anchor
            anchor_price = max(prices) if prices else current_price
            self._persistent_anchors[symbol] = (anchor_price, current_time)

        return anchor_price

    def on_trade_completed(self, symbol: str) -> None:
        """
        Notify service of completed trade for Mode 4 anchor reset.

        Args:
            symbol: Trading symbol that completed a trade
        """
        with self._lock:
            self._last_trade_close[symbol] = datetime.utcnow()
            logger.debug(f"Trade completion recorded for {symbol}")

    def evaluate_buy_signal(self, symbol: str, current_price: float) -> Tuple[bool, Dict[str, Any]]:
        """
        Evaluate if symbol meets buy trigger criteria.

        Args:
            symbol: Trading symbol
            current_price: Current market price

        Returns:
            Tuple of (buy_triggered, context_data)
        """
        with self._lock:
            try:
                # Calculate anchor price
                anchor = self._calculate_anchor_price(symbol, current_price)
                if anchor is None or anchor <= 0:
                    return False, {"error": "No valid anchor price"}

                # Calculate trigger price
                trigger_price = anchor * self.drop_trigger_value

                # Check if trigger is hit
                buy_triggered = current_price <= trigger_price

                # Calculate metrics
                drop_ratio = (current_price / anchor) - 1.0  # Negative = drop
                drop_pct = drop_ratio * 100.0
                restweg_ratio = (current_price / anchor) - self.drop_trigger_value
                restweg_bps = int(round(restweg_ratio * 10000))  # Basis points

                # Build context data
                context = {
                    "symbol": symbol,
                    "current_price": current_price,
                    "anchor": anchor,
                    "trigger_price": trigger_price,
                    "drop_pct": drop_pct,
                    "restweg_bps": restweg_bps,
                    "mode": self.drop_trigger_mode,
                    "lookback_min": self.drop_trigger_lookback_min,
                    "trigger_value": self.drop_trigger_value,
                    "buy_triggered": buy_triggered
                }

                # Detailed audit logging
                if self.enable_minutely_audit:
                    self._log_trigger_audit(symbol, context)

                if buy_triggered:
                    logger.info(f"BUY TRIGGER HIT: {symbol} at {current_price:.6f} "
                               f"(drop: {drop_pct:.2f}%, anchor: {anchor:.6f})")

                return buy_triggered, context

            except Exception as e:
                logger.error(f"Error evaluating buy signal for {symbol}: {e}")
                return False, {"error": str(e)}

    def _log_trigger_audit(self, symbol: str, context: Dict[str, Any]) -> None:
        """Log detailed trigger audit information."""
        try:
            # Get additional price history metrics
            history = self._price_history.get(symbol, deque())
            prices = [price for _, price in history]

            # Calculate session and rolling peaks for comparison
            session_peak = max(prices) if prices else None
            lookback = min(self.drop_trigger_lookback_min, len(prices))
            rolling_peak = max(prices[-lookback:]) if lookback > 0 and prices else None

            # Create timestamp rounded to minute for easier analysis
            ts_minute = datetime.utcnow().replace(second=0, microsecond=0).isoformat() + "Z"

            audit_data = {
                "event_type": "DROP_TRIGGER_AUDIT",
                "symbol": symbol,
                "timestamp": ts_minute,
                "mode": context["mode"],
                "lookback_min": context["lookback_min"],
                "anchor": context["anchor"],
                "session_peak": session_peak,
                "rolling_peak": rolling_peak,
                "current_price": context["current_price"],
                "trigger_value": context["trigger_value"],
                "trigger_price": context["trigger_price"],
                "drop_pct": context["drop_pct"],
                "restweg_bps": context["restweg_bps"],
                "buy_triggered": context["buy_triggered"]
            }

            logger.debug(f"DROP_TRIGGER_AUDIT: {symbol}", extra=audit_data)

        except Exception as e:
            logger.warning(f"Audit logging failed for {symbol}: {e}")

    def get_top_drops(self, limit: int = 10) -> List[Dict[str, Any]]:
        """
        Get symbols with biggest drops since their anchors.

        Args:
            limit: Maximum number of symbols to return

        Returns:
            List of drop data sorted by drop percentage
        """
        with self._lock:
            drops = []

            for symbol in self._price_history:
                if not self._price_history[symbol]:
                    continue

                # Get current price
                _, current_price = self._price_history[symbol][-1]

                # Calculate anchor
                anchor = self._calculate_anchor_price(symbol, current_price)
                if not anchor or anchor <= 0:
                    continue

                # Calculate drop metrics
                drop_ratio = (current_price / anchor) - 1.0
                drop_pct = drop_ratio * 100.0
                trigger_price = anchor * self.drop_trigger_value
                restweg_ratio = (current_price / anchor) - self.drop_trigger_value
                restweg_bps = int(round(restweg_ratio * 10000))

                drops.append({
                    "symbol": symbol,
                    "drop_pct": drop_pct,
                    "current_price": current_price,
                    "anchor": anchor,
                    "trigger_price": trigger_price,
                    "restweg_bps": restweg_bps,
                    "hit_trigger": current_price <= trigger_price
                })

            # Sort by drop percentage (most negative first)
            drops.sort(key=lambda x: x["drop_pct"])
            return drops[:limit]

    def get_statistics(self) -> Dict[str, Any]:
        """Get service statistics and status."""
        with self._lock:
            return {
                "symbols_tracked": len(self._price_history),
                "session_peaks": len(self._session_peaks),
                "persistent_anchors": len(self._persistent_anchors),
                "config": {
                    "drop_trigger_value": self.drop_trigger_value,
                    "drop_trigger_mode": self.drop_trigger_mode,
                    "drop_trigger_lookback_min": self.drop_trigger_lookback_min,
                    "enable_minutely_audit": self.enable_minutely_audit
                }
            }