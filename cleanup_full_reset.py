#!/usr/bin/env python3
"""
Trading Bot - FULL RESET (No Portfolio)
========================================

⚠️  DESTRUKTIV! Löscht ALLE Bot-Daten!

Löscht: ALLES (Logs, State, Sessions, Anchors, Cache, Orders)
Behält: Portfolio auf Exchange (verkauft NICHTS!)

Für: Kompletter Neustart, State-Korruption, Major-Updates
"""

import json
import shutil
import sys
import time
from pathlib import Path


def cleanup_full_reset():
    """Complete bot reset - deletes ALL data."""
    bot_root = Path(__file__).parent.absolute()

    print("=" * 60)
    print("  ⚠️  FULL BOT RESET ⚠️")
    print("=" * 60)
    print()
    print("⚠️  WARNING: This is DESTRUCTIVE!")
    print()
    print("Will DELETE:")
    print("  ✗ All log files")
    print("  ✗ All session data (FSM snapshots)")
    print("  ✗ All state databases (ledger.db, etc.)")
    print("  ✗ All drop windows and anchors")
    print("  ✗ Python cache")
    print("  ✗ Stuck orders")
    print()
    print("Will PRESERVE:")
    print("  ✓ Portfolio on exchange (NO assets sold)")
    print()
    print("This action CANNOT be undone!")
    print()

    # Double confirmation
    response1 = input("Are you sure? Type 'reset' to confirm: ")
    if response1.strip() != "reset":
        print("Aborted.")
        return 1

    response2 = input("Really delete ALL bot data? Type 'DELETE ALL': ")
    if response2.strip() != "DELETE ALL":
        print("Aborted.")
        return 1

    print()
    print("Starting full reset...")
    print()

    # 1. Clear logs
    logs_dir = bot_root / "logs"
    if logs_dir.exists():
        count = sum(1 for _ in logs_dir.rglob('*') if _.is_file())
        for item in logs_dir.iterdir():
            if item.is_file():
                item.unlink()
            elif item.is_dir():
                shutil.rmtree(item)
        print(f"✅ Removed {count} log files")

    # 2. Clear sessions
    sessions_dir = bot_root / "sessions"
    if sessions_dir.exists():
        count = 0
        for session_dir in sessions_dir.glob("session_*"):
            if session_dir.is_dir():
                shutil.rmtree(session_dir)
                count += 1

        current_dir = sessions_dir / "current"
        if current_dir.exists():
            for item in current_dir.iterdir():
                if item.is_file():
                    item.unlink()
                elif item.is_dir():
                    shutil.rmtree(item)

        print(f"✅ Removed {count} session directories")

    # 3. Clear state databases
    state_dir = bot_root / "state"
    if state_dir.exists():
        count = 0
        for db_file in state_dir.glob("*.db*"):
            db_file.unlink()
            count += 1
        print(f"✅ Removed {count} state database files")

    # 4. Clear drop_anchors.json
    anchor_file = bot_root / "drop_anchors.json"
    if anchor_file.exists():
        anchor_file.unlink()
        print("✅ Removed drop_anchors.json")

    # 5. Clear drop_windows
    drop_windows_dir = bot_root / "state" / "drop_windows"
    if drop_windows_dir.exists():
        count = sum(1 for _ in drop_windows_dir.rglob('*') if _.is_file())
        shutil.rmtree(drop_windows_dir)
        drop_windows_dir.mkdir(parents=True, exist_ok=True)
        print(f"✅ Removed {count} drop window files")

    # 6. Clear Python cache
    count = 0
    for pycache_dir in bot_root.rglob("__pycache__"):
        if pycache_dir.is_dir():
            shutil.rmtree(pycache_dir)
            count += 1
    for pyc_file in bot_root.rglob("*.pyc"):
        if pyc_file.is_file():
            pyc_file.unlink()
    print(f"✅ Removed {count} cache directories")

    # 7. Clear stuck orders
    order_file = bot_root / "open_buy_orders.json"
    if order_file.exists():
        with open(order_file, 'r') as f:
            orders = json.load(f)
        if orders:
            backup = bot_root / f"open_buy_orders_backup_{int(time.time())}.json"
            shutil.copy(order_file, backup)
        with open(order_file, 'w') as f:
            json.dump({}, f, indent=2)
        print(f"✅ Cleared {len(orders)} stuck orders")

    held_file = bot_root / "held_assets.json"
    if held_file.exists():
        with open(held_file, 'w') as f:
            json.dump({"__dust_ledger__": {}, "__last_buy_time__": {}}, f, indent=2)

    print()
    print("=" * 60)
    print("✅ FULL RESET COMPLETE!")
    print("=" * 60)
    print()
    print("Bot is now in completely clean state.")
    print("Portfolio on exchange is UNCHANGED (no assets sold).")
    print()
    print("You can now start the bot with: python3 main.py")
    print()

    return 0


if __name__ == "__main__":
    try:
        sys.exit(cleanup_full_reset())
    except KeyboardInterrupt:
        print("\n\nAborted by user.")
        sys.exit(1)
    except Exception as e:
        print(f"❌ Error: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)
