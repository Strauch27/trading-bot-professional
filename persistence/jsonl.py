#!/usr/bin/env python3
"""
JSONL Writer with Rotation and Atomic Writes (V9_3 Phase 4)

Provides thread-safe JSONL persistence with:
- Atomic writes (.tmp + rename)
- Daily rotation (midnight UTC)
- Size-based rotation (configurable MB threshold)
- Concurrent write safety
- Auto-directory creation
"""

import json
import logging
from datetime import datetime
from pathlib import Path
from threading import RLock
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)


class RotatingJSONLWriter:
    """
    Thread-safe JSONL writer with rotation support.

    Features:
    - Atomic writes: Uses .tmp + rename pattern for crash safety
    - Daily rotation: Rotates at midnight UTC
    - Size rotation: Rotates when file exceeds max_mb threshold
    - Sequential naming: Files rotate as prefix_YYYYMMDD_001.jsonl, _002.jsonl, etc.
    - Thread-safe: RLock for concurrent writes
    """

    def __init__(
        self,
        base_dir: str,
        prefix: str,
        max_mb: int = 50,
        daily_rotation: bool = True
    ):
        """
        Initialize JSONL writer.

        Args:
            base_dir: Base directory for JSONL files
            prefix: File prefix (e.g., "ticks", "snapshots")
            max_mb: Maximum file size in MB before rotation (default: 50)
            daily_rotation: Enable daily rotation at midnight UTC (default: True)
        """
        self.base_dir = Path(base_dir)
        self.prefix = prefix
        self.max_bytes = max_mb * 1024 * 1024
        self.daily_rotation = daily_rotation

        self.current_file: Optional[Path] = None
        self.current_date: Optional[str] = None
        self.current_seq: int = 0

        self._lock = RLock()

        # Create base directory
        self.base_dir.mkdir(parents=True, exist_ok=True)

        logger.debug(f"RotatingJSONLWriter initialized: {self.base_dir}/{self.prefix}")

    def append(self, obj: Dict[str, Any]) -> bool:
        """
        Append object to JSONL file.

        Uses atomic write pattern:
        1. Write to .tmp file
        2. Append newline
        3. If file is new or doesn't exist, rename .tmp to target
        4. Otherwise append to existing file

        Args:
            obj: Dictionary to serialize as JSONL

        Returns:
            True if successful, False otherwise
        """
        with self._lock:
            try:
                # Check if rotation needed
                self._rotate_if_needed()

                # Ensure current file is set
                if not self.current_file:
                    self._set_current_file()

                # Serialize to JSON
                line = json.dumps(obj, separators=(',', ':')) + '\n'

                # Atomic write strategy:
                # - For new files: write to .tmp then rename
                # - For existing files: append directly (append is atomic on POSIX)
                if not self.current_file.exists():
                    # New file - use atomic rename
                    tmp_path = self.current_file.with_suffix('.jsonl.tmp')

                    with tmp_path.open('w', encoding='utf-8') as f:
                        f.write(line)

                    # Atomic rename
                    tmp_path.rename(self.current_file)
                else:
                    # Existing file - append directly
                    with self.current_file.open('a') as f:
                        f.write(line)

                return True

            except Exception as e:
                logger.error(f"Failed to append to {self.current_file}: {e}")
                return False

    def _rotate_if_needed(self) -> None:
        """
        Check if rotation is needed and rotate if necessary.

        Rotation triggers:
        1. Date change (if daily_rotation enabled)
        2. File size exceeds max_bytes
        """
        today = datetime.utcnow().strftime("%Y%m%d")

        # Daily rotation check
        if self.daily_rotation and self.current_date != today:
            self._set_current_file()
            logger.info(
                f"JSONL daily rotation: {self.prefix}_{today}.jsonl",
                extra={
                    'event_type': 'JSONL_DAILY_ROTATION',
                    'prefix': self.prefix,
                    'date': today,
                    'new_file': str(self.current_file)
                }
            )
            return

        # Size rotation check
        if self.current_file and self.current_file.exists():
            size_bytes = self.current_file.stat().st_size

            if size_bytes >= self.max_bytes:
                # Increment sequence number and rotate
                self.current_seq += 1
                self._set_current_file()

                logger.info(
                    f"JSONL size rotation: {self.prefix}_{today}_{self.current_seq:03d}.jsonl "
                    f"(size: {size_bytes / 1024 / 1024:.2f}MB)",
                    extra={
                        'event_type': 'JSONL_SIZE_ROTATION',
                        'prefix': self.prefix,
                        'size_mb': size_bytes / 1024 / 1024,
                        'sequence': self.current_seq,
                        'new_file': str(self.current_file)
                    }
                )

    def _set_current_file(self) -> None:
        """
        Set current file path based on date and sequence.

        File naming:
        - Without sequence: prefix_YYYYMMDD.jsonl
        - With sequence: prefix_YYYYMMDD_001.jsonl, prefix_YYYYMMDD_002.jsonl, etc.
        """
        today = datetime.utcnow().strftime("%Y%m%d")

        # Reset sequence on date change
        if self.current_date != today:
            self.current_date = today
            self.current_seq = 0

        # Find next available sequence number
        while True:
            if self.current_seq == 0:
                # Try base filename first
                candidate = self.base_dir / f"{self.prefix}_{today}.jsonl"
            else:
                # Use sequence number
                candidate = self.base_dir / f"{self.prefix}_{today}_{self.current_seq:03d}.jsonl"

            # Check if file exists and is under size limit
            if not candidate.exists():
                self.current_file = candidate
                break

            # File exists - check size
            size_bytes = candidate.stat().st_size
            if size_bytes < self.max_bytes:
                # File exists and has space - use it
                self.current_file = candidate
                break

            # File is full - try next sequence number
            self.current_seq += 1

    def get_current_file(self) -> Optional[Path]:
        """
        Get current output file path.

        Returns:
            Current file path or None if not set
        """
        with self._lock:
            return self.current_file

    def get_statistics(self) -> Dict[str, Any]:
        """
        Get writer statistics.

        Returns:
            Dict with current file info, size, and write count
        """
        with self._lock:
            stats = {
                "base_dir": str(self.base_dir),
                "prefix": self.prefix,
                "current_file": str(self.current_file) if self.current_file else None,
                "current_date": self.current_date,
                "current_seq": self.current_seq,
                "max_mb": self.max_bytes / 1024 / 1024,
                "daily_rotation": self.daily_rotation
            }

            if self.current_file and self.current_file.exists():
                size_bytes = self.current_file.stat().st_size
                stats["current_size_mb"] = size_bytes / 1024 / 1024
                stats["current_size_bytes"] = size_bytes

            return stats

    def close(self) -> None:
        """
        CRITICAL FIX (C-SERV-03): Explicit cleanup for resource management.

        While file handles are closed after each write via context managers,
        this method provides explicit cleanup hook for shutdown coordination
        and future enhancements (e.g., buffering).
        """
        with self._lock:
            # Clear current file reference
            self.current_file = None
            logger.debug(f"RotatingJSONLWriter closed: {self.prefix}")


