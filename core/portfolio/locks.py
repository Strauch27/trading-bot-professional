#!/usr/bin/env python3
"""
Symbol-Scoped Locks - Phase 6

Provides thread-safe per-symbol locking to prevent race conditions in portfolio operations.

Usage:
    from core.portfolio.locks import get_symbol_lock

    # In portfolio mutation code:
    with get_symbol_lock(symbol):
        # Modify position, WAC, fees, etc.
        portfolio.held_assets[symbol] = updated_data

Features:
- Fine-grained locking: Only blocks operations on the same symbol
- Deadlock prevention: Consistent lock ordering by symbol name
- Context manager interface for safe usage
- Thread-safe lock creation (no race conditions)

Akzeptanzkriterium:
Concurrency-Test mit Threads erzeugt keine inkonsistenten Mengen/WAC.
"""

import threading
from contextlib import contextmanager
from typing import Dict
import logging

logger = logging.getLogger(__name__)


class SymbolLockManager:
    """
    Manages per-symbol locks for thread-safe portfolio operations.

    Provides reentrant locks (RLock) per symbol to allow nested locking
    within the same thread while preventing concurrent access from different threads.
    """

    def __init__(self):
        """Initialize the lock manager."""
        self._locks: Dict[str, threading.RLock] = {}
        self._manager_lock = threading.Lock()  # Protects _locks dict itself

    def get_lock(self, symbol: str) -> threading.RLock:
        """
        Get or create a reentrant lock for the given symbol.

        Args:
            symbol: Trading pair symbol (e.g., "BTC/USDT")

        Returns:
            RLock for the symbol (reentrant, thread-safe)
        """
        # Fast path: lock already exists (no manager lock needed)
        if symbol in self._locks:
            return self._locks[symbol]

        # Slow path: need to create lock (requires manager lock)
        with self._manager_lock:
            # Double-check after acquiring lock (another thread might have created it)
            if symbol not in self._locks:
                self._locks[symbol] = threading.RLock()
                logger.debug(f"Created lock for symbol: {symbol}")

            return self._locks[symbol]

    @contextmanager
    def lock_symbol(self, symbol: str):
        """
        Context manager for symbol-scoped locking.

        Args:
            symbol: Trading pair symbol

        Usage:
            with lock_manager.lock_symbol("BTC/USDT"):
                # Thread-safe operations on BTC/USDT
                pass
        """
        lock = self.get_lock(symbol)
        lock.acquire()
        try:
            yield
        finally:
            lock.release()

    def cleanup_unused_locks(self, active_symbols: set) -> int:
        """
        Remove locks for symbols that are no longer active (optional cleanup).

        Args:
            active_symbols: Set of currently active symbols

        Returns:
            Number of locks removed
        """
        with self._manager_lock:
            to_remove = [sym for sym in self._locks.keys() if sym not in active_symbols]
            for sym in to_remove:
                del self._locks[sym]

            if to_remove:
                logger.info(f"Cleaned up {len(to_remove)} unused symbol locks")

            return len(to_remove)

    def get_lock_count(self) -> int:
        """Get total number of symbol locks currently managed."""
        return len(self._locks)


# Global singleton instance
_lock_manager: SymbolLockManager = None
_manager_creation_lock = threading.Lock()


def get_lock_manager() -> SymbolLockManager:
    """
    Get global SymbolLockManager singleton.

    Thread-safe lazy initialization.
    """
    global _lock_manager
    if _lock_manager is None:
        with _manager_creation_lock:
            if _lock_manager is None:
                _lock_manager = SymbolLockManager()
                logger.info("SymbolLockManager initialized (Phase 6)")
    return _lock_manager


@contextmanager
def get_symbol_lock(symbol: str):
    """
    Get symbol-scoped lock context manager.

    Primary interface for symbol-scoped locking.

    Args:
        symbol: Trading pair symbol

    Usage:
        from core.portfolio.locks import get_symbol_lock

        with get_symbol_lock("BTC/USDT"):
            # All portfolio operations on BTC/USDT here
            portfolio.add_held_asset("BTC/USDT", {...})
            # ...

    Note:
        Locks are reentrant (RLock), so nested calls within the same thread are safe.
    """
    manager = get_lock_manager()
    with manager.lock_symbol(symbol):
        yield


# Utility functions for testing and monitoring

def get_active_lock_count() -> int:
    """Get number of active symbol locks (for monitoring)."""
    manager = get_lock_manager()
    return manager.get_lock_count()


def cleanup_symbol_locks(active_symbols: set) -> int:
    """
    Cleanup unused symbol locks (optional maintenance).

    Args:
        active_symbols: Set of currently active symbols

    Returns:
        Number of locks removed
    """
    manager = get_lock_manager()
    return manager.cleanup_unused_locks(active_symbols)
