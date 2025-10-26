#!/usr/bin/env python3
"""
Debounced State Writer - Reduces I/O load for frequent state updates

Collects state changes in memory and writes to disk at configurable intervals
(debounced) instead of on every update. Ensures clean persistence on shutdown.

Thread-safe implementation with automatic flush on shutdown.
"""

import json
import logging
import os
import threading
import time
from typing import Any, Callable, Dict, Optional

logger = logging.getLogger(__name__)


class DebouncedStateWriter:
    """
    Debounced state writer with automatic flush.

    Features:
    - Batches state updates to reduce I/O
    - Thread-safe operations
    - Automatic flush on shutdown
    - Configurable write interval
    - Error recovery with backup files

    Usage:
        writer = DebouncedStateWriter("state/transient.json", interval_s=10.0)
        writer.update({"key": "value"})
        # ... state is written every 10s
        writer.shutdown()  # Final flush
    """

    def __init__(
        self,
        file_path: str,
        interval_s: float = 10.0,
        auto_start: bool = True,
        state_loader: Optional[Callable] = None
    ):
        """
        Initialize debounced state writer.

        Args:
            file_path: Path to state file
            interval_s: Write interval in seconds (debounce time)
            auto_start: Start background thread automatically
            state_loader: Optional custom state loader function
        """
        self.file_path = file_path
        self.interval_s = interval_s
        self._state: Dict[str, Any] = {}
        self._dirty = False
        self._lock = threading.RLock()
        self._shutdown_flag = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._last_write_time = 0.0
        self._write_count = 0
        self._error_count = 0

        # Create parent directory if it doesn't exist
        os.makedirs(os.path.dirname(file_path), exist_ok=True)

        # Load existing state
        if state_loader:
            try:
                self._state = state_loader(file_path) or {}
                logger.info(f"Loaded {len(self._state)} entries from {file_path}")
            except Exception as e:
                logger.warning(f"Failed to load state from {file_path}: {e}")
        else:
            self._load_state()

        # Start background writer
        if auto_start:
            self.start()

    def _load_state(self):
        """Load state from disk"""
        try:
            if os.path.exists(self.file_path):
                with open(self.file_path, 'r', encoding='utf-8') as f:
                    self._state = json.load(f)
                logger.info(f"Loaded state from {self.file_path}: {len(self._state)} entries")
            else:
                logger.debug(f"No existing state file at {self.file_path}")
        except Exception as e:
            logger.warning(f"Failed to load state from {self.file_path}: {e}")
            # Try backup
            backup_path = f"{self.file_path}.backup"
            if os.path.exists(backup_path):
                try:
                    with open(backup_path, 'r', encoding='utf-8') as f:
                        self._state = json.load(f)
                    logger.info(f"Recovered state from backup: {len(self._state)} entries")
                except Exception as backup_error:
                    logger.error(f"Backup recovery also failed: {backup_error}")

    def start(self):
        """Start background writer thread"""
        if self._thread and self._thread.is_alive():
            logger.warning("State writer thread already running")
            return

        self._shutdown_flag.clear()
        self._thread = threading.Thread(
            target=self._writer_loop,
            name="DebouncedStateWriter",
            daemon=False  # Non-daemon for clean shutdown
        )
        self._thread.start()
        logger.info(f"State writer started: {self.file_path} (interval={self.interval_s}s)")

    def _writer_loop(self):
        """Background writer loop - writes state at intervals"""
        try:
            while not self._shutdown_flag.is_set():
                # Wait for interval or shutdown signal
                if self._shutdown_flag.wait(timeout=self.interval_s):
                    break

                # Write if dirty
                with self._lock:
                    if self._dirty:
                        self._write_state()
                        self._dirty = False

        except Exception as e:
            logger.error(f"State writer loop error: {e}", exc_info=True)

        finally:
            # Final flush before exit
            with self._lock:
                if self._dirty:
                    self._write_state()
                    logger.info("State writer: Final flush completed")

    def _write_state(self):
        """Write state to disk (assumes lock is held)"""
        try:
            # Create backup of existing file
            if os.path.exists(self.file_path):
                backup_path = f"{self.file_path}.backup"
                try:
                    import shutil
                    shutil.copy2(self.file_path, backup_path)
                except Exception as backup_error:
                    logger.debug(f"Backup creation failed: {backup_error}")

            # Write to temp file first (atomic write)
            temp_path = f"{self.file_path}.tmp"
            with open(temp_path, 'w', encoding='utf-8') as f:
                json.dump(self._state, f, indent=2)

            # Atomic rename
            os.replace(temp_path, self.file_path)

            self._last_write_time = time.time()
            self._write_count += 1

            logger.debug(
                f"State written: {self.file_path} "
                f"({len(self._state)} entries, write #{self._write_count})"
            )

        except Exception as e:
            self._error_count += 1
            logger.error(f"Failed to write state to {self.file_path}: {e}")

    def update(self, state_dict: Dict[str, Any]):
        """
        Update state (replaces entire state dict).

        Args:
            state_dict: New state dictionary
        """
        with self._lock:
            self._state = dict(state_dict)
            self._dirty = True

    def set(self, key: str, value: Any):
        """
        Set a single key in state.

        Args:
            key: State key
            value: Value to set
        """
        with self._lock:
            self._state[key] = value
            self._dirty = True

    def remove(self, key: str):
        """
        Remove a key from state.

        Args:
            key: Key to remove
        """
        with self._lock:
            self._state.pop(key, None)
            self._dirty = True

    def get(self) -> Dict[str, Any]:
        """
        Get current state (copy).

        Returns:
            Copy of current state dict
        """
        with self._lock:
            return dict(self._state)

    def flush(self):
        """Force immediate write to disk"""
        with self._lock:
            if self._dirty:
                self._write_state()
                self._dirty = False
                logger.debug("State flushed to disk")

    def shutdown(self):
        """
        Shutdown writer thread and flush final state.

        Ensures all pending writes are completed before returning.
        """
        logger.info(f"Shutting down state writer: {self.file_path}")

        # Signal shutdown
        self._shutdown_flag.set()

        # Wait for thread to complete
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=5.0)
            if self._thread.is_alive():
                logger.warning("State writer thread did not exit cleanly")

        # Final flush
        with self._lock:
            if self._dirty:
                self._write_state()

        logger.info(
            f"State writer shutdown complete: "
            f"writes={self._write_count}, errors={self._error_count}"
        )

    def get_statistics(self) -> Dict[str, Any]:
        """Get writer statistics"""
        with self._lock:
            return {
                "file_path": self.file_path,
                "interval_s": self.interval_s,
                "write_count": self._write_count,
                "error_count": self._error_count,
                "last_write_time": self._last_write_time,
                "dirty": self._dirty,
                "state_size": len(self._state),
                "thread_alive": self._thread.is_alive() if self._thread else False
            }


# Convenience function for simple use cases
def create_state_writer(
    file_path: str,
    interval_s: float = 10.0
) -> DebouncedStateWriter:
    """
    Create and start a debounced state writer.

    Args:
        file_path: Path to state file
        interval_s: Write interval in seconds

    Returns:
        DebouncedStateWriter instance
    """
    return DebouncedStateWriter(file_path, interval_s=interval_s, auto_start=True)
