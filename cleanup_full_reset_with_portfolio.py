#!/usr/bin/env python3
"""
Trading Bot - FULL RESET + PORTFOLIO LIQUIDATION
=================================================

⚠️  ⚠️  ⚠️  MAXIMUM DESTRUKTIV! ⚠️  ⚠️  ⚠️

Löscht: ALLES (Logs, State, Sessions, Anchors, Cache, Orders)
Verkauft: ALLE ASSETS auf Exchange (außer USDT)!

Für: Kompletter Neustart von Null
     Wechsel zu anderem Trading-Setup
     Ende des Bot-Betriebs

⚠️  ACHTUNG: Verkauft ALLE Coins! Nicht rückgängig zu machen!
"""

import json
import shutil
import sys
import time
from pathlib import Path


def cleanup_full_reset_with_portfolio():
    """Complete reset including portfolio liquidation."""
    bot_root = Path(__file__).parent.absolute()

    print("=" * 60)
    print("  ⚠️  ⚠️  ⚠️  FULL RESET + PORTFOLIO LIQUIDATION ⚠️  ⚠️  ⚠️")
    print("=" * 60)
    print()
    print("⚠️  MAXIMUM DESTRUCTIVE OPERATION!")
    print()
    print("This will:")
    print("  1. SELL ALL ASSETS on exchange (except USDT)!")
    print("  2. DELETE all bot data (logs, state, sessions, etc.)")
    print()
    print("After this:")
    print("  - All coins will be converted to USDT")
    print("  - All bot history will be lost")
    print("  - You start from complete zero")
    print()
    print("This action CANNOT be undone!")
    print()

    # Triple confirmation
    response1 = input("Are you ABSOLUTELY sure? Type 'liquidate': ")
    if response1.strip() != "liquidate":
        print("Aborted. (Good choice!)")
        return 1

    print()
    print("⚠️  FINAL WARNING: This will sell ALL your assets!")
    print()

    response2 = input("Type 'SELL ALL ASSETS' to confirm: ")
    if response2.strip() != "SELL ALL ASSETS":
        print("Aborted.")
        return 1

    print()
    print("=" * 60)
    print("STEP 1/2: Portfolio Liquidation")
    print("=" * 60)
    print()
    print("Selling all assets on exchange...")
    print("This may take several minutes...")
    print()

    # Portfolio reset
    try:
        import config
        from adapters.exchange import get_exchange
        from trading.settlement import SettlementManager
        from trading.portfolio_reset import full_portfolio_reset

        config.init_runtime_config()
        exchange = get_exchange()
        settlement_manager = SettlementManager(exchange)

        success = full_portfolio_reset(exchange, settlement_manager)

        if not success:
            print()
            print("⚠️  Portfolio liquidation encountered errors!")
            print()
            response = input("Continue with data cleanup anyway? (yes/no): ")
            if response.lower() not in ["yes", "y"]:
                print("Aborted.")
                return 1
        else:
            print()
            print("✅ Portfolio liquidation complete!")
            print()

    except Exception as e:
        print()
        print(f"❌ Portfolio liquidation failed: {e}")
        print()
        response = input("Continue with data cleanup anyway? (yes/no): ")
        if response.lower() not in ["yes", "y"]:
            print("Aborted.")
            return 1

    print()
    print("=" * 60)
    print("STEP 2/2: Data Cleanup")
    print("=" * 60)
    print()
    print("Deleting all bot data...")
    print()

    # Same cleanup as full_reset
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
        with open(order_file, 'w') as f:
            json.dump({}, f, indent=2)

    held_file = bot_root / "held_assets.json"
    if held_file.exists():
        with open(held_file, 'w') as f:
            json.dump({"__dust_ledger__": {}, "__last_buy_time__": {}}, f, indent=2)

    print()
    print("=" * 60)
    print("✅ COMPLETE RESET SUCCESSFUL!")
    print("=" * 60)
    print()
    print("🎯 Bot is now at zero state:")
    print("   - All assets converted to USDT")
    print("   - All bot data deleted")
    print("   - Ready for fresh start")
    print()
    print("You can now start the bot with: python3 main.py")
    print()

    return 0


if __name__ == "__main__":
    try:
        sys.exit(cleanup_full_reset_with_portfolio())
    except KeyboardInterrupt:
        print("\n\nAborted by user.")
        sys.exit(1)
    except Exception as e:
        print(f"❌ Error: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)
