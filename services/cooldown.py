#!/usr/bin/env python3
"""
Symbol Cooldown Manager - Post-Trade Locks

Prevents immediate re-entry after closing a position:
- Symbol-specific cooldown periods
- Configurable duration per symbol
- Automatic expiration tracking
- Batch cleanup of expired cooldowns

Use cases:
- Prevent churning on same symbol
- Allow market to stabilize after exit
- Avoid emotional revenge trading
- Implement trading rules (e.g., "wait 15min after loss")

Example:
    cooldown = CooldownManager()
    cooldown.set("BTC/USDT", duration_s=900)  # 15 minutes

    if cooldown.is_active("BTC/USDT"):
        # Skip this symbol - still in cooldown
        return

    # OK to trade
"""

import logging
import time
from typing import Dict, List, Optional

logger = logging.getLogger(__name__)


class CooldownManager:
    """
    Symbol-specific cooldown tracking.

    Maintains cooldown periods for symbols after position closure,
    preventing immediate re-entry.
    """

    def __init__(self):
        """Initialize cooldown manager with empty state"""
        # Map: symbol -> cooldown_release_timestamp
        self._cooldowns: Dict[str, float] = {}

        logger.info("Cooldown manager initialized")

    def set(self, symbol: str, duration_s: float) -> None:
        """
        Set cooldown for a symbol.

        Args:
            symbol: Trading pair (e.g., "BTC/USDT")
            duration_s: Cooldown duration in seconds
        """
        release_ts = time.time() + duration_s

        self._cooldowns[symbol] = release_ts

        logger.debug(
            f"Cooldown set: {symbol} for {duration_s:.0f}s "
            f"(until {time.strftime('%H:%M:%S', time.localtime(release_ts))})"
        )

    def is_active(self, symbol: str) -> bool:
        """
        Check if symbol is currently in cooldown.

        Args:
            symbol: Trading pair

        Returns:
            True if cooldown active, False otherwise
        """
        if symbol not in self._cooldowns:
            return False

        release_ts = self._cooldowns[symbol]
        now = time.time()

        # Check if cooldown expired
        if now >= release_ts:
            # Auto-cleanup expired cooldown
            del self._cooldowns[symbol]
            return False

        return True

    def get_remaining(self, symbol: str) -> float:
        """
        Get remaining cooldown time in seconds.

        Args:
            symbol: Trading pair

        Returns:
            Remaining seconds (0 if not in cooldown)
        """
        if symbol not in self._cooldowns:
            return 0.0

        release_ts = self._cooldowns[symbol]
        now = time.time()

        remaining = max(0.0, release_ts - now)

        # Auto-cleanup if expired
        if remaining == 0.0:
            del self._cooldowns[symbol]

        return remaining

    def clear(self, symbol: str) -> bool:
        """
        Clear cooldown for a specific symbol.

        Args:
            symbol: Trading pair

        Returns:
            True if cooldown was active, False otherwise
        """
        if symbol in self._cooldowns:
            del self._cooldowns[symbol]
            logger.debug(f"Cooldown cleared: {symbol}")
            return True

        return False

    def clear_all(self) -> int:
        """
        Clear all cooldowns.

        Returns:
            Number of cooldowns cleared
        """
        count = len(self._cooldowns)
        self._cooldowns.clear()

        if count > 0:
            logger.info(f"Cleared {count} cooldowns")

        return count

    def cleanup_expired(self) -> int:
        """
        Remove all expired cooldowns.

        Returns:
            Number of cooldowns removed
        """
        now = time.time()

        expired = [
            symbol for symbol, release_ts in self._cooldowns.items()
            if now >= release_ts
        ]

        for symbol in expired:
            del self._cooldowns[symbol]

        if expired:
            logger.debug(f"Cleaned up {len(expired)} expired cooldowns")

        return len(expired)

    def get_active_symbols(self) -> List[str]:
        """
        Get list of all symbols currently in cooldown.

        Auto-cleans expired cooldowns before returning.

        Returns:
            List of symbol strings
        """
        # Cleanup first
        self.cleanup_expired()

        return list(self._cooldowns.keys())

    def get_stats(self) -> Dict[str, any]:
        """
        Get cooldown statistics.

        Returns:
            Dict with active count and details
        """
        # Cleanup first
        self.cleanup_expired()

        active_symbols = list(self._cooldowns.keys())
        now = time.time()

        # Calculate remaining times
        details = {}
        for symbol in active_symbols:
            release_ts = self._cooldowns[symbol]
            remaining_s = release_ts - now

            details[symbol] = {
                "remaining_s": remaining_s,
                "remaining_m": remaining_s / 60.0,
                "release_ts": release_ts
            }

        return {
            "active_count": len(active_symbols),
            "active_symbols": active_symbols,
            "details": details
        }


# Global singleton instance
_cooldown_instance: Optional[CooldownManager] = None


def get_cooldown_manager() -> CooldownManager:
    """
    Get global cooldown manager instance (singleton).

    Returns:
        CooldownManager instance
    """
    global _cooldown_instance

    if _cooldown_instance is None:
        _cooldown_instance = CooldownManager()

    return _cooldown_instance
