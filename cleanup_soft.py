#!/usr/bin/env python3
"""
Trading Bot - Soft Cleanup
==========================

Leichter Cleanup für normale Betriebsunterbrechungen.

Löscht:
✓ Logs (alte Log-Dateien)
✓ Python Cache (__pycache__, *.pyc)
✓ Stuck Orders (optional)

Behält:
✓ State (FSM Snapshots, Ledger DB)
✓ Sessions (historische Daten)
✓ Anchors & Drop Windows (Markt-Kontext)

Verwendung:
  python3 cleanup_soft.py              # Interactive mode
  python3 cleanup_soft.py -y           # Auto-confirm
  python3 cleanup_soft.py --orders     # Include stuck order cleanup
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
    """Clear all logs (keeps log directories)."""
    logs_dir = bot_root / "logs"
    if not logs_dir.exists():
        if verbose:
            print("⚠️  logs directory not found")
        return 0

    count = 0
    for item in logs_dir.iterdir():
        if item.is_file():
            item.unlink()
            count += 1
        elif item.is_dir():
            # Clear files in subdirectories but keep structure
            for subitem in item.rglob('*'):
                if subitem.is_file():
                    subitem.unlink()
                    count += 1

    if verbose:
        print(f"✅ Removed {count} log files")

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
    """Clean up stuck orders from open_buy_orders.json."""
    order_file = bot_root / "open_buy_orders.json"

    if not order_file.exists():
        if verbose:
            print("⚠️  No open_buy_orders.json file found")
        return 0

    try:
        with open(order_file, 'r') as f:
            orders = json.load(f)

        count = len(orders)

        if count > 0:
            # Create backup
            backup_file = bot_root / f"open_buy_orders_backup_{int(time.time())}.json"
            with open(backup_file, 'w') as f:
                json.dump(orders, f, indent=2)

            # Clear orders
            with open(order_file, 'w') as f:
                json.dump({}, f, indent=2)

            if verbose:
                print(f"✅ Cleared {count} stuck orders (backup: {backup_file.name})")

            return count
        else:
            if verbose:
                print("✓ No stuck orders found")
            return 0

    except Exception as e:
        if verbose:
            print(f"⚠️  Failed to process stuck orders: {e}")
        return 0


def show_current_state(bot_root):
    """Show current state before cleanup."""
    print("\n" + "=" * 60)
    print("Current State (before cleanup):")
    print("=" * 60)

    logs_count = count_files(bot_root / "logs")
    cache_count = sum(1 for _ in bot_root.rglob("__pycache__") if _.is_dir())

    order_file = bot_root / "open_buy_orders.json"
    stuck_orders = 0
    if order_file.exists():
        try:
            with open(order_file, 'r') as f:
                stuck_orders = len(json.load(f))
        except Exception:
            pass

    print(f"  Log files:          {logs_count}")
    print(f"  Python cache dirs:  {cache_count}")
    print(f"  Stuck orders:       {stuck_orders}")
    print()


def verify_cleanup(bot_root, cleaned_orders):
    """Verify cleanup results."""
    print("\n" + "=" * 60)
    print("Cleanup Results:")
    print("=" * 60)

    logs_count = count_files(bot_root / "logs")
    cache_count = sum(1 for _ in bot_root.rglob("__pycache__") if _.is_dir())

    print(f"  Remaining log files:    {logs_count}")
    print(f"  Remaining cache dirs:   {cache_count}")

    if cleaned_orders:
        print(f"  Stuck orders cleared:   {cleaned_orders}")

    print()

    if logs_count == 0 and cache_count == 0:
        print("✅ Soft cleanup complete!")
    else:
        print("⚠️  Some files still remain (may be normal)")

    print()


def main():
    parser = argparse.ArgumentParser(
        description="Trading Bot Soft Cleanup (logs and cache)",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python3 cleanup_soft.py              # Interactive mode
  python3 cleanup_soft.py -y           # Auto-confirm
  python3 cleanup_soft.py --orders     # Include stuck order cleanup
  python3 cleanup_soft.py -y --orders  # Auto + orders
        """
    )

    parser.add_argument(
        "-y", "--yes",
        action="store_true",
        help="Auto-confirm deletion (skip confirmation prompt)"
    )

    parser.add_argument(
        "--orders",
        action="store_true",
        help="Also cleanup stuck orders from open_buy_orders.json"
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
    print("  Trading Bot - Soft Cleanup")
    print("=" * 60)
    print()
    print("This cleanup is SAFE and non-destructive.")
    print("It only removes logs and cache.")
    print()

    # Show current state
    show_current_state(bot_root)

    # Confirm deletion
    if not args.yes:
        print("⚠️  This will delete:")
        print("   - All log files (*.log, *.jsonl)")
        print("   - Python cache (__pycache__, *.pyc)")
        if args.orders:
            print("   - Stuck orders from open_buy_orders.json")
        print()
        print("✓ This will KEEP:")
        print("   - State (FSM snapshots, ledger DB)")
        print("   - Sessions (historical data)")
        print("   - Anchors & drop windows")
        print()

        response = input("Continue with soft cleanup? (yes/no): ")

        if response.lower() not in ["yes", "y"]:
            print("Aborted.")
            return 1
        print()

    # Perform cleanup
    print("Starting soft cleanup...")
    print()

    logs_cleaned = cleanup_logs(bot_root, args.verbose)
    cache_cleaned = cleanup_python_cache(bot_root, args.verbose)
    orders_cleaned = 0

    if args.orders:
        orders_cleaned = cleanup_stuck_orders(bot_root, args.verbose)

    print()
    print("=" * 60)
    print("✅ Soft Cleanup Complete!")
    print("=" * 60)
    print()
    print(f"Cleaned: {logs_cleaned} log files, {cache_cleaned} cache dirs" +
          (f", {orders_cleaned} stuck orders" if args.orders else ""))
    print()

    # Verify
    verify_cleanup(bot_root, orders_cleaned if args.orders else 0)

    print("State, sessions, and market context have been preserved.")
    print("You can now restart the bot with: python3 main.py")
    print()

    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        print("\n\nAborted by user.")
        sys.exit(1)
    except Exception as e:
        print(f"❌ Error during cleanup: {e}")
        sys.exit(1)
