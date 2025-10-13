#!/usr/bin/env python3
"""
Live Monitors - Real-time Terminal UI Components

Provides live-updating terminal displays:
- LiveHeartbeat: System status and metrics
- DropMonitorView: Top market drops
- PortfolioMonitorView: Active positions
"""

from datetime import datetime, timedelta
from typing import Dict, List, Any, Optional

try:
    from rich.live import Live
    from rich.table import Table
    from rich.panel import Panel
    from rich.console import Group
    RICH_AVAILABLE = True
except ImportError:
    RICH_AVAILABLE = False
    # Mock types for when Rich is not available
    Table = None
    Panel = None
    Live = None
    Group = None


def _fmt_time(dt: Optional[datetime]) -> str:
    """
    Format datetime to HH:MM:SS string.

    Args:
        dt: Datetime object or None

    Returns:
        Formatted time string or "—"
    """
    return dt.strftime("%H:%M:%S") if dt else "—"


def _fmt_duration(seconds: float) -> str:
    """
    Format duration in seconds to human-readable string.

    Args:
        seconds: Duration in seconds

    Returns:
        Formatted duration (e.g., "01:23:45" or "2d 03:45")
    """
    if seconds < 0:
        return "—"

    duration = timedelta(seconds=int(seconds))
    days = duration.days
    hours, remainder = divmod(duration.seconds, 3600)
    minutes, seconds = divmod(remainder, 60)

    if days > 0:
        return f"{days}d {hours:02d}:{minutes:02d}"
    else:
        return f"{hours:02d}:{minutes:02d}:{seconds:02d}"


class LiveHeartbeat:
    """
    Live-updating heartbeat display showing system status.

    Updates continuously with:
    - Session-ID
    - Engine status
    - Trading mode
    - Budget and positions
    - Memory usage
    - Uptime
    """

    def __init__(self):
        """Initialize LiveHeartbeat display."""
        self._live = None
        self._last_stats = {}

    def _render(self, stats: Dict[str, Any]) -> Table:
        """
        Render heartbeat table from stats.

        Args:
            stats: Statistics dict with keys:
                - session_id: Session ID
                - time: Current time
                - engine: Engine running status
                - mode: Trading mode
                - budget: Budget string
                - positions: Number of positions
                - coins: Number of coins
                - rss_mb: RSS memory in MB
                - mem_pct: Memory percentage
                - uptime_s: Uptime in seconds

        Returns:
            Rich Table object
        """
        table = Table(
            title="📊 Heartbeat",
            show_header=True,
            header_style="bold cyan on blue",
            border_style="blue",
            expand=True
        )

        # Add columns
        columns = [
            "Session-ID", "Time", "Engine", "Mode", "Budget",
            "Positions", "#Coins", "RSS MB", "Mem %", "Uptime"
        ]
        for col in columns:
            table.add_column(col, justify="center")

        # Add data row
        table.add_row(
            stats.get("session_id", "—"),
            stats.get("time", "—"),
            str(stats.get("engine", False)),
            stats.get("mode", "—"),
            stats.get("budget", "—"),
            str(stats.get("positions", 0)),
            str(stats.get("coins", 0)),
            f'{stats.get("rss_mb", 0.0):.1f}',
            f'{stats.get("mem_pct", 0.0):.1f}',
            _fmt_duration(stats.get("uptime_s", 0))
        )

        return table

    def update(self, stats: Dict[str, Any]):
        """
        Update heartbeat display with new stats.

        Args:
            stats: Current statistics
        """
        if not RICH_AVAILABLE:
            return

        self._last_stats = stats

        if self._live is None:
            self._live = Live(
                self._render(stats),
                refresh_per_second=0.5,
                console=None  # Use default console
            )
            self._live.start()
        else:
            self._live.update(self._render(stats))

    def stop(self):
        """Stop live heartbeat display."""
        if self._live:
            self._live.stop()
            self._live = None


