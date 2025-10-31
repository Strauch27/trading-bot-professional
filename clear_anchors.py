#!/usr/bin/env python3
"""
Clear Drop Anchors Script
Deletes all drop anchor data and resets the system for fresh start
"""

import os
import sys
import json
import shutil
from pathlib import Path
import subprocess


def check_bot_running():
    """Check if trading bot is currently running"""
    try:
        result = subprocess.run(
            ['pgrep', '-f', 'python3 main.py'],
            capture_output=True,
            text=True
        )
        return bool(result.stdout.strip())
    except Exception:
        return False


def count_files(directory):
    """Count number of files in directory recursively"""
    if not os.path.exists(directory):
        return 0
    return sum(1 for _ in Path(directory).rglob('*') if _.is_file())


def clear_anchors():
    """Main function to clear all anchor data"""

    print("=" * 50)
    print("  Drop Anchors Cleanup Script")
    print("=" * 50)
    print()

    # Get script directory
    script_dir = Path(__file__).parent.absolute()
    os.chdir(script_dir)

    # Check if bot is running
    if check_bot_running():
        print("⚠️  WARNING: Trading bot is currently running!")
        print()
        response = input("Stop the bot before clearing anchors? (y/n): ").strip().lower()
        if response == 'y':
            print("Stopping bot...")
            try:
                subprocess.run(['pkill', '-f', 'python3 main.py'], check=False)
                import time
                time.sleep(2)
                print("✅ Bot stopped")
            except Exception as e:
                print(f"⚠️  Could not stop bot: {e}")
        else:
            print("⚠️  Continuing without stopping bot (not recommended)")
        print()

    # Show current state
    print("Current State:")

    anchor_file = Path("drop_anchors.json")
    if anchor_file.exists():
        size = anchor_file.stat().st_size
        print(f"  drop_anchors.json: {size} bytes")
    else:
        print("  drop_anchors.json: Not found")

    drop_windows = Path("state/drop_windows")
    if drop_windows.exists():
        print(f"  Anchor files: {count_files(drop_windows / 'anchors')}")
        print(f"  Tick files: {count_files(drop_windows / 'ticks')}")
        print(f"  Window files: {count_files(drop_windows / 'windows')}")
        print(f"  Snapshot files: {count_files(drop_windows / 'snapshots')}")
    else:
        print("  state/drop_windows: Not found")

    print()

    # Confirm deletion
    response = input("Are you sure you want to delete all anchor data? (yes/no): ").strip().lower()
    if response != 'yes':
        print("Aborted.")
        return

    print()
    print("Starting cleanup...")
    print()

    # Clear drop_anchors.json
    if anchor_file.exists():
        with open(anchor_file, 'w') as f:
            json.dump({}, f)
        print("✅ Cleared drop_anchors.json")
    else:
        print("⚠️  drop_anchors.json not found, skipping")

    # Clear anchor directories
    if drop_windows.exists():
        subdirs = ['anchors', 'ticks', 'windows', 'snapshots']

        for subdir in subdirs:
            subdir_path = drop_windows / subdir
            if subdir_path.exists():
                file_count = count_files(subdir_path)

                # Remove all files in subdirectory
                for item in subdir_path.iterdir():
                    if item.is_file():
                        item.unlink()
                    elif item.is_dir():
                        shutil.rmtree(item)

                print(f"✅ Removed {file_count} {subdir} files")
            else:
                print(f"⚠️  {subdir_path} not found, skipping")
    else:
        print("⚠️  state/drop_windows directory not found")

    print()
    print("=" * 50)
    print("✅ Cleanup Complete!")
    print("=" * 50)
    print()

    # Verify cleanup
    total_remaining = count_files(drop_windows) if drop_windows.exists() else 0
    print("Verification:")
    print(f"  Total files remaining: {total_remaining}")

    if anchor_file.exists():
        with open(anchor_file, 'r') as f:
            content = f.read().strip()
            print(f"  drop_anchors.json: {content}")
    else:
        print("  drop_anchors.json: N/A")

    print()

    if total_remaining == 0:
        print("✅ All anchor data successfully deleted")
        print()
        print("The bot will start with fresh anchors on next run.")
    else:
        print(f"⚠️  Warning: {total_remaining} files still remain")

    print()
    print("You can now start the bot with: python3 main.py")
    print()


if __name__ == "__main__":
    try:
        clear_anchors()
    except KeyboardInterrupt:
        print("\n\nAborted by user.")
        sys.exit(1)
    except Exception as e:
        print(f"\n❌ Error: {e}", file=sys.stderr)
        sys.exit(1)
