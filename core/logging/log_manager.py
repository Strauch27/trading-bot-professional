#!/usr/bin/env python3
"""
Log Management System for Trading Bot

Handles log rotation, compression, and cleanup for the adaptive logging system.
Features:
- Automatic rotation based on size/time
- Gzip compression for archived logs
- Retention policy with automatic cleanup
- Session-based organization
- Critical event preservation
"""

import gzip
import json
import logging
import os
import shutil
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional

try:
    from config import DEBUG_ENABLE_LOG_COMPRESSION, DEBUG_LOG_RETENTION_DAYS, LOG_DIR, SESSION_DIR, run_timestamp
except ImportError:
    # Fallback defaults
    LOG_DIR = "logs"
    DEBUG_ENABLE_LOG_COMPRESSION = True
    DEBUG_LOG_RETENTION_DAYS = 30
    SESSION_DIR = "."
    run_timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")


class LogManager:
    """
    Manages log file rotation, compression, and cleanup
    """

    def __init__(self, base_log_dir: str = None):
        self.base_log_dir = base_log_dir or LOG_DIR
        self.compression_enabled = DEBUG_ENABLE_LOG_COMPRESSION
        self.retention_days = DEBUG_LOG_RETENTION_DAYS
        self.current_session = run_timestamp

        # Rotation thresholds
        self.max_file_size_mb = 50  # Rotate when file exceeds 50MB
        self.max_age_hours = 6      # Rotate every 6 hours

        # Critical event patterns - never compressed or deleted
        self.critical_patterns = [
            "TRADE_OPEN", "TRADE_CLOSE", "ORDER_FILLED", "ORDER_PLACED",
            "BUY_TRIGGERED", "SELL_TRIGGERED", "ERROR", "WARNING",
            "CONFIG_CHANGE", "ENGINE_START", "ENGINE_STOP",
            "SESSION_START", "SESSION_END", "DECISION_START", "DECISION_END"
        ]

        # Thread safety
        self._lock = threading.RLock()
        self._rotation_thread = None
        self._running = False

        self.logger = logging.getLogger(__name__)

    def start(self):
        """Start the log manager background tasks"""
        with self._lock:
            if self._running:
                return

            self._running = True
            # Ensure base log directory exists and is valid
            try:
                if isinstance(self.base_log_dir, (str, os.PathLike)) and self.base_log_dir:
                    Path(self.base_log_dir).mkdir(parents=True, exist_ok=True)
                else:
                    # Fallback to LOG_DIR
                    self.base_log_dir = LOG_DIR
                    Path(self.base_log_dir).mkdir(parents=True, exist_ok=True)
            except Exception as e:
                self.logger.warning(f"Could not ensure base log dir: {e}")
            self._rotation_thread = threading.Thread(target=self._rotation_worker, daemon=True)
            self._rotation_thread.start()

            self.logger.info("Log manager started")

    def stop(self):
        """Stop the log manager background tasks"""
        with self._lock:
            self._running = False

            if self._rotation_thread:
                self._rotation_thread.join(timeout=5.0)

            self.logger.info("Log manager stopped")

    def _rotation_worker(self):
        """Background worker for log rotation and cleanup"""
        while self._running:
            try:
                self._check_rotation_needed()
                self._cleanup_old_logs()
                time.sleep(300)  # Check every 5 minutes

            except Exception as e:
                self.logger.error(f"Log rotation worker error: {e}")
                time.sleep(60)  # Wait 1 minute before retrying

    def _check_rotation_needed(self):
        """Check if any log files need rotation"""
        session_log_dir = self.base_log_dir if isinstance(self.base_log_dir, (str, os.PathLike)) else None
        if not session_log_dir or not os.path.exists(session_log_dir):
            return

        for filename in os.listdir(session_log_dir):
            if not filename.endswith('.jsonl'):
                continue

            filepath = os.path.join(session_log_dir, filename)
            if self._should_rotate(filepath):
                self._rotate_file(filepath)

    def _should_rotate(self, filepath: str) -> bool:
        """Check if a file should be rotated"""
        try:
            stat = os.stat(filepath)

            # Check file size
            size_mb = stat.st_size / (1024 * 1024)
            if size_mb > self.max_file_size_mb:
                return True

            # Check file age
            age_hours = (time.time() - stat.st_mtime) / 3600
            if age_hours > self.max_age_hours:
                return True

            return False

        except OSError:
            return False

    def _rotate_file(self, filepath: str):
        """Rotate a log file"""
        try:
            with self._lock:
                if not os.path.exists(filepath):
                    return

                # Generate rotated filename
                timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
                base_name = os.path.splitext(os.path.basename(filepath))[0]
                rotated_name = f"{base_name}_{timestamp}.jsonl"
                rotated_path = os.path.join(os.path.dirname(filepath), rotated_name)

                # Move current file to rotated name
                shutil.move(filepath, rotated_path)

                # Compress if enabled
                if self.compression_enabled:
                    compressed_path = self._compress_file(rotated_path)
                    if compressed_path:
                        os.remove(rotated_path)  # Remove uncompressed version
                        self.logger.info(f"Log rotated and compressed: {filepath} -> {compressed_path}")
                    else:
                        self.logger.warning(f"Log rotated but compression failed: {filepath} -> {rotated_path}")
                else:
                    self.logger.info(f"Log rotated: {filepath} -> {rotated_path}")

        except Exception as e:
            self.logger.error(f"Log rotation failed for {filepath}: {e}")

    def _compress_file(self, filepath: str) -> Optional[str]:
        """Compress a log file using gzip"""
        try:
            compressed_path = f"{filepath}.gz"

            with open(filepath, 'rb') as f_in:
                with gzip.open(compressed_path, 'wb') as f_out:
                    shutil.copyfileobj(f_in, f_out)

            return compressed_path

        except Exception as e:
            self.logger.error(f"Log compression failed for {filepath}: {e}")
            return None

    def _cleanup_old_logs(self):
        """Clean up old log files based on retention policy"""
        try:
            # FIX: Check if base_log_dir is valid before cleanup
            if not self.base_log_dir or not isinstance(self.base_log_dir, (str, os.PathLike)):
                self.logger.debug("Log cleanup skipped: base_log_dir not initialized")
                return

            cutoff_time = time.time() - (self.retention_days * 24 * 3600)

            # Find all session directories
            base_dir = os.path.dirname(self.base_log_dir)
            if not base_dir or not os.path.exists(base_dir):
                self.logger.debug(f"Log cleanup skipped: base directory {base_dir} does not exist")
                return

            for item in os.listdir(base_dir):
                if not item.startswith('session_'):
                    continue

                session_dir = os.path.join(base_dir, item)
                if not os.path.isdir(session_dir):
                    continue

                session_log_dir = os.path.join(session_dir, 'logs')
                if not os.path.exists(session_log_dir):
                    continue

                self._cleanup_session_logs(session_log_dir, cutoff_time)

        except Exception as e:
            self.logger.error(f"Log cleanup error: {e}")

    def _cleanup_session_logs(self, session_log_dir: str, cutoff_time: float):
        """Clean up logs in a specific session directory"""
        preserved_files = []
        deleted_files = []

        for filename in os.listdir(session_log_dir):
            filepath = os.path.join(session_log_dir, filename)

            try:
                stat = os.stat(filepath)
                if stat.st_mtime > cutoff_time:
                    continue  # File is still within retention period

                # Check if file contains critical events
                if self._contains_critical_events(filepath):
                    preserved_files.append(filename)
                    continue

                # Delete old non-critical files
                os.remove(filepath)
                deleted_files.append(filename)

            except OSError as e:
                self.logger.error(f"Error processing {filepath}: {e}")

        if deleted_files:
            self.logger.info(f"Cleaned up {len(deleted_files)} old log files from {session_log_dir}")

        if preserved_files:
            self.logger.info(f"Preserved {len(preserved_files)} files with critical events in {session_log_dir}")

    def _contains_critical_events(self, filepath: str) -> bool:
        """Check if a log file contains critical events that should be preserved"""
        try:
            # For compressed files
            if filepath.endswith('.gz'):
                with gzip.open(filepath, 'rt', encoding='utf-8') as f:
                    return self._scan_file_for_critical_events(f)
            else:
                with open(filepath, 'r', encoding='utf-8') as f:
                    return self._scan_file_for_critical_events(f)

        except Exception as e:
            self.logger.error(f"Error scanning {filepath} for critical events: {e}")
            return True  # Preserve on error

    def _scan_file_for_critical_events(self, file_handle) -> bool:
        """Scan file content for critical event patterns"""
        lines_checked = 0
        max_lines_to_check = 1000  # Don't scan entire large files

        for line in file_handle:
            if lines_checked >= max_lines_to_check:
                break

            lines_checked += 1

            try:
                # Try to parse as JSON
                if line.strip():
                    data = json.loads(line)
                    event_type = data.get('event_type', '')
                    message = data.get('message', '')

                    # Check for critical patterns
                    for pattern in self.critical_patterns:
                        if pattern in event_type or pattern in message:
                            return True

            except (json.JSONDecodeError, KeyError):
                # For non-JSON lines, do simple string search
                for pattern in self.critical_patterns:
                    if pattern in line:
                        return True

        return False

    def force_rotation(self, log_type: str = None):
        """Force rotation of specific log type or all logs"""
        try:
            session_log_dir = os.path.join(self.base_log_dir)

            if not os.path.exists(session_log_dir):
                return

            for filename in os.listdir(session_log_dir):
                if not filename.endswith('.jsonl'):
                    continue

                if log_type and log_type not in filename:
                    continue

                filepath = os.path.join(session_log_dir, filename)
                self._rotate_file(filepath)

            self.logger.info(f"Forced rotation completed for {log_type or 'all logs'}")

        except Exception as e:
            self.logger.error(f"Force rotation failed: {e}")

    def get_log_statistics(self) -> Dict[str, any]:
        """Get statistics about log files"""
        try:
            stats = {
                'current_session': self.current_session,
                'files': {},
                'total_size_mb': 0,
                'compressed_files': 0,
                'uncompressed_files': 0
            }

            session_log_dir = os.path.join(self.base_log_dir)
            if not os.path.exists(session_log_dir):
                return stats

            for filename in os.listdir(session_log_dir):
                filepath = os.path.join(session_log_dir, filename)

                try:
                    stat = os.stat(filepath)
                    size_mb = stat.st_size / (1024 * 1024)

                    stats['files'][filename] = {
                        'size_mb': round(size_mb, 2),
                        'modified': datetime.fromtimestamp(stat.st_mtime).isoformat(),
                        'compressed': filename.endswith('.gz')
                    }

                    stats['total_size_mb'] += size_mb

                    if filename.endswith('.gz'):
                        stats['compressed_files'] += 1
                    else:
                        stats['uncompressed_files'] += 1

                except OSError:
                    continue

            stats['total_size_mb'] = round(stats['total_size_mb'], 2)
            return stats

        except Exception as e:
            self.logger.error(f"Error getting log statistics: {e}")
            return {'error': str(e)}

    def extract_critical_events(self, output_file: str, days_back: int = 7) -> int:
        """Extract critical events from all logs into a summary file"""
        try:
            cutoff_time = time.time() - (days_back * 24 * 3600)
            critical_events = []

            # Scan all session directories
            base_dir = os.path.dirname(self.base_log_dir)
            for item in os.listdir(base_dir):
                if not item.startswith('session_'):
                    continue

                session_dir = os.path.join(base_dir, item)
                session_log_dir = os.path.join(session_dir, 'logs')

                if not os.path.exists(session_log_dir):
                    continue

                for filename in os.listdir(session_log_dir):
                    filepath = os.path.join(session_log_dir, filename)

                    try:
                        stat = os.stat(filepath)
                        if stat.st_mtime < cutoff_time:
                            continue

                        events = self._extract_critical_events_from_file(filepath)
                        critical_events.extend(events)

                    except OSError:
                        continue

            # Sort events by timestamp
            critical_events.sort(key=lambda x: x.get('timestamp', x.get('ts', 0)))

            # Write to output file
            with open(output_file, 'w', encoding='utf-8') as f:
                for event in critical_events:
                    f.write(json.dumps(event, ensure_ascii=False) + '\n')

            self.logger.info(f"Extracted {len(critical_events)} critical events to {output_file}")
            return len(critical_events)

        except Exception as e:
            self.logger.error(f"Critical events extraction failed: {e}")
            return 0

    def _extract_critical_events_from_file(self, filepath: str) -> List[Dict]:
        """Extract critical events from a single log file"""
        events = []

        try:
            # Handle compressed files
            if filepath.endswith('.gz'):
                with gzip.open(filepath, 'rt', encoding='utf-8') as f:
                    events = self._parse_critical_events(f)
            else:
                with open(filepath, 'r', encoding='utf-8') as f:
                    events = self._parse_critical_events(f)

        except Exception as e:
            self.logger.error(f"Error extracting events from {filepath}: {e}")

        return events

    def _parse_critical_events(self, file_handle) -> List[Dict]:
        """Parse critical events from file content"""
        events = []

        for line_num, line in enumerate(file_handle, 1):
            try:
                if not line.strip():
                    continue

                data = json.loads(line)
                event_type = data.get('event_type', '')
                message = data.get('message', '')

                # Check for critical patterns
                is_critical = False
                for pattern in self.critical_patterns:
                    if pattern in event_type or pattern in message:
                        is_critical = True
                        break

                if is_critical:
                    # Add source information
                    data['_source_line'] = line_num
                    events.append(data)

            except json.JSONDecodeError:
                # Skip malformed JSON
                continue

        return events


# Global log manager instance
_log_manager = None


def get_log_manager() -> LogManager:
    """Get the global log manager instance"""
    global _log_manager
    if _log_manager is None:
        _log_manager = LogManager()
    return _log_manager


def start_log_management():
    """Start log management background tasks"""
    get_log_manager().start()


def stop_log_management():
    """Stop log management background tasks"""
    if _log_manager:
        _log_manager.stop()


def force_log_rotation(log_type: str = None):
    """Force rotation of logs"""
    get_log_manager().force_rotation(log_type)


def get_log_stats() -> Dict[str, any]:
    """Get log statistics"""
    return get_log_manager().get_log_statistics()


def extract_critical_events_summary(output_file: str, days_back: int = 7) -> int:
    """Extract critical events summary"""
    return get_log_manager().extract_critical_events(output_file, days_back)
