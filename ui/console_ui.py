#!/usr/bin/env python3
"""
Console UI - Rich-based Terminal Output for Trading Bot

Provides beautiful terminal output with:
- Banner with Session-ID and timestamp
- Start summary with config overview
- Timestamped line logging
"""

from datetime import datetime
from typing import Dict, Any

try:
    from rich.console import Console
    from rich.panel import Panel
    from rich.table import Table
    from rich.text import Text
    RICH_AVAILABLE = True
except ImportError:
    RICH_AVAILABLE = False

# Global console instance
console = Console() if RICH_AVAILABLE else None


def ts() -> str:
    """
    Generate current timestamp string (HH:MM:SS).

    Returns:
        Timestamp string
    """
    return datetime.now().strftime("%H:%M:%S")


def shorten(path: str, maxlen: int = 72) -> str:
    """
    Shorten path string if too long.

    Args:
        path: Path string to shorten
        maxlen: Maximum length

    Returns:
        Shortened path string
    """
    return ("…" + path[-maxlen:]) if len(path) > maxlen else path


def banner(app_name: str, mode: str, session_dir: str, session_id: str, start_iso: str):
    """
    Display startup banner with session information.

    Args:
        app_name: Application name (e.g., "Trading Bot")
        mode: Trading mode (e.g., "LIVE", "OBSERVE")
        session_dir: Session directory path
        session_id: Session ID
        start_iso: Start timestamp in ISO format
    """
    if not RICH_AVAILABLE or not console:
        # Fallback to simple print
        print(f"\n{'='*80}")
        print(f"🚀 Start • {app_name}  Mode: {mode}")
        print(f"Session: {shorten(session_dir)}")
        print(f"Session-ID: {session_id}  Start: {start_iso}")
        print(f"{'='*80}\n")
        return

    # Mode color coding
    mode_style = "bold green" if mode == "LIVE" else "bold yellow"

    # Build content
    content = Text()
    content.append(" 🚀 Start • ", style="bold cyan")
    content.append(app_name, style="bold white")
    content.append("  Mode: ", style="white")
    content.append(mode, style=mode_style)
    content.append("\n")
    content.append("Session: ", style="dim white")
    content.append(shorten(session_dir), style="cyan")
    content.append("\n")
    content.append("Session-ID: ", style="dim white")
    content.append(session_id, style="bold cyan")
    content.append("    Start: ", style="dim white")
    content.append(start_iso, style="cyan")

    # Display panel
    panel = Panel(
        content,
        border_style="cyan",
        expand=False,
        padding=(0, 1)
    )
    console.print(panel)
    console.print()


def start_summary(coins: int, budget_usdt: float, cfg: Dict[str, Any]):
    """
    Display start summary with configuration overview.

    Args:
        coins: Number of tradeable coins
        budget_usdt: Starting budget in USDT
        cfg: Configuration dict with keys:
            - TP: Take profit threshold
            - SL: Stop loss threshold
            - DT: Drop trigger value
            - FSM_MODE: FSM mode (if applicable)
            - FSM_ENABLED: FSM enabled flag
    """
    if not RICH_AVAILABLE or not console:
        # Fallback to simple print
        print(f"Coins: {coins}")
        print(f"Budget: {budget_usdt:.2f} USDT")
        print(f"TP/SL/DT: {cfg.get('TP', 0)}/{cfg.get('SL', 0)}/{cfg.get('DT', 0)}")
        print(f"FSM: {cfg.get('FSM_MODE', 'legacy')} • enabled={cfg.get('FSM_ENABLED', False)}\n")
        return

    # Create summary table
    table = Table(
        show_header=True,
        header_style="bold magenta",
        border_style="magenta",
        expand=False,
        padding=(0, 2)
    )

    table.add_column("Metric", style="bold white", justify="left")
    table.add_column("Value", style="cyan", justify="left")

    # Add rows
    table.add_row("Coins", f"{coins} handelbar")
    table.add_row("Budget", f"{budget_usdt:.2f} USDT")

    # Trading parameters
    tp = cfg.get('TP', 0)
    sl = cfg.get('SL', 0)
    dt = cfg.get('DT', 0)
    tp_pct = (tp - 1.0) * 100 if tp > 0 else 0
    sl_pct = (sl - 1.0) * 100 if sl > 0 else 0
    dt_pct = (dt - 1.0) * 100 if dt > 0 else 0

    table.add_row("TP / SL / DT", f"{tp_pct:+.1f}% / {sl_pct:+.1f}% / {dt_pct:+.1f}%")

    # FSM info
    fsm_mode = cfg.get('FSM_MODE', 'legacy')
    fsm_enabled = cfg.get('FSM_ENABLED', False)
    fsm_text = f"{fsm_mode} • enabled={fsm_enabled}"
    table.add_row("FSM", fsm_text)

    console.print(table)
    console.print()


def line(label: str, msg: str):
    """
    Print timestamped log line with label.

    Args:
        label: Log label (e.g., "ENGINE", "MARKETS")
        msg: Log message
    """
    if not RICH_AVAILABLE or not console:
        # Fallback to simple print
        print(f"{ts()} {label:16s} {msg}")
        return

    # Rich formatted output
    timestamp = Text(ts(), style="dim")
    label_text = Text(label, style="bold")

    console.print(timestamp, label_text, msg)


