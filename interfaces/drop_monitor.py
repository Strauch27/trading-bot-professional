#!/usr/bin/env python3
"""
Live Drop Monitor - Terminal UI for Market Drops

Displays real-time Top N drops in a rich terminal table:
- Symbol
- Current Price
- Peak/Anchor Price
- Drop % (from peak)
- Distance to Trigger
- Trend Indicator

Color-coded:
- Green: Safe (far from trigger)
- Yellow: Warning (< 1% to trigger)
- Red: Critical (< 0.5% to trigger)
- Blue: Triggered!
"""

import time
import logging
from typing import Dict, List, Optional, Tuple
from datetime import datetime, timezone

try:
    from rich.console import Console
    from rich.table import Table
    from rich.live import Live
    from rich.text import Text
    RICH_AVAILABLE = True
except ImportError:
    RICH_AVAILABLE = False

logger = logging.getLogger(__name__)


class DropMonitor:
    """
    Live terminal monitor showing Top N drops across all symbols.

    Updates continuously and displays:
    - Current price vs peak/anchor
    - Drop percentage
    - Distance to trigger threshold
    - Color-coded status
    """

    def __init__(
        self,
        engine,
        config,
        top_n: int = 10,
        refresh_seconds: float = 5.0
    ):
        """
        Initialize drop monitor.

        Args:
            engine: Trading engine instance (for market data access)
            config: Config module
            top_n: Number of top drops to display
            refresh_seconds: Update interval in seconds
        """
        self.engine = engine
        self.config = config
        self.top_n = min(top_n, 20)  # Cap at 20
        self.refresh_seconds = refresh_seconds
        self.console = Console() if RICH_AVAILABLE else None
        self.running = False

        # Config parameters
        self.drop_trigger_value = getattr(config, 'DROP_TRIGGER_VALUE', 0.985)
        self.drop_trigger_mode = getattr(config, 'DROP_TRIGGER_MODE', 4)
        self.use_drop_anchor = getattr(config, 'USE_DROP_ANCHOR', True)

        logger.info(f"DropMonitor initialized: top_n={top_n}, refresh={refresh_seconds}s")

    def _calculate_drop_data(self) -> List[Dict]:
        """
        Calculate drop data for all symbols.

        Returns:
            List of drop data dicts, sorted by drop % descending
        """
        drops = []

        try:
            # Get topcoins from engine
            topcoins = getattr(self.engine, 'topcoins', {})
            if not topcoins:
                return []

            # Get portfolio for anchors (if applicable)
            portfolio = getattr(self.engine, 'portfolio', None)

            for symbol, price_history in topcoins.items():
                try:
                    # Get current price
                    if not price_history or len(price_history) == 0:
                        continue

                    current_price = price_history[-1] if hasattr(price_history, '__getitem__') else None
                    if not current_price or current_price <= 0:
                        continue

                    # Determine peak/anchor price based on mode
                    peak_price = None

                    if self.use_drop_anchor and portfolio:
                        # Mode 4: Use persistent anchor
                        anchor_data = portfolio.get_drop_anchor(symbol)
                        if anchor_data:
                            peak_price = anchor_data.get('peak_price')

                    # Fallback: Use recent peak from price history
                    if not peak_price and hasattr(price_history, '__iter__'):
                        try:
                            history_list = list(price_history)
                            if history_list:
                                peak_price = max(history_list)
                        except Exception:
                            peak_price = current_price

                    if not peak_price or peak_price <= 0:
                        peak_price = current_price

                    # Calculate drop percentage
                    drop_ratio = current_price / peak_price
                    drop_pct = (drop_ratio - 1.0) * 100  # Negative for drops

                    # Calculate distance to trigger
                    trigger_pct = (self.drop_trigger_value - 1.0) * 100
                    distance_to_trigger = drop_pct - trigger_pct

                    # Determine status
                    if drop_ratio <= self.drop_trigger_value:
                        status = "TRIGGERED"
                    elif distance_to_trigger < 0.5:
                        status = "CRITICAL"
                    elif distance_to_trigger < 1.0:
                        status = "WARNING"
                    else:
                        status = "SAFE"

                    drops.append({
                        'symbol': symbol,
                        'current_price': current_price,
                        'peak_price': peak_price,
                        'drop_pct': drop_pct,
                        'drop_ratio': drop_ratio,
                        'trigger_pct': trigger_pct,
                        'distance_to_trigger': distance_to_trigger,
                        'status': status
                    })

                except Exception as e:
                    logger.debug(f"Error calculating drop for {symbol}: {e}")
                    continue

            # Sort by drop % (most negative first)
            drops.sort(key=lambda x: x['drop_pct'])

            return drops[:self.top_n]

        except Exception as e:
            logger.warning(f"Error calculating drop data: {e}")
            return []

    def _create_table(self, drop_data: List[Dict]) -> Table:
        """
        Create Rich table from drop data.

        Args:
            drop_data: List of drop data dicts

        Returns:
            Rich Table object
        """
        table = Table(
            title=f"[bold cyan]Top {self.top_n} Market Drops[/bold cyan]",
            title_style="bold cyan",
            show_header=True,
            header_style="bold white on blue",
            border_style="blue",
            expand=False
        )

        # Add columns
        table.add_column("Rank", justify="right", style="dim", width=4)
        table.add_column("Symbol", style="bold", width=12)
        table.add_column("Current", justify="right", width=10)
        table.add_column("Peak", justify="right", width=10)
        table.add_column("Drop %", justify="right", width=9)
        table.add_column("Trigger", justify="right", width=9)
        table.add_column("Distance", justify="right", width=10)
        table.add_column("Status", justify="center", width=10)

        # Add rows
        for i, drop in enumerate(drop_data, start=1):
            symbol = drop['symbol']
            current = f"{drop['current_price']:.6f}"
            peak = f"{drop['peak_price']:.6f}"
            drop_pct = drop['drop_pct']
            trigger_pct = drop['trigger_pct']
            distance = drop['distance_to_trigger']
            status = drop['status']

            # Color-code based on status
            if status == "TRIGGERED":
                drop_style = "bold blue"
                distance_text = Text("TRIGGERED!", style="bold blue")
                status_text = Text("🔵 TRIG", style="bold blue")
            elif status == "CRITICAL":
                drop_style = "bold red"
                distance_text = Text(f"{distance:.2f}%", style="bold red")
                status_text = Text("🔴 CRIT", style="bold red")
            elif status == "WARNING":
                drop_style = "bold yellow"
                distance_text = Text(f"{distance:.2f}%", style="bold yellow")
                status_text = Text("🟡 WARN", style="bold yellow")
            else:
                drop_style = "green"
                distance_text = Text(f"{distance:.2f}%", style="green")
                status_text = Text("🟢 SAFE", style="green")

            table.add_row(
                f"{i}",
                symbol,
                current,
                peak,
                f"[{drop_style}]{drop_pct:.2f}%[/{drop_style}]",
                f"{trigger_pct:.2f}%",
                distance_text,
                status_text
            )

        # Add footer with timestamp
        timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
        table.caption = f"Last update: {timestamp} | Refresh: {self.refresh_seconds}s | Trigger: {self.drop_trigger_value:.3f}"

        return table

    def _generate_table_content(self):
        """
        Generate table content for Live display.

        Yields:
            Rich Table objects
        """
        while self.running:
            try:
                # Calculate drop data
                drop_data = self._calculate_drop_data()

                # Create table
                if drop_data:
                    table = self._create_table(drop_data)
                    yield table
                else:
                    # No data available
                    empty_table = Table(title="[yellow]No drop data available[/yellow]")
                    empty_table.add_column("Status")
                    empty_table.add_row("Waiting for market data...")
                    yield empty_table

                # Sleep until next refresh
                time.sleep(self.refresh_seconds)

            except Exception as e:
                logger.error(f"Error generating table content: {e}", exc_info=True)
                error_table = Table(title="[red]Error[/red]")
                error_table.add_column("Status")
                error_table.add_row(f"Error: {str(e)}")
                yield error_table
                time.sleep(self.refresh_seconds)

    def start(self):
        """
        Start live drop monitor (blocking).

        This will take over the terminal with a live-updating table.
        Call this in a background thread to avoid blocking main execution.
        """
        if not RICH_AVAILABLE:
            logger.warning("Rich library not available - drop monitor disabled")
            return

        if not self.console:
            logger.warning("Console not initialized - drop monitor disabled")
            return

        self.running = True

        try:
            logger.info("Starting live drop monitor...")

            with Live(
                self._generate_table_content(),
                console=self.console,
                refresh_per_second=1.0 / self.refresh_seconds,
                screen=False,  # Don't take over entire screen
                auto_refresh=True
            ) as live:
                while self.running:
                    time.sleep(1)

        except KeyboardInterrupt:
            logger.info("Drop monitor interrupted by user")
        except Exception as e:
            logger.error(f"Drop monitor error: {e}", exc_info=True)
        finally:
            self.running = False
            logger.info("Drop monitor stopped")

    def stop(self):
        """Stop drop monitor."""
        self.running = False
        logger.info("Drop monitor stop requested")


def run_live_drop_monitor(
    engine,
    config,
    top_n: int = 10,
    refresh_seconds: float = 5.0
):
    """
    Run live drop monitor (blocking).

    Args:
        engine: Trading engine instance
        config: Config module
        top_n: Number of top drops to display
        refresh_seconds: Update interval in seconds
    """
    if not RICH_AVAILABLE:
        logger.warning("Rich library not available - cannot start drop monitor")
        return

    monitor = DropMonitor(
        engine=engine,
        config=config,
        top_n=top_n,
        refresh_seconds=refresh_seconds
    )

    try:
        monitor.start()
    except KeyboardInterrupt:
        monitor.stop()
    except Exception as e:
        logger.error(f"Failed to run drop monitor: {e}", exc_info=True)
        monitor.stop()
