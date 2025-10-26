#!/usr/bin/env python3
"""
Idempotency Store - Prevent Duplicate Orders

Provides persistent idempotency key storage using SQLite to prevent
duplicate order submissions due to network failures or retries.

Key Features:
- SQLite-backed persistence
- Thread-safe operations
- Automatic duplicate detection
- Order status tracking
- Cleanup of old entries

Usage:
    from core.idempotency import get_idempotency_store

    store = get_idempotency_store()

    # Before placing order
    existing_order_id = store.register_order(
        order_req_id="oreq_abc123",
        symbol="BTC/USDT",
        side="buy",
        amount=0.001,
        price=50000.0
    )

    if existing_order_id:
        # Duplicate detected - return existing order
        return exchange.fetch_order(existing_order_id, symbol)

    # Proceed with order placement...
    order = exchange.create_order(...)

    # Update with exchange order ID
    store.update_order_status(
        order_req_id="oreq_abc123",
        exchange_order_id=order['id'],
        status=order['status']
    )
"""

import logging
import sqlite3
import threading
import time
from pathlib import Path
from typing import Dict, Optional

from core.logger_factory import AUDIT_LOG, log_event
from core.trace_context import Trace

logger = logging.getLogger(__name__)


class IdempotencyStore:
    """
    Persistent idempotency key storage for order deduplication.

    Prevents duplicate orders by tracking order_req_id → exchange_order_id mappings.
    """

    def __init__(self, db_path: str = "state/idempotency.db"):
        """
        Initialize idempotency store with SQLite backend.

        CRITICAL FIX (C-INFRA-02): Use thread-local connections to prevent corruption.
        Each thread gets its own connection with check_same_thread=True (safe default).

        Args:
            db_path: Path to SQLite database file
        """
        self.db_path = db_path
        self._lock = threading.RLock()
        self._local = threading.local()  # Thread-local storage for connections

        # Ensure directory exists
        Path(db_path).parent.mkdir(parents=True, exist_ok=True)

        # Create schema on initial connection
        self._create_table()

        logger.info(f"Idempotency store initialized with thread-local connections: {db_path}")

    def _get_connection(self) -> sqlite3.Connection:
        """
        Get thread-local database connection.

        CRITICAL FIX (C-INFRA-02): Each thread gets its own connection.
        Prevents database corruption from concurrent access.

        Returns:
            Thread-local SQLite connection
        """
        if not hasattr(self._local, 'db'):
            # Create new connection for this thread (check_same_thread=True is safe)
            self._local.db = sqlite3.connect(self.db_path, check_same_thread=True)
            logger.debug(f"Created new SQLite connection for thread {threading.current_thread().name}")
        return self._local.db

    def _create_table(self):
        """Create idempotency_keys table if not exists"""
        with self._lock:
            db = self._get_connection()
            db.execute("""
                CREATE TABLE IF NOT EXISTS idempotency_keys (
                    order_req_id TEXT PRIMARY KEY,
                    symbol TEXT NOT NULL,
                    side TEXT NOT NULL,
                    amount REAL NOT NULL,
                    price REAL,
                    exchange_order_id TEXT,
                    status TEXT NOT NULL,
                    created_at REAL NOT NULL,
                    updated_at REAL NOT NULL,
                    completed_at REAL,
                    client_order_id TEXT
                )
            """)
            db.commit()

            # Create indexes for fast lookups
            db.execute("""
                CREATE INDEX IF NOT EXISTS idx_exchange_order_id
                ON idempotency_keys(exchange_order_id)
            """)
            db.execute("""
                CREATE INDEX IF NOT EXISTS idx_created_at
                ON idempotency_keys(created_at)
            """)
            db.execute("""
                CREATE INDEX IF NOT EXISTS idx_status
                ON idempotency_keys(status)
            """)
            db.commit()

    def register_order(
        self,
        order_req_id: str,
        symbol: str,
        side: str,
        amount: float,
        price: Optional[float] = None,
        client_order_id: Optional[str] = None
    ) -> Optional[str]:
        """
        Register order attempt - returns existing exchange_order_id if duplicate.

        Args:
            order_req_id: Unique order request ID (from new_order_req_id())
            symbol: Trading symbol (e.g., "BTC/USDT")
            side: Order side ("buy" | "sell")
            amount: Order quantity
            price: Order price (optional for market orders)
            client_order_id: Client order ID for additional tracking

        Returns:
            None if new order (proceed with placement)
            exchange_order_id (str) if duplicate (skip placement, return existing)
        """
        with self._lock:
            db = self._get_connection()

            # Check for existing order
            cursor = db.execute(
                "SELECT exchange_order_id, status, created_at FROM idempotency_keys WHERE order_req_id = ?",
                (order_req_id,)
            )
            existing = cursor.fetchone()

            if existing:
                exchange_order_id, status, created_at = existing
                age_seconds = time.time() - created_at

                logger.warning(
                    f"Duplicate order blocked: {order_req_id} → {exchange_order_id} "
                    f"(status: {status}, age: {age_seconds:.1f}s)"
                )

                # Log duplicate detection event
                try:
                    from core.event_schemas import DuplicateOrderBlocked

                    duplicate_event = DuplicateOrderBlocked(
                        order_req_id=order_req_id,
                        symbol=symbol,
                        side=side,
                        existing_exchange_order_id=exchange_order_id or "unknown",
                        existing_status=status,
                        age_seconds=age_seconds
                    )

                    with Trace(order_req_id=order_req_id):
                        log_event(
                            AUDIT_LOG(),
                            "duplicate_order_blocked",
                            **duplicate_event.model_dump(),
                            level=logging.WARNING
                        )
                except Exception as e:
                    logger.debug(f"Failed to log duplicate_order_blocked: {e}")

                return exchange_order_id  # Return existing order ID

            # Register new order (pending state)
            now = time.time()
            db.execute(
                """
                INSERT INTO idempotency_keys
                (order_req_id, symbol, side, amount, price, exchange_order_id, status, created_at, updated_at, completed_at, client_order_id)
                VALUES (?, ?, ?, ?, ?, NULL, 'pending', ?, ?, NULL, ?)
                """,
                (order_req_id, symbol, side, amount, price, now, now, client_order_id)
            )
            db.commit()

            logger.debug(f"Registered new order request: {order_req_id} ({symbol} {side})")

            return None  # No duplicate, proceed with order placement

    def update_order_status(
        self,
        order_req_id: str,
        exchange_order_id: str,
        status: str
    ):
        """
        Update order status after exchange response.

        Args:
            order_req_id: Order request ID
            exchange_order_id: Exchange-assigned order ID
            status: Order status ("open", "filled", "canceled", "expired", "rejected", "failed")
        """
        with self._lock:
            db = self._get_connection()
            now = time.time()

            # Determine if order is completed
            completed_statuses = {'filled', 'canceled', 'cancelled', 'expired', 'rejected', 'failed'}
            completed_at = now if status.lower() in completed_statuses else None

            db.execute(
                """
                UPDATE idempotency_keys
                SET exchange_order_id = ?, status = ?, updated_at = ?, completed_at = ?
                WHERE order_req_id = ?
                """,
                (exchange_order_id, status, now, completed_at, order_req_id)
            )
            db.commit()

            logger.debug(
                f"Updated order status: {order_req_id} → {exchange_order_id} "
                f"(status: {status}, completed: {completed_at is not None})"
            )

    def get_order_by_req_id(self, order_req_id: str) -> Optional[Dict]:
        """
        Get order details by order_req_id.

        Args:
            order_req_id: Order request ID

        Returns:
            Dict with order details or None if not found
        """
        with self._lock:
            db = self._get_connection()
            cursor = db.execute(
                "SELECT * FROM idempotency_keys WHERE order_req_id = ?",
                (order_req_id,)
            )
            row = cursor.fetchone()

            if not row:
                return None

            return {
                'order_req_id': row[0],
                'symbol': row[1],
                'side': row[2],
                'amount': row[3],
                'price': row[4],
                'exchange_order_id': row[5],
                'status': row[6],
                'created_at': row[7],
                'updated_at': row[8],
                'completed_at': row[9],
                'client_order_id': row[10]
            }

    def get_order_by_exchange_id(self, exchange_order_id: str) -> Optional[Dict]:
        """
        Get order details by exchange_order_id.

        Args:
            exchange_order_id: Exchange-assigned order ID

        Returns:
            Dict with order details or None if not found
        """
        with self._lock:
            db = self._get_connection()
            cursor = db.execute(
                "SELECT * FROM idempotency_keys WHERE exchange_order_id = ?",
                (exchange_order_id,)
            )
            row = cursor.fetchone()

            if not row:
                return None

            return {
                'order_req_id': row[0],
                'symbol': row[1],
                'side': row[2],
                'amount': row[3],
                'price': row[4],
                'exchange_order_id': row[5],
                'status': row[6],
                'created_at': row[7],
                'updated_at': row[8],
                'completed_at': row[9],
                'client_order_id': row[10]
            }

    def cleanup_old_orders(self, max_age_days: int = 7) -> int:
        """
        Remove completed orders older than max_age_days.

        Args:
            max_age_days: Maximum age in days for completed orders

        Returns:
            Number of records deleted
        """
        with self._lock:
            db = self._get_connection()
            cutoff_time = time.time() - (max_age_days * 86400)

            deleted = db.execute(
                """
                DELETE FROM idempotency_keys
                WHERE completed_at IS NOT NULL AND completed_at < ?
                """,
                (cutoff_time,)
            ).rowcount

            db.commit()

            if deleted > 0:
                logger.info(f"Cleaned up {deleted} old idempotency records (older than {max_age_days}d)")

            return deleted

    def get_stats(self) -> Dict:
        """
        Get idempotency store statistics.

        Returns:
            Dict with counts by status
        """
        with self._lock:
            db = self._get_connection()
            cursor = db.execute(
                "SELECT status, COUNT(*) FROM idempotency_keys GROUP BY status"
            )
            stats = {row[0]: row[1] for row in cursor.fetchall()}

            # Total count
            cursor = db.execute("SELECT COUNT(*) FROM idempotency_keys")
            total = cursor.fetchone()[0]

            stats['total'] = total

            return stats

    def close(self):
        """
        Close all thread-local database connections.

        CRITICAL FIX (C-INFRA-02): Close connections for all threads.
        Note: This only closes the connection for the calling thread.
        Other thread connections will be closed when those threads exit.
        """
        with self._lock:
            if hasattr(self._local, 'db'):
                self._local.db.close()
                delattr(self._local, 'db')
                logger.info(f"Closed SQLite connection for thread {threading.current_thread().name}")


# =============================================================================
# SINGLETON PATTERN
# =============================================================================

_idempotency_store: Optional[IdempotencyStore] = None
_store_lock = threading.Lock()


def get_idempotency_store(db_path: str = "state/idempotency.db") -> IdempotencyStore:
    """
    Get global idempotency store singleton.

    Args:
        db_path: Path to SQLite database file

    Returns:
        IdempotencyStore instance
    """
    global _idempotency_store

    if _idempotency_store is None:
        with _store_lock:
            # Double-check locking pattern
            if _idempotency_store is None:
                _idempotency_store = IdempotencyStore(db_path)

    return _idempotency_store


def initialize_idempotency_store(db_path: str = "state/idempotency.db") -> IdempotencyStore:
    """
    Initialize idempotency store at bot startup.

    Args:
        db_path: Path to SQLite database file

    Returns:
        IdempotencyStore instance
    """
    store = get_idempotency_store(db_path)

    # Log initialization
    stats = store.get_stats()
    logger.info(
        f"Idempotency store ready: {stats.get('total', 0)} total records, "
        f"{stats.get('pending', 0)} pending, {stats.get('filled', 0)} filled"
    )

    return store
