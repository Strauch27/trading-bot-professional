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

import glob
import logging
import time
from collections import deque
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List

try:
    import psutil
    PSUTIL_AVAILABLE = True
except ImportError:
    PSUTIL_AVAILABLE = False

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

# Global state for debug display
_show_debug = True  # Show debug footer by default
_last_symbol = None  # Last snapshot symbol
_last_price = None   # Last snapshot price


class DashboardEventBus:
    """
    Thread-safe event bus for dashboard notifications.
    Stores recent events for display in the "Last Event" section.
    """

    def __init__(self, max_events: int = 100):
        import threading
        self.events = deque(maxlen=max_events)
        self.last_event = "Bot gestartet..."
        self._lock = threading.Lock()  # Thread-safe access

    def emit(self, event_type: str, message: str):
        """Emit an event to the dashboard (thread-safe)."""
        timestamp = datetime.now(timezone.utc).strftime("%H:%M:%S")
        event = f"[{timestamp}] [{event_type}] {message}"

        with self._lock:
            self.events.append(event)
            self.last_event = event

        # Log dashboard events for correlation with backend actions
        try:
            import inspect
            # Get caller information
            frame = inspect.currentframe()
            caller_frame = frame.f_back if frame else None
            source = "unknown"
            if caller_frame:
                source = f"{caller_frame.f_code.co_filename}:{caller_frame.f_lineno}"

            logger.info(
                "DASH_EVENT",
                extra={
                    'event_type': 'DASH_EVENT',
                    'dashboard_event_type': event_type,
                    'message': message,
                    'source': source,
                    'timestamp': timestamp
                }
            )
        except Exception as e:
            logger.debug(f"Failed to log dashboard event: {e}")

    def get_last_event(self) -> str:
        """Get the most recent event (thread-safe)."""
        with self._lock:
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


def update_snapshot_debug(symbol: str, price: float):
    """
    Update last snapshot debug info.

    Call this from engine when snapshots are received.
    """
    global _last_symbol, _last_price
    _last_symbol = symbol
    _last_price = price


def get_log_tail(n_lines: int = 20) -> List[str]:
    """
    Read the last N lines from the most recent JSONL log files.

    Returns:
        List of log lines (may be fewer than n_lines if file is short)
    """
    try:
        # Get current working directory
        import json
        import os
        cwd = os.getcwd()

        # Try multiple log locations (prioritize JSONL over legacy .log)
        log_patterns = [
            os.path.join(cwd, "sessions/*/logs/*.jsonl"),          # Primary: JSONL logs in sessions
            os.path.join(cwd, "logs/*.jsonl"),                     # Alternative: Root logs folder
            os.path.join(cwd, "logs/trading_bot_*.log"),           # Legacy: Old .log format
            os.path.join(cwd, "sessions/*/logs/*.log"),            # Legacy: Session .log
        ]

        log_files = []
        for pattern in log_patterns:
            found = glob.glob(pattern)
            log_files.extend(found)

        if not log_files:
            return [
                f"No log files found in {cwd}",
                "Checked patterns:",
                f"  {cwd}/sessions/*/logs/*.jsonl",
                f"  {cwd}/logs/*.jsonl",
                f"  {cwd}/logs/trading_bot_*.log",
            ]

        # Get most recent log file
        latest_log = max(log_files, key=lambda p: Path(p).stat().st_mtime)
        is_jsonl = latest_log.endswith('.jsonl')

        # Read last N lines
        with open(latest_log, 'r', encoding='utf-8', errors='ignore') as f:
            lines = deque(f, maxlen=n_lines)
            result = [f"[LOG] {Path(latest_log).name} (last {n_lines} lines):"]

            # Format JSONL entries nicely
            if is_jsonl:
                for line in lines:
                    try:
                        entry = json.loads(line.strip())
                        timestamp = entry.get('timestamp', entry.get('time', ''))
                        level = entry.get('level', 'INFO')
                        message = entry.get('message', entry.get('msg', ''))
                        event_type = entry.get('event_type', '')

                        if event_type:
                            formatted = f"{timestamp} [{level}] {event_type}: {message}"
                        else:
                            formatted = f"{timestamp} [{level}] {message}"
                        result.append(formatted[:120])  # Truncate long lines
                    except json.JSONDecodeError:
                        result.append(line.strip()[:120])
            else:
                # Plain text logs
                result.extend([line.strip()[:120] for line in lines])

            return result

    except Exception as e:
        import traceback
        return [f"Error reading logs: {e}", traceback.format_exc()]


