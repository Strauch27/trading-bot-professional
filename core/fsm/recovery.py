#!/usr/bin/env python3
"""
FSM Crash Recovery

Recovers FSM state from snapshots on startup.
"""

import logging
from typing import Dict

from core.fsm.snapshot import get_snapshot_manager
from core.fsm.state import CoinState
from core.fsm.state_data import StateData

logger = logging.getLogger(__name__)


class RecoveryManager:
    """
    Manages FSM crash recovery from snapshots.
    """

    def __init__(self):
        self.snapshot_manager = get_snapshot_manager()
        self.recovered_states: Dict[str, CoinState] = {}

    def recover_all_states(self) -> Dict[str, CoinState]:
        """
        Recover all FSM states from snapshots.

        Returns:
            Dict mapping symbol -> CoinState
        """
        snapshots = self.snapshot_manager.list_all_snapshots()

        if not snapshots:
            logger.info("No FSM snapshots found, starting fresh")
            return {}

        logger.info(f"Recovering from {len(snapshots)} FSM snapshots...")

        recovered_count = 0
        failed_count = 0

        for snapshot_info in snapshots:
            symbol = snapshot_info['symbol']

            try:
                # Initialize coin state
                coin_state = CoinState(symbol=symbol)

                # Initialize fsm_data before restoration
                coin_state.fsm_data = StateData()

                # Restore from snapshot
                if self.snapshot_manager.restore_state(symbol, coin_state):
                    # CRITICAL FIX (C-FSM-03): Validate recovered state before accepting
                    self.recovered_states[symbol] = coin_state
                    validation_result = self.validate_recovered_state(symbol)

                    if validation_result['valid']:
                        recovered_count += 1
                        logger.info(
                            f"Recovered {symbol}: {coin_state.phase.name} "
                            f"(amount={coin_state.amount:.6f}, entry={coin_state.entry_price:.4f})"
                        )
                    else:
                        # State is invalid - reset to IDLE
                        logger.warning(
                            f"Invalid state recovered for {symbol}: {validation_result['issues']} "
                            f"- resetting to IDLE"
                        )
                        from core.fsm.fsm_enums import Phase
                        coin_state.phase = Phase.IDLE
                        coin_state.amount = 0.0
                        coin_state.entry_price = 0.0
                        coin_state.entry_ts = 0.0
                        # Still count as recovered (but reset)
                        recovered_count += 1
                else:
                    failed_count += 1

            except Exception as e:
                logger.error(f"Failed to recover snapshot for {symbol}: {e}")
                failed_count += 1

        logger.info(
            f"FSM recovery complete: {recovered_count}/{len(snapshots)} restored, "
            f"{failed_count} failed"
        )

        # Log recovery event
        try:
            from core.logger_factory import log_event, DECISION_LOG
            log_event(
                DECISION_LOG(),
                "fsm_recovery",
                snapshots_found=len(snapshots),
                recovered_count=recovered_count,
                failed_count=failed_count,
                recovered_symbols=list(self.recovered_states.keys())
            )
        except Exception as e:
            logger.debug(f"Failed to log recovery event: {e}")

        return self.recovered_states

    def get_recovery_summary(self) -> Dict:
        """Get summary of recovery operation"""
        return {
            'recovered_count': len(self.recovered_states),
            'recovered_symbols': list(self.recovered_states.keys()),
            'phases': {
                symbol: state.phase.name
                for symbol, state in self.recovered_states.items()
            }
        }

    def validate_recovered_state(self, symbol: str) -> Dict:
        """
        Validate recovered state for consistency.

        Returns:
            Dict with validation results
        """
        if symbol not in self.recovered_states:
            return {'valid': False, 'reason': 'symbol_not_recovered'}

        coin_state = self.recovered_states[symbol]

        issues = []

        # Check for position phase without amount
        if coin_state.phase.name == 'POSITION' and coin_state.amount <= 0:
            issues.append('position_phase_without_amount')

        # Check for amount without position phase
        if coin_state.amount > 0 and coin_state.phase.name not in ['POSITION', 'EXIT_EVAL', 'PLACE_SELL', 'WAIT_SELL_FILL']:
            issues.append('amount_in_wrong_phase')

        # Check for stale timestamps
        if coin_state.entry_ts > 0:
            import time
            age_hours = (time.time() - coin_state.entry_ts) / 3600
            if age_hours > 24:
                issues.append(f'stale_position_age_{age_hours:.1f}h')

        return {
            'valid': len(issues) == 0,
            'issues': issues,
            'symbol': symbol,
            'phase': coin_state.phase.name,
            'amount': coin_state.amount
        }


# Global singleton
_recovery_manager = None


def get_recovery_manager() -> RecoveryManager:
    """Get global recovery manager singleton"""
    global _recovery_manager
    if _recovery_manager is None:
        _recovery_manager = RecoveryManager()
    return _recovery_manager


def recover_fsm_states_on_startup() -> Dict[str, CoinState]:
    """
    Convenience function for FSM recovery on startup.

    Usage in engine __init__:
        from core.fsm.recovery import recover_fsm_states_on_startup

        # Recover FSM states
        recovered_states = recover_fsm_states_on_startup()
        for symbol, coin_state in recovered_states.items():
            self.coin_states[symbol] = coin_state
    """
    recovery_manager = get_recovery_manager()
    return recovery_manager.recover_all_states()
