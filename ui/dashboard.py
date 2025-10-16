#!/usr/bin/env python3
"""
Live Trading Dashboard - Rich Terminal UI

Provides a comprehensive real-time dashboard with:
- Trading mode, session info, and heartbeat status
- Configuration overview (TP/SL/DT/Max Trades)
- Top drops with trigger distance
- Portfolio with real-time P&L
- Last event indicator

Updates continuously without scrolling using Rich Live.
"""

import time
import logging
from datetime import datetime, timezone
from typing import Dict, List, Any
from collections import deque

try:
    from rich.layout import Layout
    from rich.live import Live
    from rich.panel import Panel
    from rich.table import Table
    from rich.text import Text
    RICH_AVAILABLE = True
except ImportError:
    RICH_AVAILABLE = False

logger = logging.getLogger(__name__)


class DashboardEventBus:
    """
    Simple event bus for dashboard notifications.
    Stores recent events for display in the "Last Event" section.
    """

    def __init__(self, max_events: int = 100):
        self.events = deque(maxlen=max_events)
        self.last_event = "Bot gestartet..."

    def emit(self, event_type: str, message: str):
        """Emit an event to the dashboard."""
        timestamp = datetime.now(timezone.utc).strftime("%H:%M:%S")
        event = f"[{timestamp}] [{event_type}] {message}"
        self.events.append(event)
        self.last_event = event

    def get_last_event(self) -> str:
        """Get the most recent event."""
        return self.last_event


# Global event bus instance
_event_bus = DashboardEventBus()


def emit_dashboard_event(event_type: str, message: str):
    """
    Emit an event to the dashboard.

    Call this from anywhere in the codebase to update the "Last Event" display.

    Example:
        emit_dashboard_event("BUY_FILLED", "APE/USDT @ 1.2351")
    """
    _event_bus.emit(event_type, message)


def get_config_data(config_module, start_time: float) -> Dict[str, Any]:
    """Collect configuration and status data."""
    tp = getattr(config_module, 'TAKE_PROFIT_THRESHOLD', 1.0)
    sl = getattr(config_module, 'STOP_LOSS_THRESHOLD', 1.0)
    dt = getattr(config_module, 'DROP_TRIGGER_VALUE', 1.0)
    uptime_seconds = time.time() - start_time

    return {
        "GLOBAL_TRADING": getattr(config_module, 'GLOBAL_TRADING', False),
        "SESSION_ID": getattr(config_module, 'SESSION_DIR_NAME', 'unknown')[-10:],  # Last 10 chars
        "TP": (tp - 1.0) * 100,
        "SL": (sl - 1.0) * 100,
        "DT": (dt - 1.0) * 100,
        "MAX_TRADES": getattr(config_module, 'MAX_TRADES', 0),
        "uptime": time.strftime("%H:%M:%S", time.gmtime(uptime_seconds)),
    }


def get_portfolio_data(portfolio, engine) -> Dict[str, Any]:
    """Collect and calculate portfolio data."""
    positions_data = []
    total_value = portfolio.my_budget

    # Get all held assets
    held_assets = getattr(portfolio, 'held_assets', {}) or {}

    for symbol, asset in held_assets.items():
        try:
            # Get current price
            current_price = engine.get_current_price(symbol)
            if not current_price:
                current_price = asset.get('entry_price', 0)

            entry_price = asset.get('entry_price', 0) or asset.get('buy_price', 0)
            amount = asset.get('amount', 0)

            if amount > 0:
                position_value = current_price * amount
                total_value += position_value

                positions_data.append({
                    'symbol': symbol,
                    'amount': amount,
                    'entry': entry_price,
                    'current': current_price,
                })
        except Exception as e:
            logger.debug(f"Error processing position {symbol}: {e}")
            continue

    return {
        "total_value": total_value,
        "budget": portfolio.my_budget,
        "positions": positions_data,
    }


