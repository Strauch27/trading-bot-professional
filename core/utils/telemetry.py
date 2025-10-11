# telemetry.py - Heartbeat Telemetry System
from collections import deque

class RollingStats:
    def __init__(self, maxlen=300):
        """
        Initialize rolling statistics tracker

        Args:
            maxlen: Maximum number of entries to keep in memory
        """
        self.fills = deque(maxlen=maxlen)  # (timestamp, slippage_bp)
        self.drawdown_peak = 0.0
        self.session_equity_high = None

    def add_fill(self, ts, slippage_bp):
        """
        Record a new fill with timestamp and slippage

        Args:
            ts: Timestamp of fill
            slippage_bp: Slippage in basis points
        """
        self.fills.append((ts, slippage_bp))

    def fill_rate_last(self, seconds, now_ts):
        """
        Count fills in the last N seconds

        Args:
            seconds: Time window in seconds
            now_ts: Current timestamp

        Returns:
            Number of fills in time window
        """
        return sum(1 for t, _ in self.fills if now_ts - t <= seconds)

    def last_5m(self, now_ts):
        """Get slippage values from last 5 minutes"""
        return [bp for t,bp in self.fills if now_ts - t <= 300]

    def avg_slip_5m(self, now_ts):
        """Calculate average slippage in last 5 minutes"""
        xs = self.last_5m(now_ts)
        return sum(xs)/len(xs) if xs else 0.0

    def avg_slippage_5m(self):
        """
        Berechne durchschnittliche Slippage der letzten 5 Minuten (Legacy).

        Returns:
            Durchschnittliche Slippage in bps
        """
        import time
        return self.avg_slip_5m(time.time())

    def avg_slippage_bp_last(self, seconds, now_ts):
        """
        Calculate average slippage in the last N seconds

        Args:
            seconds: Time window in seconds
            now_ts: Current timestamp

        Returns:
            Average slippage in basis points
        """
        vals = [bp for t, bp in self.fills if now_ts - t <= seconds]
        return sum(vals) / len(vals) if vals else 0.0

    def update_equity(self, equity_pct, now_ts):
        """
        Update equity tracking for drawdown calculation

        Args:
            equity_pct: Current equity as percentage of initial
            now_ts: Current timestamp
        """
        if self.session_equity_high is None:
            self.session_equity_high = equity_pct

        # Track new high
        self.session_equity_high = max(self.session_equity_high, equity_pct)

        # Calculate drawdown from peak
        dd = equity_pct - self.session_equity_high
        self.drawdown_peak = min(self.drawdown_peak, dd)

def heartbeat_emit(log_event, pnl_snapshot: dict, rolling: RollingStats, positions_open: int, equity: float, now_ts: float):
    """
    Emit Heartbeat mit standardisiertem Format (kompatibler mit Feedback).
    """
    log_event("HEARTBEAT",
        pnl_realized=round(pnl_snapshot["pnl_realized"],6),
        pnl_unrealized=round(pnl_snapshot["pnl_unrealized"],6),
        fees=round(pnl_snapshot["fees"],6),
        equity_delta=round(pnl_snapshot["equity_delta"],6),
        fills_last_5m=len(rolling.last_5m(now_ts)) if rolling else 0,
        avg_slippage_bp_last_5m=round(rolling.avg_slip_5m(now_ts),2) if rolling else 0.0,
        session_drawdown_peak_pct=round(rolling.drawdown_peak if rolling else 0.0, 3),
        positions_open=positions_open,
        equity=round(equity,6)
    )

def heartbeat_emit_legacy(pnl_tracker, rolling_stats, equity, drawdown_peak_pct):
    """
    Emit regelmäßiger Heartbeat mit PnL und Performance-Metriken (Legacy).

    Args:
        pnl_tracker: PnLTracker-Instanz
        rolling_stats: RollingStats-Instanz
        equity: Aktuelles Equity
        drawdown_peak_pct: Peak-Drawdown in Prozent
    """
    import logging
    log = logging.getLogger(__name__)

    pnl_summary = pnl_tracker.get_total_pnl()

    log.info("HEARTBEAT", extra={
        "event_type": "HEARTBEAT",
        "pnl_realized": round(pnl_summary["realized"], 6),
        "pnl_unrealized": round(pnl_summary["unrealized"], 6),
        "fees": round(pnl_summary["fees"], 6),
        "fills_last_5m": len(rolling_stats.fills),
        "avg_slippage_bp_last_5m": round(rolling_stats.avg_slippage_5m(), 2),
        "session_drawdown_peak_pct": round(drawdown_peak_pct, 3),
        "equity": round(equity, 6),
        "net_pnl": round(pnl_summary["net_after_fees"], 6)
    })