def get_system_resources() -> Dict[str, Any]:
    """Collect system CPU and RAM usage (cross-platform)."""
    if not PSUTIL_AVAILABLE:
        return {
            "cpu_percent": None,
            "ram_percent": None,
            "ram_used_gb": None,
            "ram_total_gb": None,
        }

    try:
        # CPU percentage (non-blocking, interval=None uses cached value)
        cpu_percent = psutil.cpu_percent(interval=None)

        # Memory info
        mem = psutil.virtual_memory()
        ram_percent = mem.percent
        ram_used_gb = mem.used / (1024 ** 3)  # Convert to GB
        ram_total_gb = mem.total / (1024 ** 3)  # Convert to GB

        return {
            "cpu_percent": cpu_percent,
            "ram_percent": ram_percent,
            "ram_used_gb": ram_used_gb,
            "ram_total_gb": ram_total_gb,
        }
    except Exception as e:
        logger.debug(f"Error getting system resources: {e}")
        return {
            "cpu_percent": None,
            "ram_percent": None,
            "ram_used_gb": None,
            "ram_total_gb": None,
        }


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
        "MAX_POSITIONS": getattr(config_module, 'MAX_CONCURRENT_POSITIONS', 10),
        "uptime": time.strftime("%H:%M:%S", time.gmtime(uptime_seconds)),
    }