def get_drop_data(engine, portfolio, config_module) -> List[Dict[str, Any]]:
    """Collect and calculate top drop data from snapshot store (long-term solution)."""
    drops = []

    try:
        # Access drop snapshot store (long-term solution)
        drop_snapshot_store = getattr(engine, 'drop_snapshot_store', {})

        # If snapshot store is available and populated, use it (preferred)
        if drop_snapshot_store:
            logger.debug(f"Using drop_snapshot_store with {len(drop_snapshot_store)} symbols")

            for symbol, snap in drop_snapshot_store.items():
                try:
                    drop_pct = snap.get('drop_pct')
                    if drop_pct is None:
                        continue

                    drops.append({
                        'symbol': symbol,
                        'drop_pct': drop_pct,
                        'current_price': snap.get('current', 0),
                        'anchor': snap.get('peak', 0),
                    })
                except Exception as e:
                    logger.debug(f"Error processing snapshot for {symbol}: {e}")
                    continue

        # Fallback to legacy rolling_windows (quick fix compatibility)
        else:
            logger.debug("Falling back to legacy rolling_windows")
            topcoins = getattr(engine, 'topcoins', {})
            rolling_windows = getattr(engine, 'rolling_windows', {})

            for symbol in topcoins.keys():
                try:
                    # Get current price
                    current_price = engine.get_current_price(symbol)
                    if not current_price or current_price <= 0:
                        continue

                    # Get anchor (peak price) from rolling_windows
                    anchor = None
                    if symbol in rolling_windows:
                        try:
                            # Use the MAXIMUM price in the rolling window (peak), not the oldest price
                            anchor = rolling_windows[symbol].max()
                            # Validate anchor: must be valid float and > 0
                            if anchor in (None, float("-inf")) or anchor <= 0:
                                anchor = None
                        except AttributeError:
                            # Window exists but doesn't have max() method (shouldn't happen)
                            logger.debug(f"Rolling window for {symbol} missing max() method")

                    # Fallback to portfolio anchor
                    if not anchor or anchor <= 0:
                        try:
                            anchor = portfolio.get_drop_anchor(symbol)
                        except Exception:
                            pass

                    # Final fallback: use current price as anchor (window not yet filled)
                    if not anchor or anchor <= 0:
                        anchor = current_price

                    # Calculate drop percentage
                    drop_pct = ((current_price / anchor) - 1.0) * 100

                    # Debug log for transparency
                    logger.debug(f"[DROP_UI] {symbol} cur={current_price:.8f} anchor={anchor:.8f} drop={drop_pct:.2f}%")

                    drops.append({
                        'symbol': symbol,
                        'drop_pct': drop_pct,
                        'current_price': current_price,
                        'anchor': anchor,
                    })
                except Exception as e:
                    logger.debug(f"Error calculating drop for {symbol}: {e}")
                    continue

        # Sort by drop % (most negative first)
        drops.sort(key=lambda x: x['drop_pct'])

    except Exception as e:
        logger.error(f"Error getting drop data: {e}")

    return drops


def make_header_panel(config_data: Dict[str, Any]) -> Panel:
    """Create the header panel with status and config."""
    grid = Table.grid(expand=True)
    grid.add_column(justify="left", ratio=1)
    grid.add_column(justify="right", ratio=1)

    mode_style = "bold green" if config_data.get("GLOBAL_TRADING") else "bold yellow"
    mode_text = 'LIVE' if config_data.get('GLOBAL_TRADING') else 'OBSERVE'

    # Line 1: Mode, Session, Uptime, Heartbeat
    line1 = Text.assemble(
        ("MODE: ", "white"),
        (f"[{mode_text}]", mode_style),
        ("  |  SESSION: ", "white"),
        (f"...{config_data.get('SESSION_ID', 'unknown')}", "cyan"),
        ("  |  UPTIME: ", "white"),
        (config_data.get('uptime', '00:00:00'), "cyan"),
        ("  |  💓 HEARTBEAT: ", "white"),
        ("OK", "green"),
        (f" @ {datetime.now(timezone.utc).strftime('%H:%M:%S')}", "dim white"),
    )

    # Line 2: Config
    line2 = Text.assemble(
        ("CONFIG: ", "white"),
        (f"TP: {config_data.get('TP', 0):+.1f}%", "cyan"),
        (" | ", "dim white"),
        (f"SL: {config_data.get('SL', 0):+.1f}%", "cyan"),
        (" | ", "dim white"),
        (f"DT: {config_data.get('DT', 0):+.1f}%", "cyan"),
        (" | ", "dim white"),
        (f"MAX TRADES: {config_data.get('MAX_TRADES', 0)}", "cyan"),
    )

    # Combine
    content = Text()
    content.append("🤖 TRADING BOT DASHBOARD", style="bold white")
    content.append("\n")
    content.append(line1)
    content.append("\n")
    content.append(line2)

    return Panel(
        content,
        border_style="blue",
        expand=True
    )


