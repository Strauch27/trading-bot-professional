#!/bin/bash
# Clear Drop Anchors Script
# Deletes all drop anchor data and resets the system for fresh start

set -e  # Exit on error

echo "=========================================="
echo "  Drop Anchors Cleanup Script"
echo "=========================================="
echo ""

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

# Get script directory
SCRIPT_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"
cd "$SCRIPT_DIR"

# Check if bot is running
if pgrep -f "python3 main.py" > /dev/null; then
    echo -e "${RED}⚠️  WARNING: Trading bot is currently running!${NC}"
    echo ""
    read -p "Do you want to stop the bot before clearing anchors? (y/n): " -n 1 -r
    echo
    if [[ $REPLY =~ ^[Yy]$ ]]; then
        echo "Stopping bot..."
        pkill -f "python3 main.py" || true
        sleep 2
        echo -e "${GREEN}✅ Bot stopped${NC}"
    else
        echo -e "${YELLOW}Continuing without stopping bot (not recommended)${NC}"
    fi
    echo ""
fi

# Function to count files in directory
count_files() {
    find "$1" -type f 2>/dev/null | wc -l | tr -d ' '
}

# Show current state
echo "Current State:"
echo "  drop_anchors.json: $(wc -c < drop_anchors.json 2>/dev/null || echo '0') bytes"
echo "  Anchor files: $(count_files 'state/drop_windows/anchors')"
echo "  Tick files: $(count_files 'state/drop_windows/ticks')"
echo "  Window files: $(count_files 'state/drop_windows/windows')"
echo "  Snapshot files: $(count_files 'state/drop_windows/snapshots')"
echo ""

# Confirm deletion
read -p "Are you sure you want to delete all anchor data? (yes/no): " -r
echo
if [[ ! $REPLY =~ ^[Yy][Ee][Ss]$ ]]; then
    echo "Aborted."
    exit 0
fi

echo ""
echo "Starting cleanup..."
echo ""

# Clear drop_anchors.json
if [ -f "drop_anchors.json" ]; then
    echo "{}" > drop_anchors.json
    echo -e "${GREEN}✅ Cleared drop_anchors.json${NC}"
else
    echo -e "${YELLOW}⚠️  drop_anchors.json not found, skipping${NC}"
fi

# Clear anchor directories
if [ -d "state/drop_windows" ]; then
    # Remove all JSON files from subdirectories
    if [ -d "state/drop_windows/anchors" ]; then
        removed=$(count_files 'state/drop_windows/anchors')
        rm -rf state/drop_windows/anchors/*
        echo -e "${GREEN}✅ Removed $removed anchor files${NC}"
    fi

    if [ -d "state/drop_windows/ticks" ]; then
        removed=$(count_files 'state/drop_windows/ticks')
        rm -rf state/drop_windows/ticks/*
        echo -e "${GREEN}✅ Removed $removed tick files${NC}"
    fi

    if [ -d "state/drop_windows/windows" ]; then
        removed=$(count_files 'state/drop_windows/windows')
        rm -rf state/drop_windows/windows/*
        echo -e "${GREEN}✅ Removed $removed window files${NC}"
    fi

    if [ -d "state/drop_windows/snapshots" ]; then
        removed=$(count_files 'state/drop_windows/snapshots')
        rm -rf state/drop_windows/snapshots/*
        echo -e "${GREEN}✅ Removed $removed snapshot files${NC}"
    fi
else
    echo -e "${YELLOW}⚠️  state/drop_windows directory not found${NC}"
fi

echo ""
echo "=========================================="
echo -e "${GREEN}✅ Cleanup Complete!${NC}"
echo "=========================================="
echo ""

# Verify cleanup
total_remaining=$(count_files 'state/drop_windows')
echo "Verification:"
echo "  Total files remaining: $total_remaining"
echo "  drop_anchors.json: $(cat drop_anchors.json 2>/dev/null || echo 'N/A')"
echo ""

if [ "$total_remaining" -eq 0 ]; then
    echo -e "${GREEN}✅ All anchor data successfully deleted${NC}"
    echo ""
    echo "The bot will start with fresh anchors on next run."
else
    echo -e "${YELLOW}⚠️  Warning: $total_remaining files still remain${NC}"
fi

echo ""
echo "You can now start the bot with: python3 main.py"
echo ""
