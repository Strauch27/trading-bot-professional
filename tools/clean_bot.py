#!/usr/bin/env python3
"""
Trading Bot Complete Cleanup Script (Python)
Deletes all bot data (logs, sessions, states, anchors, snapshots) for fresh start
"""

import argparse
import glob
import os
import shutil
import sys
from pathlib import Path


def count_files(directory):
    """Count files in directory."""
    if not os.path.exists(directory):
        return 0
    return sum(1 for _ in Path(directory).rglob('*') if _.is_file())


def get_bot_root():
    """Get bot root directory."""
    script_dir = Path(__file__).parent
    bot_root = script_dir.parent
    return bot_root


def show_current_state(bot_root):
    """Show current state of bot data."""
    print("\n" + "=" * 50)
    print("Current State:")
    print("=" * 50)

    logs_count = count_files(bot_root / "logs")
    sessions_count = sum(1 for _ in (bot_root / "sessions").rglob('*') if _.is_file())
    state_dbs = len(list((bot_root / "state").glob("*.db*")))
    drop_windows = count_files(bot_root / "state" / "drop_windows")
    anchor_file = (bot_root / "drop_anchors.json").exists()
    fsm_snapshots = len(list((bot_root / "sessions").rglob("fsm_snapshots/*.json")))

    print(f"  Log files:        {logs_count}")
    print(f"  Session files:    {sessions_count}")
    print(f"  State DB files:   {state_dbs}")
    print(f"  Drop window files: {drop_windows}")
    print(f"  Anchor files:     {1 if anchor_file else 0}")
    print(f"  FSM snapshots:    {fsm_snapshots}")
    print()


def cleanup_logs(bot_root, verbose=False):
    """Clear all logs."""
    logs_dir = bot_root / "logs"
    if not logs_dir.exists():
        print("⚠️  logs directory not found")
        return 0

    count = count_files(logs_dir)

    # Remove all contents
    for item in logs_dir.iterdir():
        if item.is_file():
            item.unlink()
        elif item.is_dir():
            shutil.rmtree(item)

    if verbose:
        print(f"✅ Removed {count} log files")

    return count


def cleanup_sessions(bot_root, verbose=False):
    """Clear all sessions."""
    sessions_dir = bot_root / "sessions"
    if not sessions_dir.exists():
        print("⚠️  sessions directory not found")
        return 0

    count = 0

    # Remove all session_* directories
    for session_dir in sessions_dir.glob("session_*"):
        if session_dir.is_dir():
            shutil.rmtree(session_dir)
            count += 1

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

    if verbose:
        print(f"✅ Removed {count} session directories and cleared current session")

    return count


def cleanup_state(bot_root, verbose=False):
    """Clear state databases."""
    state_dir = bot_root / "state"
    if not state_dir.exists():
        print("⚠️  state directory not found")
        return 0

    count = 0

    # Remove all .db and .db-* files
    for db_file in state_dir.glob("*.db*"):
        db_file.unlink()
        count += 1

    if verbose:
        print(f"✅ Removed {count} state database files")

    return count


def cleanup_anchors(bot_root, verbose=False):
    """Clear drop_anchors.json."""
    anchor_file = bot_root / "drop_anchors.json"

    if anchor_file.exists():
        anchor_file.unlink()
        if verbose:
            print("✅ Removed drop_anchors.json")
        return 1
    else:
        if verbose:
            print("⚠️  drop_anchors.json not found, skipping")
        return 0


def cleanup_drop_windows(bot_root, verbose=False):
    """Clear all drop_windows data."""
    drop_windows_dir = bot_root / "state" / "drop_windows"

    if not drop_windows_dir.exists():
        print("⚠️  state/drop_windows directory not found")
        return 0

    count = count_files(drop_windows_dir)

    # Remove directory and recreate empty
    shutil.rmtree(drop_windows_dir)
    drop_windows_dir.mkdir(parents=True, exist_ok=True)

    if verbose:
        print(f"✅ Removed {count} drop window files (complete reset)")

    return count