# ============================================================================
# Enhanced Logging Functions
# ============================================================================

def log_info(message: str, details: Dict[str, Any] = None):
    """
    Log info message with optional structured details.

    Args:
        message: Log message
        details: Optional key-value details
    """
    if not RICH_AVAILABLE or not console:
        print(f"[INFO] {message}")
        if details:
            for k, v in details.items():
                print(f"       {k}: {v}")
        return

    console.print(f"[cyan]ℹ {message}[/cyan]")
    if details:
        for k, v in details.items():
            console.print(f"  [dim]{k}:[/dim] [bold]{v}[/bold]")


def log_success(message: str, details: Dict[str, Any] = None):
    """
    Log success message.

    Args:
        message: Log message
        details: Optional details
    """
    if not RICH_AVAILABLE or not console:
        print(f"[SUCCESS] {message}")
        if details:
            for k, v in details.items():
                print(f"          {k}: {v}")
        return

    console.print(f"[green bold]✓ {message}[/green bold]")
    if details:
        for k, v in details.items():
            console.print(f"  [dim]{k}:[/dim] [bold]{v}[/bold]")


def log_warning(message: str, details: Dict[str, Any] = None):
    """
    Log warning message.

    Args:
        message: Log message
        details: Optional details
    """
    if not RICH_AVAILABLE or not console:
        print(f"[WARNING] {message}")
        if details:
            for k, v in details.items():
                print(f"          {k}: {v}")
        return

    console.print(f"[yellow bold]⚠ {message}[/yellow bold]")
    if details:
        for k, v in details.items():
            console.print(f"  [dim]{k}:[/dim] [bold]{v}[/bold]")


def log_error(message: str, error: str = None, details: Dict[str, Any] = None):
    """
    Log error message.

    Args:
        message: Log message
        error: Error string
        details: Optional details
    """
    if not RICH_AVAILABLE or not console:
        print(f"[ERROR] {message}")
        if error:
            print(f"        Error: {error}")
        if details:
            for k, v in details.items():
                print(f"        {k}: {v}")
        return

    console.print(f"[red bold]✗ {message}[/red bold]")
    if error:
        console.print(f"  [dim]Error:[/dim] [red]{error}[/red]")
    if details:
        for k, v in details.items():
            console.print(f"  [dim]{k}:[/dim] [bold]{v}[/bold]")


# ============================================================================
# Trade Summaries
# ============================================================================

def print_trade_summary(
    symbol: str,
    side: str,
    quantity: float,
    price: float,
    pnl: float = None,
    status: str = "FILLED"
):
    """
    Print formatted trade summary.

    Args:
        symbol: Trading symbol
        side: BUY/SELL
        quantity: Order quantity
        price: Execution price
        pnl: P&L (for sells)
        status: Order status
    """
    if not RICH_AVAILABLE or not console:
        pnl_str = f" | P&L: ${pnl:+.2f}" if pnl is not None else ""
        print(f"[TRADE] {side} {quantity:.4f} {symbol} @ ${price:.2f} | Status: {status}{pnl_str}")
        return

    # Side color
    side_style = "green bold" if side == "BUY" else "red bold"

    # Status color
    status_style = "green" if status == "FILLED" else "yellow"

    parts = [
        Text("🔄 ", style="dim"),
        Text(side, style=side_style),
        Text(f" {quantity:.4f} ", style="white"),
        Text(symbol, style="yellow bold"),
        Text(f" @ ${price:.2f}", style="white"),
        Text(f" [{status}]", style=status_style)
    ]

    if pnl is not None:
        pnl_style = "green bold" if pnl > 0 else "red bold"
        parts.append(Text(f" | P&L: ${pnl:+.2f}", style=pnl_style))

    console.print(Text.assemble(*parts))


def print_metric_line(label: str, value: Any, unit: str = "", good_threshold: float = None):
    """
    Print a metric line with optional threshold coloring.

    Args:
        label: Metric label
        value: Metric value
        unit: Unit string (e.g., "ms", "%", "USDT")
        good_threshold: If set, color value based on threshold
    """
    if not RICH_AVAILABLE or not console:
        print(f"{label}: {value}{unit}")
        return

    # Determine value color
    value_style = "bold"
    if good_threshold is not None and isinstance(value, (int, float)):
        if value <= good_threshold:
            value_style = "green bold"
        else:
            value_style = "yellow bold"

    console.print(f"[cyan]{label}:[/cyan] [{value_style}]{value}{unit}[/{value_style}]")


# ============================================================================
# Utility Functions
# ============================================================================

def is_rich_available() -> bool:
    """Check if Rich library is available."""
    return RICH_AVAILABLE


def clear_screen():
    """Clear the console screen."""
    if RICH_AVAILABLE and console:
        console.clear()
    else:
        # Fallback: ANSI escape code
        print("\033[2J\033[H", end="")


def print_separator(char: str = "─", length: int = 80):
    """
    Print a separator line.

    Args:
        char: Character to use
        length: Line length
    """
    if not RICH_AVAILABLE or not console:
        print(char * length)
        return

    console.print(f"[dim]{char * length}[/dim]")
