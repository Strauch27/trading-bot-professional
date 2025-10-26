#!/usr/bin/env python3
"""
Log Query Tool for Trading Bot

Query and analyze structured JSONL logs for debugging and performance analysis.

Usage:
    python scripts/query_logs.py --session session_20250112_123456 --query trades
    python scripts/query_logs.py --session session_20250112_123456 --query guards --symbol BTC/USDT
    python scripts/query_logs.py --session session_20250112_123456 --query orders --status filled
    python scripts/query_logs.py --session session_20250112_123456 --query performance

Query Types:
    trades       - List all completed trades with entry/exit/PnL
    guards       - Show guard evaluations (market quality checks)
    orders       - Show order lifecycle events
    performance  - Calculate win rate, PnL, avg trade duration
"""

import argparse
import json
import sys
from pathlib import Path
from typing import Dict, List, Optional


class LogQuery:
    """
    Query trading bot JSONL logs.

    Provides structured queries over decision logs, order logs, and audit logs
    for post-session analysis and debugging.
    """

    def __init__(self, session_dir: Path):
        """
        Initialize log query for session.

        Args:
            session_dir: Path to session directory containing logs/
        """
        self.session_dir = session_dir
        self.decision_log = session_dir / "logs" / "decisions" / "decision.jsonl"
        self.order_log = session_dir / "logs" / "orders" / "order.jsonl"
        self.audit_log = session_dir / "logs" / "audit" / "audit.jsonl"

    def query_trades(self) -> List[Dict]:
        """
        Get all completed trades.

        Matches position_opened with position_closed events to reconstruct
        complete trade lifecycle.

        Returns:
            List of trade dicts with entry/exit/PnL/duration
        """
        trades = []

        # Track open positions
        opens = {}

        if not self.decision_log.exists():
            return trades

        with open(self.decision_log) as f:
            for line in f:
                try:
                    event = json.loads(line)

                    if event.get('event') == 'position_opened':
                        opens[event['symbol']] = event

                    elif event.get('event') == 'position_closed':
                        symbol = event['symbol']
                        if symbol in opens:
                            open_event = opens[symbol]
                            trades.append({
                                'symbol': symbol,
                                'entry_price': open_event['avg_entry'],
                                'exit_price': event['exit_price'],
                                'qty': event['qty_closed'],
                                'realized_pnl': event['realized_pnl_usdt'],
                                'realized_pct': event.get('realized_pnl_pct', 0),
                                'duration_minutes': event.get('duration_minutes'),
                                'reason': event['reason'],
                                'opened_at': open_event.get('opened_at'),
                                'fee_total': event.get('fee_total', 0)
                            })
                            del opens[symbol]

                except (json.JSONDecodeError, KeyError):
                    continue

        return trades

    def query_guards(self, symbol: Optional[str] = None) -> List[Dict]:
        """
        Get guard evaluation events.

        Args:
            symbol: Optional symbol filter

        Returns:
            List of guards_eval events
        """
        guards = []

        if not self.decision_log.exists():
            return guards

        with open(self.decision_log) as f:
            for line in f:
                try:
                    event = json.loads(line)

                    if event.get('event') == 'guards_eval':
                        if symbol is None or event.get('symbol') == symbol:
                            guards.append(event)

                except (json.JSONDecodeError, KeyError):
                    continue

        return guards

    def query_orders(self, status: Optional[str] = None) -> List[Dict]:
        """
        Get order lifecycle events.

        Reconstructs order lifecycle by grouping order_attempt, order_ack, order_done events.

        Args:
            status: Optional filter by final_status (filled, canceled, etc.)

        Returns:
            List of order lifecycle dicts
        """
        orders = {}  # order_req_id → events

        if not self.order_log.exists():
            return []

        with open(self.order_log) as f:
            for line in f:
                try:
                    event = json.loads(line)

                    if event.get('event') in ['order_attempt', 'order_ack', 'order_done']:
                        order_req_id = event.get('order_req_id')
                        if order_req_id:
                            if order_req_id not in orders:
                                orders[order_req_id] = {}
                            orders[order_req_id][event['event']] = event

                except (json.JSONDecodeError, KeyError):
                    continue

        # Filter by status if specified
        if status:
            orders = {
                k: v for k, v in orders.items()
                if v.get('order_done', {}).get('final_status') == status
            }

        return list(orders.values())

    def query_performance(self) -> Dict:
        """
        Calculate performance metrics from trades.

        Returns:
            Dict with win_rate, total_pnl, avg_pnl, avg_duration, etc.
        """
        trades = self.query_trades()

        if not trades:
            return {
                'total_trades': 0,
                'winning_trades': 0,
                'losing_trades': 0,
                'win_rate': 0.0,
                'total_pnl': 0.0,
                'avg_pnl_per_trade': 0.0,
                'avg_win': 0.0,
                'avg_loss': 0.0,
                'avg_duration_minutes': 0.0
            }

        total_pnl = sum(t['realized_pnl'] for t in trades)
        winning_trades = [t for t in trades if t['realized_pnl'] > 0]
        losing_trades = [t for t in trades if t['realized_pnl'] < 0]

        # Calculate average duration (filter None values)
        durations = [t['duration_minutes'] for t in trades if t.get('duration_minutes')]
        avg_duration = sum(durations) / len(durations) if durations else 0

        return {
            'total_trades': len(trades),
            'winning_trades': len(winning_trades),
            'losing_trades': len(losing_trades),
            'win_rate': len(winning_trades) / len(trades) if trades else 0,
            'total_pnl': total_pnl,
            'avg_pnl_per_trade': total_pnl / len(trades),
            'avg_win': sum(t['realized_pnl'] for t in winning_trades) / len(winning_trades) if winning_trades else 0,
            'avg_loss': sum(t['realized_pnl'] for t in losing_trades) / len(losing_trades) if losing_trades else 0,
            'avg_duration_minutes': avg_duration,
            'total_fees': sum(t.get('fee_total', 0) for t in trades)
        }

    def query_risk_events(self) -> List[Dict]:
        """Get risk limit events"""
        risk_events = []

        if not self.decision_log.exists():
            return risk_events

        with open(self.decision_log) as f:
            for line in f:
                try:
                    event = json.loads(line)

                    if event.get('event') == 'risk_limits_eval':
                        if not event.get('all_passed'):
                            risk_events.append(event)

                except (json.JSONDecodeError, KeyError):
                    continue

        return risk_events


