#!/usr/bin/env python3
"""
Ghost Store - Tracks rejected/aborted buy intents

Maintains temporary records of buy intents that failed validation.
Useful for UI transparency and debugging.

Ghost positions:
- Do NOT affect PnL calculations
- Do NOT affect budget calculations
- Are purely informational
- Auto-expire after TTL (default 24h)
"""

import logging
import threading
import time
from typing import Dict, List, Optional

logger = logging.getLogger(__name__)


class GhostStore:
    """
    Thread-safe store for ghost positions (rejected buy intents).

    Ghost positions are buy intents that failed validation and were aborted.
    They're tracked temporarily for UI transparency and debugging.
    """

    def __init__(self, ttl_sec: int = 86400):
        """
        Initialize ghost store.

        Args:
            ttl_sec: Time-to-live for ghost entries (default 24h)
        """
        self._ttl = ttl_sec
        self._items: Dict[str, dict] = {}  # intent_id -> ghost
        self._lock = threading.RLock()
        logger.info(f"GhostStore initialized (TTL={ttl_sec}s)")

    def create(
        self,
        intent_id: str,
        symbol: str,
        q_price: float,
        q_amount: float,
        violations: List[str],
        abort_reason: str,
        raw_price: Optional[float] = None,
        raw_amount: Optional[float] = None,
        market_precision: Optional[dict] = None
    ) -> dict:
        """
        Create a new ghost position.

        Args:
            intent_id: Unique intent identifier
            symbol: Trading symbol
            q_price: Quantized price
            q_amount: Quantized amount
            violations: List of violation types
            abort_reason: Reason for abortion
            raw_price: Original raw price (before quantization)
            raw_amount: Original raw amount (before quantization)
            market_precision: Market precision dict (tick, step, min_cost)

        Returns:
            Ghost position dict
        """
        with self._lock:
            ghost = {
                "id": intent_id,
                "symbol": symbol,
                "created_at": time.time(),
                "state": "GHOST",
                "violations": violations,
                "abort_reason": abort_reason,
                "q_price": q_price,
                "q_amount": q_amount,
                "raw_price": raw_price,
                "raw_amount": raw_amount,
                "market_precision": market_precision,
                "size": 0,
                "exposure": 0,
                "affects_pnl": False,
                "affects_budget": False
            }

            self._items[intent_id] = ghost

            logger.info(
                f"[GHOST_CREATED] {symbol} intent={intent_id[:8]} "
                f"reason={abort_reason} violations={violations}"
            )

            return ghost

    def remove_by_intent(self, intent_id: str) -> Optional[dict]:
        """
        Remove ghost position by intent ID.

        Args:
            intent_id: Intent identifier

        Returns:
            Removed ghost dict or None if not found
        """
        with self._lock:
            ghost = self._items.pop(intent_id, None)
            if ghost:
                logger.debug(
                    f"[GHOST_REMOVED] {ghost['symbol']} intent={intent_id[:8]}"
                )
            return ghost

    def remove_by_symbol(self, symbol: str) -> List[dict]:
        """
        Remove all ghost positions for a symbol.

        Args:
            symbol: Trading symbol

        Returns:
            List of removed ghosts
        """
        with self._lock:
            removed = []
            to_remove = [
                iid for iid, g in self._items.items()
                if g["symbol"] == symbol
            ]

            for iid in to_remove:
                ghost = self._items.pop(iid)
                removed.append(ghost)

            if removed:
                logger.info(
                    f"[GHOST_REMOVED] {symbol} removed {len(removed)} ghosts"
                )

            return removed

    def get(self, intent_id: str) -> Optional[dict]:
        """
        Get ghost position by intent ID.

        Args:
            intent_id: Intent identifier

        Returns:
            Ghost dict or None if not found
        """
        with self._lock:
            return self._items.get(intent_id)

    def list_active(self, symbol: Optional[str] = None) -> List[dict]:
        """
        List active ghost positions (not expired).

        Args:
            symbol: Optional symbol filter

        Returns:
            List of active ghost dicts
        """
        now = time.time()

        with self._lock:
            active = []
            expired_ids = []

            for iid, ghost in self._items.items():
                age = now - ghost["created_at"]

                if age > self._ttl:
                    expired_ids.append(iid)
                    continue

                if symbol is None or ghost["symbol"] == symbol:
                    active.append(ghost)

            # Cleanup expired
            for iid in expired_ids:
                del self._items[iid]

            if expired_ids:
                logger.debug(f"[GHOST_CLEANUP] Removed {len(expired_ids)} expired ghosts")

            return active

    def list_all(self) -> List[dict]:
        """
        List all ghost positions (including expired).

        Returns:
            List of all ghost dicts
        """
        with self._lock:
            return list(self._items.values())

    def count(self, symbol: Optional[str] = None) -> int:
        """
        Count active ghost positions.

        Args:
            symbol: Optional symbol filter

        Returns:
            Count of active ghosts
        """
        return len(self.list_active(symbol=symbol))

    def clear(self):
        """Clear all ghost positions."""
        with self._lock:
            count = len(self._items)
            self._items.clear()
            logger.info(f"[GHOST_CLEARED] Removed all {count} ghosts")

    def get_statistics(self) -> dict:
        """
        Get ghost store statistics.

        Returns:
            Statistics dict
        """
        with self._lock:
            total = len(self._items)
            active = self.list_active()

            by_reason = {}
            by_symbol = {}

            for ghost in active:
                reason = ghost["abort_reason"]
                symbol = ghost["symbol"]

                by_reason[reason] = by_reason.get(reason, 0) + 1
                by_symbol[symbol] = by_symbol.get(symbol, 0) + 1

            return {
                "total": total,
                "active": len(active),
                "expired": total - len(active),
                "by_reason": by_reason,
                "by_symbol": by_symbol,
                "ttl_sec": self._ttl
            }
