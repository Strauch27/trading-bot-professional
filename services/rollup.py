#!/usr/bin/env python3
"""
Daily KPI Rollup - Aggregate Trade Statistics

Generates daily summary reports from trade journal:
- Total trades, wins, losses, win rate
- Total PnL, fees, net PnL
- Best/worst trades
- Average trade duration
- Per-exit-reason breakdown

Rollups are saved as JSON files:
state/rollups/rollup_YYYY-MM-DD.json

This provides daily performance snapshots for:
- Performance tracking
- Strategy evaluation
- Dashboard displays
"""

import os
import json
import logging
from typing import Optional, Dict, Any
from datetime import datetime

from services.journal import get_trade_journal

logger = logging.getLogger(__name__)


class DailyRollup:
    """
    Daily KPI aggregation from trade journal.

    Reads daily trades and computes comprehensive statistics.
    """

    def __init__(self, rollup_dir: str = "state/rollups"):
        """
        Initialize daily rollup generator.

        Args:
            rollup_dir: Directory for rollup files
        """
        self.rollup_dir = rollup_dir
        self.journal = get_trade_journal()

        # Ensure rollup directory exists
        os.makedirs(self.rollup_dir, exist_ok=True)

        logger.info(f"Daily rollup initialized: {self.rollup_dir}")

    def _get_rollup_path(self, date_str: str) -> str:
        """
        Get path for daily rollup file.

        Args:
            date_str: Date string in YYYY-MM-DD format

        Returns:
            Path to daily rollup file
        """
        return os.path.join(self.rollup_dir, f"rollup_{date_str}.json")

    def compute_daily_kpis(self, date_str: Optional[str] = None) -> Dict[str, Any]:
        """
        Compute comprehensive daily KPIs from trade journal.

        Args:
            date_str: Date string in YYYY-MM-DD format (default: today)

        Returns:
            Dict with complete daily statistics
        """
        if date_str is None:
            date_str = datetime.now().strftime("%Y-%m-%d")

        # Read trades for the day
        trades = self.journal.read_day(date_str)

        if not trades:
            return {
                "date": date_str,
                "total_trades": 0,
                "summary": "No trades for this date"
            }

        # Basic counts
        total_trades = len(trades)
        wins = [t for t in trades if t.get("realized_pnl", 0) > 0]
        losses = [t for t in trades if t.get("realized_pnl", 0) < 0]
        breakeven = [t for t in trades if t.get("realized_pnl", 0) == 0]

        win_count = len(wins)
        loss_count = len(losses)
        breakeven_count = len(breakeven)

        # Win rate
        win_rate = (win_count / total_trades) * 100.0 if total_trades > 0 else 0.0

        # PnL metrics
        total_pnl = sum(t.get("realized_pnl", 0) for t in trades)
        total_fees = sum(t.get("fees_paid", 0) for t in trades)
        net_pnl = total_pnl - total_fees

        # Best/worst trades
        best_trade = max(trades, key=lambda t: t.get("realized_pnl", 0))
        worst_trade = min(trades, key=lambda t: t.get("realized_pnl", 0))

        # Duration metrics
        avg_duration_m = sum(t.get("duration_m", 0) for t in trades) / total_trades

        # Per-reason breakdown
        reason_breakdown = {}
        for t in trades:
            reason = t.get("exit_reason", "UNKNOWN")
            if reason not in reason_breakdown:
                reason_breakdown[reason] = {
                    "count": 0,
                    "total_pnl": 0.0,
                    "wins": 0,
                    "losses": 0
                }

            reason_breakdown[reason]["count"] += 1
            pnl = t.get("realized_pnl", 0)
            reason_breakdown[reason]["total_pnl"] += pnl

            if pnl > 0:
                reason_breakdown[reason]["wins"] += 1
            elif pnl < 0:
                reason_breakdown[reason]["losses"] += 1

        # Build complete KPI dict
        kpis = {
            "date": date_str,
            "generated_at": datetime.now().isoformat(),

            # Trade Counts
            "total_trades": total_trades,
            "wins": win_count,
            "losses": loss_count,
            "breakeven": breakeven_count,
            "win_rate_pct": round(win_rate, 2),

            # PnL Metrics
            "total_pnl": round(total_pnl, 2),
            "total_fees": round(total_fees, 2),
            "net_pnl": round(net_pnl, 2),
            "avg_pnl_per_trade": round(total_pnl / total_trades, 2) if total_trades > 0 else 0.0,

            # Best/Worst
            "best_trade": {
                "symbol": best_trade.get("symbol"),
                "pnl": round(best_trade.get("realized_pnl", 0), 2),
                "pnl_pct": round(best_trade.get("realized_pnl_pct", 0), 2)
            },
            "worst_trade": {
                "symbol": worst_trade.get("symbol"),
                "pnl": round(worst_trade.get("realized_pnl", 0), 2),
                "pnl_pct": round(worst_trade.get("realized_pnl_pct", 0), 2)
            },

            # Duration
            "avg_duration_minutes": round(avg_duration_m, 2),

            # Exit Reason Breakdown
            "exit_reasons": reason_breakdown
        }

        return kpis

    def rollup_daily(self, date_str: Optional[str] = None) -> Optional[Dict[str, Any]]:
        """
        Compute and save daily rollup to JSON file.

        Args:
            date_str: Date string in YYYY-MM-DD format (default: today)

        Returns:
            KPI dict if successful, None otherwise
        """
        try:
            if date_str is None:
                date_str = datetime.now().strftime("%Y-%m-%d")

            # Compute KPIs
            kpis = self.compute_daily_kpis(date_str)

            if kpis.get("total_trades", 0) == 0:
                logger.debug(f"No trades to rollup for {date_str}")
                return None

            # Save to JSON
            rollup_path = self._get_rollup_path(date_str)

            with open(rollup_path, "w", encoding="utf-8") as f:
                json.dump(kpis, f, indent=2)

            logger.info(
                f"Daily rollup saved: {date_str} - "
                f"{kpis['total_trades']} trades, "
                f"PnL: {kpis['net_pnl']:+.2f}, "
                f"WR: {kpis['win_rate_pct']:.1f}%"
            )

            return kpis

        except Exception as e:
            logger.error(f"Daily rollup failed for {date_str}: {e}")
            return None

    def read_rollup(self, date_str: str) -> Optional[Dict[str, Any]]:
        """
        Read saved rollup for a specific date.

        Args:
            date_str: Date string in YYYY-MM-DD format

        Returns:
            KPI dict if file exists, None otherwise
        """
        try:
            rollup_path = self._get_rollup_path(date_str)

            if not os.path.exists(rollup_path):
                return None

            with open(rollup_path, "r") as f:
                return json.load(f)

        except Exception as e:
            logger.error(f"Rollup read failed for {date_str}: {e}")
            return None


# Global singleton instance
_rollup_instance: Optional[DailyRollup] = None


def get_daily_rollup(rollup_dir: str = "state/rollups") -> DailyRollup:
    """
    Get global daily rollup instance (singleton).

    Args:
        rollup_dir: Directory for rollup files

    Returns:
        DailyRollup instance
    """
    global _rollup_instance

    if _rollup_instance is None:
        _rollup_instance = DailyRollup(rollup_dir)

    return _rollup_instance


def rollup_daily(date_str: Optional[str] = None) -> Optional[Dict[str, Any]]:
    """
    Convenience function to compute and save daily rollup.

    Args:
        date_str: Date string in YYYY-MM-DD format (default: today)

    Returns:
        KPI dict if successful, None otherwise
    """
    rollup = get_daily_rollup()
    return rollup.rollup_daily(date_str)