class DropMonitorView:
    """
    View renderer for Drop Monitor display.

    Renders a table showing top market drops with:
    - Symbol
    - Drop percentage
    - Peak price and timestamp
    - Current price
    - Status (TRIGGERED, WARNING, SAFE)
    """

    def render(self, rows: List[Dict[str, Any]]) -> Panel:
        """
        Render drop monitor table.

        Args:
            rows: List of drop data dicts with keys:
                - ts: Timestamp
                - symbol: Symbol name
                - drop_pct: Drop percentage
                - from_price: Peak price
                - from_ts: Peak timestamp
                - now_price: Current price
                - status: Status string

        Returns:
            Rich Panel containing drop table
        """
        if not RICH_AVAILABLE:
            return None

        tab = Table(
            show_header=True,
            header_style="bold cyan",
            border_style="blue",
            expand=True,
            title="DROP MONITOR (Top N, 60s)"
        )

        # Add columns
        columns = ["Ts", "Symbol", "Drop%", "From High", "Now", "Status"]
        for col in columns:
            tab.add_column(col)

        # Add rows
        for r in rows:
            # Color-code based on status
            status = r.get("status", "—")
            if "TRIGGERED" in status or "✅" in status:
                style = "bold green"
            elif "⚠️" in status or "ZONE" in status.upper():
                style = "bold yellow"
            else:
                style = "white"

            tab.add_row(
                r.get("ts", "—"),
                r.get("symbol", "—"),
                f"[{style}]{r.get('drop_pct', 0):+.1f}%[/{style}]",
                f"{r.get('from_price', '—')} @{r.get('from_ts', '—')}",
                str(r.get("now_price", "—")),
                f"[{style}]{status}[/{style}]",
            )

        return Panel(tab, border_style="blue", expand=True)


class PortfolioMonitorView:
    """
    View renderer for Portfolio Monitor display.

    Renders a table showing active positions with:
    - Slot number
    - Symbol
    - Quantity
    - Buy price and current price
    - PnL (% and $)
    - Buy time and age
    """

    def render(
        self,
        slots_used: int,
        slots_total: int,
        rows: List[Dict[str, Any]],
        free_slots: int,
        invested: int,
        pnl_total: float,
        realized_today: float,
        ts: str
    ) -> Panel:
        """
        Render portfolio monitor table.

        Args:
            slots_used: Number of used position slots
            slots_total: Total position slots
            rows: List of position dicts with keys:
                - slot: Slot number
                - coin: Symbol
                - qty: Quantity
                - buy_px: Buy price
                - last_px: Last price
                - pnl_pct: PnL percentage
                - pnl_usd: PnL in USD
                - buy_time: Buy timestamp
                - age: Position age string
            free_slots: Number of free slots
            invested: Number of invested positions
            pnl_total: Total PnL in USDT
            realized_today: Realized PnL today in USDT
            ts: Current timestamp

        Returns:
            Rich Panel containing portfolio table
        """
        if not RICH_AVAILABLE:
            return None

        tab = Table(
            show_header=True,
            header_style="bold green",
            border_style="green",
            expand=True,
            title=f"PORTFOLIO MONITOR (Slots {slots_used} / {slots_total})"
        )

        # Add columns
        columns = ["Ts", "Slot", "Coin", "Qty", "BuyPx", "LastPx", "PnL %", "PnL $", "Buy Time", "Age"]
        for col in columns:
            tab.add_column(col)

        # Add position rows
        for r in rows:
            pnl_pct = r.get("pnl_pct", 0)
            pnl_usd = r.get("pnl_usd", 0)

            # Color-code PnL
            if pnl_pct > 0:
                pnl_style = "bold green"
            elif pnl_pct < 0:
                pnl_style = "bold red"
            else:
                pnl_style = "white"

            tab.add_row(
                ts,
                str(r.get("slot", "—")),
                r.get("coin", "—"),
                f"{r.get('qty', 0):.4f}",
                f"{r.get('buy_px', 0):.6f}",
                f"{r.get('last_px', 0):.6f}",
                f"[{pnl_style}]{pnl_pct:+.2f}[/{pnl_style}]",
                f"[{pnl_style}]{pnl_usd:+.2f}[/{pnl_style}]",
                r.get("buy_time", "—"),
                r.get("age", "—"),
            )

        # Add empty row separator if no positions
        if not rows:
            tab.add_row("—", "—", "—", "—", "—", "—", "—", "—", "—", "—")

        # Footer with summary
        footer = (
            f"Free Slots: {free_slots} / {slots_total} | "
            f"Investiert: {invested} | "
            f"Gesamt-PnL: {pnl_total:+.2f} USDT | "
            f"Realisiert heute: {realized_today:+.2f} USDT"
        )

        return Panel(
            Group(tab),
            title=footer,
            title_align="left",
            border_style="green",
            expand=True
        )