def get_portfolio_data(portfolio, engine) -> Dict[str, Any]:
    """Collect and calculate portfolio data."""
    positions_data = []
    total_value = portfolio.my_budget

    # Get all held assets - create a copy to avoid race conditions
    held_assets = getattr(portfolio, 'held_assets', {}) or {}
    held_assets_copy = dict(held_assets)

    for symbol, asset in held_assets_copy.items():
        try:
            # Get current price
            current_price = engine.get_current_price(symbol)
            if not current_price:
                snap, snap_ts = engine.get_snapshot_entry(symbol) if hasattr(engine, 'get_snapshot_entry') else (None, None)
                if snap:
                    current_price = snap.get('price', {}).get('last')
            if not current_price:
                current_price = asset.get('entry_price', 0) or getattr(portfolio, 'last_prices', {}).get(symbol)

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
    global _last_symbol, _last_price
    drops = []

    try:
        # Access drop snapshot store (long-term solution)
        drop_snapshot_store = getattr(engine, 'drop_snapshot_store', {})
        stale_symbols: List[str] = []
        stale_ttl = config_module.SNAPSHOT_STALE_TTL_S
        now_ts = time.time()

        # If snapshot store is available and populated, use it (preferred)
        if drop_snapshot_store:
            logger.debug(f"Using drop_snapshot_store with {len(drop_snapshot_store)} symbols")

            iter_entries = getattr(engine, 'iter_snapshot_entries', None)
            if callable(iter_entries):
                snapshot_entries = list(iter_entries())
            else:
                snapshot_entries = []
                for symbol, entry in drop_snapshot_store.items():
                    if isinstance(entry, dict) and 'snapshot' in entry:
                        snapshot_entries.append((symbol, entry.get('snapshot'), entry.get('ts')))
                    else:
                        snapshot_entries.append((symbol, entry, None))

            for symbol, snap, snap_ts in snapshot_entries:
                try:
                    if not snap:
                        continue

                    if snap_ts is not None and (now_ts - snap_ts) > stale_ttl:
                        stale_symbols.append(symbol)
                        continue

                    # Validate snapshot version
                    if snap.get('v') != 1:
                        logger.debug(f"Skipping snapshot for {symbol}: unknown version {snap.get('v')}")
                        continue

                    # Read from MarketSnapshot structure
                    windows = snap.get('windows', {})
                    drop_pct = windows.get('drop_pct')

                    if drop_pct is None:
                        continue

                    price_data = snap.get('price', {})
                    current_price = price_data.get('last', 0)
                    # V9_3: Read anchor from snapshot (fallback to peak for compatibility)
                    anchor = windows.get('anchor') or windows.get('peak', 0)

                    # V9_3: Read spread from liquidity data
                    liquidity = snap.get('liquidity', {})
                    spread_pct = liquidity.get('spread_pct')

                    drops.append({
                        'symbol': symbol,
                        'drop_pct': drop_pct,
                        'current_price': current_price,
                        'anchor': anchor,
                        'spread_pct': spread_pct,
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
                        'spread_pct': None,  # Spread not available in legacy fallback
                    })
                except Exception as e:
                    logger.debug(f"Error calculating drop for {symbol}: {e}")
                    continue

        # Expose stale symbols for health monitoring
        setattr(engine, '_last_stale_snapshot_symbols', stale_symbols)

        # Log stale snapshot warnings if threshold exceeded
        stale_threshold = getattr(config_module, 'STALE_SNAPSHOT_WARN_THRESHOLD', 5)
        if len(stale_symbols) >= stale_threshold:
            logger.warning(
                f"STALE_SNAPSHOTS: {len(stale_symbols)} symbols have stale market data (>{stale_ttl}s old)",
                extra={
                    'event_type': 'STALE_SNAPSHOTS_WARNING',
                    'stale_count': len(stale_symbols),
                    'stale_symbols': stale_symbols[:10],  # First 10
                    'threshold': stale_threshold,
                    'stale_ttl_s': stale_ttl
                }
            )

            # Emit health event for monitoring systems
            try:
                import logging as log_module

                from core.logger_factory import AUDIT_LOG, log_event
                log_event(
                    AUDIT_LOG(),
                    "stale_snapshots_health",
                    message=f"Stale snapshot health degradation: {len(stale_symbols)}/{len(drop_snapshot_store)} symbols stale",
                    level=log_module.WARNING,
                    stale_count=len(stale_symbols),
                    total_symbols=len(drop_snapshot_store),
                    stale_symbols=stale_symbols[:10],
                    stale_ttl_s=stale_ttl
                )
            except Exception as e:
                logger.debug(f"Failed to emit stale snapshot health event: {e}")

        if drops:
            first_drop = drops[0]
            price_val = first_drop.get('current_price')
            if price_val:
                _last_symbol = first_drop.get('symbol')
                _last_price = price_val

        # Custom sorting: BTC first, then biggest losers
        # 1. Extract BTC/USDT if present
        btc_drop = None
        other_drops = []

        for drop in drops:
            if drop['symbol'] == 'BTC/USDT':
                btc_drop = drop
            else:
                other_drops.append(drop)

        # 2. Sort other drops by drop % (most negative first = biggest losers)
        other_drops.sort(key=lambda x: x['drop_pct'])

        # 3. Combine: BTC first (if available), then biggest losers
        sorted_drops = []
        if btc_drop:
            sorted_drops.append(btc_drop)
        sorted_drops.extend(other_drops)

        drops = sorted_drops

    except Exception as e:
        logger.error(f"Error getting drop data: {e}")

    return drops


def get_health_data(engine, config_module) -> Dict[str, Any]:
    """Collect health metrics for footer display."""
    stats = getattr(engine, '_last_market_data_stats', {}) or {}
    snapshot_ts = getattr(engine, '_last_snapshot_ts', 0.0)
    stale_symbols = getattr(engine, '_last_stale_snapshot_symbols', []) or []

    snapshot_age = None
    if snapshot_ts:
        snapshot_age = max(0.0, time.time() - snapshot_ts)

    return {
        'requested': stats.get('requested', 0),
        'fetched': stats.get('fetched', 0),
        'failed': stats.get('failed', 0),
        'degraded': stats.get('degraded', 0),
        'retry_attempts': stats.get('retry_attempts', 0),
        'failures': stats.get('failures', []),
        'degraded_symbols': stats.get('degraded_symbols', []),
        'snapshot_age': snapshot_age,
        'stale_count': len(stale_symbols),
        'stale_symbols': stale_symbols,
        'snapshot_ttl': config_module.SNAPSHOT_STALE_TTL_S
    }


