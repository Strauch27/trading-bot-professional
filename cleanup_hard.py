#!/usr/bin/env python3
"""
Trading Bot - Hard Reset
=========================

Vollständiger Bot-Reset für kompletten Neustart.

Löscht:
✗ Logs (alle Log-Dateien)
✗ Python Cache
✗ Sessions (FSM Snapshots, historische Daten)
✗ State (Ledger DB, Idempotency DB)
✗ Anchors & Drop Windows (komplett)
✗ Stuck Orders

Optional:
⚠ Portfolio Reset (verkauft alle Assets außer USDT)

Verwendung:
  python3 cleanup_hard.py                    # Interactive mode
  python3 cleanup_hard.py -y                 # Auto-confirm (DANGEROUS!)
  python3 cleanup_hard.py --portfolio-reset  # Include portfolio reset
  python3 cleanup_hard.py --dry-run          # Preview what would be deleted
"""

import argparse
import json
import shutil
import sys
import time
from pathlib import Path


def count_files(directory):
    """Count files in directory."""
    if not isinstance(directory, Path):
        directory = Path(directory)
    if not directory.exists():
        return 0
    return sum(1 for _ in directory.rglob('*') if _.is_file())


def cleanup_logs(bot_root, verbose=False):
    """Clear all logs."""
    logs_dir = bot_root / "logs"
    if not logs_dir.exists():
        if verbose:
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
        if verbose:
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
        if verbose:
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
        if verbose:
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


def cleanup_stuck_orders(bot_root, verbose=False):
    """Clear stuck orders."""
    order_file = bot_root / "open_buy_orders.json"
    held_file = bot_root / "held_assets.json"

    count = 0

    if order_file.exists():
        with open(order_file, 'r') as f:
            orders = json.load(f)
        count = len(orders)

        # Backup and clear
        if count > 0:
            backup = bot_root / f"open_buy_orders_backup_{int(time.time())}.json"
            shutil.copy(order_file, backup)

        with open(order_file, 'w') as f:
            json.dump({}, f, indent=2)

    if held_file.exists():
        with open(held_file, 'r') as f:
            data = json.load(f)

        # Reset to empty state with structure
        with open(held_file, 'w') as f:
            json.dump({"__dust_ledger__": {}, "__last_buy_time__": {}}, f, indent=2)

    if verbose and count > 0:
        print(f"✅ Cleared {count} stuck orders")

    return count


def portfolio_reset(bot_root, verbose=False):
    """Execute portfolio reset (sell all assets)."""
    print()
    print("=" * 60)
    print("⚠️  PORTFOLIO RESET")
    print("=" * 60)
    print()
    print("This will sell ALL assets on the exchange (except USDT).")
    print("This action CANNOT be undone!")
    print()

    response = input("Are you ABSOLUTELY SURE? Type 'SELL ALL' to confirm: ")

    if response.strip() != "SELL ALL":
        print("Portfolio reset cancelled.")
        return False

    print()
    print("Starting portfolio reset...")
    print("This may take several minutes...")
    print()

    try:
        # Import modules
        import config
        from adapters.exchange import get_exchange
        from trading.settlement import SettlementManager

        # Initialize
        config.init_runtime_config()
        exchange = get_exchange()
        settlement_manager = SettlementManager(exchange)

        # Execute reset
        from trading.portfolio_reset import full_portfolio_reset
        success = full_portfolio_reset(exchange, settlement_manager)

        if success:
            print()
            print("✅ Portfolio reset completed successfully!")
            print()
            return True
        else:
            print()
            print("⚠️  Portfolio reset encountered errors (check logs)")
            print()
            return False

    except Exception as e:
        print()
        print(f"❌ Portfolio reset failed: {e}")
        print()
        return False


def show_current_state(bot_root):
    """Show current state."""
    print("\n" + "=" * 60)
    print("Current State (before cleanup):")
    print("=" * 60)

    logs_count = count_files(bot_root / "logs")
    sessions_count = sum(1 for _ in (bot_root / "sessions").rglob('*') if _.is_file())
    state_dbs = len(list((bot_root / "state").glob("*.db*")))
    drop_windows = count_files(bot_root / "state" / "drop_windows")
    anchor_file = (bot_root / "drop_anchors.json").exists()
    cache_count = sum(1 for _ in bot_root.rglob("__pycache__") if _.is_dir())

    print(f"  Log files:        {logs_count}")
    print(f"  Session files:    {sessions_count}")
    print(f"  State DB files:   {state_dbs}")
    print(f"  Drop window files: {drop_windows}")
    print(f"  Anchor files:     {1 if anchor_file else 0}")
    print(f"  Cache dirs:       {cache_count}")
    print()


