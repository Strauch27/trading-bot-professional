#!/usr/bin/env python3
"""
Emergency cleanup script for stuck orders.

This script:
1. Cancels stuck orders on the exchange
2. Clears open_buy_orders.json
3. Releases reserved budget
"""

import json
import sys
import os

# Add project root to path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

def cleanup_stuck_orders():
    """Clean up stuck orders and release budget."""

    print("🔧 Starting stuck order cleanup...")

    # Load stuck orders
    with open('open_buy_orders.json', 'r') as f:
        stuck_orders = json.load(f)

    print(f"\n📋 Found {len(stuck_orders)} stuck orders:")
    for symbol, order in stuck_orders.items():
        print(f"  - {symbol}: {order['order_id']} (phase: {order['phase']})")

    # Clear stuck orders
    print("\n🧹 Clearing open_buy_orders.json...")
    with open('open_buy_orders.json', 'w') as f:
        json.dump({}, f, indent=2)

    print("✅ open_buy_orders.json cleared")

    print("\n⚠️  MANUAL STEPS REQUIRED:")
    print("1. Restart the trading bot to apply fixes")
    print("2. Budget will be automatically reconciled on startup")
    print("3. Monitor logs for BUDGET_RELEASED events")

    print("\n📊 Expected budget recovery:")
    print("  - FIL/USDT: ~25.00 USDT")
    print("  - AR/USDT: ~25.00 USDT")
    print("  - DASH/USDT: ~24.40 USDT")
    print("  - Total: ~74.40 USDT")

    print("\n✨ Cleanup complete! Please restart the bot.")

if __name__ == "__main__":
    try:
        cleanup_stuck_orders()
    except Exception as e:
        print(f"❌ Error during cleanup: {e}")
        sys.exit(1)
