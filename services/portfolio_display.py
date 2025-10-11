#!/usr/bin/env python3
"""
Portfolio Display - Terminal UI for Position Overview

Displays a clean, formatted table showing:
- Active positions with PnL
- Portfolio summary
- Budget and equity status
"""

import time
import logging
from typing import Dict, Any, Optional
from datetime import datetime, timezone

logger = logging.getLogger(__name__)


def format_duration(seconds: float) -> str:
    """Format duration in seconds to human-readable string"""
    if seconds < 60:
        return f"{int(seconds)}s"
    elif seconds < 3600:
        mins = int(seconds / 60)
        secs = int(seconds % 60)
        return f"{mins}m {secs}s"
    else:
        hours = int(seconds / 3600)
        mins = int((seconds % 3600) / 60)
        return f"{hours}h {mins}m"


def format_pnl_color(pnl: float) -> str:
    """Add color coding to PnL values (green/red)"""
    if pnl > 0:
        return f"\033[92m+{pnl:.2f}\033[0m"  # Green
    elif pnl < 0:
        return f"\033[91m{pnl:.2f}\033[0m"   # Red
    else:
        return f"{pnl:.2f}"


def format_pnl_pct_color(pnl_pct: float) -> str:
    """Add color coding to PnL percentage"""
    if pnl_pct > 0:
        return f"\033[92m+{pnl_pct:.2f}%\033[0m"  # Green
    elif pnl_pct < 0:
        return f"\033[91m{pnl_pct:.2f}%\033[0m"   # Red
    else:
        return f"{pnl_pct:.2f}%"


def display_portfolio(
    positions: Dict[str, Dict],
    portfolio_manager,
    market_data_provider,
    pnl_tracker,
    max_positions: int = 10
):
    """
    Display formatted portfolio overview in terminal

    Args:
        positions: Dict of active positions {symbol: position_data}
        portfolio_manager: Portfolio manager instance
        market_data_provider: Market data provider for current prices
        pnl_tracker: PnL tracker instance
        max_positions: Maximum allowed positions
    """
    try:
        now = time.time()

        # Get current budget and PnL
        usdt_balance = portfolio_manager.get_balance("USDT")
        pnl_summary = pnl_tracker.get_total_pnl()

        realized_pnl = pnl_summary.get("realized", 0.0)
        unrealized_pnl = pnl_summary.get("unrealized", 0.0)
        total_fees = pnl_summary.get("fees", 0.0)
        net_pnl = pnl_summary.get("net_after_fees", 0.0)

        equity = usdt_balance + unrealized_pnl

        # Build header
        header_line = "═" * 80
        title = f"{'PORTFOLIO':^80}"

        status_line = (f"Positions: {len(positions)}/{max_positions} | "
                      f"Budget: {usdt_balance:.2f} USDT | "
                      f"Equity: {equity:.2f} USDT")

        print(f"\n{header_line}")
        print(title)
        print(header_line)
        print(status_line)
        print()

        # If no positions, show simple message
        if not positions:
            print(f"{'No active positions':^80}")
            print(f"\n{header_line}\n")
            return

        # Table header
        print(f"{'Symbol':<12} {'Entry':>10} {'Current':>10} {'PnL':>10} {'PnL%':>10} {'Age':>10}")
        print("─" * 80)

        # Position rows
        total_pnl_abs = 0.0
        total_cost = 0.0

        for symbol, pos_data in sorted(positions.items()):
            entry_price = pos_data.get('buying_price', 0.0)
            amount = pos_data.get('amount', 0.0)
            entry_time = pos_data.get('time', now)

            # Get current price
            current_price = market_data_provider.get_price(symbol)
            if not current_price:
                current_price = entry_price

            # Calculate PnL
            cost = entry_price * amount
            current_value = current_price * amount
            pnl_abs = current_value - cost
            pnl_pct = ((current_price / entry_price) - 1.0) * 100.0 if entry_price > 0 else 0.0

            total_pnl_abs += pnl_abs
            total_cost += cost

            # Format age
            age_seconds = now - entry_time
            age_str = format_duration(age_seconds)

            # Format with color
            pnl_str = format_pnl_color(pnl_abs)
            pnl_pct_str = format_pnl_pct_color(pnl_pct)

            # Print row
            print(f"{symbol:<12} {entry_price:>10.6f} {current_price:>10.6f} "
                  f"{pnl_str:>18} {pnl_pct_str:>18} {age_str:>10}")

        # Separator
        print("─" * 80)

        # Total row
        total_pnl_pct = (total_pnl_abs / total_cost * 100.0) if total_cost > 0 else 0.0
        total_pnl_str = format_pnl_color(total_pnl_abs)
        total_pnl_pct_str = format_pnl_pct_color(total_pnl_pct)

        print(f"{'TOTAL':<12} {'':<10} {'':<10} "
              f"{total_pnl_str:>18} {total_pnl_pct_str:>18}")

        print()

        # Summary line
        realized_str = format_pnl_color(realized_pnl)
        unrealized_str = format_pnl_color(unrealized_pnl)
        fees_str = format_pnl_color(-total_fees)
        net_str = format_pnl_color(net_pnl)

        summary = (f"Unrealized: {unrealized_str} | "
                  f"Realized: {realized_str} | "
                  f"Fees: {fees_str} | "
                  f"Net: {net_str}")

        print(summary)
        print(f"{header_line}\n")

    except Exception as e:
        logger.error(f"Portfolio display error: {e}", exc_info=True)
        print(f"\n⚠️  Portfolio display error: {e}\n")


def display_compact_portfolio(
    positions: Dict[str, Dict],
    portfolio_manager,
    pnl_tracker,
    max_positions: int = 10
):
    """
    Display compact one-line portfolio status

    Args:
        positions: Dict of active positions
        portfolio_manager: Portfolio manager instance
        pnl_tracker: PnL tracker instance
        max_positions: Maximum allowed positions
    """
    try:
        usdt_balance = portfolio_manager.get_balance("USDT")
        pnl_summary = pnl_tracker.get_total_pnl()

        unrealized_pnl = pnl_summary.get("unrealized", 0.0)
        net_pnl = pnl_summary.get("net_after_fees", 0.0)

        equity = usdt_balance + unrealized_pnl

        # Format PnL with color
        net_str = format_pnl_color(net_pnl)
        unrealized_str = format_pnl_color(unrealized_pnl)

        status = (f"💼 Portfolio: {len(positions)}/{max_positions} pos | "
                 f"Budget: {usdt_balance:.2f} USDT | "
                 f"Equity: {equity:.2f} USDT | "
                 f"Unrealized: {unrealized_str} | "
                 f"Net: {net_str}")

        print(status)

    except Exception as e:
        logger.error(f"Compact portfolio display error: {e}")
