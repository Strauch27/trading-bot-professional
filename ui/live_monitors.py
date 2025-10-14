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
    Live-updating heartbeat display showing system status and metrics.

    Updates continuously with:
    - Session-ID, Engine status, Trading mode
    - Budget, positions, memory, uptime
    - API Metrics (latency P50/P95/P99, cache hit rate, throttle rate)
    - Fill Metrics (fill rate, limit/market success)
    - Exchange connection status
    """

    def __init__(self, market_data_provider=None, fill_tracker=None):
        """
        Initialize LiveHeartbeat display.

        Args:
            market_data_provider: MarketDataProvider instance for API metrics
            fill_tracker: FillTracker instance for fill metrics
        """
        self._live = None
        self._last_stats = {}
        self.md_provider = market_data_provider
        self.fill_tracker = fill_tracker

    def _render(self, stats: Dict[str, Any]) -> Panel:
        """
        Render heartbeat panel with system and API metrics.

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
            Rich Panel with multiple tables
        """
        # Table 1: System Status
        system_table = Table(
            title="System Status",
            show_header=True,
            header_style="bold cyan",
            border_style="blue",
            expand=True
        )

        system_columns = [
            "Session-ID", "Time", "Engine", "Mode", "Budget",
            "Positions", "#Coins", "RSS MB", "Mem %", "Uptime"
        ]
        for col in system_columns:
            system_table.add_column(col, justify="center")

        system_table.add_row(
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

        # Table 2: API Metrics (if MarketDataProvider available)
        metrics_table = None
        if self.md_provider:
            try:
                md_stats = self.md_provider.get_statistics()

                metrics_table = Table(
                    title="API Metrics",
                    show_header=True,
                    header_style="bold yellow",
                    border_style="yellow",
                    expand=True
                )

                # Columns for API metrics
                metric_columns = [
                    "Ticker Req", "Cache Hit%", "Stale%", "OHLCV Req",
                    "Coalesce%", "Throttled%", "Errors"
                ]
                for col in metric_columns:
                    metrics_table.add_column(col, justify="center")

                # Extract statistics
                provider_stats = md_stats.get("provider", {})
                cache_stats = md_stats.get("ticker_cache", {})
                coalesce_stats = md_stats.get("coalescing", {})
                rate_limit_stats = md_stats.get("rate_limiting", {})

                ticker_requests = provider_stats.get("ticker_requests", 0)
                cache_hits = provider_stats.get("ticker_cache_hits", 0)
                stale_hits = provider_stats.get("ticker_stale_hits", 0)
                ohlcv_requests = provider_stats.get("ohlcv_requests", 0)
                errors = provider_stats.get("errors", 0)

                # Calculate percentages
                cache_hit_pct = (cache_hits / ticker_requests * 100) if ticker_requests > 0 else 0
                stale_pct = (stale_hits / ticker_requests * 100) if ticker_requests > 0 else 0
                coalesce_rate = coalesce_stats.get("coalesce_rate", 0) * 100 if coalesce_stats else 0

                # Throttle rate from rate limiter
                throttle_pct = 0
                if rate_limit_stats:
                    public_stats = rate_limit_stats.get("public", {})
                    throttle_pct = public_stats.get("throttle_rate", 0) * 100

                # Color coding based on values
                def color_value(value, good_threshold, warn_threshold):
                    if value <= good_threshold:
                        return f"[green]{value:.1f}[/green]"
                    elif value <= warn_threshold:
                        return f"[yellow]{value:.1f}[/yellow]"
                    else:
                        return f"[red]{value:.1f}[/red]"

                metrics_table.add_row(
                    str(ticker_requests),
                    color_value(cache_hit_pct, 70, 50) + "%",
                    color_value(stale_pct, 10, 30) + "%",
                    str(ohlcv_requests),
                    color_value(coalesce_rate, 50, 20) + "%",
                    color_value(throttle_pct, 5, 20) + "%",
                    f"[red]{errors}[/red]" if errors > 0 else "0"
                )

            except Exception as e:
                # If metrics retrieval fails, skip metrics table
                metrics_table = None

        # Table 3: Fill Metrics (if FillTracker available)
        fill_table = None
        if self.fill_tracker:
            try:
                fill_stats = self.fill_tracker.get_statistics()

                fill_table = Table(
                    title="Fill Metrics",
                    show_header=True,
                    header_style="bold green",
                    border_style="green",
                    expand=True
                )

                fill_columns = [
                    "Total Orders", "Full Fill%", "Partial%", "No Fill%",
                    "Avg Latency", "P95 Latency", "Avg Slippage"
                ]
                for col in fill_columns:
                    fill_table.add_column(col, justify="center")

                total_orders = fill_stats.get("total_orders", 0)
                fill_rates = fill_stats.get("fill_rates", {})
                latency = fill_stats.get("latency", {})
                slippage = fill_stats.get("slippage", {})

                full_fill_pct = fill_rates.get("full_fill", 0) * 100
                partial_pct = fill_rates.get("partial_fill", 0) * 100
                no_fill_pct = fill_rates.get("no_fill", 0) * 100
                avg_latency_ms = latency.get("mean_ms", 0)
                p95_latency_ms = latency.get("p95_ms", 0)
                avg_slippage_bps = slippage.get("mean_bps", 0)

                # Color coding
                def color_latency(ms):
                    if ms <= 50:
                        return f"[green]{ms:.0f}ms[/green]"
                    elif ms <= 200:
                        return f"[yellow]{ms:.0f}ms[/yellow]"
                    else:
                        return f"[red]{ms:.0f}ms[/red]"

                fill_table.add_row(
                    str(total_orders),
                    f"[green]{full_fill_pct:.1f}%[/green]",
                    f"[yellow]{partial_pct:.1f}%[/yellow]" if partial_pct > 0 else "0%",
                    f"[red]{no_fill_pct:.1f}%[/red]" if no_fill_pct > 0 else "0%",
                    color_latency(avg_latency_ms),
                    color_latency(p95_latency_ms),
                    f"{avg_slippage_bps:+.1f} bps"
                )

            except Exception as e:
                # If fill metrics retrieval fails, skip fill table
                fill_table = None

        # Combine tables into panel
        tables = [system_table]
        if metrics_table:
            tables.append(metrics_table)
        if fill_table:
            tables.append(fill_table)

        return Panel(
            Group(*tables),
            title="📊 Live Heartbeat",
            border_style="cyan",
            expand=True
        )

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


# ============================================================================
# Combined Live Dashboard
# ============================================================================

class LiveDashboard:
    """
    Combined live dashboard showing all monitors.

    Displays:
    - LiveHeartbeat (system + API + fill metrics)
    - DropMonitorView (top drops)
    - PortfolioMonitorView (positions)

    Usage:
        dashboard = LiveDashboard(
            market_data_provider=md_provider,
            fill_tracker=tracker,
            portfolio=portfolio
        )

        # In main loop:
        dashboard.update(
            system_stats={...},
            drop_rows=[...],
            portfolio_rows=[...]
        )

        # Stop when done:
        dashboard.stop()
    """

    def __init__(
        self,
        market_data_provider=None,
        fill_tracker=None,
        portfolio=None
    ):
        """
        Initialize live dashboard.

        Args:
            market_data_provider: MarketDataProvider instance
            fill_tracker: FillTracker instance
            portfolio: PortfolioManager instance
        """
        self.heartbeat = LiveHeartbeat(market_data_provider, fill_tracker)
        self.drop_view = DropMonitorView()
        self.portfolio_view = PortfolioMonitorView()
        self.portfolio = portfolio
        self._live = None

    def _render_all(
        self,
        system_stats: Dict[str, Any],
        drop_rows: List[Dict[str, Any]] = None,
        portfolio_data: Dict[str, Any] = None
    ) -> Group:
        """
        Render all monitors into a single group.

        Args:
            system_stats: System statistics for heartbeat
            drop_rows: Drop monitor rows
            portfolio_data: Portfolio data dict with keys:
                - slots_used, slots_total, rows, free_slots,
                  invested, pnl_total, realized_today, ts

        Returns:
            Rich Group with all panels
        """
        panels = []

        # Heartbeat panel
        try:
            heartbeat_panel = self.heartbeat._render(system_stats)
            panels.append(heartbeat_panel)
        except Exception as e:
            pass  # Skip on error

        # Drop monitor panel
        if drop_rows:
            try:
                drop_panel = self.drop_view.render(drop_rows)
                if drop_panel:
                    panels.append(drop_panel)
            except Exception as e:
                pass  # Skip on error

        # Portfolio monitor panel
        if portfolio_data:
            try:
                portfolio_panel = self.portfolio_view.render(
                    slots_used=portfolio_data.get("slots_used", 0),
                    slots_total=portfolio_data.get("slots_total", 10),
                    rows=portfolio_data.get("rows", []),
                    free_slots=portfolio_data.get("free_slots", 10),
                    invested=portfolio_data.get("invested", 0),
                    pnl_total=portfolio_data.get("pnl_total", 0.0),
                    realized_today=portfolio_data.get("realized_today", 0.0),
                    ts=portfolio_data.get("ts", "")
                )
                if portfolio_panel:
                    panels.append(portfolio_panel)
            except Exception as e:
                pass  # Skip on error

        return Group(*panels) if panels else Group()

    def update(
        self,
        system_stats: Dict[str, Any],
        drop_rows: List[Dict[str, Any]] = None,
        portfolio_data: Dict[str, Any] = None
    ):
        """
        Update dashboard with new data.

        Args:
            system_stats: System statistics
            drop_rows: Drop monitor rows
            portfolio_data: Portfolio data
        """
        if not RICH_AVAILABLE:
            return

        rendered = self._render_all(system_stats, drop_rows, portfolio_data)

        if self._live is None:
            from rich.console import Console
            self._live = Live(
                rendered,
                refresh_per_second=0.5,
                console=Console()
            )
            self._live.start()
        else:
            self._live.update(rendered)

    def stop(self):
        """Stop live dashboard."""
        if self._live:
            self._live.stop()
            self._live = None
