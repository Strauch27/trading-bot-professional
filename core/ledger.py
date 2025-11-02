#!/usr/bin/env python3
"""
Double-Entry Ledger System

Provides complete accounting ledger for all portfolio transactions:
- Asset accounts (inventory positions)
- Cash accounts (USDT balance)
- Fee accounts (trading expenses)

Every trade creates balanced debit/credit entries ensuring accurate portfolio tracking
and enabling balance verification at any point in time.

CRITICAL FIX (C-LEDGER-01): Thread-Safe SQLite Implementation
- Uses thread-local connections (one per thread)
- WAL mode for better concurrency
- Retry logic for database locks
- No more check_same_thread=False (was causing memory corruption!)

Usage:
    from core.ledger import DoubleEntryLedger

    ledger = DoubleEntryLedger()
    ledger.record_trade(
        symbol="BTC/USDT",
        side="buy",
        qty=0.1,
        price=50000.0,
        fee=5.0
    )
"""

import logging
import sqlite3
import threading
import time
import uuid
from pathlib import Path
from typing import Dict, Optional

from core.logger_factory import AUDIT_LOG, log_event
from core.trace_context import Trace

logger = logging.getLogger(__name__)


class DoubleEntryLedger:
    """
    Double-entry accounting ledger for portfolio tracking.

    Every trade creates balanced debit/credit entries across:
    - Asset accounts (inventory)
    - Cash accounts (USDT balance)
    - Fee accounts (expenses)

    Example:
        Buy 0.1 BTC @ 50000 with 5 USDT fee:
            - Debit:  asset:BTC/USDT    5000.00
            - Credit: cash:USDT         5000.00
            - Credit: fees:trading         5.00
            Total: Debit 5000 = Credit 5005 (INCORRECT - should balance)

        Correct:
            - Debit:  asset:BTC/USDT    5000.00
            - Debit:  fees:trading         5.00
            - Credit: cash:USDT         5005.00
            Total: Debit 5005 = Credit 5005 ✓

    CRITICAL FIX (C-LEDGER-01): Thread-Safe Implementation
    --------------------------------------------------------
    This class now uses thread-local SQLite connections to prevent
    memory corruption from concurrent access. Each thread gets its
    own connection with check_same_thread=True (safe default).

    Previous implementation used check_same_thread=False which caused
    VS Code crashes after ~33 hours due to race conditions.
    """

    def __init__(self, db_path: str = "state/ledger.db"):
        """
        Initialize ledger with SQLite backend.

        CRITICAL FIX (C-LEDGER-01): Thread-local connections for safety.

        Args:
            db_path: Path to SQLite database file
        """
        self.db_path = db_path
        self._lock = threading.RLock()
        self._local = threading.local()  # Thread-local storage for connections

        # Ensure directory exists
        Path(db_path).parent.mkdir(parents=True, exist_ok=True)

        # Create schema on initial connection
        self._create_tables()

        logger.info(
            f"Ledger initialized with thread-local connections: {db_path}",
            extra={'event_type': 'LEDGER_INIT', 'db_path': db_path}
        )

    def _get_connection(self) -> sqlite3.Connection:
        """
        Get thread-local database connection.

        CRITICAL FIX (C-LEDGER-01): Each thread gets its own connection.
        Prevents database corruption from concurrent access.

        Returns:
            Thread-local SQLite connection
        """
        if not hasattr(self._local, 'db'):
            # Create new connection for this thread (check_same_thread=True is safe)
            self._local.db = sqlite3.connect(self.db_path, check_same_thread=True)

            # Enable WAL mode for better concurrency (write-ahead logging)
            # This allows multiple readers while one writer is active
            self._local.db.execute("PRAGMA journal_mode=WAL")
            self._local.db.execute("PRAGMA synchronous=NORMAL")

            # Reduce busy timeout to prevent long waits
            self._local.db.execute("PRAGMA busy_timeout=5000")  # 5 seconds max wait

            logger.debug(
                f"Created new SQLite connection for thread {threading.current_thread().name}",
                extra={'event_type': 'LEDGER_CONN_CREATE', 'thread': threading.current_thread().name}
            )

        return self._local.db

    def _create_tables(self):
        """Create ledger tables with indexes and WAL mode"""
        with self._lock:
            db = self._get_connection()

            # Ledger entries - the complete transaction log
            db.execute("""
                CREATE TABLE IF NOT EXISTS ledger_entries (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    timestamp REAL NOT NULL,
                    transaction_id TEXT NOT NULL,
                    account TEXT NOT NULL,
                    debit REAL NOT NULL,
                    credit REAL NOT NULL,
                    balance_after REAL NOT NULL,
                    symbol TEXT,
                    side TEXT,
                    qty REAL,
                    price REAL,
                    metadata TEXT
                )
            """)

            # Account balances - current state snapshot
            db.execute("""
                CREATE TABLE IF NOT EXISTS account_balances (
                    account TEXT PRIMARY KEY,
                    balance REAL NOT NULL,
                    updated_at REAL NOT NULL
                )
            """)

            db.commit()

            # Create indexes for fast lookups
            db.execute("CREATE INDEX IF NOT EXISTS idx_transaction_id ON ledger_entries(transaction_id)")
            db.execute("CREATE INDEX IF NOT EXISTS idx_timestamp ON ledger_entries(timestamp)")
            db.execute("CREATE INDEX IF NOT EXISTS idx_account ON ledger_entries(account)")
            db.commit()

    def _execute_with_retry(
        self,
        query: str,
        params: tuple = (),
        max_retries: int = 3,
        retry_delay: float = 0.1
    ) -> sqlite3.Cursor:
        """
        Execute SQL query with retry logic for database locks.

        CRITICAL FIX (C-LEDGER-01): Handles "database is locked" gracefully.

        Args:
            query: SQL query string
            params: Query parameters
            max_retries: Maximum retry attempts
            retry_delay: Initial delay between retries (exponential backoff)

        Returns:
            SQLite cursor

        Raises:
            sqlite3.OperationalError: If all retries exhausted
        """
        db = self._get_connection()
        last_error = None

        for attempt in range(max_retries):
            try:
                cursor = db.execute(query, params)
                return cursor

            except sqlite3.OperationalError as e:
                last_error = e

                if "database is locked" in str(e).lower():
                    if attempt < max_retries - 1:
                        # Exponential backoff
                        delay = retry_delay * (2 ** attempt)
                        logger.warning(
                            f"Database locked (attempt {attempt + 1}/{max_retries}), "
                            f"retrying in {delay:.2f}s...",
                            extra={
                                'event_type': 'LEDGER_LOCK_RETRY',
                                'attempt': attempt + 1,
                                'delay': delay
                            }
                        )
                        time.sleep(delay)
                        continue

                # Not a lock error or final attempt - re-raise
                raise

        # All retries exhausted
        logger.error(
            f"Database operation failed after {max_retries} retries: {last_error}",
            extra={'event_type': 'LEDGER_LOCK_FAILED', 'error': str(last_error)}
        )
        raise last_error

    def record_trade(
        self,
        symbol: str,
        side: str,
        qty: float,
        price: float,
        fee: float,
        timestamp: Optional[float] = None
    ):
        """
        Record trade as double-entry ledger transaction.

        Buy Trade:
            - Debit:  Asset (increase inventory)
            - Debit:  Fees (expense)
            - Credit: Cash (decrease USDT)

        Sell Trade:
            - Debit:  Cash (increase USDT)
            - Credit: Asset (decrease inventory)
            - Credit: Fees (expense)

        Args:
            symbol: Trading symbol
            side: "buy" or "sell"
            qty: Quantity traded
            price: Price per unit
            fee: Fee in quote currency (USDT)
            timestamp: Trade timestamp (defaults to now)
        """
        timestamp = timestamp or time.time()
        transaction_id = f"trade_{uuid.uuid4().hex[:12]}"

        notional = qty * price

        # Define double-entry accounting entries
        if side.lower() == "buy":
            # BUY: Debit Asset + Debit Fees = Credit Cash
            entries = [
                {
                    "account": f"asset:{symbol}",
                    "debit": notional,
                    "credit": 0,
                    "description": f"Buy {qty} {symbol} @ {price}"
                },
                {
                    "account": "fees:trading",
                    "debit": fee,
                    "credit": 0,
                    "description": f"Trading fee {fee} USDT"
                },
                {
                    "account": "cash:USDT",
                    "debit": 0,
                    "credit": notional + fee,
                    "description": f"Pay {notional + fee} USDT (cost + fee)"
                }
            ]
        else:  # sell
            # SELL: Debit Cash = Credit Asset + Credit Fees
            entries = [
                {
                    "account": "cash:USDT",
                    "debit": notional - fee,
                    "credit": 0,
                    "description": f"Receive {notional - fee} USDT (proceeds - fee)"
                },
                {
                    "account": f"asset:{symbol}",
                    "debit": 0,
                    "credit": notional,
                    "description": f"Sell {qty} {symbol} @ {price}"
                },
                {
                    "account": "fees:trading",
                    "debit": 0,
                    "credit": fee,
                    "description": f"Trading fee {fee} USDT"
                }
            ]

        # Verify double-entry balance (debits must equal credits)
        total_debit = sum(e['debit'] for e in entries)
        total_credit = sum(e['credit'] for e in entries)

        if abs(total_debit - total_credit) > 1e-6:
            raise ValueError(
                f"Ledger imbalance! Debit: {total_debit}, Credit: {total_credit}, "
                f"Difference: {abs(total_debit - total_credit)}"
            )

        # Write entries to database atomically
        with self._lock:
            db = self._get_connection()

            for entry in entries:
                # Get current balance
                balance = self._get_account_balance(entry['account'])

                # Calculate new balance (Assets increase with debit, decrease with credit)
                new_balance = balance + entry['debit'] - entry['credit']

                # Insert ledger entry with retry logic
                self._execute_with_retry(
                    """
                    INSERT INTO ledger_entries
                    (timestamp, transaction_id, account, debit, credit, balance_after, symbol, side, qty, price, metadata)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        timestamp, transaction_id, entry['account'],
                        entry['debit'], entry['credit'], new_balance,
                        symbol, side, qty, price,
                        entry['description']
                    )
                )

                # Update account balance
                self._set_account_balance(entry['account'], new_balance, timestamp)

                # Phase 4: Log ledger_entry event
                try:
                    from core.event_schemas import LedgerEntry

                    ledger_event = LedgerEntry(
                        timestamp=timestamp,
                        transaction_id=transaction_id,
                        account=entry['account'],
                        debit=entry['debit'],
                        credit=entry['credit'],
                        balance_after=new_balance,
                        symbol=symbol,
                        side=side,
                        qty=qty,
                        price=price
                    )

                    with Trace():
                        log_event(AUDIT_LOG(), "ledger_entry", **ledger_event.model_dump())

                except Exception as e:
                    logger.debug(f"Failed to log ledger_entry event: {e}")

            db.commit()

        logger.debug(
            f"Ledger: {side.upper()} {qty} {symbol} @ {price} "
            f"(tx: {transaction_id}, fee: {fee})"
        )

    def _get_account_balance(self, account: str) -> float:
        """
        Get current balance for account.

        Thread-safe lookup of account balance.
        Returns 0.0 if account doesn't exist.
        """
        cursor = self._execute_with_retry(
            "SELECT balance FROM account_balances WHERE account = ?",
            (account,)
        )
        row = cursor.fetchone()
        return row[0] if row else 0.0

    def _set_account_balance(self, account: str, balance: float, timestamp: float):
        """
        Set account balance.

        Thread-safe update of account balance with timestamp.
        """
        self._execute_with_retry(
            """
            INSERT OR REPLACE INTO account_balances (account, balance, updated_at)
            VALUES (?, ?, ?)
            """,
            (account, balance, timestamp)
        )

    def get_all_balances(self) -> Dict[str, float]:
        """
        Get all account balances.

        Returns:
            Dict mapping account names to balances
        """
        with self._lock:
            cursor = self._execute_with_retry(
                "SELECT account, balance FROM account_balances"
            )
            return {row[0]: row[1] for row in cursor.fetchall()}

    def get_cash_balance(self) -> float:
        """Get current USDT cash balance"""
        return self._get_account_balance("cash:USDT")

    def get_asset_balance(self, symbol: str) -> float:
        """Get current notional value of asset"""
        return self._get_account_balance(f"asset:{symbol}")

    def get_total_fees(self) -> float:
        """Get total trading fees paid"""
        return abs(self._get_account_balance("fees:trading"))

    def verify_balance(self, account: str, expected_balance: float, tolerance: float = 0.01) -> bool:
        """
        Verify account balance matches expected value.

        Args:
            account: Account name
            expected_balance: Expected balance value
            tolerance: Acceptable difference

        Returns:
            True if balance matches within tolerance
        """
        with self._lock:
            actual_balance = self._get_account_balance(account)
            diff = abs(actual_balance - expected_balance)

            if diff > tolerance:
                logger.error(
                    f"Balance mismatch for {account}: "
                    f"expected {expected_balance}, got {actual_balance} (diff: {diff})"
                )
                return False

            return True

    def get_transaction_history(self, limit: int = 100) -> list:
        """
        Get recent transaction history.

        Args:
            limit: Maximum number of transactions to return

        Returns:
            List of transaction dicts
        """
        with self._lock:
            cursor = self._execute_with_retry(
                """
                SELECT timestamp, transaction_id, account, debit, credit, balance_after,
                       symbol, side, qty, price, metadata
                FROM ledger_entries
                ORDER BY timestamp DESC
                LIMIT ?
                """,
                (limit,)
            )

            columns = ['timestamp', 'transaction_id', 'account', 'debit', 'credit',
                      'balance_after', 'symbol', 'side', 'qty', 'price', 'metadata']

            return [dict(zip(columns, row)) for row in cursor.fetchall()]

    def close(self):
        """
        Close all thread-local database connections.

        CRITICAL FIX (C-LEDGER-01): Close connections for all threads.
        Note: This only closes the connection for the calling thread.
        Other thread connections will be closed when those threads exit.
        """
        with self._lock:
            if hasattr(self._local, 'db'):
                self._local.db.close()
                delattr(self._local, 'db')
                logger.info(
                    f"Closed SQLite connection for thread {threading.current_thread().name}",
                    extra={'event_type': 'LEDGER_CONN_CLOSE', 'thread': threading.current_thread().name}
                )


# Global ledger instance
_ledger = None
_ledger_lock = threading.Lock()


def get_ledger() -> DoubleEntryLedger:
    """Get or create global ledger instance (singleton pattern)"""
    global _ledger
    with _ledger_lock:
        if _ledger is None:
            _ledger = DoubleEntryLedger()
        return _ledger
