#!/usr/bin/env python3
"""
V9_3-Compatible Anchor Manager

Manages price anchors for drop-trigger logic with 4 operating modes:
- Mode 1: Session-High (max price since bot start)
- Mode 2: Rolling-High (max price in lookback window)
- Mode 3: Hybrid (max of session + rolling)
- Mode 4: Persistent (with stale-reset, clamps, and persistence)
"""

import json
import time
import logging
from pathlib import Path
from typing import Dict, Optional

logger = logging.getLogger(__name__)


class AnchorManager:
    """
    Manages price anchors for drop-trigger logic.

    Implements V9_3-compatible anchor calculation with:
    - 4 operating modes (session/rolling/hybrid/persistent)
    - Stale-reset for old anchors
    - Over-peak clamp (anchor <= session_peak * (1 + clamp%))
    - Start-drop clamp (anchor >= start_price * (1 - max_drop%))
    """

    def __init__(self, base_path: str = "state/anchors", load_on_start: bool = True):
        """
        Initialize AnchorManager.

        Args:
            base_path: Directory for anchor persistence (Mode 4 only)
            load_on_start: Whether to load persisted anchors on startup (default: True with TTL check)
        """
        self.base_path = base_path
        self.load_on_start = load_on_start
        Path(self.base_path).mkdir(parents=True, exist_ok=True)

        # Persistent anchors (Mode 4): {symbol: {"anchor": float, "ts": float}}
        self._anchors: Dict[str, Dict[str, float]] = {}

        # Session tracking: {symbol: float}
        self._session_high: Dict[str, float] = {}
        self._session_start: Dict[str, float] = {}  # First price seen per symbol

        if self.load_on_start:
            self._load()
            logger.info(f"AnchorManager initialized with persisted anchors (base_path={self.base_path})")
        else:
            logger.info(f"AnchorManager initialized with fresh start (base_path={self.base_path}, persistence disabled on startup)")

    def note_price(self, symbol: str, price: float, now: float) -> None:
        """
        Track price for anchor calculation.

        Updates session-high and session-start prices.

        Args:
            symbol: Trading symbol
            price: Current price
            now: Current timestamp
        """
        # Record session start price (first price seen)
        if symbol not in self._session_start:
            self._session_start[symbol] = price
            logger.debug(f"Session start price for {symbol}: {price}")

        # Update session high
        prev_high = self._session_high.get(symbol, price)
        self._session_high[symbol] = max(price, prev_high)

    def compute_anchor(
        self,
        symbol: str,
        last: float,
        now: float,
        rolling_peak: float
    ) -> float:
        """
        Compute anchor price based on configured mode.

        Args:
            symbol: Trading symbol
            last: Current price
            now: Current timestamp
            rolling_peak: Peak price from rolling window

        Returns:
            Anchor price for drop trigger calculation
        """
        import config

        mode = getattr(config, "DROP_TRIGGER_MODE", 4)
        session_peak = self._session_high.get(symbol, last)

        # Get previous anchor (Mode 4 only)
        anchor_prev = self._anchors.get(symbol, {}).get("anchor")
        anchor_ts = self._anchors.get(symbol, {}).get("ts", 0.0)

        # Mode-specific anchor calculation
        if mode == 1:
            # Mode 1: Session-High (most sensitive)
            anchor = session_peak
        elif mode == 2:
            # Mode 2: Rolling-High (time-based window)
            anchor = rolling_peak
        elif mode == 3:
            # Mode 3: Hybrid (max of session + rolling)
            anchor = max(session_peak, rolling_peak)
        else:
            # Mode 4: Persistent with clamps (most robust)
            base = max(session_peak, rolling_peak)

            # Start with previous anchor or base
            if anchor_prev is None:
                anchor = base
            else:
                # Anchor can only rise, never fall below base
                anchor = max(base, anchor_prev)

            # Stale-Reset: If anchor is too old, reset to base
            stale_min = getattr(config, "ANCHOR_STALE_MINUTES", 60)
            if now - anchor_ts > stale_min * 60:
                anchor = base
                logger.debug(f"{symbol}: Stale anchor reset (age={int((now-anchor_ts)/60)}min > {stale_min}min)")

        # Apply clamps (all modes)
        anchor = self._apply_clamps(symbol, anchor, session_peak)

        # Persist in Mode 4 only
        if mode == 4:
            self._anchors[symbol] = {
                "anchor": float(anchor),
                "ts": float(now)
            }

        return float(anchor)

    def _apply_clamps(self, symbol: str, anchor: float, session_peak: float) -> float:
        """
        Apply clamps to anchor value.

        Clamps:
        1. Over-Peak-Clamp: anchor <= session_peak * (1 + max_pct)
        2. Start-Drop-Clamp: anchor >= session_start * (1 - max_drop_pct)

        Args:
            symbol: Trading symbol
            anchor: Raw anchor value
            session_peak: Current session peak

        Returns:
            Clamped anchor value
        """
        import config

        # Over-Peak-Clamp: Anchor can't be too far above session peak
        clamp_pct = getattr(config, "ANCHOR_CLAMP_MAX_ABOVE_PEAK_PCT", 0.5) / 100.0
        max_anchor = session_peak * (1.0 + clamp_pct)
        if anchor > max_anchor:
            logger.debug(f"{symbol}: Over-peak clamp {anchor:.6f} -> {max_anchor:.6f}")
            anchor = max_anchor

        # Start-Drop-Clamp: Anchor can't be too far below session start
        if symbol in self._session_start:
            max_drop_pct = getattr(config, "ANCHOR_MAX_START_DROP_PCT", 8.0) / 100.0
            min_anchor = self._session_start[symbol] * (1.0 - max_drop_pct)
            if anchor < min_anchor:
                logger.debug(f"{symbol}: Start-drop clamp {anchor:.6f} -> {min_anchor:.6f}")
                anchor = min_anchor

        return anchor

    def get_session_peak(self, symbol: str) -> Optional[float]:
        """
        Get current session peak for symbol.

        Args:
            symbol: Trading symbol

        Returns:
            Session peak price or None if not tracked
        """
        return self._session_high.get(symbol)

    def get_session_start(self, symbol: str) -> Optional[float]:
        """
        Get session start price for symbol.

        Args:
            symbol: Trading symbol

        Returns:
            Session start price or None if not tracked
        """
        return self._session_start.get(symbol)

    def save(self) -> None:
        """
        Persist anchors to disk (Mode 4 only).

        Saves anchor data to JSON file in base_path using atomic writes.
        Uses .tmp + rename pattern for crash safety.
        """
        if not self._anchors:
            return

        path = Path(self.base_path) / "anchors.json"
        tmp_path = path.with_suffix(".json.tmp")

        try:
            # Write to temporary file first
            with tmp_path.open("w", encoding="utf-8") as f:
                json.dump(self._anchors, f, indent=2)

            # Atomic rename (crashes can't corrupt the file)
            tmp_path.rename(path)

            logger.debug(f"Saved {len(self._anchors)} anchors to {path} (atomic)")
        except Exception as e:
            logger.warning(f"Failed to save anchors: {e}")
            # Clean up temporary file if it exists
            if tmp_path.exists():
                try:
                    tmp_path.unlink()
                except Exception:
                    pass

    def _load(self) -> None:
        """
        Load persisted anchors from disk with TTL check (Mode 4 only).

        Loads anchor data from JSON file if available, filtering out
        anchors older than ANCHOR_MAX_AGE_HOURS.
        """
        path = Path(self.base_path) / "anchors.json"
        if not path.exists():
            return

        try:
            with path.open("r") as f:
                loaded_anchors = json.load(f)

            # Import config for TTL setting
            import config
            max_age_hours = getattr(config, "ANCHOR_MAX_AGE_HOURS", 24)
            max_age_seconds = max_age_hours * 3600
            now = time.time()

            # Filter out stale anchors (older than max_age_hours)
            valid_anchors = {}
            discarded_count = 0

            for symbol, anchor_data in loaded_anchors.items():
                anchor_ts = anchor_data.get("ts", 0)
                age_seconds = now - anchor_ts

                if age_seconds <= max_age_seconds:
                    valid_anchors[symbol] = anchor_data
                    logger.debug(f"Loaded anchor for {symbol}: {anchor_data['anchor']:.6f} (age: {age_seconds/3600:.1f}h)")
                else:
                    discarded_count += 1
                    logger.info(f"Discarded stale anchor for {symbol} (age: {age_seconds/3600:.1f}h > {max_age_hours}h)")

            self._anchors = valid_anchors

            if valid_anchors:
                logger.info(f"Loaded {len(valid_anchors)} persisted anchors from {path} (discarded {discarded_count} stale)")
            elif discarded_count > 0:
                logger.info(f"All {discarded_count} anchors were stale (> {max_age_hours}h), starting fresh")
            else:
                logger.info("No anchors found in persistence file")

        except Exception as e:
            logger.warning(f"Failed to load anchors: {e}")
            self._anchors = {}

    def clear(self) -> None:
        """Clear all anchor state (for testing)."""
        self._anchors.clear()
        self._session_high.clear()
        self._session_start.clear()
        logger.info("Anchor state cleared")

    def reset_anchor(self, symbol: str, price: float, now: float) -> None:
        """
        Reset anchor to specific price (called after buy fill).

        This allows the anchor to be reset to the fill price after a successful buy,
        enabling the drop trigger to work from the new entry price.

        Args:
            symbol: Trading symbol
            price: New anchor price (typically the fill price)
            now: Current timestamp
        """
        import config
        mode = getattr(config, "DROP_TRIGGER_MODE", 4)

        if mode == 4:
            # Only reset in Mode 4 (Persistent mode)
            self._anchors[symbol] = {
                "anchor": float(price),
                "ts": float(now)
            }
            logger.info(f"Anchor reset for {symbol}: {price:.6f} (post-buy, mode={mode})")
        else:
            # In other modes, anchor is recalculated automatically
            logger.debug(f"Anchor reset skipped for {symbol} (mode={mode} does not use persistent anchors)")