def make_header_panel(config_data: Dict[str, Any], system_resources: Dict[str, Any]) -> Panel:
    """Create the header panel with status, config, and system resources."""
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
        ("  |  [HEARTBEAT]: ", "white"),
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
        (f"MAX POS: {config_data.get('MAX_POSITIONS', 0)}", "cyan"),
    )

    # Line 3: System Resources (CPU & RAM)
    cpu_percent = system_resources.get('cpu_percent')
    ram_percent = system_resources.get('ram_percent')
    ram_used_gb = system_resources.get('ram_used_gb')
    ram_total_gb = system_resources.get('ram_total_gb')

    if cpu_percent is not None and ram_percent is not None:
        # Color coding for CPU
        cpu_style = "green" if cpu_percent < 50 else "yellow" if cpu_percent < 80 else "red"
        # Color coding for RAM
        ram_style = "green" if ram_percent < 60 else "yellow" if ram_percent < 80 else "red"

        line3 = Text.assemble(
            ("SYSTEM: ", "white"),
            ("CPU: ", "white"),
            (f"{cpu_percent:.1f}%", cpu_style),
            (" | ", "dim white"),
            ("RAM: ", "white"),
            (f"{ram_percent:.1f}%", ram_style),
            (f" ({ram_used_gb:.1f}/{ram_total_gb:.1f} GB)", "dim cyan"),
        )
    else:
        line3 = Text.assemble(
            ("SYSTEM: ", "white"),
            ("CPU/RAM monitoring unavailable", "dim white"),
        )

    # Combine
    content = Text()
    content.append("[TRADING BOT DASHBOARD]", style="bold white")
    content.append("\n")
    content.append(line1)
    content.append("\n")
    content.append(line2)
    content.append("\n")
    content.append(line3)

    return Panel(
        content,
        border_style="blue",
        expand=True
    )


def make_portfolio_panel(portfolio_data: Dict[str, Any]) -> Panel:
    """Create the portfolio panel."""
    try:
        table = Table(expand=True, border_style="magenta", show_header=True)
        table.add_column("Symbol", style="cyan", no_wrap=True)
        table.add_column("Menge", style="white", justify="right")
        table.add_column("Entry", style="yellow", justify="right")
        table.add_column("Current", style="yellow", justify="right")
        table.add_column("PnL ($/%)", justify="right")

        positions = portfolio_data.get("positions", []) if portfolio_data else []
    except Exception as e:
        return Panel(Text(f"Portfolio Panel Error: {e}", style="red"), title="PORTFOLIO ERROR", border_style="red")

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

    title = f"[PORTFOLIO] Wert: ${portfolio_data.get('total_value', 0):.2f} / Budget: ${portfolio_data.get('budget', 0):.2f}"
    return Panel(table, title=title, border_style="magenta", expand=True)


