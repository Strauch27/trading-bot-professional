#!/bin/bash
# Migrate deprecated config variables
# MODE → DROP_TRIGGER_MODE
# POLL_MS → MD_POLL_MS
# MAX_TRADES → MAX_CONCURRENT_POSITIONS

set -e

echo "=== Migrating Deprecated Variables ==="
echo ""

# Count current usages
echo "Current usages:"
mode_count=$(grep -r "\bconfig\.MODE\b" --include="*.py" --exclude-dir=venv --exclude-dir=sessions . 2>/dev/null | grep -v "DROP_TRIGGER_MODE" | wc -l | tr -d ' ')
poll_count=$(grep -r "\bconfig\.POLL_MS\b" --include="*.py" --exclude-dir=venv --exclude-dir=sessions . 2>/dev/null | grep -v "MD_POLL_MS" | wc -l | tr -d ' ')
trades_count=$(grep -r "\bconfig\.MAX_TRADES\b" --include="*.py" --exclude-dir=venv --exclude-dir=sessions . 2>/dev/null | grep -v "MAX_CONCURRENT_POSITIONS" | wc -l | tr -d ' ')

echo "  MODE: $mode_count"
echo "  POLL_MS: $poll_count"
echo "  MAX_TRADES: $trades_count"
echo ""

# Replace MODE with DROP_TRIGGER_MODE
echo "1. Migrating config.MODE → config.DROP_TRIGGER_MODE..."
find . -name "*.py" -not -path "./venv/*" -not -path "./sessions/*" -not -path "./.git/*" -not -path "./__pycache__/*" \
  -exec sed -i '' 's/config\.MODE\b/config.DROP_TRIGGER_MODE/g' {} \;

# Replace POLL_MS with MD_POLL_MS
echo "2. Migrating config.POLL_MS → config.MD_POLL_MS..."
find . -name "*.py" -not -path "./venv/*" -not -path "./sessions/*" -not -path "./.git/*" -not -path "./__pycache__/*" \
  -exec sed -i '' 's/config\.POLL_MS\b/config.MD_POLL_MS/g' {} \;

# Replace MAX_TRADES with MAX_CONCURRENT_POSITIONS
echo "3. Migrating config.MAX_TRADES → config.MAX_CONCURRENT_POSITIONS..."
find . -name "*.py" -not -path "./venv/*" -not -path "./sessions/*" -not -path "./.git/*" -not -path "./__pycache__/*" \
  -exec sed -i '' 's/config\.MAX_TRADES\b/config.MAX_CONCURRENT_POSITIONS/g' {} \;

echo ""
echo "=== Verification ==="

# Verify after migration
mode_after=$(grep -r "\bconfig\.MODE\b" --include="*.py" --exclude-dir=venv --exclude-dir=sessions . 2>/dev/null | grep -v "DROP_TRIGGER_MODE" | grep -v "\.sh:" | wc -l | tr -d ' ')
poll_after=$(grep -r "\bconfig\.POLL_MS\b" --include="*.py" --exclude-dir=venv --exclude-dir=sessions . 2>/dev/null | grep -v "MD_POLL_MS" | grep -v "\.sh:" | wc -l | tr -d ' ')
trades_after=$(grep -r "\bconfig\.MAX_TRADES\b" --include="*.py" --exclude-dir=venv --exclude-dir=sessions . 2>/dev/null | grep -v "MAX_CONCURRENT_POSITIONS" | grep -v "\.sh:" | wc -l | tr -d ' ')

echo "After migration:"
echo "  MODE: $mode_after (was $mode_count)"
echo "  POLL_MS: $poll_after (was $poll_count)"
echo "  MAX_TRADES: $trades_after (was $trades_count)"
echo ""

if [ "$mode_after" -eq 0 ] && [ "$poll_after" -eq 0 ] && [ "$trades_after" -eq 0 ]; then
    echo "✅ Migration successful!"
else
    echo "⚠️  Some usages remain - manual review needed"
fi

echo ""
echo "Done! Review changes with: git diff"
