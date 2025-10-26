#!/usr/bin/env python3
"""
Transactional Portfolio Updates

Ensures FSM state and portfolio state stay synchronized.
Uses 2-phase commit pattern.
"""

import logging
from contextlib import contextmanager
from typing import Dict, Optional

logger = logging.getLogger(__name__)


class PortfolioTransaction:
    """
    Transactional portfolio update.

    Pattern:
    1. Begin transaction (save old state)
    2. Update portfolio
    3. Update FSM state
    4. Commit (save snapshot) or Rollback (restore old state)
    """

    def __init__(self, portfolio, pnl_service, snapshot_manager):
        self.portfolio = portfolio
        self.pnl_service = pnl_service
        self.snapshot_manager = snapshot_manager

        self._in_transaction = False
        self._rollback_state = None

    @contextmanager
    def begin(self, symbol: str, coin_state):
        """
        Context manager for transactional update.

        Usage:
            with portfolio_tx.begin(symbol, coin_state) as tx:
                portfolio.add_position(...)
                coin_state.phase = Phase.POSITION
                # If exception: automatic rollback
                # If success: automatic commit + snapshot
        """
        if self._in_transaction:
            raise RuntimeError("Nested transactions not supported")

        self._in_transaction = True
        self._rollback_state = self._capture_state(symbol, coin_state)

        try:
            yield self
            # Success - commit
            self._commit(symbol, coin_state)

        except Exception as e:
            # Failure - rollback
            logger.error(f"Transaction failed for {symbol}, rolling back: {e}", exc_info=True)
            self._rollback(symbol, coin_state)
            raise

        finally:
            self._in_transaction = False
            self._rollback_state = None

    def _capture_state(self, symbol: str, coin_state) -> Dict:
        """Capture current state for potential rollback"""
        # Get portfolio position (deep copy to avoid reference issues)
        portfolio_position = None
        if symbol in self.portfolio:
            portfolio_position = dict(self.portfolio[symbol])  # Shallow copy of dict

        # Capture FSM state (we'll make a shallow copy)
        return {
            'symbol': symbol,
            'phase': coin_state.phase,
            'portfolio_position': portfolio_position,
            'amount': coin_state.amount,
            'entry_price': coin_state.entry_price,
            'entry_ts': coin_state.entry_ts,
            'order_id': coin_state.order_id,
            'cooldown_until': coin_state.cooldown_until,
        }

    def _commit(self, symbol: str, coin_state):
        """Commit transaction and save snapshot"""
        # Save FSM snapshot
        success = self.snapshot_manager.save_snapshot(symbol, coin_state)
        if not success:
            logger.warning(f"Snapshot save failed for {symbol} (transaction committed)")

        logger.debug(f"Transaction committed: {symbol} @ {coin_state.phase.name}")

    def _rollback(self, symbol: str, coin_state):
        """Rollback transaction to previous state"""
        if not self._rollback_state:
            logger.error(f"Cannot rollback {symbol}: no saved state")
            return

        # Restore FSM phase
        coin_state.phase = self._rollback_state['phase']
        coin_state.amount = self._rollback_state['amount']
        coin_state.entry_price = self._rollback_state['entry_price']
        coin_state.entry_ts = self._rollback_state['entry_ts']
        coin_state.order_id = self._rollback_state['order_id']
        coin_state.cooldown_until = self._rollback_state['cooldown_until']

        # Restore portfolio position
        old_position = self._rollback_state['portfolio_position']
        if old_position:
            self.portfolio[symbol] = old_position
        elif symbol in self.portfolio:
            del self.portfolio[symbol]

        logger.warning(f"Transaction rolled back: {symbol} → {coin_state.phase.name}")


# Global singleton (initialized in engine)
_portfolio_transaction: Optional[PortfolioTransaction] = None


def get_portfolio_transaction() -> PortfolioTransaction:
    """Get global portfolio transaction manager"""
    global _portfolio_transaction
    if _portfolio_transaction is None:
        # Will be initialized by engine with real dependencies
        raise RuntimeError("PortfolioTransaction not initialized. Call init_portfolio_transaction() first.")
    return _portfolio_transaction


def init_portfolio_transaction(portfolio, pnl_service, snapshot_manager):
    """Initialize portfolio transaction manager (called by engine)"""
    global _portfolio_transaction
    _portfolio_transaction = PortfolioTransaction(portfolio, pnl_service, snapshot_manager)
    logger.info("PortfolioTransaction initialized")
