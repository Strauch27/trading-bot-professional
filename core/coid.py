#!/usr/bin/env python3
"""
Client Order ID (COID) Management with Idempotency

Provides deterministic, idempotent COID generation with persistent state tracking.
Ensures no duplicate orders on retries/crashes by reusing COIDs until terminal status.

Features:
- Deterministic COID generation: f"{decision_id}_{leg_idx}_{side}_{timestamp}"
- Persistent KV store with atomic writes
- Status tracking: PENDING → TERMINAL (FILLED/CANCELED/REJECTED)
- Reconciliation against exchange on startup
- Thread-safe operations

Akzeptanzkriterium:
Crash mitten im Retry → Neustart → keine zweite Order (COID wird wiederverwendet)
"""

from __future__ import annotations
import os
import json
import time
import threading
import logging
from typing import Optional, Dict, Any, List
from dataclasses import dataclass, asdict
from enum import Enum
from pathlib import Path

logger = logging.getLogger(__name__)


class COIDStatus(Enum):
    """COID lifecycle status"""
    PENDING = "pending"  # Order submitted, waiting for terminal state
    FILLED = "filled"  # Order fully filled (terminal)
    PARTIALLY_FILLED = "partially_filled"  # Order partially filled but still active
    CANCELED = "canceled"  # Order canceled (terminal)
    REJECTED = "rejected"  # Order rejected (terminal)
    EXPIRED = "expired"  # Order expired (terminal)
    UNKNOWN = "unknown"  # Initial/unknown state

    @property
    def is_terminal(self) -> bool:
        """Check if status is terminal (no more updates expected)"""
        return self in {
            COIDStatus.FILLED,
            COIDStatus.CANCELED,
            COIDStatus.REJECTED,
            COIDStatus.EXPIRED
        }


@dataclass
class COIDEntry:
    """
    COID entry in KV store.

    Attributes:
        coid: Client order ID
        decision_id: Trading decision ID
        leg_idx: Order leg index (for multi-leg orders)
        side: "buy" or "sell"
        symbol: Trading pair
        status: Current COID status
        order_id: Exchange order ID (if known)
        created_ts: Creation timestamp
        updated_ts: Last update timestamp
        attempt_count: Number of submission attempts
        metadata: Additional metadata
    """
    coid: str
    decision_id: str
    leg_idx: int
    side: str
    symbol: str
    status: str  # COIDStatus enum value
    order_id: Optional[str] = None
    created_ts: float = 0.0
    updated_ts: float = 0.0
    attempt_count: int = 0
    metadata: Dict[str, Any] = None

    def __post_init__(self):
        if self.metadata is None:
            self.metadata = {}
        if self.created_ts == 0.0:
            self.created_ts = time.time()
        if self.updated_ts == 0.0:
            self.updated_ts = self.created_ts

    def to_dict(self) -> dict:
        """Convert to dictionary for JSON serialization"""
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> COIDEntry:
        """Create from dictionary"""
        return cls(**data)

    @property
    def is_terminal(self) -> bool:
        """Check if entry is in terminal state"""
        try:
            return COIDStatus(self.status).is_terminal
        except ValueError:
            return False