class MultiStreamJSONLWriter:
    """
    Manager for multiple JSONL streams.

    Convenience wrapper for managing multiple RotatingJSONLWriter instances
    (e.g., ticks, snapshots, windows, anchors).
    """

    def __init__(self, base_dir: str, max_mb: int = 50, daily_rotation: bool = True):
        """
        Initialize multi-stream writer.

        Args:
            base_dir: Base directory for all streams
            max_mb: Maximum file size per stream in MB
            daily_rotation: Enable daily rotation
        """
        self.base_dir = Path(base_dir)
        self.max_mb = max_mb
        self.daily_rotation = daily_rotation

        self.writers: Dict[str, RotatingJSONLWriter] = {}
        self._lock = RLock()

        # Create base directory
        self.base_dir.mkdir(parents=True, exist_ok=True)

    def get_writer(self, stream_name: str) -> RotatingJSONLWriter:
        """
        Get or create writer for stream.

        Args:
            stream_name: Stream name (e.g., "ticks", "snapshots")

        Returns:
            RotatingJSONLWriter for the stream
        """
        with self._lock:
            if stream_name not in self.writers:
                stream_dir = self.base_dir / stream_name
                self.writers[stream_name] = RotatingJSONLWriter(
                    base_dir=str(stream_dir),
                    prefix=stream_name,
                    max_mb=self.max_mb,
                    daily_rotation=self.daily_rotation
                )

            return self.writers[stream_name]

    def write(self, stream_name: str, obj: Dict[str, Any]) -> bool:
        """
        Write object to stream.

        Args:
            stream_name: Stream name
            obj: Object to write

        Returns:
            True if successful
        """
        writer = self.get_writer(stream_name)
        return writer.append(obj)

    def get_statistics(self) -> Dict[str, Dict[str, Any]]:
        """
        Get statistics for all streams.

        Returns:
            Dict mapping stream names to their statistics
        """
        with self._lock:
            return {
                name: writer.get_statistics()
                for name, writer in self.writers.items()
            }

    def close_all(self) -> None:
        """
        CRITICAL FIX (C-SERV-03): Close all writers explicitly.

        Ensures proper cleanup of all managed streams during shutdown.
        """
        with self._lock:
            for name, writer in self.writers.items():
                try:
                    writer.close()
                except Exception as e:
                    logger.error(f"Failed to close writer {name}: {e}")
            self.writers.clear()
            logger.debug("MultiStreamJSONLWriter closed all streams")


# Utility function for reading JSONL files
def read_jsonl(file_path: str, limit: Optional[int] = None) -> list:
    """
    Read JSONL file and return list of objects.

    Args:
        file_path: Path to JSONL file
        limit: Maximum number of lines to read (default: all)

    Returns:
        List of parsed JSON objects
    """
    objects = []
    path = Path(file_path)

    if not path.exists():
        return objects

    try:
        with path.open('r') as f:
            for i, line in enumerate(f):
                if limit and i >= limit:
                    break

                line = line.strip()
                if not line:
                    continue

                try:
                    obj = json.loads(line)
                    objects.append(obj)
                except json.JSONDecodeError as e:
                    logger.warning(f"Failed to parse line {i + 1} in {file_path}: {e}")
                    continue

    except Exception as e:
        logger.error(f"Failed to read JSONL file {file_path}: {e}")

    return objects


def read_jsonl_tail(file_path: str, n: int = 300) -> list:
    """
    Read last N lines from JSONL file (for warm-start).

    Args:
        file_path: Path to JSONL file
        n: Number of lines to read from end

    Returns:
        List of last N parsed JSON objects
    """
    path = Path(file_path)

    if not path.exists():
        return []

    try:
        # Read all lines (for small files) or use deque for tail
        from collections import deque

        with path.open('r') as f:
            # Use deque with maxlen for efficient tail reading
            tail_lines = deque(f, maxlen=n)

        # Parse JSON objects
        objects = []
        for i, line in enumerate(tail_lines):
            line = line.strip()
            if not line:
                continue

            try:
                obj = json.loads(line)
                objects.append(obj)
            except json.JSONDecodeError as e:
                logger.warning(f"Failed to parse tail line {i + 1}: {e}")
                continue

        return objects

    except Exception as e:
        logger.error(f"Failed to read JSONL tail from {file_path}: {e}")
        return []