def cleanup_python_cache(bot_root, verbose=False):
    """Clear Python cache."""
    count = 0

    # Find all __pycache__ directories
    for pycache_dir in bot_root.rglob("__pycache__"):
        if pycache_dir.is_dir():
            shutil.rmtree(pycache_dir)
            count += 1

    # Remove .pyc files
    for pyc_file in bot_root.rglob("*.pyc"):
        if pyc_file.is_file():
            pyc_file.unlink()

    if count > 0 and verbose:
        print(f"✅ Removed {count} Python cache directories")

    return count


def verify_cleanup(bot_root):
    """Verify cleanup was successful."""
    print("\n" + "=" * 50)
    print("Verification:")
    print("=" * 50)

    logs_count = count_files(bot_root / "logs")
    sessions_count = sum(1 for _ in (bot_root / "sessions").rglob('*') if _.is_file())
    state_dbs = len(list((bot_root / "state").glob("*.db*")))
    drop_windows = count_files(bot_root / "state" / "drop_windows")
    anchor_exists = (bot_root / "drop_anchors.json").exists()
    fsm_snapshots = len(list((bot_root / "sessions").rglob("fsm_snapshots/*.json")))

    print(f"  Log files:        {logs_count}")
    print(f"  Session files:    {sessions_count}")
    print(f"  State DBs:        {state_dbs}")
    print(f"  Drop windows:     {drop_windows}")
    print(f"  Anchors:          {'exists' if anchor_exists else 'removed'}")
    print(f"  FSM snapshots:    {fsm_snapshots}")
    print()

    # Check if cleanup was successful
    if (logs_count == 0 and sessions_count == 0 and
        state_dbs == 0 and drop_windows == 0 and not anchor_exists):
        print("✅ All bot data successfully deleted!")
        print()
        print("🚀 The bot is now in a completely clean state.")
        print("   All logs, sessions, states, and anchors have been removed.")
        return True
    else:
        print("⚠️  Warning: Some files still remain")
        print(f"   Logs: {logs_count} | Sessions: {sessions_count} | "
              f"State: {state_dbs} | Drops: {drop_windows}")
        return False


def main():
    parser = argparse.ArgumentParser(
        description="Trading Bot Complete Cleanup Script",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python3 tools/clean_bot.py              # Interactive mode
  python3 tools/clean_bot.py -y           # Auto-confirm
  python3 tools/clean_bot.py --dry-run    # Show what would be deleted
        """
    )

    parser.add_argument(
        "-y", "--yes",
        action="store_true",
        help="Auto-confirm deletion (skip confirmation prompt)"
    )

    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Show what would be deleted without actually deleting"
    )

    parser.add_argument(
        "-v", "--verbose",
        action="store_true",
        help="Verbose output"
    )

    args = parser.parse_args()

    # Get bot root
    bot_root = get_bot_root()

    print("=" * 50)
    print("  Trading Bot Complete Cleanup Script")
    print("=" * 50)
    print()

    # Show current state
    show_current_state(bot_root)

    # Confirm deletion
    if not args.yes and not args.dry_run:
        print("⚠️  This will delete ALL bot data:")
        print("   - All log files (*.log, *.jsonl)")
        print("   - All session data and FSM snapshots")
        print("   - All state databases (ledger.db, idempotency.db)")
        print("   - All drop windows, anchors, and ticks")
        print("   - Python cache (__pycache__)")
        print()

        response = input("Are you sure you want to delete ALL bot data? (yes/no): ")

        if response.lower() not in ["yes", "y"]:
            print("Aborted.")
            return 1
        print()

    if args.dry_run:
        print("🔍 DRY RUN - No files will be deleted")
        print()
        return 0

    # Perform cleanup
    print("Starting cleanup...")
    print()

    cleanup_logs(bot_root, args.verbose)
    cleanup_sessions(bot_root, args.verbose)
    cleanup_state(bot_root, args.verbose)
    cleanup_anchors(bot_root, args.verbose)
    cleanup_drop_windows(bot_root, args.verbose)
    cleanup_python_cache(bot_root, args.verbose)

    print()
    print("=" * 50)
    print("✅ Cleanup Complete!")
    print("=" * 50)
    print()

    # Verify
    success = verify_cleanup(bot_root)

    print()
    print("You can now start the bot with: python3 main.py")
    print()

    return 0 if success else 1


if __name__ == "__main__":
    sys.exit(main())
