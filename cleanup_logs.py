#!/usr/bin/env python3
"""
Trading Bot - Cleanup: Logs Only
=================================

Löscht nur Log-Dateien, behält alles andere.

Sicher für: Tägliche Verwendung
Löscht: Logs
Behält: State, Sessions, Anchors, Cache
"""

import shutil
import sys
from pathlib import Path


def cleanup_logs():
    """Clear all log files."""
    bot_root = Path(__file__).parent.absolute()
    logs_dir = bot_root / "logs"

    print("=" * 60)
    print("  Trading Bot - Cleanup Logs")
    print("=" * 60)
    print()

    if not logs_dir.exists():
        print("⚠️  logs directory not found")
        return 0

    # Count files
    count = sum(1 for _ in logs_dir.rglob('*') if _.is_file())

    print(f"Found {count} log files")
    print()

    # Confirm
    response = input("Delete all log files? (yes/no): ")
    if response.lower() not in ["yes", "y"]:
        print("Aborted.")
        return 1

    print()
    print("Deleting logs...")

    # Remove all contents
    removed = 0
    for item in logs_dir.iterdir():
        if item.is_file():
            item.unlink()
            removed += 1
        elif item.is_dir():
            shutil.rmtree(item)
            removed += 1

    print()
    print(f"✅ Removed {removed} log items")
    print()
    print("Logs cleared. State and sessions preserved.")
    print()

    return 0


if __name__ == "__main__":
    try:
        sys.exit(cleanup_logs())
    except KeyboardInterrupt:
        print("\n\nAborted by user.")
        sys.exit(1)
    except Exception as e:
        print(f"❌ Error: {e}")
        sys.exit(1)