def main():
    """CLI entry point"""
    parser = argparse.ArgumentParser(
        description="Query trading bot logs",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__
    )

    parser.add_argument(
        '--session',
        required=True,
        help="Session directory name (e.g., session_20250112_123456)"
    )

    parser.add_argument(
        '--query',
        required=True,
        choices=['trades', 'guards', 'orders', 'performance', 'risk'],
        help="Query type"
    )

    parser.add_argument(
        '--symbol',
        help="Filter by symbol (for guards query)"
    )

    parser.add_argument(
        '--status',
        help="Filter orders by status (filled, canceled, etc.)"
    )

    parser.add_argument(
        '--limit',
        type=int,
        default=100,
        help="Limit number of results (default: 100)"
    )

    args = parser.parse_args()

    # Find session directory
    session_dir = Path("sessions") / args.session
    if not session_dir.exists():
        print(f"❌ Error: Session directory not found: {session_dir}")
        print("\nAvailable sessions:")
        sessions_dir = Path("sessions")
        if sessions_dir.exists():
            for session in sorted(sessions_dir.iterdir(), reverse=True):
                if session.is_dir():
                    print(f"  - {session.name}")
        sys.exit(1)

    # Execute query
    query = LogQuery(session_dir)

    if args.query == 'trades':
        trades = query.query_trades()
        print(f"\n📊 Found {len(trades)} completed trades:\n")

        for i, trade in enumerate(trades[-args.limit:], 1):
            pnl_sign = "+" if trade['realized_pnl'] >= 0 else ""
            pnl_emoji = "✅" if trade['realized_pnl'] >= 0 else "❌"
            print(
                f"{i:3d}. {pnl_emoji} {trade['symbol']:12s} "
                f"{trade['entry_price']:.6f} → {trade['exit_price']:.6f} "
                f"({pnl_sign}{trade['realized_pnl']:7.2f} USDT, {pnl_sign}{trade['realized_pct']:+6.2f}%) "
                f"[{trade['reason']}]"
            )

        if len(trades) > args.limit:
            print(f"\n... showing last {args.limit} of {len(trades)} trades (use --limit to see more)")

    elif args.query == 'guards':
        guards = query.query_guards(symbol=args.symbol)
        filter_msg = f" for {args.symbol}" if args.symbol else ""
        print(f"\n🛡️  Found {len(guards)} guard evaluations{filter_msg}:\n")

        for event in guards[-args.limit:]:
            passed = "✅ PASS" if event.get('all_passed') else "❌ BLOCK"
            print(f"{event.get('symbol', 'N/A'):12s}: {passed}")

            for guard in event.get('guards', []):
                status = "✅" if guard.get('passed') else "❌"
                value = guard.get('value', 'N/A')
                threshold = guard.get('threshold', 'N/A')
                print(f"  {status} {guard.get('name', 'unknown'):20s}: {value} (threshold: {threshold})")

    elif args.query == 'orders':
        orders = query.query_orders(status=args.status)
        filter_msg = f" with status='{args.status}'" if args.status else ""
        print(f"\n📋 Found {len(orders)} orders{filter_msg}:\n")

        for order_events in orders[-args.limit:]:
            attempt = order_events.get('order_attempt', {})
            order_events.get('order_ack', {})
            done = order_events.get('order_done', {})

            final_status = done.get('final_status', 'pending')
            status_emoji = "✅" if final_status == 'filled' else "❌"

            print(
                f"{status_emoji} {attempt.get('symbol', 'N/A'):12s} "
                f"{attempt.get('side', 'N/A'):4s} {attempt.get('qty', 0):.6f} "
                f"@ {attempt.get('price', 0):.6f} "
                f"→ {final_status}"
            )

    elif args.query == 'performance':
        perf = query.query_performance()
        print("\n📈 Performance Summary:\n")
        print("=" * 50)
        print(f"  Total Trades:     {perf['total_trades']}")
        print(f"  Winning Trades:   {perf['winning_trades']} ({perf.get('win_rate', 0):.1%})")
        print(f"  Losing Trades:    {perf['losing_trades']}")
        print("=" * 50)
        pnl_sign = "+" if perf.get('total_pnl', 0) >= 0 else ""
        print(f"  Total PnL:        {pnl_sign}{perf.get('total_pnl', 0):.2f} USDT")
        print(f"  Avg PnL/Trade:    {perf.get('avg_pnl_per_trade', 0):+.2f} USDT")
        print(f"  Avg Win:          +{perf.get('avg_win', 0):.2f} USDT")
        print(f"  Avg Loss:         {perf.get('avg_loss', 0):.2f} USDT")
        print("=" * 50)
        print(f"  Avg Duration:     {perf.get('avg_duration_minutes', 0):.1f} minutes")
        print(f"  Total Fees:       {perf.get('total_fees', 0):.2f} USDT")
        print("=" * 50)

    elif args.query == 'risk':
        risk_events = query.query_risk_events()
        print(f"\n⚠️  Found {len(risk_events)} risk limit blocks:\n")

        for event in risk_events[-args.limit:]:
            print(f"{event.get('symbol', 'N/A'):12s}: blocked by {event.get('blocking_limit', 'unknown')}")
            for check in event.get('limit_checks', []):
                if check.get('hit'):
                    print(f"  ❌ {check.get('limit')}: {check.get('value')} > {check.get('threshold')}")

    print()  # Final newline


if __name__ == "__main__":
    main()
