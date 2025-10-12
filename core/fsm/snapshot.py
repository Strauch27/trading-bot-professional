#!/usr/bin/env python3
"""
FSM Snapshot System - Crash Recovery

Persists FSM state to disk after every transition.
Enables recovery from crashes without losing positions.
"""

import json
import logging
import threading
from pathlib import Path
from typing import Dict, Optional

from core.fsm.phases import Phase
from core.fsm.state import CoinState
from core.fsm.state_data import StateData, OrderContext

logger = logging.getLogger(__name__)


class SnapshotManager:
    """
    Manages FSM state snapshots for crash recovery.

    Snapshots are written to:
    - sessions/current/fsm_snapshots/{symbol}.json

    Format:
    {
        "symbol": "BTC/USDT",
        "phase": "POSITION",
        "timestamp": 1234567890.123,
        "state_data": {...},
        "snapshot_version": 1
    }
    """

    def __init__(self, snapshot_dir: Optional[Path] = None):
        if snapshot_dir is None:
            # Default: sessions/current/fsm_snapshots/
            snapshot_dir = Path("sessions") / "current" / "fsm_snapshots"

        self.snapshot_dir = snapshot_dir
        self.snapshot_dir.mkdir(parents=True, exist_ok=True)

        self._lock = threading.RLock()
        self._write_count = 0

        logger.info(f"Snapshot manager initialized: {self.snapshot_dir}")

    def save_snapshot(self, symbol: str, coin_state: CoinState) -> bool:
        """
        Save current FSM state snapshot.

        Returns:
            True if successful
        """
        with self._lock:
            try:
                snapshot = self._serialize_state(symbol, coin_state)
                snapshot_path = self._get_snapshot_path(symbol)

                # Write atomically (write to temp, then rename)
                temp_path = snapshot_path.with_suffix('.tmp')
                with open(temp_path, 'w') as f:
                    json.dump(snapshot, f, indent=2)

                temp_path.rename(snapshot_path)

                self._write_count += 1

                logger.debug(f"Snapshot saved: {symbol} @ {coin_state.phase.name}")
                return True

            except Exception as e:
                logger.error(f"Failed to save snapshot for {symbol}: {e}")
                return False

    def load_snapshot(self, symbol: str) -> Optional[Dict]:
        """
        Load FSM state snapshot.

        Returns:
            Snapshot dict or None if not found
        """
        with self._lock:
            snapshot_path = self._get_snapshot_path(symbol)

            if not snapshot_path.exists():
                return None

            try:
                with open(snapshot_path) as f:
                    snapshot = json.load(f)

                logger.info(f"Snapshot loaded: {symbol} @ {snapshot['phase']}")
                return snapshot

            except Exception as e:
                logger.error(f"Failed to load snapshot for {symbol}: {e}")
                return None

    def restore_state(self, symbol: str, coin_state: CoinState) -> bool:
        """
        Restore coin state from snapshot.

        Returns:
            True if restored successfully
        """
        snapshot = self.load_snapshot(symbol)
        if not snapshot:
            return False

        try:
            # Restore phase
            coin_state.phase = Phase[snapshot['phase']]

            # Restore state data if present
            if snapshot.get('state_data'):
                if not hasattr(coin_state, 'fsm_data') or coin_state.fsm_data is None:
                    coin_state.fsm_data = StateData()

                self._deserialize_state_data_into(snapshot['state_data'], coin_state.fsm_data)

            # Restore basic coin state fields
            if snapshot.get('coin_state'):
                cs = snapshot['coin_state']
                coin_state.amount = cs.get('amount', 0.0)
                coin_state.entry_price = cs.get('entry_price', 0.0)
                coin_state.entry_ts = cs.get('entry_ts', 0.0)
                coin_state.order_id = cs.get('order_id')
                coin_state.cooldown_until = cs.get('cooldown_until', 0.0)

            logger.info(f"State restored: {symbol} → {coin_state.phase.name}")
            return True

        except Exception as e:
            logger.error(f"Failed to restore state for {symbol}: {e}")
            return False

    def delete_snapshot(self, symbol: str) -> bool:
        """Delete snapshot (after position closed)"""
        with self._lock:
            snapshot_path = self._get_snapshot_path(symbol)

            if snapshot_path.exists():
                try:
                    snapshot_path.unlink()
                    logger.debug(f"Snapshot deleted: {symbol}")
                    return True
                except Exception as e:
                    logger.error(f"Failed to delete snapshot for {symbol}: {e}")
                    return False

            return False

    def _serialize_state(self, symbol: str, coin_state: CoinState) -> Dict:
        """Serialize coin state to dict"""
        import time

        snapshot = {
            'symbol': symbol,
            'phase': coin_state.phase.name,
            'timestamp': time.time(),
            'snapshot_version': 1
        }

        # Serialize basic coin state
        snapshot['coin_state'] = {
            'amount': coin_state.amount,
            'entry_price': coin_state.entry_price,
            'entry_ts': coin_state.entry_ts,
            'order_id': coin_state.order_id,
            'cooldown_until': coin_state.cooldown_until,
            'error_count': coin_state.error_count,
            'last_error': coin_state.last_error
        }

        # Serialize state data if present
        if hasattr(coin_state, 'fsm_data') and coin_state.fsm_data:
            state_data = coin_state.fsm_data
            snapshot['state_data'] = {
                # Order contexts
                'buy_order': self._serialize_order_context(state_data.buy_order),
                'sell_order': self._serialize_order_context(state_data.sell_order),

                # Entry evaluation
                'signal_detected_at': state_data.signal_detected_at,
                'signal_type': state_data.signal_type,

                # Position
                'position_opened_at': state_data.position_opened_at,
                'position_entry_price': state_data.position_entry_price,
                'position_qty': state_data.position_qty,
                'position_fees_accum': state_data.position_fees_accum,

                # Exit
                'exit_signal': state_data.exit_signal,
                'exit_detected_at': state_data.exit_detected_at,
                'peak_price': state_data.peak_price,
                'trailing_stop': state_data.trailing_stop,

                # Cooldown
                'cooldown_started_at': state_data.cooldown_started_at,

                # Error
                'error_count': state_data.error_count,
                'last_error': state_data.last_error,
                'last_error_at': state_data.last_error_at
            }

        return snapshot

    def _serialize_order_context(self, order_ctx: Optional[OrderContext]) -> Optional[Dict]:
        """Serialize order context"""
        if not order_ctx:
            return None

        return {
            'order_id': order_ctx.order_id,
            'client_order_id': order_ctx.client_order_id,
            'placed_at': order_ctx.placed_at,
            'cumulative_qty': order_ctx.cumulative_qty,
            'target_qty': order_ctx.target_qty,
            'avg_price': order_ctx.avg_price,
            'total_fees': order_ctx.total_fees,
            'status': order_ctx.status,
            'retry_count': order_ctx.retry_count
        }

    def _deserialize_state_data_into(self, data: Dict, state_data: StateData):
        """Deserialize state data from dict into existing StateData object"""
        # Restore order contexts
        if data.get('buy_order'):
            state_data.buy_order = self._deserialize_order_context(data['buy_order'])

        if data.get('sell_order'):
            state_data.sell_order = self._deserialize_order_context(data['sell_order'])

        # Restore other fields
        state_data.signal_detected_at = data.get('signal_detected_at')
        state_data.signal_type = data.get('signal_type')
        state_data.position_opened_at = data.get('position_opened_at')
        state_data.position_entry_price = data.get('position_entry_price', 0.0)
        state_data.position_qty = data.get('position_qty', 0.0)
        state_data.position_fees_accum = data.get('position_fees_accum', 0.0)
        state_data.exit_signal = data.get('exit_signal')
        state_data.exit_detected_at = data.get('exit_detected_at')
        state_data.peak_price = data.get('peak_price', 0.0)
        state_data.trailing_stop = data.get('trailing_stop')
        state_data.cooldown_started_at = data.get('cooldown_started_at')
        state_data.error_count = data.get('error_count', 0)
        state_data.last_error = data.get('last_error')
        state_data.last_error_at = data.get('last_error_at')

    def _deserialize_order_context(self, data: Dict) -> OrderContext:
        """Deserialize order context from dict"""
        return OrderContext(
            order_id=data.get('order_id'),
            client_order_id=data.get('client_order_id'),
            placed_at=data.get('placed_at'),
            cumulative_qty=data.get('cumulative_qty', 0.0),
            target_qty=data.get('target_qty', 0.0),
            avg_price=data.get('avg_price', 0.0),
            total_fees=data.get('total_fees', 0.0),
            status=data.get('status', 'pending'),
            retry_count=data.get('retry_count', 0)
        )

    def _get_snapshot_path(self, symbol: str) -> Path:
        """Get snapshot file path for symbol"""
        # Replace / with _ for filename
        safe_symbol = symbol.replace('/', '_')
        return self.snapshot_dir / f"{safe_symbol}.json"

    def get_stats(self) -> Dict:
        """Get snapshot statistics"""
        with self._lock:
            snapshots = list(self.snapshot_dir.glob("*.json"))
            return {
                'snapshot_count': len(snapshots),
                'write_count': self._write_count,
                'snapshot_dir': str(self.snapshot_dir)
            }

    def list_all_snapshots(self) -> list:
        """List all snapshot files"""
        with self._lock:
            snapshots = []
            for snapshot_path in self.snapshot_dir.glob("*.json"):
                try:
                    symbol = snapshot_path.stem.replace('_', '/')
                    with open(snapshot_path) as f:
                        snapshot = json.load(f)

                    snapshots.append({
                        'symbol': symbol,
                        'phase': snapshot.get('phase'),
                        'timestamp': snapshot.get('timestamp'),
                        'file': str(snapshot_path)
                    })
                except Exception as e:
                    logger.debug(f"Failed to read snapshot {snapshot_path}: {e}")

            return snapshots


# Global singleton
_snapshot_manager: Optional[SnapshotManager] = None


def get_snapshot_manager() -> SnapshotManager:
    """Get global snapshot manager singleton"""
    global _snapshot_manager
    if _snapshot_manager is None:
        _snapshot_manager = SnapshotManager()
    return _snapshot_manager