class COIDManager:
    """
    Manages COID lifecycle with persistent storage.

    Thread-safe operations with file-based KV store.
    """

    def __init__(self, store_path: Optional[str] = None):
        """
        Initialize COID manager.

        Args:
            store_path: Path to COID KV store JSON file (defaults to state/coid_kv.json)
        """
        if store_path is None:
            # Default: state/coid_kv.json in BASE_DIR
            import config
            base_dir = getattr(config, 'BASE_DIR', os.getcwd())
            state_dir = os.path.join(base_dir, "state")
            os.makedirs(state_dir, exist_ok=True)
            store_path = os.path.join(state_dir, "coid_kv.json")

        self.store_path = store_path
        self._lock = threading.RLock()
        self._store: Dict[str, COIDEntry] = {}
        self._load_store()

        logger.info(f"COIDManager initialized with store: {self.store_path}")

    def next_client_order_id(
        self,
        decision_id: str,
        leg_idx: int,
        side: str,
        symbol: str,
        force_new: bool = False
    ) -> str:
        """
        Generate or retrieve idempotent COID.

        If a COID exists for this (decision_id, leg_idx, side) and is not terminal,
        the same COID is returned (idempotent retry).

        Args:
            decision_id: Trading decision ID
            leg_idx: Order leg index (0 for single orders)
            side: "buy" or "sell"
            symbol: Trading pair
            force_new: Force generation of new COID even if non-terminal exists

        Returns:
            Client order ID string
        """
        with self._lock:
            # Check for existing non-terminal COID
            if not force_new:
                existing = self._find_pending_coid(decision_id, leg_idx, side)
                if existing:
                    logger.info(
                        f"Reusing existing COID: {existing.coid} (attempt {existing.attempt_count + 1})"
                    )
                    existing.attempt_count += 1
                    existing.updated_ts = time.time()
                    self._save_store()
                    return existing.coid

            # Generate new COID
            timestamp_ms = int(time.time() * 1000)
            coid = f"{decision_id}_{leg_idx}_{side}_{timestamp_ms}"

            # Create entry
            entry = COIDEntry(
                coid=coid,
                decision_id=decision_id,
                leg_idx=leg_idx,
                side=side,
                symbol=symbol,
                status=COIDStatus.PENDING.value,
                created_ts=time.time(),
                updated_ts=time.time(),
                attempt_count=1
            )

            self._store[coid] = entry
            self._save_store()

            logger.info(f"Generated new COID: {coid} for {symbol} {side}")
            return coid

    def update_status(
        self,
        coid: str,
        status: COIDStatus,
        order_id: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None
    ) -> bool:
        """
        Update COID status.

        Args:
            coid: Client order ID
            status: New status
            order_id: Exchange order ID (if available)
            metadata: Additional metadata to merge

        Returns:
            True if updated, False if COID not found
        """
        with self._lock:
            entry = self._store.get(coid)
            if not entry:
                logger.warning(f"COID not found for update: {coid}")
                return False

            entry.status = status.value
            entry.updated_ts = time.time()

            if order_id:
                entry.order_id = order_id

            if metadata:
                entry.metadata.update(metadata)

            self._save_store()

            logger.info(
                f"COID status updated: {coid} → {status.value}"
                + (f" (order_id: {order_id})" if order_id else "")
            )
            return True

    def get_entry(self, coid: str) -> Optional[COIDEntry]:
        """Get COID entry by ID"""
        with self._lock:
            return self._store.get(coid)

    def reconcile_with_exchange(self, exchange, symbols: Optional[List[str]] = None) -> int:
        """
        Reconcile pending COIDs against exchange state.

        Queries exchange for open orders and updates COID statuses accordingly.
        This should be called on startup to recover from crashes.

        Args:
            exchange: CCXT exchange instance
            symbols: List of symbols to reconcile (None = all symbols)

        Returns:
            Number of COIDs reconciled
        """
        with self._lock:
            pending_entries = [
                entry for entry in self._store.values()
                if entry.status == COIDStatus.PENDING.value
            ]

            if not pending_entries:
                logger.info("No pending COIDs to reconcile")
                return 0

            logger.info(f"Reconciling {len(pending_entries)} pending COIDs...")

            reconciled_count = 0
            for entry in pending_entries:
                if symbols and entry.symbol not in symbols:
                    continue

                try:
                    # Query exchange for order status
                    if entry.order_id:
                        # Use order_id if known
                        order = exchange.fetch_order(entry.order_id, entry.symbol)
                    else:
                        # Try to find by client_order_id
                        open_orders = exchange.fetch_open_orders(entry.symbol)
                        order = None
                        for o in open_orders:
                            if o.get('clientOrderId') == entry.coid:
                                order = o
                                entry.order_id = o.get('id')
                                break

                    if order:
                        # Update status based on exchange state
                        new_status = self._map_exchange_status(order.get('status'))
                        self.update_status(
                            entry.coid,
                            new_status,
                            order_id=order.get('id'),
                            metadata={'reconciled_at': time.time()}
                        )
                        reconciled_count += 1
                        logger.info(
                            f"Reconciled COID {entry.coid}: {entry.status} → {new_status.value}"
                        )
                    else:
                        # Order not found - assume expired/canceled
                        self.update_status(
                            entry.coid,
                            COIDStatus.EXPIRED,
                            metadata={'reconciled_at': time.time(), 'reason': 'not_found'}
                        )
                        reconciled_count += 1
                        logger.warning(f"COID {entry.coid} not found on exchange, marked EXPIRED")

                except Exception as e:
                    logger.error(f"Failed to reconcile COID {entry.coid}: {e}")
                    continue

            logger.info(f"Reconciliation complete: {reconciled_count} COIDs updated")
            return reconciled_count

    def cleanup_old_entries(self, max_age_days: int = 7) -> int:
        """
        Remove old terminal entries from store.

        Args:
            max_age_days: Maximum age in days for terminal entries

        Returns:
            Number of entries removed
        """
        with self._lock:
            cutoff_ts = time.time() - (max_age_days * 86400)
            to_remove = []

            for coid, entry in self._store.items():
                if entry.is_terminal and entry.updated_ts < cutoff_ts:
                    to_remove.append(coid)

            for coid in to_remove:
                del self._store[coid]

            if to_remove:
                self._save_store()
                logger.info(f"Cleaned up {len(to_remove)} old COID entries")

            return len(to_remove)

    def get_stats(self) -> Dict[str, Any]:
        """Get COID store statistics"""
        with self._lock:
            stats = {
                'total_entries': len(self._store),
                'by_status': {},
                'pending_count': 0,
                'terminal_count': 0
            }

            for entry in self._store.values():
                status = entry.status
                stats['by_status'][status] = stats['by_status'].get(status, 0) + 1

                if entry.is_terminal:
                    stats['terminal_count'] += 1
                else:
                    stats['pending_count'] += 1

            return stats

    def _find_pending_coid(self, decision_id: str, leg_idx: int, side: str) -> Optional[COIDEntry]:
        """Find existing non-terminal COID for given decision/leg/side"""
        for entry in self._store.values():
            if (entry.decision_id == decision_id and
                entry.leg_idx == leg_idx and
                entry.side == side and
                not entry.is_terminal):
                return entry
        return None

    def _load_store(self):
        """Load COID store from disk (atomic read)"""
        if not os.path.exists(self.store_path):
            logger.info(f"COID store not found, creating new: {self.store_path}")
            self._store = {}
            return

        try:
            with open(self.store_path, 'r') as f:
                data = json.load(f)

            self._store = {}
            for coid, entry_dict in data.items():
                try:
                    self._store[coid] = COIDEntry.from_dict(entry_dict)
                except Exception as e:
                    logger.error(f"Failed to load COID entry {coid}: {e}")
                    continue

            logger.info(f"Loaded {len(self._store)} COID entries from {self.store_path}")

        except Exception as e:
            logger.error(f"Failed to load COID store: {e}")
            self._store = {}

    def _save_store(self):
        """Save COID store to disk (atomic write)"""
        try:
            # Serialize store
            data = {coid: entry.to_dict() for coid, entry in self._store.items()}

            # Atomic write: write to temp file, then rename
            temp_path = self.store_path + '.tmp'
            with open(temp_path, 'w', encoding='utf-8') as f:
                json.dump(data, f, indent=2)

            # Atomic rename (POSIX guarantees atomicity)
            os.replace(temp_path, self.store_path)

            logger.debug(f"Saved {len(self._store)} COID entries to {self.store_path}")

        except Exception as e:
            logger.error(f"Failed to save COID store: {e}")

    @staticmethod
    def _map_exchange_status(exchange_status: Optional[str]) -> COIDStatus:
        """Map exchange order status to COID status"""
        if not exchange_status:
            return COIDStatus.UNKNOWN

        status_upper = exchange_status.upper()

        if status_upper in ('FILLED', 'CLOSED'):
            return COIDStatus.FILLED
        elif status_upper == 'CANCELED':
            return COIDStatus.CANCELED
        elif status_upper == 'REJECTED':
            return COIDStatus.REJECTED
        elif status_upper == 'EXPIRED':
            return COIDStatus.EXPIRED
        elif status_upper in ('PARTIALLY_FILLED', 'PARTIAL'):
            return COIDStatus.PARTIALLY_FILLED
        elif status_upper in ('NEW', 'OPEN', 'PENDING'):
            return COIDStatus.PENDING
        else:
            return COIDStatus.UNKNOWN


# Global singleton instance
_coid_manager: Optional[COIDManager] = None
_manager_lock = threading.Lock()


def get_coid_manager() -> COIDManager:
    """Get global COID manager singleton"""
    global _coid_manager
    if _coid_manager is None:
        with _manager_lock:
            if _coid_manager is None:
                _coid_manager = COIDManager()
                logger.info("Global COIDManager initialized")
    return _coid_manager


def next_client_order_id(
    decision_id: str,
    leg_idx: int,
    side: str,
    symbol: str,
    force_new: bool = False
) -> str:
    """
    Convenience function for COID generation.

    Uses global COIDManager singleton.
    """
    manager = get_coid_manager()
    return manager.next_client_order_id(decision_id, leg_idx, side, symbol, force_new)
