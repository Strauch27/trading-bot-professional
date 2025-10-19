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
import time
from collections import deque
from datetime import datetime, timedelta
from typing import Dict, Optional, Tuple, Any, List
import numpy as np
import config

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

    def on_trade_completed(self, symbol: str) -> None:
        """
        Notify service of completed trade for Mode 4 anchor reset.

        Args:
            symbol: Trading symbol that completed a trade
        """
        with self._lock:
            self._last_trade_close[symbol] = datetime.utcnow()
            logger.debug(f"Trade completion recorded for {symbol}")

    def evaluate_buy_signal(self, symbol: str, current_price: float,
                           drop_snapshot_store=None) -> Tuple[bool, Dict[str, Any]]:
        """
        Evaluate if symbol meets buy trigger criteria (V9_3).

        Args:
            symbol: Trading symbol
            current_price: Current market price
            drop_snapshot_store: Snapshot store with V9_3 anchor data (required)

        Returns:
            Tuple of (buy_triggered, context_data)
        """
        with self._lock:
            try:
                # V9_3: Read anchor from MarketSnapshot (required)
                if not drop_snapshot_store:
                    return False, {"error": "Snapshot store required for V9_3"}

                snapshot_entry = drop_snapshot_store.get(symbol)
                snapshot_ts = None
                snapshot = None
                if snapshot_entry:
                    if isinstance(snapshot_entry, dict) and 'snapshot' in snapshot_entry:
                        snapshot = snapshot_entry.get('snapshot')
                        snapshot_ts = snapshot_entry.get('ts')
                    else:
                        snapshot = snapshot_entry

                if not snapshot:
                    return False, {"error": f"No snapshot found for {symbol}"}

                stale_ttl = getattr(config, 'SNAPSHOT_STALE_TTL_S', 30.0)
                if snapshot_ts is not None and (time.time() - snapshot_ts) > stale_ttl:
                    return False, {"error": f"Snapshot for {symbol} is stale"}

                anchor = snapshot.get('windows', {}).get('anchor')
                if not anchor or anchor <= 0:
                    return False, {"error": "No valid anchor in snapshot"}

                # V9_3: Calculate trigger price
                trigger_price = anchor * self.drop_trigger_value

                # V9_3: Apply BUY_MODE logic
                buy_mode = getattr(config, "BUY_MODE", "PREDICTIVE")

                if buy_mode == "PREDICTIVE":
                    # PREDICTIVE mode: Buy zone threshold (stricter)
                    predictive_pct = getattr(config, "PREDICTIVE_BUY_ZONE_PCT", 0.995)
                    threshold = trigger_price * predictive_pct
                    buy_triggered = current_price <= threshold
                    threshold_used = threshold
                else:
                    # RAW mode: Direct trigger
                    buy_triggered = current_price <= trigger_price
                    threshold_used = trigger_price

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
                    "threshold": threshold_used,
                    "buy_mode": buy_mode,
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
                    logger.info(f"BUY TRIGGER HIT ({buy_mode}): {symbol} at {current_price:.6f} "
                               f"(drop: {drop_pct:.2f}%, anchor: {anchor:.6f}, threshold: {threshold_used:.6f})")

                return buy_triggered, context

            except Exception as e:
                logger.error(f"Error evaluating buy signal for {symbol}: {e}")
                return False, {"error": str(e)}

    def _log_trigger_audit(self, symbol: str, context: Dict[str, Any]) -> None:
        """Log detailed trigger audit information (V9_3)."""
        try:
            # Create timestamp rounded to minute for easier analysis
            ts_minute = datetime.utcnow().replace(second=0, microsecond=0).isoformat() + "Z"

            audit_data = {
                "event_type": "DROP_TRIGGER_AUDIT",
                "symbol": symbol,
                "timestamp": ts_minute,
                "mode": context["mode"],
                "anchor": context["anchor"],
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

    def get_top_drops(self, limit: int = 10, drop_snapshot_store=None) -> List[Dict[str, Any]]:
        """
        Get symbols with biggest drops since their anchors (V9_3).

        Args:
            limit: Maximum number of symbols to return
            drop_snapshot_store: Snapshot store with V9_3 anchor data (required)

        Returns:
            List of drop data sorted by drop percentage
        """
        with self._lock:
            drops = []

            if not drop_snapshot_store:
                logger.warning("get_top_drops requires snapshot store for V9_3")
                return drops

            stale_ttl = getattr(config, 'SNAPSHOT_STALE_TTL_S', 30.0)
            now_ts = time.time()

            # Iterate over snapshots instead of price history
            for symbol, entry in drop_snapshot_store.items():
                snapshot = None
                snapshot_ts = None
                if isinstance(entry, dict) and 'snapshot' in entry:
                    snapshot = entry.get('snapshot')
                    snapshot_ts = entry.get('ts')
                else:
                    snapshot = entry

                if not snapshot:
                    continue

                if snapshot_ts is not None and (now_ts - snapshot_ts) > stale_ttl:
                    continue

                # Get price and anchor from snapshot
                current_price = snapshot.get('price', {}).get('last')
                anchor = snapshot.get('windows', {}).get('anchor')

                if not current_price or current_price <= 0:
                    continue
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