def make_drop_panel(drop_data: List[Dict[str, Any]], config_data: Dict[str, Any], engine) -> Panel:
    """Create the top drops panel (V9_3: with current price, anchor and spread columns)."""
    try:
        global _last_symbol, _last_price
        table = Table(expand=True, show_header=True)
        table.add_column("#", style="dim", justify="right", width=3)
        table.add_column("Symbol", style="bold", width=10)
        table.add_column("Last", justify="right", width=11)
        table.add_column("Drop %", justify="right", width=8)
        table.add_column("Spread %", justify="right", width=8)
        table.add_column("Anchor", justify="right", width=11)
        table.add_column("To Trig", justify="right", width=8)

        drop_trigger_pct = config_data.get('DT', 0) if config_data else 0
    except Exception as e:
        return Panel(Text(f"Drop Panel Error: {e}", style="red"), title="DROPS ERROR", border_style="red")

    # Show top 10 drops
    top_drops = drop_data[:10]

    if not top_drops:
        msg = "Keine Daten. Prüfe Market-Data-Loop. Siehe Logs: MARKET_DATA_THREAD_STARTED / MD_LOOP_CFG / MD_THREAD_STATUS."
        table.add_row("—", msg, "—", "—", "—", "—", "—")
    else:
        for i, drop in enumerate(top_drops, 1):
            distance = drop['drop_pct'] - drop_trigger_pct
            anchor = drop.get('anchor', 0)
            spread_pct = drop.get('spread_pct')
            current_price = drop.get('current_price', 0)

            # Color based on distance to trigger
            style = "white"
            if distance < 0.1:
                style = "bold red"
            elif distance < 0.5:
                style = "bold yellow"

            # Format spread percentage
            if spread_pct is not None:
                spread_str = f"{spread_pct:.3f}%"
            else:
                spread_str = "—"

            table.add_row(
                str(i),
                drop['symbol'],
                f"${current_price:.6f}" if current_price else "—",
                f"{drop['drop_pct']:.2f}%",
                spread_str,
                f"${anchor:.6f}" if anchor else "—",
                Text(f"{distance:+.2f}%", style=style)
            )

    # DEBUG_DROPS: Add snapshot reception counter to title
    snap_rx = getattr(engine, '_snap_recv', 0) if engine else 0
    last_tick_ts = "-"
    if drop_data and engine:
        try:
            primary_symbol = drop_data[0]['symbol']
            if hasattr(engine, 'get_snapshot_entry'):
                snap, snap_ts = engine.get_snapshot_entry(primary_symbol)
                if snap_ts:
                    try:
                        last_tick_ts = datetime.fromtimestamp(snap_ts, tz=timezone.utc).strftime("%H:%M:%S")
                    except Exception:
                        pass
                elif not snap and hasattr(engine, 'iter_snapshot_entries'):
                    try:
                        first_symbol, first_snap, first_ts = next(engine.iter_snapshot_entries())
                        if first_ts:
                            last_tick_ts = datetime.fromtimestamp(first_ts, tz=timezone.utc).strftime("%H:%M:%S")
                            primary_symbol = first_symbol
                            snap = first_snap
                    except (StopIteration, AttributeError, TypeError, ValueError):
                        pass
        except Exception:
            pass  # Fail gracefully if engine methods don't exist

        global _last_symbol, _last_price
        price_val = drop_data[0].get('current_price')
        if price_val:
            _last_symbol = drop_data[0]['symbol']
            _last_price = price_val

    # Add last snapshot symbol and price
    last_snap_info = ""
    if _last_symbol and _last_price is not None:
        last_snap_info = f" • {_last_symbol}={_last_price:.8f}"

    title = f"📉 Top Drops (Trigger: {drop_trigger_pct:.1f}%) • rx={snap_rx} • ts={last_tick_ts}{last_snap_info}"
    return Panel(table, title=title, border_style="cyan", expand=True)


def make_footer_panel(last_event: str, health: Dict[str, Any]) -> Panel:
    """Create the footer panel with last event and market-data health."""
    timestamp = datetime.now(timezone.utc).strftime("%H:%M:%S")

    content = Text()
    content.append(Text.assemble(
        ("[LAST EVENT]: ", "yellow"),
        (f"[{timestamp}] ", "dim white"),
        (last_event, "white")
    ))

    md_requested = health.get('requested', 0)
    md_fetched = health.get('fetched', 0)
    md_failed = health.get('failed', 0)
    md_degraded = health.get('degraded', 0)
    md_retries = health.get('retry_attempts', 0)
    snapshot_age = health.get('snapshot_age')
    snapshot_ttl = health.get('snapshot_ttl', 30.0)
    stale_count = health.get('stale_count', 0)

    if snapshot_age is None:
        snapshot_str = "n/a"
    else:
        snapshot_str = f"{snapshot_age:.1f}s"
        if snapshot_age > snapshot_ttl:
            snapshot_str += " !"

    status_style = "green" if md_failed == 0 else "red"

    failures = health.get('failures') or []
    degraded_symbols = health.get('degraded_symbols') or []

    content.append("\n")
    content.append(Text.assemble(
        (" MD ", "cyan"),
        (f"{md_fetched}/{md_requested} fetched", status_style),
        ("  |  failures=", "dim white"),
        (str(md_failed), status_style),
        ("  |  degraded=", "dim white"),
        (str(md_degraded), "magenta" if md_degraded else "dim white"),
        ("  |  retries=", "dim white"),
        (str(md_retries), "white"),
        ("  |  stale=", "dim white"),
        (str(stale_count), "yellow" if stale_count else "dim white"),
        ("  |  snapshot age=", "dim white"),
        (snapshot_str, "white" if snapshot_age is None or snapshot_age <= snapshot_ttl else "red")
    ))

    if failures:
        content.append("\n")
        content.append(Text(f"   fails: {', '.join(failures[:3])}", style="red"))
    if degraded_symbols:
        content.append("\n")
        content.append(Text(f"   slow: {', '.join(degraded_symbols[:3])}", style="magenta"))

    # Display stale symbols (if any)
    stale_symbols = health.get('stale_symbols', [])
    if stale_symbols:
        content.append("\n")
        content.append(Text(f"   stale: {', '.join(stale_symbols[:3])}", style="yellow"))

    return Panel(content, border_style="yellow", expand=True)


