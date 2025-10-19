#!/usr/bin/env python3
"""
Intent Tracking Dashboard Widget

Displays active intents with age, status, and metrics for monitoring.
Modular design - returns formatted string for display in main dashboard.
"""

import time
from typing import Dict, Any, List, Optional
import config


def render_intent_panel(pending_intents: Dict[str, Dict[str, Any]], rolling_stats=None) -> str:
    """
    Render intent tracking panel showing active intents and metrics.

    Args:
        pending_intents: Dict of intent_id -> metadata
        rolling_stats: Optional RollingStats instance for latency metrics

    Returns:
        Formatted string for dashboard display
    """
    if not pending_intents:
        return "📊 Intent Tracking | ✅ No pending intents"

    lines = [
        "=" * 80,
        f"📊 INTENT TRACKING | {len(pending_intents)} pending",
        "=" * 80,
    ]

    # Sort by age (oldest first)
    sorted_intents = sorted(
        pending_intents.items(),
        key=lambda x: x[1].get("start_ts", time.time()),
        reverse=False
    )

    # Display each intent
    for intent_id, metadata in sorted_intents:
        symbol = metadata.get("symbol", "UNKNOWN")
        signal = metadata.get("signal", "UNKNOWN")
        start_ts = metadata.get("start_ts", time.time())
        age_s = time.time() - start_ts
        quote_budget = metadata.get("quote_budget", 0.0)

        # Status indicator based on age
        status = _get_status_indicator(age_s)

        # Format line
        intent_line = (
            f"{status} {symbol:12s} | {signal:20s} | "
            f"${quote_budget:>7.2f} | Age: {_format_age(age_s):>8s} | "
            f"{intent_id[:12]}..."
        )
        lines.append(intent_line)

    # Add latency metrics if available
    if rolling_stats and hasattr(rolling_stats, 'avg_latency'):
        lines.append("-" * 80)
        lines.append("Latency Metrics (last 5 min):")

        # Intent-to-fill latency
        avg_latency = rolling_stats.avg_latency("intent_to_fill", seconds=300)
        p95_latency = rolling_stats.p95_latency("intent_to_fill", seconds=300) if hasattr(rolling_stats, 'p95_latency') else 0.0

        if avg_latency > 0:
            lines.append(f"  Intent→Fill: avg={avg_latency:.1f}ms, p95={p95_latency:.1f}ms")
        else:
            lines.append("  Intent→Fill: No data")

    lines.append("=" * 80)

    return "\n".join(lines)


def _get_status_indicator(age_s: float) -> str:
    """
    Get status indicator emoji based on intent age.

    Args:
        age_s: Age in seconds

    Returns:
        Status emoji
    """
    stale_threshold = getattr(config, 'INTENT_STALE_THRESHOLD_S', 60)

    if age_s > stale_threshold:
        return "🔴 STALE"
    elif age_s > stale_threshold * 0.5:
        return "🟡 PENDING"
    else:
        return "🟢 FRESH"


def _format_age(age_s: float) -> str:
    """
    Format age in human-readable format.

    Args:
        age_s: Age in seconds

    Returns:
        Formatted age string (e.g., "45.2s", "1.2m", "2.5h")
    """
    if age_s < 60:
        return f"{age_s:.1f}s"
    elif age_s < 3600:
        return f"{age_s / 60:.1f}m"
    else:
        return f"{age_s / 3600:.1f}h"


def render_latency_summary(rolling_stats) -> str:
    """
    Render latency summary panel.

    Args:
        rolling_stats: RollingStats instance

    Returns:
        Formatted latency summary
    """
    if not rolling_stats or not hasattr(rolling_stats, 'latencies'):
        return "📈 Latency Metrics | No data available"

    lines = [
        "=" * 80,
        "📈 LATENCY METRICS (Last 5 Minutes)",
        "=" * 80,
    ]

    # Get all tracked metrics
    if not rolling_stats.latencies:
        lines.append("No latency data collected yet")
    else:
        for metric_name in sorted(rolling_stats.latencies.keys()):
            avg = rolling_stats.avg_latency(metric_name, seconds=300)
            p95 = rolling_stats.p95_latency(metric_name, seconds=300) if hasattr(rolling_stats, 'p95_latency') else 0.0

            # Get count
            count = len([ts for ts, _ in rolling_stats.latencies[metric_name] if time.time() - ts <= 300])

            lines.append(
                f"  {metric_name:25s} | avg: {avg:>7.1f}ms | p95: {p95:>7.1f}ms | count: {count:>4d}"
            )

    lines.append("=" * 80)

    return "\n".join(lines)


def render_compact_intent_status(pending_intents: Dict[str, Dict[str, Any]]) -> str:
    """
    Render compact single-line intent status for main dashboard.

    Args:
        pending_intents: Dict of intent_id -> metadata

    Returns:
        Compact status line
    """
    if not pending_intents:
        return "Intents: ✅ None"

    count = len(pending_intents)

    # Count by status
    fresh = sum(1 for m in pending_intents.values() if (time.time() - m.get("start_ts", time.time())) < 30)
    pending = sum(1 for m in pending_intents.values() if 30 <= (time.time() - m.get("start_ts", time.time())) < 60)
    stale = count - fresh - pending

    status_parts = []
    if fresh > 0:
        status_parts.append(f"🟢{fresh}")
    if pending > 0:
        status_parts.append(f"🟡{pending}")
    if stale > 0:
        status_parts.append(f"🔴{stale}")

    return f"Intents: {' '.join(status_parts)} ({count} total)"


def get_intent_statistics(pending_intents: Dict[str, Dict[str, Any]]) -> Dict[str, Any]:
    """
    Get statistical summary of pending intents.

    Args:
        pending_intents: Dict of intent_id -> metadata

    Returns:
        Dict with statistics (count, avg_age, oldest_age, etc.)
    """
    if not pending_intents:
        return {
            "count": 0,
            "avg_age_s": 0.0,
            "oldest_age_s": 0.0,
            "total_budget_reserved": 0.0,
            "fresh_count": 0,
            "pending_count": 0,
            "stale_count": 0
        }

    now = time.time()
    ages = [now - m.get("start_ts", now) for m in pending_intents.values()]
    budgets = [m.get("quote_budget", 0.0) for m in pending_intents.values()]

    # Categorize by status
    fresh = sum(1 for age in ages if age < 30)
    pending = sum(1 for age in ages if 30 <= age < 60)
    stale = len(ages) - fresh - pending

    return {
        "count": len(pending_intents),
        "avg_age_s": sum(ages) / len(ages) if ages else 0.0,
        "oldest_age_s": max(ages) if ages else 0.0,
        "total_budget_reserved": sum(budgets),
        "fresh_count": fresh,
        "pending_count": pending,
        "stale_count": stale
    }


# Example usage in main dashboard:
"""
from ui.intent_dashboard import render_intent_panel, render_compact_intent_status

# In main dashboard loop:
while running:
    # ... other panels

    # Full panel
    intent_panel = render_intent_panel(
        engine.pending_buy_intents,
        engine.rolling_stats
    )
    print(intent_panel)

    # OR compact status line
    intent_status = render_compact_intent_status(engine.pending_buy_intents)
    print(f"Status: {intent_status}")

    time.sleep(1)
"""
