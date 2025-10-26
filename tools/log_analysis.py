#!/usr/bin/env python3
"""
Log Analysis Tools for Intent Flow Debugging

Provides helpers to trace intent lifecycle through JSONL logs.

Usage:
    python tools/log_analysis.py <intent_id>
    python tools/log_analysis.py --recent 10
    python tools/log_analysis.py --latency-report
"""

import json
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

import config

# Centralized path constants
LOG_PATHS = {
    "session_dir": Path(config.SESSION_DIR) if hasattr(config, 'SESSION_DIR') else Path("sessions"),
    "log_dir": Path(config.LOG_DIR) if hasattr(config, 'LOG_DIR') else Path("logs"),
    "state_dir": Path(config.STATE_DIR) if hasattr(config, 'STATE_DIR') else Path("state"),
}


def get_latest_session_logs() -> Path:
    """
    Get path to latest session logs directory.

    Returns:
        Path to latest session's log directory
    """
    session_dir = LOG_PATHS["session_dir"]

    if not session_dir.exists():
        return LOG_PATHS["log_dir"]  # Fallback to default

    # Find latest session
    sessions = sorted(session_dir.glob("session_*"), reverse=True)
    if sessions:
        latest = sessions[0] / "logs"
        if latest.exists():
            return latest

    return LOG_PATHS["log_dir"]


def parse_jsonl_logs(log_dir: Path, pattern: str = "**/*.jsonl") -> List[Dict[str, Any]]:
    """
    Parse all JSONL log files in directory.

    Args:
        log_dir: Directory to search
        pattern: Glob pattern for log files

    Returns:
        List of parsed log events
    """
    events = []

    for log_file in log_dir.glob(pattern):
        try:
            with open(log_file, 'r', encoding='utf-8') as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        event = json.loads(line)
                        events.append(event)
                    except json.JSONDecodeError as e:
                        print(f"Warning: Failed to parse line in {log_file}: {e}", file=sys.stderr)
        except Exception as e:
            print(f"Warning: Failed to read {log_file}: {e}", file=sys.stderr)

    return events


def trace_intent_flow(intent_id: str, log_dir: Optional[Path] = None) -> List[Dict[str, Any]]:
    """
    Trace complete flow for a specific intent ID.

    Args:
        intent_id: Intent ID to trace
        log_dir: Optional log directory (defaults to latest session)

    Returns:
        List of events related to intent, sorted by timestamp
    """
    if log_dir is None:
        log_dir = get_latest_session_logs()

    print(f"Searching for intent_id={intent_id} in {log_dir}")

    # Parse all logs
    all_events = parse_jsonl_logs(log_dir)

    # Filter by intent_id
    intent_events = [
        event for event in all_events
        if event.get("intent_id") == intent_id
    ]

    # Sort by timestamp
    intent_events.sort(key=lambda e: e.get("timestamp", e.get("ts", 0)))

    return intent_events


def print_intent_flow(events: List[Dict[str, Any]]):
    """
    Pretty-print intent flow events.

    Args:
        events: List of events to display
    """
    if not events:
        print("No events found for this intent_id")
        return

    print(f"\n{'='*80}")
    print(f"Intent Flow: {events[0].get('intent_id', 'UNKNOWN')}")
    print(f"{'='*80}\n")

    for i, event in enumerate(events, 1):
        # Extract event details
        event_type = event.get("event_type") or event.get("event_name") or event.get("state") or "UNKNOWN"
        timestamp = event.get("timestamp") or event.get("ts", 0)
        dt = datetime.fromtimestamp(timestamp) if timestamp else None
        symbol = event.get("symbol", "N/A")

        # Format timestamp
        time_str = dt.strftime('%H:%M:%S.%f')[:-3] if dt else "N/A"

        # Print event
        print(f"{i:2d}. [{time_str}] {event_type:25s} | {symbol}")

        # Print relevant details based on event type
        if event_type == "order_intent":
            qty = event.get("qty", 0)
            limit_price = event.get("limit_price", 0)
            print(f"    → qty={qty:.8f}, limit_price={limit_price:.8f}")

        elif event_type in ["SENT", "order_sent"]:
            order_id = event.get("order_id", "N/A")
            print(f"    → order_id={order_id}")

        elif event_type in ["FILLED", "order_filled"]:
            filled_qty = event.get("filled_qty", 0)
            avg_price = event.get("average_price") or event.get("avg_price", 0)
            print(f"    → filled_qty={filled_qty:.8f}, avg_price={avg_price:.8f}")

        elif event_type == "order_done":
            final_status = event.get("final_status", "N/A")
            latency_total = event.get("latency_ms_total", 0)
            print(f"    → status={final_status}, latency={latency_total}ms")

            # Show latency breakdown if available
            breakdown = event.get("latency_breakdown")
            if breakdown:
                print(f"    → breakdown: placement={breakdown.get('placement_ms', 0):.1f}ms, "
                      f"exchange={breakdown.get('exchange_fill_ms', 0):.1f}ms, "
                      f"reconcile={breakdown.get('reconcile_ms', 0):.1f}ms")

    print(f"\n{'='*80}")
    print(f"Total Events: {len(events)}")
    print(f"{'='*80}\n")


def list_recent_intents(count: int = 10, log_dir: Optional[Path] = None) -> List[Dict[str, Any]]:
    """
    List recent intents from logs.

    Args:
        count: Number of recent intents to show
        log_dir: Optional log directory

    Returns:
        List of recent intent metadata
    """
    if log_dir is None:
        log_dir = get_latest_session_logs()

    # Parse logs
    all_events = parse_jsonl_logs(log_dir)

    # Find order_intent events
    intent_events = [
        event for event in all_events
        if event.get("event_type") == "order_intent" or event.get("event_name") == "order_intent"
    ]

    # Sort by timestamp (newest first)
    intent_events.sort(key=lambda e: e.get("timestamp", e.get("ts", 0)), reverse=True)

    # Return top N
    return intent_events[:count]


