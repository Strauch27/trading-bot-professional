#!/usr/bin/env python3
"""
Log Retention Manager

Automated cleanup of old log files based on retention policies.
Helps maintain disk space and comply with data retention requirements.

Features:
- Configurable retention periods per log type
- Automatic compression of old logs
- Safe deletion with audit trail
- Scheduled daily cleanup
- Manual cleanup on demand

Usage:
    from core.log_retention import LogRetentionManager

    # Configure retention policies (in days)
    retention_manager = LogRetentionManager({
        'decisions': 365,  # 1 year
        'orders': 365,     # 1 year
        'tracer': 60,      # 2 months
        'audit': 730,      # 2 years
        'health': 30       # 1 month
    })

    # Run cleanup
    removed, freed_mb = retention_manager.cleanup_old_logs()
    print(f"Removed {removed} files, freed {freed_mb:.1f}MB")
"""

import os
import time
import gzip
import shutil
import logging
from pathlib import Path
from typing import Dict, Tuple
from datetime import datetime, timezone

logger = logging.getLogger(__name__)


class LogRetentionManager:
    """
    Manage log file retention policies and automated cleanup.

    Retention policies are specified in days per log type.
    Files older than the policy are automatically deleted.
    """

    def __init__(self, retention_days: Dict[str, int], compress_before_delete: bool = False):
        """
        Initialize retention manager.

        Args:
            retention_days: Dict mapping log type to retention days
                           e.g., {'decisions': 365, 'orders': 365, 'tracer': 60}
            compress_before_delete: If True, compress old logs instead of deleting
        """
        self.retention_days = retention_days
        self.compress_before_delete = compress_before_delete
        self.last_cleanup = None
        self.cleanup_stats = {
            'total_cleanups': 0,
            'total_files_removed': 0,
            'total_mb_freed': 0.0
        }

        logger.info(
            f"Log retention manager initialized with policies: {retention_days}"
        )

    def cleanup_old_logs(
        self,
        sessions_dir: Path = Path("sessions"),
        dry_run: bool = False
    ) -> Tuple[int, float]:
        """
        Remove log files older than retention policy.

        Args:
            sessions_dir: Root directory containing session subdirectories
            dry_run: If True, only report what would be deleted without actually deleting

        Returns:
            (files_removed, mb_freed) tuple
        """
        now = time.time()
        total_removed = 0
        total_size_mb = 0.0

        if not sessions_dir.exists():
            logger.warning(f"Sessions directory not found: {sessions_dir}")
            return 0, 0.0

        logger.info(
            f"Starting log cleanup (dry_run={dry_run}) in {sessions_dir}"
        )

        # Iterate through all session directories
        for session_dir in sorted(sessions_dir.iterdir()):
            if not session_dir.is_dir():
                continue

            logs_dir = session_dir / "logs"
            if not logs_dir.exists():
                continue

            # Check each log type against its retention policy
            for log_type, retention_days in self.retention_days.items():
                log_subdir = logs_dir / log_type
                if not log_subdir.exists():
                    continue

                cutoff_timestamp = now - (retention_days * 86400)  # Convert days to seconds

                # Find and process old log files
                for log_file in log_subdir.glob("*.jsonl*"):
                    try:
                        file_stat = log_file.stat()
                        file_age_seconds = now - file_stat.st_mtime
                        file_age_days = file_age_seconds / 86400

                        # Check if file exceeds retention period
                        if file_stat.st_mtime < cutoff_timestamp:
                            file_size_mb = file_stat.st_size / (1024 * 1024)

                            if dry_run:
                                logger.info(
                                    f"[DRY RUN] Would remove: {log_file.relative_to(sessions_dir)} "
                                    f"(age: {file_age_days:.1f}d, size: {file_size_mb:.2f}MB)"
                                )
                            else:
                                # Compress before delete if enabled
                                if self.compress_before_delete and not str(log_file).endswith('.gz'):
                                    compressed_path = self._compress_file(log_file)
                                    if compressed_path:
                                        logger.info(
                                            f"Compressed old log: {log_file.name} → {compressed_path.name}"
                                        )
                                        # Don't delete after compression, just compress
                                        continue

                                # Delete the file
                                log_file.unlink()
                                total_removed += 1
                                total_size_mb += file_size_mb

                                logger.info(
                                    f"Removed old log: {log_file.relative_to(sessions_dir)} "
                                    f"(age: {file_age_days:.1f}d, size: {file_size_mb:.2f}MB, "
                                    f"retention: {retention_days}d)"
                                )

                    except Exception as e:
                        logger.error(f"Failed to process {log_file}: {e}")

        # Update stats
        if not dry_run:
            self.cleanup_stats['total_cleanups'] += 1
            self.cleanup_stats['total_files_removed'] += total_removed
            self.cleanup_stats['total_mb_freed'] += total_size_mb
            self.last_cleanup = datetime.now(timezone.utc)

        if total_removed > 0 or dry_run:
            action = "Would remove" if dry_run else "Removed"
            logger.info(
                f"Log cleanup complete: {action} {total_removed} files, "
                f"freed {total_size_mb:.2f}MB"
            )

        return total_removed, total_size_mb

    def _compress_file(self, file_path: Path) -> Path:
        """
        Compress log file using gzip.

        Args:
            file_path: Path to file to compress

        Returns:
            Path to compressed file (.gz) or None if failed
        """
        try:
            compressed_path = file_path.with_suffix(file_path.suffix + '.gz')

            with open(file_path, 'rb') as f_in:
                with gzip.open(compressed_path, 'wb') as f_out:
                    shutil.copyfileobj(f_in, f_out)

            # Delete original after successful compression
            file_path.unlink()

            return compressed_path

        except Exception as e:
            logger.error(f"Failed to compress {file_path}: {e}")
            return None

    def get_storage_stats(self, sessions_dir: Path = Path("sessions")) -> Dict:
        """
        Get current storage statistics for logs.

        Args:
            sessions_dir: Root directory containing session subdirectories

        Returns:
            Dict with storage stats per log type
        """
        stats = {}

        if not sessions_dir.exists():
            return stats

        for session_dir in sessions_dir.iterdir():
            if not session_dir.is_dir():
                continue

            logs_dir = session_dir / "logs"
            if not logs_dir.exists():
                continue

            for log_type in self.retention_days.keys():
                log_subdir = logs_dir / log_type
                if not log_subdir.exists():
                    continue

                if log_type not in stats:
                    stats[log_type] = {
                        'files': 0,
                        'total_mb': 0.0,
                        'oldest_days': 0,
                        'newest_days': 0
                    }

                now = time.time()
                oldest_age = 0
                newest_age = float('inf')

                for log_file in log_subdir.glob("*.jsonl*"):
                    try:
                        file_stat = log_file.stat()
                        file_age_days = (now - file_stat.st_mtime) / 86400

                        stats[log_type]['files'] += 1
                        stats[log_type]['total_mb'] += file_stat.st_size / (1024 * 1024)

                        oldest_age = max(oldest_age, file_age_days)
                        newest_age = min(newest_age, file_age_days)

                    except Exception:
                        continue

                stats[log_type]['oldest_days'] = oldest_age
                stats[log_type]['newest_days'] = newest_age if newest_age != float('inf') else 0

        return stats

    def get_cleanup_stats(self) -> Dict:
        """Get retention manager statistics"""
        return {
            **self.cleanup_stats,
            'last_cleanup': self.last_cleanup.isoformat() if self.last_cleanup else None,
            'retention_policies': self.retention_days
        }