def verify_cleanup(bot_root):
    """Verify cleanup was successful."""
    print("\n" + "=" * 60)
    print("Verification:")
    print("=" * 60)

    logs_count = count_files(bot_root / "logs")
    sessions_count = sum(1 for _ in (bot_root / "sessions").rglob('*') if _.is_file())
    state_dbs = len(list((bot_root / "state").glob("*.db*")))
    drop_windows = count_files(bot_root / "state" / "drop_windows")
    anchor_exists = (bot_root / "drop_anchors.json").exists()

    print(f"  Log files:        {logs_count}")
    print(f"  Session files:    {sessions_count}")
    print(f"  State DBs:        {state_dbs}")
    print(f"  Drop windows:     {drop_windows}")
    print(f"  Anchors:          {'exists' if anchor_exists else 'removed'}")
    print()

    # Check if cleanup was successful
    if (logs_count == 0 and sessions_count == 0 and
        state_dbs == 0 and drop_windows == 0 and not anchor_exists):
        print("✅ Complete bot reset successful!")
        print()
        print("🚀 The bot is now in a completely clean state.")
        print("   All data has been removed.")
        return True
    else:
        print("⚠️  Warning: Some files still remain")
        print(f"   Logs: {logs_count} | Sessions: {sessions_count} | "
              f"State: {state_dbs} | Drops: {drop_windows}")
        return False


def main():
    parser = argparse.ArgumentParser(
        description="Trading Bot Hard Reset (DESTRUCTIVE!)",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
⚠️  WARNING: This is DESTRUCTIVE and will delete all bot data!

Examples:
  python3 cleanup_hard.py                    # Interactive mode
  python3 cleanup_hard.py -y                 # Auto-confirm (DANGEROUS!)
  python3 cleanup_hard.py --portfolio-reset  # Include portfolio liquidation
  python3 cleanup_hard.py --dry-run          # Preview only
        """
    )

    parser.add_argument(
        "-y", "--yes",
        action="store_true",
        help="Auto-confirm deletion (DANGEROUS! Skip ALL prompts)"
    )

    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Show what would be deleted without actually deleting"
    )

    parser.add_argument(
        "--portfolio-reset",
        action="store_true",
        help="Also execute portfolio reset (sell all assets)"
    )

    parser.add_argument(
        "-v", "--verbose",
        action="store_true",
        help="Verbose output"
    )

    args = parser.parse_args()

    # Get bot root
    bot_root = Path(__file__).parent.absolute()

    print("=" * 60)
    print("  Trading Bot - Hard Reset (DESTRUCTIVE!)")
    print("=" * 60)
    print()

    # Show current state
    show_current_state(bot_root)

    # Confirm deletion
    if not args.yes and not args.dry_run:
        print("⚠️  ⚠️  ⚠️  DANGER ZONE ⚠️  ⚠️  ⚠️")
        print()
        print("This will DELETE ALL bot data:")
        print("   ✗ All log files")
        print("   ✗ All session data and FSM snapshots")
        print("   ✗ All state databases (ledger.db, etc.)")
        print("   ✗ All drop windows, anchors, and ticks")
        print("   ✗ Python cache")
        print("   ✗ Stuck orders")
        if args.portfolio_reset:
            print("   ✗ ALL ASSETS (will be sold!)")
        print()
        print("This action CANNOT be undone!")
        print()

        response = input("Are you ABSOLUTELY SURE? Type 'DELETE ALL' to confirm: ")

        if response.strip() != "DELETE ALL":
            print("Aborted. (Good choice!)")
            return 1
        print()

    if args.dry_run:
        print("🔍 DRY RUN - No files will be deleted")
        print()
        return 0

    # Portfolio reset first (if requested)
    if args.portfolio_reset:
        if not portfolio_reset(bot_root, args.verbose):
            print("Portfolio reset failed or was cancelled.")
            response = input("Continue with file cleanup anyway? (yes/no): ")
            if response.lower() not in ["yes", "y"]:
                return 1

    # Perform cleanup
    print("Starting hard reset...")
    print()

    cleanup_logs(bot_root, args.verbose)
    cleanup_sessions(bot_root, args.verbose)
    cleanup_state(bot_root, args.verbose)
    cleanup_anchors(bot_root, args.verbose)
    cleanup_drop_windows(bot_root, args.verbose)
    cleanup_python_cache(bot_root, args.verbose)
    cleanup_stuck_orders(bot_root, args.verbose)

    print()
    print("=" * 60)
    print("✅ Hard Reset Complete!")
    print("=" * 60)
    print()

    # Verify
    success = verify_cleanup(bot_root)

    print()
    print("You can now start the bot with: python3 main.py")
    print()

    return 0 if success else 1


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        print("\n\nAborted by user.")
        sys.exit(1)
    except Exception as e:
        print(f"❌ Error during cleanup: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)
