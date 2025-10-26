#!/usr/bin/env python3
"""
Risk Monitor - Drawdown and Equity Tracking

Monitors portfolio risk metrics in real-time:
- Equity curve tracking
- Peak equity and drawdown calculation
- Maximum drawdown alerts
- Equity snapshots for analysis

Use cases:
- Halt trading on excessive drawdown
- Track session performance
- Alert on risk limit breaches
- Performance visualization

Example:
    monitor = RiskMonitor(initial_equity=1000.0)
    monitor.update(equity=950.0)  # -5% drawdown

    if monitor.get_drawdown_pct() > 10.0:
        # Halt trading - max drawdown exceeded
        engine.halt_trading()
"""

import logging
import time
from typing import Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)


class RiskMonitor:
    """
    Real-time equity and drawdown monitoring.

    Tracks equity curve and calculates drawdown from peak
    for risk management and performance analysis.
    """

    def __init__(self, initial_equity: float = 0.0):
        """
        Initialize risk monitor.

        Args:
            initial_equity: Starting equity (optional)
        """
        self.equity_peak = initial_equity
        self.equity_curve: List[Tuple[float, float]] = []  # [(timestamp, equity), ...]

        # Session tracking
        self.session_start_equity = initial_equity
        self.session_start_time = time.time()

        if initial_equity > 0:
            self.update(initial_equity)

        logger.info(f"Risk monitor initialized: initial_equity={initial_equity:.2f}")

    def update(self, equity: float) -> None:
        """
        Update equity curve with new value.

        Automatically updates peak if new equity exceeds current peak.

        Args:
            equity: Current total equity
        """
        timestamp = time.time()

        # Append to equity curve
        self.equity_curve.append((timestamp, equity))

        # Update peak
        if equity > self.equity_peak:
            self.equity_peak = equity
            logger.debug(f"New equity peak: {equity:.2f}")

    def get_current_equity(self) -> float:
        """
        Get most recent equity value.

        Returns:
            Current equity (0 if no updates)
        """
        if not self.equity_curve:
            return 0.0

        return self.equity_curve[-1][1]

    def get_drawdown_pct(self) -> float:
        """
        Calculate current drawdown from peak as percentage.

        Drawdown = (Peak - Current) / Peak * 100

        Returns:
            Drawdown percentage (0-100)
        """
        if self.equity_peak == 0:
            return 0.0

        current = self.get_current_equity()

        if current >= self.equity_peak:
            return 0.0  # At or above peak - no drawdown

        drawdown = ((self.equity_peak - current) / self.equity_peak) * 100.0

        return drawdown

    def get_drawdown_abs(self) -> float:
        """
        Calculate absolute drawdown from peak.

        Returns:
            Absolute drawdown in quote currency
        """
        current = self.get_current_equity()
        return max(0.0, self.equity_peak - current)

    def get_session_pnl(self) -> float:
        """
        Get session PnL (change from start).

        Returns:
            Session PnL in quote currency
        """
        current = self.get_current_equity()
        return current - self.session_start_equity

    def get_session_pnl_pct(self) -> float:
        """
        Get session PnL as percentage.

        Returns:
            Session PnL percentage
        """
        if self.session_start_equity == 0:
            return 0.0

        pnl = self.get_session_pnl()
        return (pnl / self.session_start_equity) * 100.0

    def get_max_drawdown(self) -> Tuple[float, float]:
        """
        Calculate maximum drawdown over entire equity curve.

        Returns:
            Tuple of (max_dd_pct, max_dd_abs)
        """
        if not self.equity_curve:
            return (0.0, 0.0)

        max_dd_pct = 0.0
        max_dd_abs = 0.0
        running_peak = 0.0

        for ts, equity in self.equity_curve:
            # Update running peak
            if equity > running_peak:
                running_peak = equity

            # Calculate drawdown from running peak
            if running_peak > 0:
                dd_abs = running_peak - equity
                dd_pct = (dd_abs / running_peak) * 100.0

                if dd_pct > max_dd_pct:
                    max_dd_pct = dd_pct
                    max_dd_abs = dd_abs

        return (max_dd_pct, max_dd_abs)

    def is_drawdown_breached(self, max_dd_pct: float) -> bool:
        """
        Check if current drawdown exceeds threshold.

        Args:
            max_dd_pct: Maximum allowed drawdown percentage

        Returns:
            True if drawdown exceeds threshold
        """
        current_dd = self.get_drawdown_pct()
        return current_dd > max_dd_pct

    def get_stats(self) -> Dict[str, any]:
        """
        Get comprehensive risk statistics.

        Returns:
            Dict with all risk metrics
        """
        current_equity = self.get_current_equity()
        current_dd_pct = self.get_drawdown_pct()
        current_dd_abs = self.get_drawdown_abs()
        max_dd_pct, max_dd_abs = self.get_max_drawdown()
        session_pnl = self.get_session_pnl()
        session_pnl_pct = self.get_session_pnl_pct()

        return {
            # Current State
            "current_equity": round(current_equity, 2),
            "equity_peak": round(self.equity_peak, 2),

            # Drawdown
            "current_dd_pct": round(current_dd_pct, 2),
            "current_dd_abs": round(current_dd_abs, 2),
            "max_dd_pct": round(max_dd_pct, 2),
            "max_dd_abs": round(max_dd_abs, 2),

            # Session
            "session_start_equity": round(self.session_start_equity, 2),
            "session_pnl": round(session_pnl, 2),
            "session_pnl_pct": round(session_pnl_pct, 2),
            "session_duration_s": time.time() - self.session_start_time,

            # Equity Curve
            "equity_samples": len(self.equity_curve)
        }

    def get_equity_curve(
        self,
        limit: Optional[int] = None
    ) -> List[Tuple[float, float]]:
        """
        Get equity curve data points.

        Args:
            limit: Maximum number of points (most recent, optional)

        Returns:
            List of (timestamp, equity) tuples
        """
        if limit is None:
            return self.equity_curve.copy()

        return self.equity_curve[-limit:]

    def reset(self) -> None:
        """
        Reset monitor to current equity (new session).

        Preserves equity curve but resets peak and session tracking.
        """
        current = self.get_current_equity()

        self.equity_peak = current
        self.session_start_equity = current
        self.session_start_time = time.time()

        logger.info(f"Risk monitor reset: new session at equity={current:.2f}")

    def clear_history(self) -> None:
        """
        Clear equity curve history.

        Keeps current state but removes all historical data.
        """
        current = self.get_current_equity()

        if current > 0:
            # Keep only the most recent point
            self.equity_curve = [(time.time(), current)]
        else:
            self.equity_curve = []

        logger.info("Risk monitor history cleared")


# Global singleton instance
_risk_monitor_instance: Optional[RiskMonitor] = None


def get_risk_monitor(initial_equity: float = 0.0) -> RiskMonitor:
    """
    Get global risk monitor instance (singleton).

    Args:
        initial_equity: Starting equity (only used on first call)

    Returns:
        RiskMonitor instance
    """
    global _risk_monitor_instance

    if _risk_monitor_instance is None:
        _risk_monitor_instance = RiskMonitor(initial_equity)

    return _risk_monitor_instance