def make_portfolio_panel(portfolio_data: Dict[str, Any]) -> Panel:
    """Create the portfolio panel."""
    table = Table(expand=True, border_style="magenta", show_header=True)
    table.add_column("Symbol", style="cyan", no_wrap=True)
    table.add_column("Menge", style="white", justify="right")
    table.add_column("Entry", style="yellow", justify="right")
    table.add_column("Current", style="yellow", justify="right")
    table.add_column("PnL ($/%)", justify="right")

    positions = portfolio_data.get("positions", [])

    if not positions:
        table.add_row("—", "—", "—", "—", "—")
    else:
        for pos in positions:
            pnl = (pos['current'] - pos['entry']) * pos['amount']
            pnl_pct = ((pos['current'] / pos['entry']) - 1) * 100 if pos['entry'] > 0 else 0
            style = "green" if pnl >= 0 else "red"

            table.add_row(
                pos['symbol'],
                f"{pos['amount']:.4f}",
                f"${pos['entry']:.4f}",
                f"${pos['current']:.4f}",
                Text(f"{pnl:+.2f} ({pnl_pct:+.2f}%)", style=style)
            )

    title = f"💼 Portfolio (Wert: ${portfolio_data.get('total_value', 0):.2f} / Budget: ${portfolio_data.get('budget', 0):.2f})"
    return Panel(table, title=title, border_style="magenta", expand=True)


def make_drop_panel(drop_data: List[Dict[str, Any]], config_data: Dict[str, Any]) -> Panel:
    """Create the top drops panel."""
    table = Table(expand=True, show_header=True)
    table.add_column("#", style="dim", justify="right", width=3)
    table.add_column("Symbol", style="bold", width=12)
    table.add_column("Drop %", justify="right", width=8)
    table.add_column("To Trig", justify="right", width=8)

    drop_trigger_pct = config_data.get('DT', 0)

    # Show top 10 drops
    top_drops = drop_data[:10]

    if not top_drops:
        table.add_row("—", "No data", "—", "—")
    else:
        for i, drop in enumerate(top_drops, 1):
            distance = drop['drop_pct'] - drop_trigger_pct

            # Color based on distance to trigger
            style = "white"
            if distance < 0.1:
                style = "bold red"
            elif distance < 0.5:
                style = "bold yellow"

            table.add_row(
                str(i),
                drop['symbol'],
                f"{drop['drop_pct']:.2f}%",
                Text(f"{distance:+.2f}%", style=style)
            )

    title = f"📉 Top Drops (Trigger: {drop_trigger_pct:.1f}%)"
    return Panel(table, title=title, border_style="cyan", expand=True)


def make_footer_panel(last_event: str) -> Panel:
    """Create the footer panel with last event."""
    timestamp = datetime.now(timezone.utc).strftime("%H:%M:%S")
    content = Text.assemble(
        ("🔔 LAST EVENT: ", "yellow"),
        (f"[{timestamp}] ", "dim white"),
        (last_event, "white")
    )
    return Panel(content, border_style="yellow", expand=True)


def run_dashboard(engine, portfolio, config_module):
    """
    Main function to run the live dashboard.

    This runs in a separate thread and continuously updates the terminal display.
    """
    if not RICH_AVAILABLE:
        logger.warning("Rich library not available - dashboard disabled")
        return

    # Create layout
    layout = Layout(name="root")
    layout.split(
        Layout(name="header", size=5),
        Layout(ratio=1, name="main"),
        Layout(size=3, name="footer"),
    )
    layout["main"].split_row(
        Layout(name="side", ratio=1),
        Layout(name="body", ratio=2)
    )

    start_time = time.time()

    # Get shutdown coordinator for clean exit
    shutdown_coordinator = getattr(engine, 'shutdown_coordinator', None)

    logger.info("Starting live dashboard...", extra={'event_type': 'DASHBOARD_START'})

    try:
        with Live(layout, screen=True, redirect_stderr=False, refresh_per_second=2):
            while True:
                # Check for shutdown
                if shutdown_coordinator and shutdown_coordinator.is_shutdown_requested():
                    logger.info("Dashboard shutting down...")
                    break

                try:
                    # Collect data
                    config_data = get_config_data(config_module, start_time)
                    portfolio_data = get_portfolio_data(portfolio, engine)
                    drop_data = get_drop_data(engine, portfolio, config_module)
                    last_event = _event_bus.get_last_event()

                    # Update UI components
                    layout["header"].update(make_header_panel(config_data))
                    layout["side"].update(make_drop_panel(drop_data, config_data))
                    layout["body"].update(make_portfolio_panel(portfolio_data))
                    layout["footer"].update(make_footer_panel(last_event))

                except Exception as update_error:
                    logger.debug(f"Dashboard update error: {update_error}")

                # Sleep to reduce CPU usage
                time.sleep(1.0)

    except KeyboardInterrupt:
        logger.info("Dashboard interrupted by user")
    except Exception as e:
        logger.error(f"Dashboard crashed: {e}", exc_info=True)
    finally:
        logger.info("Dashboard stopped", extra={'event_type': 'DASHBOARD_STOPPED'})
