#!/usr/bin/env python3
"""
Trading Bot - Simple Clean All Script
Deletes all bot data without prompts or options
"""

import shutil
import sys
from pathlib import Path


def main():
    # Get bot root directory
    script_dir = Path(__file__).parent
    bot_root = script_dir.parent

    print("=" * 50)
    print("  Cleaning All Bot Data...")
    print("=" * 50)
    print()

    removed_counts = {
        "logs": 0,
        "sessions": 0,
        "state_dbs": 0,
        "anchors": 0,
        "drop_windows": 0,
        "cache": 0
    }

    # 1. Clear all logs
    logs_dir = bot_root / "logs"
    if logs_dir.exists():
        for item in logs_dir.iterdir():
            if item.is_file():
                item.unlink()
                removed_counts["logs"] += 1
            elif item.is_dir():
                shutil.rmtree(item)
                removed_counts["logs"] += 1

    # 2. Clear all sessions
    sessions_dir = bot_root / "sessions"
    if sessions_dir.exists():
        # Remove session_* directories
        for session_dir in sessions_dir.glob("session_*"):
            if session_dir.is_dir():
                shutil.rmtree(session_dir)
                removed_counts["sessions"] += 1

        # Clear current session
        current_dir = sessions_dir / "current"
        if current_dir.exists():
            for item in current_dir.iterdir():
                if item.is_file():
                    item.unlink()
                elif item.is_dir():
                    shutil.rmtree(item)

        # Remove .DS_Store
        ds_store = sessions_dir / ".DS_Store"
        if ds_store.exists():
            ds_store.unlink()

    # 3. Clear state databases
    state_dir = bot_root / "state"
    if state_dir.exists():
        for db_file in state_dir.glob("*.db*"):
            db_file.unlink()
            removed_counts["state_dbs"] += 1

    # 4. Clear drop_anchors.json
    anchor_file = bot_root / "drop_anchors.json"
    if anchor_file.exists():
        anchor_file.unlink()
        removed_counts["anchors"] = 1

    # 5. Clear drop_windows completely
    drop_windows_dir = bot_root / "state" / "drop_windows"
    if drop_windows_dir.exists():
        # Count files before removal
        removed_counts["drop_windows"] = sum(1 for _ in drop_windows_dir.rglob('*') if _.is_file())
        shutil.rmtree(drop_windows_dir)
        drop_windows_dir.mkdir(parents=True, exist_ok=True)

    # 6. Clear Python cache
    for pycache_dir in bot_root.rglob("__pycache__"):
        if pycache_dir.is_dir():
            shutil.rmtree(pycache_dir)
            removed_counts["cache"] += 1

    for pyc_file in bot_root.rglob("*.pyc"):
        if pyc_file.is_file():
            pyc_file.unlink()

    # Summary
    print(f"✅ Removed {removed_counts['logs']} log items")
    print(f"✅ Removed {removed_counts['sessions']} session directories")
    print(f"✅ Removed {removed_counts['state_dbs']} state database files")
    print(f"✅ Removed {removed_counts['anchors']} anchor file")
    print(f"✅ Removed {removed_counts['drop_windows']} drop window files")
    print(f"✅ Removed {removed_counts['cache']} Python cache directories")
    print()
    print("=" * 50)
    print("✅ Cleanup Complete!")
    print("=" * 50)
    print()
    print("🚀 Bot is clean. Ready to start with: python3 main.py")
    print()

    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as e:
        print(f"❌ Error during cleanup: {e}")
        sys.exit(1)
