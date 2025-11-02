#!/usr/bin/env python3
"""
Centralized timeout handling for orders
"""

import logging
import time
from typing import List, Optional

from core.fsm.fsm_events import EventContext, FSMEvent
from core.fsm.phases import Phase
from core.fsm.state_data import StateData

logger = logging.getLogger(__name__)


class TimeoutManager:
    """
    Manages order timeouts and emits timeout events.

    Timeouts:
    - Buy order fill: 30s (config.BUY_FILL_TIMEOUT_SECS)
    - Sell order fill: 30s (config.SELL_FILL_TIMEOUT_SECS)
    - Cooldown: 60s (config.COOLDOWN_SECS)
    """

    def __init__(self):
        # Import config here to avoid circular imports
        try:
            import config
            self.buy_timeout_secs = getattr(config, 'BUY_FILL_TIMEOUT_SECS', 30)
            self.sell_timeout_secs = getattr(config, 'SELL_FILL_TIMEOUT_SECS', 30)
            self.cooldown_secs = getattr(config, 'COOLDOWN_SECS', 60)
            # CRITICAL FIX (P2 Issue #7): Position TTL enforcement
            self.position_ttl_min = getattr(config, 'TRADE_TTL_MIN', 60)  # Default 60 minutes
        except ImportError:
            logger.warning("Config not found, using default timeout values")
            self.buy_timeout_secs = 30
            self.sell_timeout_secs = 30
            self.cooldown_secs = 60
            self.position_ttl_min = 60

        logger.info(
            f"TimeoutManager initialized: "
            f"buy={self.buy_timeout_secs}s, "
            f"sell={self.sell_timeout_secs}s, "
            f"cooldown={self.cooldown_secs}s, "
            f"position_ttl={self.position_ttl_min}min"
        )

    def check_buy_timeout(
        self,
        symbol: str,
        state_data: Optional[StateData]
    ) -> Optional[EventContext]:
        """
        Check if buy order has timed out.

        Returns:
            EventContext with BUY_ORDER_TIMEOUT event, or None
        """
        if not state_data or not state_data.buy_order or not state_data.buy_order.placed_at:
            return None

        elapsed = time.time() - state_data.buy_order.placed_at
        if elapsed > self.buy_timeout_secs:
            logger.info(
                f"Buy order timeout detected: {symbol} "
                f"(elapsed={elapsed:.1f}s, timeout={self.buy_timeout_secs}s)"
            )

            return EventContext(
                event=FSMEvent.BUY_ORDER_TIMEOUT,
                symbol=symbol,
                timestamp=time.time(),
                order_id=state_data.buy_order.order_id,
                data={'elapsed_seconds': elapsed, 'timeout_threshold': self.buy_timeout_secs}
            )

        return None

    def check_sell_timeout(
        self,
        symbol: str,
        state_data: Optional[StateData]
    ) -> Optional[EventContext]:
        """Check if sell order has timed out"""
        if not state_data or not state_data.sell_order or not state_data.sell_order.placed_at:
            return None

        elapsed = time.time() - state_data.sell_order.placed_at
        if elapsed > self.sell_timeout_secs:
            logger.info(
                f"Sell order timeout detected: {symbol} "
                f"(elapsed={elapsed:.1f}s, timeout={self.sell_timeout_secs}s)"
            )

            return EventContext(
                event=FSMEvent.SELL_ORDER_TIMEOUT,
                symbol=symbol,
                timestamp=time.time(),
                order_id=state_data.sell_order.order_id,
                data={'elapsed_seconds': elapsed, 'timeout_threshold': self.sell_timeout_secs}
            )

        return None

    def check_cooldown_expired(
        self,
        symbol: str,
        state_data: Optional[StateData]
    ) -> Optional[EventContext]:
        """Check if cooldown period has expired"""
        if not state_data or not state_data.cooldown_started_at:
            return None

        elapsed = time.time() - state_data.cooldown_started_at
        if elapsed > self.cooldown_secs:
            logger.debug(
                f"Cooldown expired: {symbol} "
                f"(duration={elapsed:.1f}s, cooldown={self.cooldown_secs}s)"
            )

            return EventContext(
                event=FSMEvent.COOLDOWN_EXPIRED,
                symbol=symbol,
                timestamp=time.time(),
                data={'cooldown_duration': elapsed}
            )

        return None

    def check_position_ttl(
        self,
        symbol: str,
        coin_state
    ) -> Optional[EventContext]:
        """
        Check if position has exceeded TTL (time-to-live).

        CRITICAL FIX (P2 Issue #7): Force exit stale positions after TRADE_TTL_MIN.
        Prevents positions from being held indefinitely.

        Returns:
            EventContext with EXIT_SIGNAL_TIMEOUT event, or None
        """
        # Need entry_ts to calculate position age
        if not hasattr(coin_state, 'entry_ts') or coin_state.entry_ts == 0:
            return None

        # Calculate position age in minutes
        position_age_secs = time.time() - coin_state.entry_ts
        position_age_min = position_age_secs / 60.0

        # Check if position exceeded TTL
        if position_age_min > self.position_ttl_min:
            logger.info(
                f"Position TTL exceeded: {symbol} "
                f"(age={position_age_min:.1f}min, ttl={self.position_ttl_min}min)"
            )

            return EventContext(
                event=FSMEvent.EXIT_SIGNAL_TIMEOUT,
                symbol=symbol,
                timestamp=time.time(),
                data={
                    'position_age_minutes': position_age_min,
                    'ttl_threshold_minutes': self.position_ttl_min,
                    'exit_reason': 'POSITION_TTL_EXCEEDED'
                }
            )

        return None

    def check_all_timeouts(
        self,
        symbol: str,
        coin_state
    ) -> List[EventContext]:
        """
        Check all timeout conditions.

        Returns:
            List of timeout events (0-1 events typically)
        """
        state_data = getattr(coin_state, 'fsm_data', None)
        if not state_data:
            return []

        events = []

        # Check buy timeout (WAIT_FILL phase)
        if coin_state.phase == Phase.WAIT_FILL:
            event = self.check_buy_timeout(symbol, state_data)
            if event:
                events.append(event)

        # Check sell timeout (WAIT_SELL_FILL phase)
        elif coin_state.phase == Phase.WAIT_SELL_FILL:
            event = self.check_sell_timeout(symbol, state_data)
            if event:
                events.append(event)

        # Check cooldown expiry (COOLDOWN phase)
        elif coin_state.phase == Phase.COOLDOWN:
            event = self.check_cooldown_expired(symbol, state_data)
            if event:
                events.append(event)

        # CRITICAL FIX (P2 Issue #7): Check position TTL (POSITION phase)
        elif coin_state.phase == Phase.POSITION:
            event = self.check_position_ttl(symbol, coin_state)
            if event:
                events.append(event)

        return events

    def get_remaining_timeout(
        self,
        symbol: str,
        coin_state
    ) -> Optional[float]:
        """
        Get remaining timeout in seconds for current phase.

        Returns:
            Remaining seconds, or None if no timeout active
        """
        state_data = getattr(coin_state, 'fsm_data', None)
        if not state_data:
            return None

        now = time.time()

        # Buy order timeout
        if coin_state.phase == Phase.WAIT_FILL and state_data.buy_order and state_data.buy_order.placed_at:
            elapsed = now - state_data.buy_order.placed_at
            remaining = self.buy_timeout_secs - elapsed
            return max(0.0, remaining)

        # Sell order timeout
        elif coin_state.phase == Phase.WAIT_SELL_FILL and state_data.sell_order and state_data.sell_order.placed_at:
            elapsed = now - state_data.sell_order.placed_at
            remaining = self.sell_timeout_secs - elapsed
            return max(0.0, remaining)

        # Cooldown
        elif coin_state.phase == Phase.COOLDOWN and state_data.cooldown_started_at:
            elapsed = now - state_data.cooldown_started_at
            remaining = self.cooldown_secs - elapsed
            return max(0.0, remaining)

        return None

    def set_buy_timeout(self, timeout_secs: int):
        """Set buy order timeout (for testing/configuration)"""
        self.buy_timeout_secs = timeout_secs
        logger.info(f"Buy timeout set to {timeout_secs}s")

    def set_sell_timeout(self, timeout_secs: int):
        """Set sell order timeout (for testing/configuration)"""
        self.sell_timeout_secs = timeout_secs
        logger.info(f"Sell timeout set to {timeout_secs}s")

    def set_cooldown_duration(self, cooldown_secs: int):
        """Set cooldown duration (for testing/configuration)"""
        self.cooldown_secs = cooldown_secs
        logger.info(f"Cooldown duration set to {cooldown_secs}s")


# Global singleton
_timeout_manager: Optional[TimeoutManager] = None


def get_timeout_manager() -> TimeoutManager:
    """Get global timeout manager singleton"""
    global _timeout_manager
    if _timeout_manager is None:
        _timeout_manager = TimeoutManager()
    return _timeout_manager
