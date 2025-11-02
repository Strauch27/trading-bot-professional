#!/bin/bash
# Trading Bot Complete Cleanup Script
# Deletes all bot data (logs, sessions, states, anchors, snapshots) for fresh start

set -e  # Exit on error

echo "=========================================="
echo "  Trading Bot Complete Cleanup Script"
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
echo "  Log files: $(count_files 'logs')"
echo "  Session files: $(find sessions -type f 2>/dev/null | wc -l | tr -d ' ')"
echo "  State DB files: $(find state -name "*.db*" 2>/dev/null | wc -l | tr -d ' ')"
echo "  Drop window files: $(count_files 'state/drop_windows')"
echo "  Anchor files: $(count_files 'state/drop_windows/anchors')"
echo "  FSM snapshots: $(find sessions -name "*.json" -path "*/fsm_snapshots/*" 2>/dev/null | wc -l | tr -d ' ')"
echo "  drop_anchors.json: $(wc -c < drop_anchors.json 2>/dev/null || echo '0') bytes"
echo ""

# Confirm deletion
echo -e "${YELLOW}⚠️  This will delete ALL bot data:${NC}"
echo "   - All log files (*.log, *.jsonl)"
echo "   - All session data and FSM snapshots"
echo "   - All state databases (ledger.db, idempotency.db)"
echo "   - All drop windows, anchors, and ticks"
echo "   - Python cache (__pycache__)"
echo ""
read -p "Are you sure you want to delete ALL bot data? (yes/no): " -r
echo
if [[ ! $REPLY =~ ^[Yy][Ee][Ss]$ ]]; then
    echo "Aborted."
    exit 0
fi

echo ""
echo "Starting cleanup..."
echo ""

# 1. Clear all logs
if [ -d "logs" ]; then
    removed=$(count_files 'logs')
    rm -rf logs/*
    echo -e "${GREEN}✅ Removed $removed log files${NC}"
else
    echo -e "${YELLOW}⚠️  logs directory not found${NC}"
fi

# 2. Clear all sessions
if [ -d "sessions" ]; then
    # Remove all session_* directories
    removed=$(find sessions -type d -name "session_*" 2>/dev/null | wc -l | tr -d ' ')
    rm -rf sessions/session_* 2>/dev/null || true

    # Clear current session
    if [ -d "sessions/current" ]; then
        rm -rf sessions/current/*
    fi

    # Remove .DS_Store
    rm -f sessions/.DS_Store 2>/dev/null || true

    echo -e "${GREEN}✅ Removed $removed session directories and cleared current session${NC}"
else
    echo -e "${YELLOW}⚠️  sessions directory not found${NC}"
fi

# 3. Clear state databases
if [ -d "state" ]; then
    removed=$(find state -name "*.db*" 2>/dev/null | wc -l | tr -d ' ')
    rm -f state/*.db state/*.db-* 2>/dev/null || true
    echo -e "${GREEN}✅ Removed $removed state database files${NC}"
else
    echo -e "${YELLOW}⚠️  state directory not found${NC}"
fi

# 4. Clear drop_anchors.json
if [ -f "drop_anchors.json" ]; then
    rm -f drop_anchors.json
    echo -e "${GREEN}✅ Removed drop_anchors.json${NC}"
else
    echo -e "${YELLOW}⚠️  drop_anchors.json not found, skipping${NC}"
fi

# 5. Clear all drop_windows data (complete directory)
if [ -d "state/drop_windows" ]; then
    removed=$(count_files 'state/drop_windows')
    rm -rf state/drop_windows
    mkdir -p state/drop_windows
    echo -e "${GREEN}✅ Removed $removed drop window files (complete reset)${NC}"
else
    echo -e "${YELLOW}⚠️  state/drop_windows directory not found${NC}"
fi

# 6. Clear Python cache
cache_removed=$(find . -type d -name "__pycache__" 2>/dev/null | wc -l | tr -d ' ')
if [ "$cache_removed" -gt 0 ]; then
    find . -type d -name "__pycache__" -exec rm -rf {} + 2>/dev/null || true
    find . -type f -name "*.pyc" -delete 2>/dev/null || true
    echo -e "${GREEN}✅ Removed $cache_removed Python cache directories${NC}"
fi

echo ""
echo "=========================================="
echo -e "${GREEN}✅ Cleanup Complete!${NC}"
echo "=========================================="
echo ""

# Verify cleanup
echo "Verification:"
echo "  Log files:        $(count_files 'logs')"
echo "  Session files:    $(find sessions -type f 2>/dev/null | wc -l | tr -d ' ')"
echo "  State DBs:        $(find state -name "*.db*" 2>/dev/null | wc -l | tr -d ' ')"
echo "  Drop windows:     $(count_files 'state/drop_windows')"
echo "  Anchors:          $(test -f "drop_anchors.json" && echo "exists" || echo "removed")"
echo "  FSM snapshots:    $(find sessions -name "*.json" -path "*/fsm_snapshots/*" 2>/dev/null | wc -l | tr -d ' ')"
echo ""

total_logs=$(count_files 'logs')
total_sessions=$(find sessions -type f 2>/dev/null | wc -l | tr -d ' ')
total_state=$(find state -name "*.db*" 2>/dev/null | wc -l | tr -d ' ')
total_drops=$(count_files 'state/drop_windows')

if [ "$total_logs" -eq 0 ] && [ "$total_sessions" -eq 0 ] && [ "$total_state" -eq 0 ] && [ "$total_drops" -eq 0 ]; then
    echo -e "${GREEN}✅ All bot data successfully deleted!${NC}"
    echo ""
    echo "🚀 The bot is now in a completely clean state."
    echo "   All logs, sessions, states, and anchors have been removed."
else
    echo -e "${YELLOW}⚠️  Warning: Some files still remain${NC}"
    echo "   Logs: $total_logs | Sessions: $total_sessions | State: $total_state | Drops: $total_drops"
fi

echo ""
echo "You can now start the bot with: python3 main.py"
echo ""