def print_recent_intents(intents: List[Dict[str, Any]]):
    """
    Pretty-print recent intents.

    Args:
        intents: List of intent events
    """
    if not intents:
        print("No recent intents found")
        return

    print(f"\n{'='*80}")
    print(f"Recent Intents ({len(intents)})")
    print(f"{'='*80}\n")

    print(f"{'#':>3} | {'Timestamp':19} | {'Symbol':12} | {'Side':4} | {'Intent ID':20}")
    print("-" * 80)

    for i, intent in enumerate(intents, 1):
        timestamp = intent.get("timestamp") or intent.get("ts", 0)
        dt = datetime.fromtimestamp(timestamp) if timestamp else None
        time_str = dt.strftime('%Y-%m-%d %H:%M:%S') if dt else "N/A"

        symbol = intent.get("symbol", "N/A")
        side = intent.get("side", "N/A")
        intent_id = intent.get("intent_id", "N/A")[:20]

        print(f"{i:3d} | {time_str} | {symbol:12s} | {side:4s} | {intent_id}")

    print(f"{'='*80}\n")


def generate_latency_report(log_dir: Optional[Path] = None) -> Dict[str, Any]:
    """
    Generate latency report from intent logs.

    Args:
        log_dir: Optional log directory

    Returns:
        Dict with latency statistics
    """
    if log_dir is None:
        log_dir = get_latest_session_logs()

    # Parse logs
    all_events = parse_jsonl_logs(log_dir)

    # Find INTENT_LATENCY events
    latency_events = [
        event for event in all_events
        if event.get("event_type") == "INTENT_LATENCY"
    ]

    if not latency_events:
        return {"count": 0, "avg_latency_ms": 0.0, "p95_latency_ms": 0.0}

    # Extract latencies
    latencies = [event.get("latency_ms", 0) for event in latency_events]
    latencies.sort()

    # Calculate statistics
    count = len(latencies)
    avg_latency = sum(latencies) / count
    p50_latency = latencies[int(count * 0.50)] if count > 0 else 0
    p95_latency = latencies[int(count * 0.95)] if count > 0 else 0
    p99_latency = latencies[int(count * 0.99)] if count > 0 else 0
    max_latency = max(latencies) if latencies else 0

    # Breakdown statistics
    breakdowns = [event.get("breakdown", {}) for event in latency_events if event.get("breakdown")]

    avg_breakdown = {}
    if breakdowns:
        for key in ["placement_ms", "exchange_fill_ms", "reconcile_ms", "fetch_order_ms"]:
            vals = [b.get(key, 0) for b in breakdowns]
            avg_breakdown[key] = sum(vals) / len(vals) if vals else 0.0

    return {
        "count": count,
        "avg_latency_ms": avg_latency,
        "p50_latency_ms": p50_latency,
        "p95_latency_ms": p95_latency,
        "p99_latency_ms": p99_latency,
        "max_latency_ms": max_latency,
        "avg_breakdown": avg_breakdown
    }


def print_latency_report(report: Dict[str, Any]):
    """
    Pretty-print latency report.

    Args:
        report: Latency report dict
    """
    print(f"\n{'='*80}")
    print("Intent-to-Fill Latency Report")
    print(f"{'='*80}\n")

    if report["count"] == 0:
        print("No latency data available")
        return

    print(f"Total Samples: {report['count']}")
    print("\nLatency Distribution:")
    print(f"  Average: {report['avg_latency_ms']:.1f}ms")
    print(f"  Median:  {report['p50_latency_ms']:.1f}ms")
    print(f"  P95:     {report['p95_latency_ms']:.1f}ms")
    print(f"  P99:     {report['p99_latency_ms']:.1f}ms")
    print(f"  Max:     {report['max_latency_ms']:.1f}ms")

    if report.get("avg_breakdown"):
        print("\nAverage Breakdown:")
        breakdown = report["avg_breakdown"]
        print(f"  Placement:     {breakdown.get('placement_ms', 0):.1f}ms")
        print(f"  Exchange Fill: {breakdown.get('exchange_fill_ms', 0):.1f}ms")
        print(f"  Reconcile:     {breakdown.get('reconcile_ms', 0):.1f}ms")
        print(f"  Fetch Order:   {breakdown.get('fetch_order_ms', 0):.1f}ms")

    print(f"\n{'='*80}\n")


def main():
    """CLI entry point"""
    import argparse

    parser = argparse.ArgumentParser(description="Analyze intent flow logs")
    parser.add_argument("intent_id", nargs="?", help="Intent ID to trace")
    parser.add_argument("--recent", type=int, metavar="N", help="Show N recent intents")
    parser.add_argument("--latency-report", action="store_true", help="Generate latency report")
    parser.add_argument("--log-dir", type=str, help="Custom log directory path")

    args = parser.parse_args()

    # Parse log directory
    log_dir = Path(args.log_dir) if args.log_dir else None

    # Execute command
    if args.latency_report:
        report = generate_latency_report(log_dir)
        print_latency_report(report)

    elif args.recent:
        intents = list_recent_intents(args.recent, log_dir)
        print_recent_intents(intents)

    elif args.intent_id:
        events = trace_intent_flow(args.intent_id, log_dir)
        print_intent_flow(events)

    else:
        parser.print_help()


if __name__ == "__main__":
    main()
