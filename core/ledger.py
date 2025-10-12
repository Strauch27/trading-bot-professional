#!/usr/bin/env python3
"""
Double-Entry Ledger System

Provides complete accounting ledger for all portfolio transactions:
- Asset accounts (inventory positions)
- Cash accounts (USDT balance)
- Fee accounts (trading expenses)

Every trade creates balanced debit/credit entries ensuring accurate portfolio tracking
and enabling balance verification at any point in time.

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

import sqlite3
import time
import logging
import threading
from typing import Dict, Optional
from pathlib import Path
import uuid

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
    """

    def __init__(self, db_path: str = "state/ledger.db"):
        """
        Initialize ledger with SQLite backend.

        Args:
            db_path: Path to SQLite database file
        """
        self.db_path = db_path
        self._lock = threading.RLock()

        # Ensure directory exists
        Path(db_path).parent.mkdir(parents=True, exist_ok=True)

        self.db = sqlite3.connect(db_path, check_same_thread=False)
        self._create_tables()

    def _create_tables(self):
        """Create ledger tables with indexes"""
        with self._lock:
            # Ledger entries - the complete transaction log
            self.db.execute("""
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
            self.db.execute("""
                CREATE TABLE IF NOT EXISTS account_balances (
                    account TEXT PRIMARY KEY,
                    balance REAL NOT NULL,
                    updated_at REAL NOT NULL
                )
            """)

            self.db.commit()

            # Create indexes for fast lookups
            self.db.execute("CREATE INDEX IF NOT EXISTS idx_transaction_id ON ledger_entries(transaction_id)")
            self.db.execute("CREATE INDEX IF NOT EXISTS idx_timestamp ON ledger_entries(timestamp)")
            self.db.execute("CREATE INDEX IF NOT EXISTS idx_account ON ledger_entries(account)")
            self.db.commit()

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
            for entry in entries:
                # Get current balance
                balance = self._get_account_balance(entry['account'])

                # Calculate new balance (Assets increase with debit, decrease with credit)
                new_balance = balance + entry['debit'] - entry['credit']

                # Insert ledger entry
                self.db.execute(
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

            self.db.commit()

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
        cursor = self.db.execute(
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
        self.db.execute(
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
            cursor = self.db.execute("SELECT account, balance FROM account_balances")
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
            cursor = self.db.execute(
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
        """Close database connection"""
        with self._lock:
            self.db.close()


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
