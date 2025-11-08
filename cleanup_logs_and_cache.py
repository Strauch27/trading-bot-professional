#!/usr/bin/env python3
"""
Trading Bot - Cleanup: Logs + Cache
====================================

Löscht Logs und Python Cache, behält alles andere.

Sicher für: Tägliche/Wöchentliche Verwendung
Löscht: Logs, __pycache__, *.pyc
Behält: State, Sessions, Anchors
"""

import shutil
import sys
from pathlib import Path


def cleanup_logs_and_cache():
    """Clear logs and Python cache."""
    bot_root = Path(__file__).parent.absolute()

    print("=" * 60)
    print("  Trading Bot - Cleanup Logs + Cache")
    print("=" * 60)
    print()

    # Count files
    logs_dir = bot_root / "logs"
    log_count = sum(1 for _ in logs_dir.rglob('*') if _.is_file()) if logs_dir.exists() else 0
    cache_count = sum(1 for _ in bot_root.rglob("__pycache__") if _.is_dir())

    print(f"Found {log_count} log files")
    print(f"Found {cache_count} cache directories")
    print()

    # Confirm
    response = input("Delete logs and cache? (yes/no): ")
    if response.lower() not in ["yes", "y"]:
        print("Aborted.")
        return 1

    print()
    print("Cleaning up...")

    # Remove logs
    logs_removed = 0
    if logs_dir.exists():
        for item in logs_dir.iterdir():
            if item.is_file():
                item.unlink()
                logs_removed += 1
            elif item.is_dir():
                shutil.rmtree(item)
                logs_removed += 1

    # Remove Python cache
    cache_removed = 0
    for pycache_dir in bot_root.rglob("__pycache__"):
        if pycache_dir.is_dir():
            shutil.rmtree(pycache_dir)
            cache_removed += 1

    for pyc_file in bot_root.rglob("*.pyc"):
        if pyc_file.is_file():
            pyc_file.unlink()

    print()
    print(f"✅ Removed {logs_removed} log items")
    print(f"✅ Removed {cache_removed} cache directories")
    print()
    print("Cleanup complete. State and sessions preserved.")
    print()

    return 0


if __name__ == "__main__":
    try:
        sys.exit(cleanup_logs_and_cache())
    except KeyboardInterrupt:
        print("\n\nAborted by user.")
        sys.exit(1)
    except Exception as e:
        print(f"❌ Error: {e}")
        sys.exit(1)
