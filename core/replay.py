#!/usr/bin/env python3
"""
Session Replay - Reconstruct Portfolio State from JSONL Logs

Provides functionality to:
- Replay trading sessions from structured JSONL logs
- Reconstruct portfolio state at any point in time
- Compare replayed state with live state to detect discrepancies
- Verify logging completeness and accuracy

Usage:
    from core.replay import SessionReplay

    replay = SessionReplay(
        decision_log_path="logs/decisions/decision.jsonl",
        order_log_path="logs/orders/order.jsonl",
        audit_log_path="logs/audit/audit.jsonl"
    )

    state = replay.replay()
    print(f"Final state: {state}")

    # Compare with live state
    comparison = replay.compare_with_live_state(live_portfolio, live_cash)
    if not comparison['matches']:
        print(f"State mismatch detected: {comparison}")
"""

import json
import logging
from pathlib import Path
from typing import Dict, List, Optional

logger = logging.getLogger(__name__)


class SessionReplay:
    """
    Reconstruct portfolio state from JSONL event logs.

    Processes structured events chronologically to rebuild:
    - Portfolio positions (symbol, qty, avg_entry)
    - Cash balance (USDT)
    - Realized P&L
    - Trade history
    """

    def __init__(
        self,
        decision_log_path: str,
        order_log_path: str,
        audit_log_path: Optional[str] = None
    ):
        """
        Initialize replay from log file paths.

        Args:
            decision_log_path: Path to decision.jsonl (position events)
            order_log_path: Path to order.jsonl (order lifecycle events)
            audit_log_path: Path to audit.jsonl (ledger entries, reconciliation)
        """
        self.decision_log_path = decision_log_path
        self.order_log_path = order_log_path
        self.audit_log_path = audit_log_path

    def replay(self) -> Dict:
        """
        Replay session and reconstruct final state.

        Processes all events chronologically and builds up the portfolio state.

        Returns:
            {
                'portfolio': {symbol: {'qty': float, 'avg_entry': float, 'opened_at': str}},
                'cash_usdt': float,
                'realized_pnl': float,
                'total_fees': float,
                'trades': [{'symbol': str, 'entry': float, 'exit': float, ...}],
                'config_hash': str (optional)
            }
        """
        # Load all events from log files
        events = self._load_all_events()

        # Sort chronologically by timestamp
        events.sort(key=lambda e: e.get('ts_ns', 0))

        # Initialize empty state
        state = {
            'portfolio': {},
            'cash_usdt': 0.0,
            'realized_pnl': 0.0,
            'total_fees': 0.0,
            'trades': [],
            'config_hash': None,
            'events_processed': 0
        }

        # Replay events one by one
        for event in events:
            try:
                self._process_event(event, state)
                state['events_processed'] += 1
            except Exception as e:
                logger.warning(f"Failed to process event {event.get('event')}: {e}")

        logger.info(
            f"Replay complete: {state['events_processed']} events processed, "
            f"{len(state['trades'])} trades, "
            f"{len(state['portfolio'])} open positions, "
            f"realized PnL: {state['realized_pnl']:.2f} USDT"
        )

        return state

    def _process_event(self, event: Dict, state: Dict):
        """Process a single event and update state"""
        event_type = event.get('event')

        if event_type == 'position_opened':
            self._handle_position_opened(event, state)

        elif event_type == 'position_closed':
            self._handle_position_closed(event, state)

        elif event_type == 'position_updated':
            self._handle_position_updated(event, state)

        elif event_type == 'ledger_entry':
            self._handle_ledger_entry(event, state)

        elif event_type == 'config_snapshot':
            self._handle_config_snapshot(event, state)

    def _handle_position_opened(self, event: Dict, state: Dict):
        """Handle position_opened event"""
        symbol = event['symbol']

        # Add position to portfolio
        state['portfolio'][symbol] = {
            'qty': event['qty'],
            'avg_entry': event['avg_entry'],
            'opened_at': event.get('opened_at'),
            'notional': event['notional'],
            'fee_accum': event.get('fee_accum', 0.0)
        }

        # Deduct cost from cash balance
        total_cost = event['notional'] + event.get('fee_accum', 0.0)
        state['cash_usdt'] -= total_cost
        state['total_fees'] += event.get('fee_accum', 0.0)

        logger.debug(
            f"Replay: Opened {symbol} @ {event['avg_entry']:.4f} x{event['qty']:.6f} "
            f"(cost: {total_cost:.2f} USDT)"
        )

    def _handle_position_closed(self, event: Dict, state: Dict):
        """Handle position_closed event"""
        symbol = event['symbol']

        if symbol not in state['portfolio']:
            logger.warning(f"Attempted to close non-existent position: {symbol}")
            return

        # Calculate proceeds
        proceeds = event['qty_closed'] * event['exit_price']
        total_fees = event['fee_total']

        # Update cash balance
        state['cash_usdt'] += proceeds - total_fees
        state['realized_pnl'] += event['realized_pnl_usdt']
        state['total_fees'] += total_fees

        # Record trade
        state['trades'].append({
            'symbol': symbol,
            'entry': state['portfolio'][symbol]['avg_entry'],
            'exit': event['exit_price'],
            'qty': event['qty_closed'],
            'pnl': event['realized_pnl_usdt'],
            'pnl_pct': event.get('realized_pnl_pct', 0.0),
            'duration_minutes': event.get('duration_minutes'),
            'reason': event.get('reason')
        })

        # Remove position from portfolio
        del state['portfolio'][symbol]

        logger.debug(
            f"Replay: Closed {symbol} @ {event['exit_price']:.4f} "
            f"(PnL: {event['realized_pnl_usdt']:.2f} USDT, {event.get('realized_pnl_pct', 0):.2f}%)"
        )

    def _handle_position_updated(self, event: Dict, state: Dict):
        """Handle position_updated event (quantity changes or state updates)"""
        symbol = event['symbol']

        if symbol not in state['portfolio']:
            logger.debug(f"Position update for non-tracked position: {symbol}")
            return

        # If quantity change fields are present, update position
        if event.get('qty_after') is not None:
            state['portfolio'][symbol]['qty'] = event['qty_after']
            state['portfolio'][symbol]['avg_entry'] = event.get('avg_entry_after', state['portfolio'][symbol]['avg_entry'])

            logger.debug(
                f"Replay: Updated {symbol} position: "
                f"{event.get('qty_before'):.6f} -> {event['qty_after']:.6f}"
            )

    def _handle_ledger_entry(self, event: Dict, state: Dict):
        """Handle ledger_entry event for precise balance tracking"""
        account = event['account']

        # Track cash balance through ledger entries
        if account == 'cash:USDT':
            # For cash: Debit increases, Credit decreases
            state['cash_usdt'] += event['debit'] - event['credit']

        # Track total fees
        elif account == 'fees:trading':
            # Fees are tracked as credits (expenses)
            state['total_fees'] += event['credit']

    def _handle_config_snapshot(self, event: Dict, state: Dict):
        """Handle config_snapshot event"""
        state['config_hash'] = event.get('config_hash')

    def _load_all_events(self) -> List[Dict]:
        """
        Load all events from log files.

        Returns:
            List of event dicts from all log files
        """
        events = []

        # Load decision log (position events)
        events.extend(self._load_jsonl_file(self.decision_log_path))

        # Load order log (order lifecycle events)
        events.extend(self._load_jsonl_file(self.order_log_path))

        # Load audit log (ledger entries, reconciliation)
        if self.audit_log_path:
            events.extend(self._load_jsonl_file(self.audit_log_path))

        logger.info(f"Loaded {len(events)} total events from log files")

        return events

    def _load_jsonl_file(self, file_path: str) -> List[Dict]:
        """Load events from a single JSONL file"""
        events = []

        if not Path(file_path).exists():
            logger.debug(f"Log file not found: {file_path}")
            return events

        try:
            with open(file_path) as f:
                for line_num, line in enumerate(f, 1):
                    try:
                        event = json.loads(line)
                        events.append(event)
                    except json.JSONDecodeError as e:
                        logger.warning(f"Failed to parse line {line_num} in {file_path}: {e}")
                        continue

            logger.debug(f"Loaded {len(events)} events from {file_path}")

        except Exception as e:
            logger.error(f"Failed to read log file {file_path}: {e}")

        return events

    def compare_with_live_state(
        self,
        live_portfolio: Dict,
        live_cash: float
    ) -> Dict:
        """
        Compare replayed state with live state to detect discrepancies.

        This is useful for:
        - Verifying logging completeness
        - Detecting state drift
        - Debugging portfolio inconsistencies

        Args:
            live_portfolio: Live portfolio dict {symbol: {'qty': float, 'avg_entry': float}}
            live_cash: Live USDT cash balance

        Returns:
            {
                'matches': bool - True if states match
                'portfolio_diff': {...} - Symbol-level differences
                'cash_diff': float - Cash balance difference
                'replayed_state': {...} - Full replayed state
            }
        """
        replayed_state = self.replay()

        # Compare portfolios symbol by symbol
        portfolio_diff = {}
        all_symbols = set(replayed_state['portfolio'].keys()) | set(live_portfolio.keys())

        for symbol in all_symbols:
            replay_qty = replayed_state['portfolio'].get(symbol, {}).get('qty', 0)
            replay_entry = replayed_state['portfolio'].get(symbol, {}).get('avg_entry', 0)

            live_qty = live_portfolio.get(symbol, {}).get('qty', 0)
            live_entry = live_portfolio.get(symbol, {}).get('avg_entry', 0)

            # Check quantity difference
            qty_diff = abs(replay_qty - live_qty)
            entry_diff = abs(replay_entry - live_entry) if live_entry > 0 and replay_entry > 0 else 0

            if qty_diff > 1e-8 or entry_diff > 1e-6:
                portfolio_diff[symbol] = {
                    'replayed_qty': replay_qty,
                    'live_qty': live_qty,
                    'qty_diff': live_qty - replay_qty,
                    'replayed_entry': replay_entry,
                    'live_entry': live_entry,
                    'entry_diff': live_entry - replay_entry
                }

        # Compare cash balance
        cash_diff = live_cash - replayed_state['cash_usdt']

        # Determine if states match (within tolerance)
        matches = (len(portfolio_diff) == 0 and abs(cash_diff) < 0.01)

        result = {
            'matches': matches,
            'portfolio_diff': portfolio_diff,
            'cash_diff': cash_diff,
            'replayed_state': replayed_state
        }

        if not matches:
            logger.warning(
                f"State mismatch detected! "
                f"Portfolio differences: {len(portfolio_diff)} symbols, "
                f"Cash diff: {cash_diff:.2f} USDT"
            )

        return result

    def get_pnl_timeline(self) -> List[Dict]:
        """
        Get timeline of P&L changes throughout the session.

        Returns:
            List of timestamped P&L snapshots
        """
        events = self._load_all_events()
        events.sort(key=lambda e: e.get('ts_ns', 0))

        timeline = []
        cumulative_pnl = 0.0

        for event in events:
            if event.get('event') == 'position_closed':
                cumulative_pnl += event.get('realized_pnl_usdt', 0.0)

                timeline.append({
                    'timestamp': event.get('ts_ns', 0) / 1e9,  # Convert to seconds
                    'symbol': event['symbol'],
                    'realized_pnl': event.get('realized_pnl_usdt', 0.0),
                    'cumulative_pnl': cumulative_pnl,
                    'reason': event.get('reason')
                })

        return timeline