def schedule_log_cleanup(retention_manager: LogRetentionManager, interval_hours: int = 24):
    """
    Schedule periodic log cleanup in background thread.

    Args:
        retention_manager: LogRetentionManager instance
        interval_hours: Cleanup interval in hours (default: 24 = daily)
    """
    import threading

    def cleanup_task():
        logger.info(
            f"Log cleanup scheduler started (interval: {interval_hours}h)"
        )

        while True:
            time.sleep(interval_hours * 3600)  # Convert hours to seconds

            try:
                removed, freed_mb = retention_manager.cleanup_old_logs()

                if removed > 0:
                    logger.info(
                        f"Scheduled log cleanup: removed {removed} files, "
                        f"freed {freed_mb:.2f}MB"
                    )

            except Exception as e:
                logger.error(f"Scheduled log cleanup failed: {e}", exc_info=True)

    cleanup_thread = threading.Thread(
        target=cleanup_task,
        daemon=True,
        name="LogCleanupScheduler"
    )
    cleanup_thread.start()

    logger.info("Log cleanup scheduler thread started")

    return cleanup_thread


# Default retention policies (in days)
DEFAULT_RETENTION_POLICIES = {
    'decisions': 365,  # 1 year - important for trade analysis
    'orders': 365,     # 1 year - important for order analytics
    'tracer': 60,      # 2 months - verbose exchange API logs
    'audit': 730,      # 2 years - compliance and ledger
    'health': 30       # 1 month - operational metrics
}
