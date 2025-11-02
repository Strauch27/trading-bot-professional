#!/usr/bin/env python3
"""
Idempotency Store - Prevents duplicate event processing

Uses event fingerprints (symbol + event + order_id + timestamp) to detect duplicates.
"""

import logging
import threading
import time
from typing import Dict, Optional, Set, Tuple

from core.fsm.fsm_events import EventContext

logger = logging.getLogger(__name__)


class IdempotencyStore:
    """
    Thread-safe store for detecting duplicate events.

    Stores (symbol, event, order_id, timestamp_bucket) tuples.
    Auto-expires entries after 1 hour (CRITICAL FIX C-FSM-02: increased from 5 min).
    """

    def __init__(self, expiry_seconds: int = 3600):  # CRITICAL FIX (C-FSM-02): 1 hour instead of 5 min
        self._store: Set[Tuple] = set()
        self._expiry_map: Dict[Tuple, float] = {}  # fingerprint → expiry_time
        self._lock = threading.RLock()
        self._expiry_seconds = expiry_seconds
        self._total_checks = 0
        self._duplicate_count = 0

    def _make_fingerprint(self, ctx: EventContext) -> Tuple:
        """
        Create event fingerprint.

        CRITICAL FIX (Punkt 6): Include decision_id and phase for proper scoping
        Schema: (symbol, decision_id, phase, event, order_id, timestamp_bucket)

        Timestamp is bucketed to 1-second intervals to handle slight timing variations.
        """
        timestamp_bucket = int(ctx.timestamp)

        # Get decision_id from context (if available)
        decision_id = ctx.data.get('decision_id', '') if hasattr(ctx, 'data') and ctx.data else ''

        # Get phase from context (if available)
        phase = ctx.data.get('phase', '') if hasattr(ctx, 'data') and ctx.data else ''

        return (
            ctx.symbol,
            decision_id,
            phase,
            ctx.event,
            ctx.order_id or "",
            timestamp_bucket
        )

    def is_duplicate(self, ctx: EventContext) -> bool:
        """
        Check if event has already been processed.

        Returns:
            True if duplicate, False if first occurrence
        """
        with self._lock:
            self._cleanup_expired()

            fingerprint = self._make_fingerprint(ctx)
            self._total_checks += 1

            is_dup = fingerprint in self._store

            if is_dup:
                self._duplicate_count += 1
                logger.debug(
                    f"Duplicate event detected: {ctx.symbol} {ctx.event.name} "
                    f"(order_id={ctx.order_id})"
                )

            return is_dup

    def mark_processed(self, ctx: EventContext) -> None:
        """Mark event as processed"""
        with self._lock:
            fingerprint = self._make_fingerprint(ctx)
            expiry_time = time.time() + self._expiry_seconds

            self._store.add(fingerprint)
            self._expiry_map[fingerprint] = expiry_time

            logger.debug(
                f"Event marked as processed: {ctx.symbol} {ctx.event.name} "
                f"(expires in {self._expiry_seconds}s)"
            )

    def _cleanup_expired(self):
        """Remove expired entries (called automatically)"""
        now = time.time()
        expired = [
            fp for fp, expiry in self._expiry_map.items()
            if expiry < now
        ]

        for fp in expired:
            self._store.discard(fp)
            del self._expiry_map[fp]

        if expired:
            logger.debug(f"Cleaned up {len(expired)} expired idempotency entries")

    def get_size(self) -> int:
        """Get current store size (for monitoring)"""
        with self._lock:
            return len(self._store)

    def get_stats(self) -> Dict:
        """Get idempotency statistics"""
        with self._lock:
            return {
                'store_size': len(self._store),
                'total_checks': self._total_checks,
                'duplicate_count': self._duplicate_count,
                'duplicate_rate': (self._duplicate_count / max(1, self._total_checks)),
                'expiry_seconds': self._expiry_seconds
            }

    def clear(self) -> None:
        """Clear all entries (for testing)"""
        with self._lock:
            self._store.clear()
            self._expiry_map.clear()
            self._total_checks = 0
            self._duplicate_count = 0
            logger.info("Idempotency store cleared")

    def force_cleanup(self) -> int:
        """Force cleanup of expired entries (manual trigger)"""
        with self._lock:
            before_size = len(self._store)
            self._cleanup_expired()
            after_size = len(self._store)
            removed = before_size - after_size

            if removed > 0:
                logger.info(f"Force cleanup removed {removed} expired entries")

            return removed


# Global singleton
_idempotency_store: Optional[IdempotencyStore] = None


def get_idempotency_store() -> IdempotencyStore:
    """Get global idempotency store singleton"""
    global _idempotency_store
    if _idempotency_store is None:
        _idempotency_store = IdempotencyStore()
        logger.info("Idempotency store initialized")
    return _idempotency_store
