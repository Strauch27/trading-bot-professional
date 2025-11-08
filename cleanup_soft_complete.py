#!/usr/bin/env python3
"""
Trading Bot - Cleanup: Soft Complete
=====================================

Kompletter Soft-Cleanup: Logs + Cache + Stuck Orders

Sicher für: Wöchentliche Wartung, bei Problemen
Löscht: Logs, Cache, Stuck Orders
Behält: State, Sessions, Anchors (Markt-Kontext bleibt!)
"""

import json
import shutil
import sys
import time
from pathlib import Path


def cleanup_soft_complete():
    """Complete soft cleanup: logs, cache, stuck orders."""
    bot_root = Path(__file__).parent.absolute()

    print("=" * 60)
    print("  Trading Bot - Soft Cleanup (Complete)")
    print("=" * 60)
    print()

    # Count files
    logs_dir = bot_root / "logs"
    log_count = sum(1 for _ in logs_dir.rglob('*') if _.is_file()) if logs_dir.exists() else 0
    cache_count = sum(1 for _ in bot_root.rglob("__pycache__") if _.is_dir())

    order_file = bot_root / "open_buy_orders.json"
    stuck_orders = 0
    if order_file.exists():
        try:
            with open(order_file, 'r') as f:
                stuck_orders = len(json.load(f))
        except Exception:
            pass

    print("Will delete:")
    print(f"  - {log_count} log files")
    print(f"  - {cache_count} cache directories")
    print(f"  - {stuck_orders} stuck orders")
    print()
    print("Will preserve:")
    print("  ✓ State (FSM snapshots, ledger DB)")
    print("  ✓ Sessions (historical data)")
    print("  ✓ Anchors & drop windows")
    print()

    # Confirm
    response = input("Continue with soft cleanup? (yes/no): ")
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

    # Clear stuck orders
    orders_removed = 0
    if order_file.exists() and stuck_orders > 0:
        # Backup
        backup_file = bot_root / f"open_buy_orders_backup_{int(time.time())}.json"
        shutil.copy(order_file, backup_file)

        # Clear
        with open(order_file, 'w') as f:
            json.dump({}, f, indent=2)

        orders_removed = stuck_orders
        print(f"  Created backup: {backup_file.name}")

    print()
    print(f"✅ Removed {logs_removed} log items")
    print(f"✅ Removed {cache_removed} cache directories")
    if orders_removed > 0:
        print(f"✅ Cleared {orders_removed} stuck orders")
    print()
    print("Soft cleanup complete!")
    print("State, sessions, and market context preserved.")
    print()

    return 0


if __name__ == "__main__":
    try:
        sys.exit(cleanup_soft_complete())
    except KeyboardInterrupt:
        print("\n\nAborted by user.")
        sys.exit(1)
    except Exception as e:
        print(f"❌ Error: {e}")
        sys.exit(1)
