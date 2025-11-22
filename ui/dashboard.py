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

    def get_recent_events(self, n: int = 5) -> List[str]:
        """Get the N most recent events (thread-safe)."""
        with self._lock:
            # Return last N events in reverse order (newest first)
            return list(self.events)[-n:][::-1]


# Global event bus instance
_event_bus = DashboardEventBus()

# Global FSM event tracking
_fsm_events = deque(maxlen=100)
_fsm_events_lock = None


def emit_fsm_event(symbol: str, from_phase: str, to_phase: str, event: str):
    """
    Track FSM state transitions for dashboard display.

    Call this from FSM engine when state transitions occur.
    """
    global _fsm_events, _fsm_events_lock

    # Initialize lock if needed
    if _fsm_events_lock is None:
        import threading
        _fsm_events_lock = threading.Lock()

    timestamp = datetime.now(timezone.utc).strftime("%H:%M:%S")

    with _fsm_events_lock:
        _fsm_events.append({
            'timestamp': timestamp,
            'symbol': symbol,
            'from_phase': from_phase,
            'to_phase': to_phase,
            'event': event
        })


def get_recent_fsm_events(n: int = 5) -> List[Dict[str, str]]:
    """Get the N most recent FSM events."""
    global _fsm_events, _fsm_events_lock

    if _fsm_events_lock is None:
        return []

    with _fsm_events_lock:
        # Return last N events in reverse order (newest first)
        return list(_fsm_events)[-n:][::-1]


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
    """Collect and calculate portfolio data including ghost positions."""
    positions_data = []
    ghost_count = 0
    total_value = portfolio.my_budget

    # FSM-Mode: Get positions directly from engine (highest priority)
    if hasattr(engine, 'get_positions'):
        try:
            fsm_positions = engine.get_positions()
            if fsm_positions:
                logger.debug(f"[DASHBOARD] Found {len(fsm_positions)} FSM positions")
                for symbol, coin_state in fsm_positions.items():
                    try:
                        current_price = engine.get_current_price(symbol)
                        if not current_price:
                            snap, snap_ts = engine.get_snapshot_entry(symbol) if hasattr(engine, 'get_snapshot_entry') else (None, None)
                            if snap:
                                current_price = snap.get('price', {}).get('last')
                        if not current_price:
                            current_price = coin_state.entry_price

                        amount = coin_state.amount
                        if amount > 0:
                            position_value = current_price * amount
                            total_value += position_value

                            entry_fee_per_unit = getattr(coin_state, 'entry_fee_per_unit', 0) or 0

                            positions_data.append({
                                'symbol': symbol,
                                'amount': amount,
                                'entry': coin_state.entry_price,
                                'current': current_price,
                                'entry_fee_per_unit': entry_fee_per_unit,
                                'is_ghost': False
                            })
                            logger.debug(f"[DASHBOARD] Added FSM position: {symbol} - {amount} @ {coin_state.entry_price}")
                    except Exception as e:
                        logger.debug(f"Error processing FSM position {symbol}: {e}")
                        continue

                # Return early if we found FSM positions
                if positions_data:
                    return {
                        "positions": positions_data,
                        "total_value": total_value,
                        "ghost_count": ghost_count,
                    }
        except Exception as e:
            logger.warning(f"Failed to get FSM positions: {e}")

    # Try to use get_positions_with_ghosts if available (new method)
    if hasattr(portfolio, 'get_positions_with_ghosts'):
        try:
            all_positions = portfolio.get_positions_with_ghosts(engine)

            for pos in all_positions:
                symbol = pos.get('symbol')
                is_ghost = pos.get('is_ghost', False)

                try:
                    if is_ghost:
                        # Ghost position - no actual value, just display info
                        ghost_count += 1
                        positions_data.append({
                            'symbol': symbol,
                            'amount': 0,
                            'entry': pos.get('entry_price', 0),
                            'current': pos.get('entry_price', 0),
                            'is_ghost': True,
                            'abort_reason': pos.get('abort_reason', 'unknown'),
                            'violations': pos.get('violations', []),
                            'raw_price': pos.get('raw_price'),
                            'raw_amount': pos.get('raw_amount'),
                            'market_precision': pos.get('market_precision', {})
                        })
                    else:
                        # Real position
                        current_price = engine.get_current_price(symbol)
                        if not current_price:
                            snap, snap_ts = engine.get_snapshot_entry(symbol) if hasattr(engine, 'get_snapshot_entry') else (None, None)
                            if snap:
                                current_price = snap.get('price', {}).get('last')
                        if not current_price:
                            current_price = pos.get('entry_price', 0)

                        amount = pos.get('amount', 0)
                        if amount > 0:
                            position_value = current_price * amount
                            total_value += position_value

                            # Extract fees for net PnL calculation
                            entry_fee_per_unit = pos.get('buy_fee_quote_per_unit', 0) or pos.get('entry_fee_per_unit', 0) or 0

                            positions_data.append({
                                'symbol': symbol,
                                'amount': amount,
                                'entry': pos.get('entry_price', 0),
                                'current': current_price,
                                'entry_fee_per_unit': entry_fee_per_unit,
                                'is_ghost': False
                            })
                except Exception as e:
                    logger.debug(f"Error processing position {symbol}: {e}")
                    continue
        except Exception as e:
            logger.warning(f"Failed to get positions with ghosts, falling back to legacy: {e}")
            # Fallback to old method below
            all_positions = []
    else:
        all_positions = []

    # Fallback: Use old method if new method not available or failed
    if not all_positions:
        held_assets = getattr(portfolio, 'held_assets', {}) or {}
        held_assets_copy = dict(held_assets)

        for symbol, asset in held_assets_copy.items():
            try:
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

                    # Extract fees for net PnL calculation
                    entry_fee_per_unit = asset.get('buy_fee_quote_per_unit', 0) or asset.get('entry_fee_per_unit', 0) or 0

                    positions_data.append({
                        'symbol': symbol,
                        'amount': amount,
                        'entry': entry_price,
                        'current': current_price,
                        'entry_fee_per_unit': entry_fee_per_unit,
                        'is_ghost': False
                    })
            except Exception as e:
                logger.debug(f"Error processing position {symbol}: {e}")
                continue

    return {
        "total_value": total_value,
        "budget": portfolio.my_budget,
        "positions": positions_data,
        "ghost_count": ghost_count
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


def make_header_panel(config_data: Dict[str, Any], system_resources: Dict[str, Any], portfolio_data: Dict[str, Any] = None) -> Panel:
    """Create the header panel with status, config, system resources, and ghost stats."""
    grid = Table.grid(expand=True)
    grid.add_column(justify="left", ratio=1)
    grid.add_column(justify="right", ratio=1)

    mode_style = "bold green" if config_data.get("GLOBAL_TRADING") else "bold yellow"
    mode_text = 'LIVE' if config_data.get('GLOBAL_TRADING') else 'OBSERVE'

    # Get ghost count from portfolio data
    ghost_count = portfolio_data.get('ghost_count', 0) if portfolio_data else 0

    # Line 1: Mode, Session, Uptime, Heartbeat, Ghosts
    line1_parts = [
        ("MODE: ", "white"),
        (f"[{mode_text}]", mode_style),
        ("  |  SESSION: ", "white"),
        (f"...{config_data.get('SESSION_ID', 'unknown')}", "cyan"),
        ("  |  UPTIME: ", "white"),
        (config_data.get('uptime', '00:00:00'), "cyan"),
        ("  |  [HEARTBEAT]: ", "white"),
        ("OK", "green"),
        (f" @ {datetime.now(timezone.utc).strftime('%H:%M:%S')}", "dim white"),
    ]

    # Add ghost count if any
    if ghost_count > 0:
        line1_parts.extend([
            ("  |  ", "dim white"),
            ("👻 GHOSTS: ", "yellow"),
            (f"{ghost_count}", "red"),
        ])

    line1 = Text.assemble(*line1_parts)

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
    """Create the portfolio panel with ghost positions."""
    try:
        from rich import box
        table = Table(show_header=True, box=box.SIMPLE, padding=(0, 1), collapse_padding=True)
        table.add_column("Symbol", style="cyan", no_wrap=True, width=11)
        table.add_column("Qty", style="white", justify="right", width=8)
        table.add_column("Entry", style="yellow", justify="right", width=9)
        table.add_column("Current", style="yellow", justify="right", width=9)
        table.add_column("PnL", justify="right", width=13)
        table.add_column("St", justify="center", no_wrap=True, width=3)

        positions = portfolio_data.get("positions", []) if portfolio_data else []
        ghost_count = portfolio_data.get("ghost_count", 0)
    except Exception as e:
        return Panel(Text(f"Portfolio Panel Error: {e}", style="red"), title="PORTFOLIO ERROR", border_style="red")

    if not positions:
        # Don't add empty row - keep table minimal
        pass
    else:
        for pos in positions:
            is_ghost = pos.get('is_ghost', False)

            if is_ghost:
                # Ghost position - rejected buy intent
                abort_reason = pos.get('abort_reason', 'unknown')
                violations = pos.get('violations', [])

                # Format violations for tooltip-like display
                violation_str = ", ".join(violations[:2])  # Show first 2 violations
                if len(violations) > 2:
                    violation_str += "..."

                table.add_row(
                    Text(pos['symbol'], style="dim cyan"),
                    Text("GHOST", style="dim white"),
                    Text(f"${pos['entry']:.4f}", style="dim yellow"),
                    Text("—", style="dim white"),
                    Text(abort_reason[:12], style="dim red"),
                    Text("👻", style="dim white")
                )
            else:
                # Real position - calculate NET PnL (after fees)
                amount = pos['amount']
                entry_price = pos['entry']
                current_price = pos['current']
                entry_fee_per_unit = pos.get('entry_fee_per_unit', 0) or 0

                # Gross PnL (price difference)
                gross_pnl = (current_price - entry_price) * amount

                # Fees: entry fees + estimated exit fees (0.1% maker fee)
                entry_fees = entry_fee_per_unit * amount
                estimated_exit_fees = current_price * amount * 0.001  # 0.1% fee estimate
                total_fees = entry_fees + estimated_exit_fees

                # Net PnL (after all fees)
                net_pnl = gross_pnl - total_fees

                # Net PnL percentage (relative to invested capital including entry fees)
                invested_capital = (entry_price * amount) + entry_fees
                net_pnl_pct = (net_pnl / invested_capital * 100) if invested_capital > 0 else 0

                style = "green" if net_pnl >= 0 else "red"

                table.add_row(
                    pos['symbol'],
                    f"{amount:.4f}",
                    f"${entry_price:.4f}",
                    f"${current_price:.4f}",
                    Text(f"{net_pnl:+.2f} ({net_pnl_pct:+.2f}%)", style=style),
                    Text("✓", style="green")
                )

    # Add ghost count to title if any
    ghost_suffix = f" | Ghosts: {ghost_count}" if ghost_count > 0 else ""
    title = f"[PORTFOLIO] Wert: ${portfolio_data.get('total_value', 0):.2f} / Budget: ${portfolio_data.get('budget', 0):.2f}{ghost_suffix}"
    return Panel(table, title=title, border_style="magenta")


def make_drop_panel(drop_data: List[Dict[str, Any]], config_data: Dict[str, Any], engine) -> Panel:
    """Create the top drops panel (V9_3: with current price, anchor and spread columns)."""
    try:
        global _last_symbol, _last_price
        from rich import box
        table = Table(show_header=True, box=box.SIMPLE, padding=(0, 1), collapse_padding=True)
        table.add_column("#", style="dim", justify="right", width=2, no_wrap=True)
        table.add_column("Symbol", style="bold", width=11, no_wrap=True)
        table.add_column("Last", justify="right", width=10, no_wrap=True)
        table.add_column("Drop", justify="right", width=7, no_wrap=True)
        table.add_column("Spread", justify="right", width=7, no_wrap=True)
        table.add_column("Anchor", justify="right", width=10, no_wrap=True)
        table.add_column("Trig", justify="right", width=7, no_wrap=True)

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
    return Panel(table, title=title, border_style="cyan")


def make_event_history_panel() -> Panel:
    """Create panel showing last 5 dashboard events."""
    try:
        from rich import box
        table = Table(show_header=True, box=box.SIMPLE, padding=(0, 1), collapse_padding=True)
        table.add_column("Zeit", style="dim cyan", width=8, no_wrap=True)
        table.add_column("Typ", style="yellow", width=12, no_wrap=True)
        table.add_column("Nachricht", style="white")

        recent_events = _event_bus.get_recent_events(5)

        if not recent_events:
            # Don't show "no events" row - keep minimal
            pass
        else:
            for event in recent_events:
                # Parse event format: "[HH:MM:SS] [TYPE] message"
                try:
                    parts = event.split("] ", 2)
                    if len(parts) >= 3:
                        timestamp = parts[0].replace("[", "").strip()
                        event_type = parts[1].replace("[", "").strip()
                        message = parts[2].strip()

                        # Truncate long messages
                        if len(message) > 80:
                            message = message[:77] + "..."

                        # Color code by event type
                        type_style = "yellow"
                        if "TRADE_EXIT" in event_type:
                            type_style = "green" if "🟢" in message else "red"
                        elif "TRADE_ENTRY" in event_type:
                            type_style = "cyan"

                        table.add_row(
                            timestamp,
                            Text(event_type, style=type_style),
                            message
                        )
                    else:
                        table.add_row("—", "—", event[:80])
                except Exception:
                    table.add_row("—", "—", event[:80])

        return Panel(table, title="📋 Event History (Last 5)", border_style="yellow")

    except Exception as e:
        return Panel(Text(f"Event History Error: {e}", style="red"), title="EVENT ERROR", border_style="red")


def make_fsm_status_panel() -> Panel:
    """Create panel showing last 5 FSM state transitions."""
    try:
        from rich import box
        table = Table(show_header=True, box=box.SIMPLE, padding=(0, 1), collapse_padding=True)
        table.add_column("Zeit", style="dim cyan", width=8, no_wrap=True)
        table.add_column("Symbol", style="cyan", width=11, no_wrap=True)
        table.add_column("Von", style="yellow", width=11, no_wrap=True)
        table.add_column("→", style="dim white", width=1, no_wrap=True)
        table.add_column("Nach", style="green", width=11, no_wrap=True)
        table.add_column("Event", style="white", width=18, no_wrap=True)

        recent_fsm = get_recent_fsm_events(5)

        if not recent_fsm:
            # Don't show "no events" row - keep minimal
            pass
        else:
            for fsm_event in recent_fsm:
                timestamp = fsm_event.get('timestamp', '—')
                symbol = fsm_event.get('symbol', '—')
                from_phase = fsm_event.get('from_phase', '—')
                to_phase = fsm_event.get('to_phase', '—')
                event = fsm_event.get('event', '—')

                # Truncate long event names
                if len(event) > 30:
                    event = event[:27] + "..."

                # Color code transitions
                from_style = "yellow"
                to_style = "green"

                if to_phase in ["IDLE", "COOLDOWN"]:
                    to_style = "dim white"
                elif to_phase in ["POSITION"]:
                    to_style = "bold green"
                elif "PLACE" in to_phase:
                    to_style = "cyan"

                table.add_row(
                    timestamp,
                    symbol,
                    Text(from_phase, style=from_style),
                    "→",
                    Text(to_phase, style=to_style),
                    event
                )

        return Panel(table, title="🔄 FSM Status (Last 5 Transitions)", border_style="magenta")

    except Exception as e:
        return Panel(Text(f"FSM Status Error: {e}", style="red"), title="FSM ERROR", border_style="red")


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
            Layout(name="header", size=6),
            Layout(name="main", ratio=2),  # Takes 2/3 of remaining space
            Layout(name="bottom", ratio=1),  # Takes 1/3 of remaining space
            Layout(size=12, name="debug"),
        )
    else:
        # Standard layout without debug panel
        layout.split(
            Layout(name="header", size=6),
            Layout(name="main", ratio=2),  # Takes 2/3 of remaining space
            Layout(name="bottom", ratio=1),  # Takes 1/3 of remaining space
        )

    layout["main"].split_row(
        Layout(name="side"),
        Layout(name="body")
    )

    # Split bottom area into Event History (left) and FSM Status (right)
    layout["bottom"].split_row(
        Layout(name="events", ratio=1),
        Layout(name="fsm", ratio=1)
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
        with Live(layout, screen=True, redirect_stderr=True, refresh_per_second=1):
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
                    layout["header"].update(make_header_panel(config_data, system_resources, portfolio_data))
                    layout["side"].update(make_drop_panel(drop_data, config_data, engine))
                    layout["body"].update(make_portfolio_panel(portfolio_data))
                    layout["events"].update(make_event_history_panel())
                    layout["fsm"].update(make_fsm_status_panel())

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