def make_debug_panel() -> Panel:
    """Create the debug panel with last 20 log lines."""
    log_lines = get_log_tail(20)

    # Create text content with log lines
    content = Text()
    for line in log_lines:
        # Truncate very long lines
        line = line.rstrip()
        if len(line) > 150:
            line = line[:147] + "..."
        content.append(line + "\n", style="dim white")

    return Panel(
        content,
        title="[DEBUG LOG] (last 20 lines) - Press 'd' to toggle",
        border_style="dim blue",
        expand=True
    )


def run_dashboard(engine, portfolio, config_module):
    """
    Main function to run the live dashboard.

    This runs in a separate thread and continuously updates the terminal display.
    """
    if not RICH_AVAILABLE:
        logger.warning("Rich library not available - dashboard disabled")
        return

    # Check if debug mode is enabled
    import config
    debug_drops = getattr(config, "DEBUG_DROPS", False)

    # Create layout
    layout = Layout(name="root")

    if debug_drops:
        # Layout with debug panel
        layout.split(
            Layout(name="header", size=6),  # Increased from 5 to 6 for system resources line
            Layout(ratio=1, name="main"),
            Layout(size=3, name="footer"),
            Layout(size=12, name="debug"),
        )
    else:
        # Standard layout without debug panel
        layout.split(
            Layout(name="header", size=6),  # Increased from 5 to 6 for system resources line
            Layout(ratio=1, name="main"),
            Layout(size=3, name="footer"),
        )

    layout["main"].split_row(
        Layout(name="side", ratio=1),
        Layout(name="body", ratio=1)
    )

    start_time = time.time()

    # Initialize CPU percentage tracking (first call to establish baseline)
    if PSUTIL_AVAILABLE:
        try:
            psutil.cpu_percent(interval=0.1)
        except Exception:
            pass

    # Get shutdown coordinator for clean exit
    shutdown_coordinator = getattr(engine, 'shutdown_coordinator', None)

    logger.info("Starting live dashboard...", extra={'event_type': 'DASHBOARD_START'})

    try:
        with Live(layout, screen=True, redirect_stderr=True, refresh_per_second=2):
            while True:
                # Check for shutdown
                if shutdown_coordinator and shutdown_coordinator.is_shutdown_requested():
                    logger.info("Dashboard shutting down...")
                    break

                try:
                    # Collect data
                    config_data = get_config_data(config_module, start_time)
                    system_resources = get_system_resources()
                    portfolio_data = get_portfolio_data(portfolio, engine)
                    drop_data = get_drop_data(engine, portfolio, config_module)
                    health_data = get_health_data(engine, config_module)
                    last_event = _event_bus.get_last_event()

                    # Update UI components
                    layout["header"].update(make_header_panel(config_data, system_resources))
                    layout["side"].update(make_drop_panel(drop_data, config_data, engine))
                    layout["body"].update(make_portfolio_panel(portfolio_data))
                    layout["footer"].update(make_footer_panel(last_event, health_data))

                    # Update debug panel if enabled
                    if debug_drops:
                        layout["debug"].update(make_debug_panel())

                except Exception as update_error:
                    logger.error(f"Dashboard update error: {update_error}", exc_info=True)
                    # Show error in dashboard instead of empty content
                    error_text = Text(f"Dashboard Update Error:\n{str(update_error)}", style="red bold")
                    layout["body"].update(Panel(error_text, title="ERROR", border_style="red"))

                # Sleep to reduce CPU usage
                time.sleep(1.0)

    except KeyboardInterrupt:
        logger.info("Dashboard interrupted by user")
    except Exception as e:
        logger.error(f"Dashboard crashed: {e}", exc_info=True)
    finally:
        # CRITICAL FIX (C-UI-01): Explicit terminal restore to prevent state leak
        # Rich Live with screen=True may leave terminal in corrupted state on crash
        import sys
        sys.stdout.write("\033[?1049l")  # Exit alternate screen
        sys.stdout.write("\033[0m")      # Reset all attributes
        sys.stdout.flush()
        logger.info("Dashboard stopped", extra={'event_type': 'DASHBOARD_STOPPED'})
