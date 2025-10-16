#!/usr/bin/env python3
"""
Trade Journal - Daily JSONL Append-Only Log

Records all completed trades with full lifecycle information:
- Entry/exit prices and timestamps
- Realized PnL and fees
- Position duration and exit reason
- Daily rotation with automatic directory creation

Each trade is written as a single JSON line in daily files:
state/journal/trades_YYYY-MM-DD.jsonl

This provides complete audit trail for:
- Performance analysis
- Tax reporting
- Trade review and learning
"""

import os
import json
import time
from typing import Optional, Dict, Any
from datetime import datetime
import logging

logger = logging.getLogger(__name__)


class TradeJournal:
    """
    Append-only trade journal with daily file rotation.

    Writes completed trades to daily JSONL files with full
    lifecycle information for audit trail and analysis.
    """

    def __init__(self, journal_dir: str = "state/journal"):
        """
        Initialize trade journal.

        Args:
            journal_dir: Directory for journal files
        """
        self.journal_dir = journal_dir

        # Ensure journal directory exists
        os.makedirs(self.journal_dir, exist_ok=True)

        logger.info(f"Trade journal initialized: {self.journal_dir}")

    def _get_daily_path(self, date_str: Optional[str] = None) -> str:
        """
        Get path for daily journal file.

        Args:
            date_str: Date string in YYYY-MM-DD format (default: today)

        Returns:
            Path to daily journal file
        """
        if date_str is None:
            date_str = datetime.now().strftime("%Y-%m-%d")

        return os.path.join(self.journal_dir, f"trades_{date_str}.jsonl")

    def append_trade(
        self,
        symbol: str,
        entry_price: float,
        entry_time: float,
        exit_price: float,
        exit_time: float,
        qty: float,
        realized_pnl: float,
        fees_paid: float,
        reason: str,
        rule_code: Optional[str] = None,
        **metadata
    ) -> bool:
        """
        Append completed trade to daily journal.

        Args:
            symbol: Trading pair (e.g., "BTC/USDT")
            entry_price: Entry price
            entry_time: Entry timestamp (unix seconds)
            exit_price: Exit price
            exit_time: Exit timestamp (unix seconds)
            qty: Position quantity (signed: +long, -short)
            realized_pnl: Realized PnL in quote currency
            fees_paid: Total fees paid (entry + exit)
            reason: Exit reason (e.g., "max_loss_reached", "target_reached")
            rule_code: Exit rule code (e.g., "HARD_SL", "HARD_TP")
            **metadata: Additional metadata to include in record

        Returns:
            True if write successful, False otherwise
        """
        try:
            # Calculate derived metrics
            duration_s = exit_time - entry_time
            duration_m = duration_s / 60.0
            pnl_pct = ((exit_price - entry_price) / entry_price) * 100.0

            # Build trade record
            record = {
                # Core Trade Info
                "ts": time.time(),  # Journal write timestamp
                "symbol": symbol,
                "qty": qty,

                # Entry
                "entry_price": entry_price,
                "entry_time": entry_time,

                # Exit
                "exit_price": exit_price,
                "exit_time": exit_time,
                "exit_reason": reason,
                "exit_rule": rule_code,

                # Metrics
                "realized_pnl": realized_pnl,
                "realized_pnl_pct": pnl_pct,
                "fees_paid": fees_paid,
                "duration_s": duration_s,
                "duration_m": duration_m,

                # Metadata
                **metadata
            }

            # Get daily file path
            journal_path = self._get_daily_path()

            # Append to daily JSONL (atomic write)
            with open(journal_path, "a") as f:
                f.write(json.dumps(record) + "\n")

            logger.debug(
                f"Trade journaled: {symbol} {pnl_pct:+.2f}% "
                f"(PnL: {realized_pnl:+.2f}, reason: {reason})"
            )

            return True

        except Exception as e:
            logger.error(f"Trade journal write failed: {e}")
            return False

    def read_day(self, date_str: str) -> list[Dict[str, Any]]:
        """
        Read all trades from a specific day.

        Args:
            date_str: Date string in YYYY-MM-DD format

        Returns:
            List of trade records
        """
        try:
            journal_path = self._get_daily_path(date_str)

            if not os.path.exists(journal_path):
                return []

            trades = []
            with open(journal_path, "r") as f:
                for line in f:
                    line = line.strip()
                    if line:
                        trades.append(json.loads(line))

            return trades

        except Exception as e:
            logger.error(f"Trade journal read failed for {date_str}: {e}")
            return []

    def get_stats(self, date_str: Optional[str] = None) -> Dict[str, Any]:
        """
        Get trade statistics for a specific day.

        Args:
            date_str: Date string in YYYY-MM-DD format (default: today)

        Returns:
            Statistics dict with counts, PnL, win rate, etc.
        """
        trades = self.read_day(date_str or datetime.now().strftime("%Y-%m-%d"))

        if not trades:
            return {
                "date": date_str,
                "total_trades": 0,
                "wins": 0,
                "losses": 0,
                "win_rate": 0.0,
                "total_pnl": 0.0,
                "total_fees": 0.0,
                "net_pnl": 0.0
            }

        # Calculate stats
        total_trades = len(trades)
        wins = sum(1 for t in trades if t.get("realized_pnl", 0) > 0)
        losses = sum(1 for t in trades if t.get("realized_pnl", 0) < 0)
        total_pnl = sum(t.get("realized_pnl", 0) for t in trades)
        total_fees = sum(t.get("fees_paid", 0) for t in trades)
        net_pnl = total_pnl - total_fees
        win_rate = (wins / total_trades) * 100.0 if total_trades > 0 else 0.0

        return {
            "date": date_str or datetime.now().strftime("%Y-%m-%d"),
            "total_trades": total_trades,
            "wins": wins,
            "losses": losses,
            "win_rate": win_rate,
            "total_pnl": total_pnl,
            "total_fees": total_fees,
            "net_pnl": net_pnl,
            "avg_pnl_per_trade": total_pnl / total_trades if total_trades > 0 else 0.0
        }


# Global singleton instance
_journal_instance: Optional[TradeJournal] = None


def get_trade_journal(journal_dir: str = "state/journal") -> TradeJournal:
    """
    Get global trade journal instance (singleton).

    Args:
        journal_dir: Directory for journal files

    Returns:
        TradeJournal instance
    """
    global _journal_instance

    if _journal_instance is None:
        _journal_instance = TradeJournal(journal_dir)

    return _journal_instance


def append_trade(
    symbol: str,
    entry_price: float,
    entry_time: float,
    exit_price: float,
    exit_time: float,
    qty: float,
    realized_pnl: float,
    fees_paid: float,
    reason: str,
    rule_code: Optional[str] = None,
    **metadata
) -> bool:
    """
    Convenience function to append trade to global journal.

    See TradeJournal.append_trade() for parameter documentation.
    """
    journal = get_trade_journal()
    return journal.append_trade(
        symbol=symbol,
        entry_price=entry_price,
        entry_time=entry_time,
        exit_price=exit_price,
        exit_time=exit_time,
        qty=qty,
        realized_pnl=realized_pnl,
        fees_paid=fees_paid,
        reason=reason,
        rule_code=rule_code,
        **metadata
    )
