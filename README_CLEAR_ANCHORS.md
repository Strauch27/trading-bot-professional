# Clear Anchors Scripts

Two scripts are provided to clear all drop anchor data and reset the system for a fresh start.

## Scripts

### 1. Bash Script (clear_anchors.sh)

**Usage:**
```bash
./clear_anchors.sh
```

**Features:**
- Checks if bot is running and offers to stop it
- Shows current state (file counts)
- Asks for confirmation before deleting
- Clears all anchor-related data
- Color-coded output
- Verification after cleanup

### 2. Python Script (clear_anchors.py)

**Usage:**
```bash
python3 clear_anchors.py
# or
./clear_anchors.py
```

**Features:**
- Same functionality as bash script
- More portable (works on Windows, macOS, Linux)
- Better error handling
- Uses Python pathlib for robust file operations

## What Gets Deleted

Both scripts delete:
- **drop_anchors.json** - Main anchor storage file (reset to `{}`)
- **state/drop_windows/anchors/** - All anchor files
- **state/drop_windows/ticks/** - All tick history files
- **state/drop_windows/windows/** - All price window files
- **state/drop_windows/snapshots/** - All snapshot files

## When to Use

Use these scripts when you want to:
- Reset the drop detection system
- Clear stale anchor data
- Start fresh after configuration changes
- Troubleshoot anchor-related issues
- Begin a new trading session with clean state

## Safety Features

Both scripts include safety features:
- ⚠️ Warns if bot is currently running
- ⚠️ Requires explicit "yes" confirmation
- ✅ Shows file counts before and after
- ✅ Verifies cleanup was successful

## Example Output

```
==========================================
  Drop Anchors Cleanup Script
==========================================

Current State:
  drop_anchors.json: 2 bytes
  Anchor files: 0
  Tick files: 136
  Window files: 0
  Snapshot files: 0

Are you sure you want to delete all anchor data? (yes/no): yes

Starting cleanup...

✅ Cleared drop_anchors.json
✅ Removed 0 anchor files
✅ Removed 136 tick files
✅ Removed 0 window files
✅ Removed 0 snapshot files

==========================================
✅ Cleanup Complete!
==========================================

Verification:
  Total files remaining: 0
  drop_anchors.json: {}

✅ All anchor data successfully deleted

The bot will start with fresh anchors on next run.

You can now start the bot with: python3 main.py
```

## After Cleanup

After running either script:
1. The bot will build new anchors from current market data
2. Drop detection will start fresh
3. No historical anchor data will be used
4. The bot may take a few minutes to establish new anchors

## Notes

- **Safe to run while bot is stopped** - Recommended approach
- **Can run while bot is running** - But will warn you (not recommended)
- **Does NOT delete trading positions** - Only anchor data
- **Does NOT delete logs** - Only drop window state
- **Does NOT affect configuration** - Settings remain unchanged

## Troubleshooting

If files remain after cleanup:
1. Check file permissions
2. Ensure no processes have files open
3. Stop the bot completely before running script
4. Run with elevated permissions if needed (sudo)

## Integration

These scripts can be integrated into:
- **Maintenance routines** - Regular anchor resets
- **Deployment pipelines** - Clean state before production
- **Testing workflows** - Fresh state for each test run
- **Cron jobs** - Automated periodic cleanup

## Quick Reference

| Command | Description |
|---------|-------------|
| `./clear_anchors.sh` | Run bash version |
| `python3 clear_anchors.py` | Run Python version |
| `chmod +x clear_anchors.*` | Make scripts executable |

---

**Created:** 2025-10-31
**Version:** 1.0
**Part of:** Trading Bot Professional
