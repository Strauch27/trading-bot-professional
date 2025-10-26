"""
Rich Terminal Status Table

Live-updating terminal UI showing FSM state for all symbols.
"""

import logging
import time
from typing import Callable, Dict

from rich.console import Console
from rich.live import Live
from rich.table import Table

from core.fsm.phases import Phase
from core.fsm.state import CoinState

logger = logging.getLogger(__name__)


def render_status_table(states: Dict[str, CoinState], show_idle: bool = False) -> Table:
    """
    Render FSM status as Rich Table.

    Args:
        states: Dict mapping symbol -> CoinState
        show_idle: If True, show idle symbols; if False, hide them

    Returns:
        Rich Table instance
    """
    table = Table(
        title="🤖 Trading Bot - FSM Status",
        title_justify="left",
        show_header=True,
        header_style="bold magenta"
    )

    # Columns
    table.add_column("Symbol", style="cyan", width=12, no_wrap=True)
    table.add_column("Phase", style="yellow", width=18, no_wrap=True)
    table.add_column("Dec ID", style="dim", width=10, no_wrap=True)
    table.add_column("Age", style="green", width=8, justify="right")
    table.add_column("Price", style="blue", width=12, justify="right")
    table.add_column("Amount", style="white", width=12, justify="right")
    table.add_column("PnL", style="white", width=10, justify="right")
    table.add_column("Note", style="dim", width=40)

    # Phase priority for sorting (most important first)
    phase_priority = {
        Phase.ERROR: 0,
        Phase.WAIT_FILL: 1,
        Phase.WAIT_SELL_FILL: 2,
        Phase.PLACE_BUY: 3,
        Phase.PLACE_SELL: 4,
        Phase.POSITION: 5,
        Phase.EXIT_EVAL: 6,
        Phase.ENTRY_EVAL: 7,
        Phase.POST_TRADE: 8,
        Phase.COOLDOWN: 9,
        Phase.IDLE: 10,
        Phase.WARMUP: 11,
    }

    # Filter states
    filtered_states = []
    for st in states.values():
        if not show_idle and st.phase in [Phase.IDLE, Phase.WARMUP]:
            continue
        filtered_states.append(st)

    # Sort by priority, then by symbol
    sorted_states = sorted(
        filtered_states,
        key=lambda s: (phase_priority.get(s.phase, 99), s.symbol)
    )

    # Add rows
    time.time()
    for st in sorted_states:
        # Age
        age_s = int(st.age_seconds())
        age_str = format_age(age_s)

        # Phase with color coding
        phase_str = format_phase(st.phase, age_s)

        # Decision ID (last 8 chars)
        dec_str = st.decision_id[-8:] if st.decision_id else "-"

        # Price
        if st.current_price > 0:
            price_str = f"{st.current_price:.6f}"
        else:
            price_str = "-"

        # Amount
        if st.amount > 0:
            amount_str = f"{st.amount:.6f}"
        else:
            amount_str = "-"

        # PnL
        if st.has_position():
            pnl = st.unrealized_pnl()
            pnl_str = format_pnl(pnl)
        else:
            pnl_str = "-"

        # Note (truncate)
        note_str = truncate_string(st.note, 38)

        # Add row
        table.add_row(
            st.symbol,
            phase_str,
            dec_str,
            age_str,
            price_str,
            amount_str,
            pnl_str,
            note_str
        )

    # Footer with summary
    active_count = sum(1 for st in states.values() if st.phase not in [Phase.IDLE, Phase.WARMUP, Phase.COOLDOWN])
    position_count = sum(1 for st in states.values() if st.has_position())
    error_count = sum(1 for st in states.values() if st.phase == Phase.ERROR)

    total_pnl = sum(st.unrealized_pnl() for st in states.values() if st.has_position())
    pnl_str = format_pnl(total_pnl) if position_count > 0 else "0.00"

    footer = f"Active: {active_count} | Positions: {position_count} | Errors: {error_count} | Total PnL: {pnl_str}"
    table.caption = footer

    return table


def format_phase(phase: Phase, age_seconds: int) -> str:
    """
    Format phase with color coding and alerts.

    Args:
        phase: Current phase
        age_seconds: Time in current phase

    Returns:
        Formatted phase string with Rich markup
    """
    # Base phase name
    phase_str = phase.value

    # Color coding by phase type
    if phase == Phase.ERROR:
        return f"[bold red]{phase_str}[/] ⚠️"
    elif phase in [Phase.WAIT_FILL, Phase.WAIT_SELL_FILL]:
        # Alert if waiting too long
        if age_seconds > 30:
            return f"[bold yellow]{phase_str}[/] ⏰"
        else:
            return f"[bold yellow]{phase_str}[/]"
    elif phase == Phase.POSITION:
        return f"[bold green]{phase_str}[/] 💎"
    elif phase in [Phase.PLACE_BUY, Phase.PLACE_SELL]:
        return f"[bold cyan]{phase_str}[/] 📤"
    elif phase in [Phase.ENTRY_EVAL, Phase.EXIT_EVAL]:
        return f"[yellow]{phase_str}[/] 🔍"
    elif phase == Phase.COOLDOWN:
        return f"[dim]{phase_str}[/] ❄️"
    elif phase == Phase.POST_TRADE:
        return f"[green]{phase_str}[/] ✅"
    else:
        return f"[dim]{phase_str}[/]"


def format_pnl(pnl: float) -> str:
    """
    Format PnL with color.

    Args:
        pnl: PnL value

    Returns:
        Formatted string with Rich markup
    """
    if pnl > 0:
        return f"[green]+{pnl:.2f}[/]"
    elif pnl < 0:
        return f"[red]{pnl:.2f}[/]"
    else:
        return "[dim]0.00[/]"


