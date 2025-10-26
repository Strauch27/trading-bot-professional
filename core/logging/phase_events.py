"""
Phase Event Logger

JSONL logger for FSM phase transitions.
Each phase change is written as a single JSON line for easy parsing and replay.
"""

import json
import logging
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)


class PhaseEventLogger:
    """
    Thread-safe JSONL logger for phase change events.

    Writes each event as a single JSON line to enable:
    - Easy grep/jq filtering
    - Sequential replay of state machine
    - Audit trail for debugging

    File format:
    {"ts_ms": 123, "event": "PHASE_CHANGE", "symbol": "BTC/USDT", ...}
    {"ts_ms": 456, "event": "PHASE_CHANGE", "symbol": "ETH/USDT", ...}
    """

    def __init__(self, log_file: Path, buffer_size: int = 8192):
        """
        Initialize Phase Event Logger.

        Args:
            log_file: Path to JSONL log file
            buffer_size: File buffer size (default: 8KB)
        """
        self.log_file = Path(log_file)
        self.buffer_size = buffer_size
        self._lock = threading.Lock()
        self._file_handle: Optional[Any] = None
        self._event_count = 0

        # Ensure directory exists
        self.log_file.parent.mkdir(parents=True, exist_ok=True)

        # Open file handle
        self._open()

        logger.info(f"PhaseEventLogger initialized: {self.log_file}")

    def _open(self):
        """Open file handle for writing."""
        try:
            self._file_handle = open(
                self.log_file,
                "a",
                encoding="utf-8",
                buffering=self.buffer_size
            )
            logger.debug(f"Opened phase event log: {self.log_file}")
        except Exception as e:
            logger.error(f"Failed to open phase event log: {e}")
            raise

    def log_phase_change(self, event: Dict[str, Any]):
        """
        Log phase change event as JSON line.

        Args:
            event: Event dict (from set_phase or create_event)
        """
        with self._lock:
            try:
                # Ensure ISO timestamp
                if "ts_iso" not in event:
                    event["ts_iso"] = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")

                # Ensure millisecond timestamp
                if "ts_ms" not in event:
                    event["ts_ms"] = int(datetime.now(timezone.utc).timestamp() * 1000)

                # Write JSON line
                json_line = json.dumps(event, ensure_ascii=False)
                self._file_handle.write(json_line + "\n")

                # Periodic flush (every 10 events)
                self._event_count += 1
                if self._event_count % 10 == 0:
                    self._file_handle.flush()

            except Exception as e:
                logger.error(f"Failed to log phase event: {e}")
                # Don't raise - logging errors shouldn't break trading

    def info(self, event: Dict[str, Any]):
        """
        Compatibility method for set_phase (expects log.info(evt)).

        Args:
            event: Event dict
        """
        self.log_phase_change(event)

    def flush(self):
        """Force flush pending writes to disk."""
        with self._lock:
            if self._file_handle:
                try:
                    self._file_handle.flush()
                except Exception as e:
                    logger.error(f"Failed to flush phase event log: {e}")

    def close(self):
        """Close file handle."""
        with self._lock:
            if self._file_handle:
                try:
                    self._file_handle.flush()
                    self._file_handle.close()
                    self._file_handle = None
                    logger.info(f"Closed phase event log: {self.log_file}")
                except Exception as e:
                    logger.error(f"Failed to close phase event log: {e}")

    def get_event_count(self) -> int:
        """Return total number of logged events."""
        return self._event_count

    def get_log_size_bytes(self) -> int:
        """Return log file size in bytes."""
        try:
            return self.log_file.stat().st_size
        except Exception:
            return 0

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.close()

    def __del__(self):
        """Cleanup on garbage collection."""
        try:
            self.close()
        except Exception:
            pass


class PhaseEventReader:
    """
    Reader for phase event JSONL files.

    Useful for:
    - Replaying state machine transitions
    - Debugging historical runs
    - Analyzing phase duration
    """

    def __init__(self, log_file: Path):
        """
        Initialize reader.

        Args:
            log_file: Path to JSONL log file
        """
        self.log_file = Path(log_file)

        if not self.log_file.exists():
            raise FileNotFoundError(f"Log file not found: {self.log_file}")

    def read_all_events(self) -> list[Dict[str, Any]]:
        """
        Read all events from log file.

        Returns:
            List of event dicts
        """
        events = []

        try:
            with open(self.log_file, "r", encoding="utf-8") as f:
                for line_num, line in enumerate(f, 1):
                    line = line.strip()
                    if not line:
                        continue

                    try:
                        event = json.loads(line)
                        events.append(event)
                    except json.JSONDecodeError as e:
                        logger.warning(f"Failed to parse line {line_num}: {e}")
                        continue

        except Exception as e:
            logger.error(f"Failed to read event log: {e}")
            raise

        return events

    def filter_events(
        self,
        symbol: Optional[str] = None,
        event_type: Optional[str] = None,
        phase: Optional[str] = None,
        after_ts_ms: Optional[int] = None,
        before_ts_ms: Optional[int] = None
    ) -> list[Dict[str, Any]]:
        """
        Filter events by criteria.

        Args:
            symbol: Filter by symbol
            event_type: Filter by event type
            phase: Filter by phase
            after_ts_ms: Filter events after timestamp (ms)
            before_ts_ms: Filter events before timestamp (ms)

        Returns:
            Filtered list of events
        """
        events = self.read_all_events()
        filtered = []

        for event in events:
            # Symbol filter
            if symbol and event.get("symbol") != symbol:
                continue

            # Event type filter
            if event_type and event.get("event") != event_type:
                continue

            # Phase filter (checks both "next" and "prev" fields)
            if phase:
                if event.get("next") != phase and event.get("prev") != phase:
                    continue

            # Timestamp filters
            ts = event.get("ts_ms", 0)
            if after_ts_ms and ts <= after_ts_ms:
                continue
            if before_ts_ms and ts >= before_ts_ms:
                continue

            filtered.append(event)

        return filtered

    def get_symbol_history(self, symbol: str) -> list[Dict[str, Any]]:
        """
        Get complete phase history for a symbol.

        Args:
            symbol: Trading symbol

        Returns:
            List of phase change events in chronological order
        """
        return self.filter_events(symbol=symbol, event_type="PHASE_CHANGE")

    def get_phase_durations(self, symbol: str) -> Dict[str, list[float]]:
        """
        Calculate time spent in each phase.

        Args:
            symbol: Trading symbol

        Returns:
            Dict mapping phase -> list of durations (seconds)
        """
        events = self.get_symbol_history(symbol)
        durations: Dict[str, list[float]] = {}

        for i in range(len(events) - 1):
            current = events[i]
            next_event = events[i + 1]

            phase = current.get("next")
            if not phase:
                continue

            ts_start = current.get("ts_ms", 0)
            ts_end = next_event.get("ts_ms", 0)

            if ts_start and ts_end:
                duration_s = (ts_end - ts_start) / 1000.0
                if phase not in durations:
                    durations[phase] = []
                durations[phase].append(duration_s)

        return durations