def format_age(seconds: int) -> str:
    """
    Format age in human-readable form.

    Args:
        seconds: Age in seconds

    Returns:
        Formatted string (e.g. "45s", "3m", "2h")
    """
    if seconds < 60:
        return f"{seconds}s"
    elif seconds < 3600:
        minutes = seconds // 60
        return f"{minutes}m"
    elif seconds < 86400:
        hours = seconds // 3600
        return f"{hours}h"
    else:
        days = seconds // 86400
        return f"{days}d"


def truncate_string(s: str, max_len: int) -> str:
    """
    Truncate string with ellipsis.

    Args:
        s: String to truncate
        max_len: Maximum length

    Returns:
        Truncated string
    """
    if len(s) <= max_len:
        return s
    return s[:max_len - 3] + "..."


def run_live_table(
    get_states_func: Callable[[], Dict[str, CoinState]],
    refresh_hz: float = 2.0,
    show_idle: bool = False
):
    """
    Run live-updating terminal table.

    This function blocks and updates the table continuously.
    Press Ctrl+C to stop.

    Args:
        get_states_func: Function that returns current states dict
        refresh_hz: Refresh rate in Hz (default: 2.0 = twice per second)
        show_idle: If True, show idle symbols
    """
    console = Console()

    logger.info(f"Starting live status table (refresh: {refresh_hz} Hz)")

    try:
        with Live(
            render_status_table(get_states_func(), show_idle=show_idle),
            console=console,
            refresh_per_second=refresh_hz,
            screen=False,  # Don't use alternate screen (allows scrollback)
            redirect_stderr=True
        ) as live:
            while True:
                try:
                    states = get_states_func()
                    live.update(render_status_table(states, show_idle=show_idle))
                    time.sleep(1 / refresh_hz)
                except KeyboardInterrupt:
                    break
                except Exception as e:
                    logger.error(f"Error updating status table: {e}")
                    time.sleep(1.0)

    except KeyboardInterrupt:
        logger.info("Live status table stopped by user")
    except Exception as e:
        logger.error(f"Fatal error in live status table: {e}")
        raise

    finally:
        console.print("\n[bold green]Status table stopped[/]")


def render_phase_distribution(states: Dict[str, CoinState]) -> Table:
    """
    Render phase distribution summary.

    Args:
        states: Dict mapping symbol -> CoinState

    Returns:
        Rich Table showing phase distribution
    """
    table = Table(
        title="📊 Phase Distribution",
        show_header=True,
        header_style="bold cyan"
    )

    table.add_column("Phase", style="yellow", width=20)
    table.add_column("Count", style="green", justify="right", width=8)
    table.add_column("Percentage", style="blue", justify="right", width=10)

    # Count phases
    phase_counts = {}
    total = len(states)

    for st in states.values():
        phase_name = st.phase.value
        phase_counts[phase_name] = phase_counts.get(phase_name, 0) + 1

    # Sort by count (descending)
    sorted_phases = sorted(phase_counts.items(), key=lambda x: x[1], reverse=True)

    # Add rows
    for phase_name, count in sorted_phases:
        percentage = (count / total * 100) if total > 0 else 0
        table.add_row(
            phase_name,
            str(count),
            f"{percentage:.1f}%"
        )

    return table


def render_stuck_symbols(states: Dict[str, CoinState], threshold_seconds: float = 60.0) -> Table:
    """
    Render table of stuck symbols (in phase too long).

    Args:
        states: Dict mapping symbol -> CoinState
        threshold_seconds: Threshold for "stuck"

    Returns:
        Rich Table showing stuck symbols
    """
    table = Table(
        title=f"⏰ Stuck Symbols (>{threshold_seconds:.0f}s)",
        show_header=True,
        header_style="bold red"
    )

    table.add_column("Symbol", style="cyan", width=12)
    table.add_column("Phase", style="yellow", width=18)
    table.add_column("Stuck Time", style="red", width=12, justify="right")
    table.add_column("Note", style="dim", width=40)

    # Find stuck symbols
    stuck = []
    for st in states.values():
        if st.phase in [Phase.IDLE, Phase.WARMUP, Phase.COOLDOWN]:
            continue  # These phases can be long

        age = st.age_seconds()
        if age > threshold_seconds:
            stuck.append(st)

    # Sort by age (longest first)
    stuck.sort(key=lambda s: s.age_seconds(), reverse=True)

    # Add rows
    for st in stuck:
        age_str = format_age(int(st.age_seconds()))
        phase_str = st.phase.value
        note_str = truncate_string(st.note, 38)

        table.add_row(
            st.symbol,
            phase_str,
            age_str,
            note_str
        )

    if not stuck:
        table.add_row("-", "-", "-", "No stuck symbols")

    return table


def print_status_summary(states: Dict[str, CoinState]):
    """
    Print one-line status summary (for heartbeat logging).

    Args:
        states: Dict mapping symbol -> CoinState
    """
    active = sum(1 for st in states.values() if st.phase not in [Phase.IDLE, Phase.WARMUP, Phase.COOLDOWN])
    positions = sum(1 for st in states.values() if st.has_position())
    errors = sum(1 for st in states.values() if st.phase == Phase.ERROR)
    total_pnl = sum(st.unrealized_pnl() for st in states.values() if st.has_position())

    summary = f"FSM: {len(states)} symbols | Active: {active} | Positions: {positions} | Errors: {errors} | PnL: {total_pnl:.2f}"
    print(summary)
