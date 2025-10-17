# V9_3 Rollback Guide

**Version:** 1.0
**Last Updated:** 2025-10-17
**Author:** Claude Code (Sonnet 4.5)

---

## Overview

This document provides step-by-step instructions for rolling back the V9_3 stability and reproducibility enhancements if issues arise. The V9_3 implementation includes:

- **4-stream JSONL persistence** (Ticks, Snapshots, Windows, Anchors)
- **Retry/Backoff guards** with ticker validation
- **Warm-start capability** from persisted ticks
- **Atomic persistence** using `.tmp + rename` pattern
- **Feature flags** for granular control

All features are controlled by feature flags in `config.py`, enabling **safe rollback without code changes**.

---

## Quick Rollback (Emergency)

If you need to disable V9_3 enhancements immediately:

### 1. Disable All V9_3 Features

Edit `config.py` and set all feature flags to `False`:

```python
# V9_3 Feature Flags (for rollback capability)
FEATURE_ANCHOR_ENABLED = False       # Disable anchor-based drop trigger
FEATURE_PERSIST_STREAMS = False      # Disable 4-stream JSONL persistence
FEATURE_WARMSTART_TICKS = False      # Disable warm-start from persisted ticks
FEATURE_RETRY_BACKOFF = False        # Disable exponential backoff for failed tickers
```

### 2. Restart the Bot

```bash
# Stop the bot
pkill -f main.py

# Start fresh
python main.py
```

This immediately reverts to legacy behavior without requiring code changes.

---

## Selective Rollback

Roll back specific features while keeping others:

### Disable Persistence Only

Keep V9_3 anchor logic but disable persistence:

```python
FEATURE_PERSIST_STREAMS = False      # Disable JSONL persistence
FEATURE_WARMSTART_TICKS = False      # Disable warm-start
```

**Use Case:** Persistence causing disk I/O issues but anchor logic works fine.

### Disable Warm-Start Only

Keep persistence but disable warm-start:

```python
FEATURE_WARMSTART_TICKS = False      # Disable warm-start only
```

**Use Case:** Warm-start causing startup delays or state corruption.

### Disable Retry/Backoff Only

Keep anchor and persistence but disable retry logic:

```python
FEATURE_RETRY_BACKOFF = False        # Disable exponential backoff
```

**Use Case:** Backoff delays causing slow ticker updates.

### Disable Anchor System Only

Revert to legacy peak-based drop calculation:

```python
FEATURE_ANCHOR_ENABLED = False       # Disable anchor-based drops
```

**Use Case:** Anchor logic producing unexpected triggers.

---

## Complete Rollback (Code-Level)

If feature flags are insufficient, revert code changes:

### Step 1: Identify Baseline Commit

Find the commit before V9_3 implementation:

```bash
git log --oneline --decorate

# Example output:
# abc1234 feat: V9_3 stability enhancements (CURRENT)
# def5678 fix: Market data pipeline (BASELINE)
```

### Step 2: Create Rollback Branch

```bash
# Create rollback branch from baseline
git checkout -b rollback-v9_3 def5678

# Or use hard reset (DESTRUCTIVE)
git reset --hard def5678
```

### Step 3: Verify Rollback

Check that rolled-back code works:

```bash
# Run tests
pytest tests/

# Start bot in observe mode
GLOBAL_TRADING=False python main.py
```

### Step 4: Deploy Rollback

```bash
# Commit rollback (if using branch)
git commit -m "Rollback V9_3 enhancements"

# Push to remote
git push origin rollback-v9_3

# Or force push to main (DANGEROUS)
git push --force origin main
```

---

## File-by-File Rollback

Roll back specific files if only certain components have issues:

### Files Modified in V9_3

#### Core Files
- `config.py` - Feature flags and persistence parameters
- `services/market_data.py` - Retry, guards, persistence, warm-start
- `market/anchor_manager.py` - Atomic persistence
- `services/buy_signals.py` - V9_3 anchor integration (cleanup)
- `ui/dashboard.py` - Spread % column

#### New Files (Can Be Deleted)
- `io/jsonl.py` - RotatingJSONLWriter
- `docs/SNAPSHOT_SCHEMA.md` - Schema documentation
- `docs/ROLLBACK.md` - This file

#### Test Files (Can Be Deleted)
- `tests/test_anchor_manager.py`
- `tests/test_jsonl_writer.py`
- `tests/test_warmstart_integration.py`
- `tests/test_property_based.py`

### Rollback Specific Files

```bash
# Rollback config.py only
git checkout baseline-commit -- config.py

# Rollback market_data.py only
git checkout baseline-commit -- services/market_data.py

# Delete new files
rm io/jsonl.py
rm docs/SNAPSHOT_SCHEMA.md
rm docs/ROLLBACK.md
rm tests/test_anchor_manager.py tests/test_jsonl_writer.py
rm tests/test_warmstart_integration.py tests/test_property_based.py
```

---

## Data Cleanup

After rollback, clean up persisted V9_3 data:

### Remove Persisted State

```bash
# Remove all V9_3 persisted data
rm -rf state/drop_windows/ticks/
rm -rf state/drop_windows/snapshots/
rm -rf state/drop_windows/windows/
rm -rf state/drop_windows/anchors/

# Remove anchor persistence
rm -f state/anchors/anchors.json
rm -f state/anchors/anchors.json.tmp
```

### Keep Historical Data (Optional)

Archive data instead of deleting:

```bash
# Create archive
mkdir -p archives/v9_3_$(date +%Y%m%d)

# Move data to archive
mv state/drop_windows/ archives/v9_3_$(date +%Y%m%d)/
mv state/anchors/ archives/v9_3_$(date +%Y%m%d)/

# Compress archive
tar -czf archives/v9_3_$(date +%Y%m%d).tar.gz archives/v9_3_$(date +%Y%m%d)/
rm -rf archives/v9_3_$(date +%Y%m%d)/
```

---

## Troubleshooting Rollback Issues

### Issue: Bot Won't Start After Rollback

**Symptom:** Bot crashes on startup with import errors.

**Solution:**
```bash
# Check for missing dependencies
pip install -r requirements.txt

# Remove __pycache__
find . -type d -name __pycache__ -exec rm -rf {} +

# Restart Python environment
deactivate && source venv/bin/activate
```

### Issue: Legacy Drop Trigger Not Working

**Symptom:** No buy triggers after rollback.

**Solution:**
1. Verify `DROP_TRIGGER_MODE` is set (default: 4)
2. Check `WINDOW_LOOKBACK_S` is configured (default: 300)
3. Ensure `RollingWindowManager` is initialized in engine

### Issue: Anchor State Corrupted

**Symptom:** Bot crashes when reading persisted anchors.

**Solution:**
```bash
# Delete corrupted anchor state
rm -f state/anchors/anchors.json*

# Restart bot (will cold-start)
python main.py
```

### Issue: Disk Space Full

**Symptom:** Bot crashes with "No space left on device"

**Solution:**
```bash
# Find largest JSONL files
du -sh state/drop_windows/*/* | sort -hr | head -n 10

# Delete old ticks (keep last 2 days)
find state/drop_windows/ticks -name "*.jsonl" -mtime +2 -delete

# Disable persistence temporarily
# In config.py: FEATURE_PERSIST_STREAMS = False
```

---

## Verification After Rollback

Verify bot is working correctly after rollback:

### 1. Check Bot Startup

```bash
# Start bot and watch logs
python main.py 2>&1 | tee logs/rollback_test.log

# Verify no V9_3-related errors
grep -i "warmstart\|jsonl\|retry_state" logs/rollback_test.log
```

### 2. Verify Market Data Loop

Check that market data is being fetched:

```bash
# Watch dashboard
# Should see ticker updates and drop percentages

# Check log for ticker fetches
grep "HEARTBEAT - Ticker fetched" logs/rollback_test.log
```

### 3. Verify Buy Signals

Test that buy triggers work:

```bash
# Set aggressive trigger for testing
# In config.py: DROP_TRIGGER_VALUE = 0.99  # -1% drop

# Watch for buy triggers
grep "BUY TRIGGER" logs/rollback_test.log
```

### 4. Run Tests

```bash
# Run test suite
pytest tests/ -v

# Skip V9_3 tests if files deleted
pytest tests/ -v --ignore=tests/test_anchor_manager.py \
                 --ignore=tests/test_jsonl_writer.py \
                 --ignore=tests/test_warmstart_integration.py \
                 --ignore=tests/test_property_based.py
```

---

## Prevention for Future Rollbacks

### Always Use Feature Flags

When adding new features:

```python
# Good: Feature flag controlled
if getattr(config, 'FEATURE_NEW_THING', True):
    new_feature_code()
else:
    legacy_code()

# Bad: No rollback mechanism
new_feature_code()  # Can't disable without code change
```

### Keep Legacy Code Paths

Don't delete legacy code when adding new features:

```python
# Good: Legacy path preserved
if use_new_pipeline:
    new_pipeline()
else:
    legacy_pipeline()  # Keep this!

# Bad: Legacy code deleted
new_pipeline()  # No fallback
```

### Document Breaking Changes

In commit messages and docs:

```
feat: Add new feature X

BREAKING CHANGES:
- Requires new dependency Y
- Changes config parameter Z
- Rollback: Set FEATURE_X = False in config.py
```

---

## Support Contacts

If rollback issues persist:

1. **Check GitHub Issues:** https://github.com/YOUR_REPO/issues
2. **Review Logs:** `logs/trading_bot_*.log`
3. **Attach Details:**
   - Rollback steps attempted
   - Error messages
   - Config settings
   - Log excerpts

---

## Rollback Checklist

Use this checklist when performing rollback:

- [ ] Backup current config and state files
- [ ] Set feature flags to `False` in `config.py`
- [ ] Restart bot and verify startup
- [ ] Check market data loop is running
- [ ] Verify drop trigger calculations
- [ ] Monitor for 1 hour in observe mode
- [ ] Clean up persisted V9_3 data (optional)
- [ ] Document rollback reason and timestamp
- [ ] Run tests to verify functionality

---

## Change Log

### 2025-10-17 (Version 1.0)
- Initial rollback documentation
- Feature flag rollback procedures
- Code-level rollback instructions
- Troubleshooting guide
- Verification procedures

---

**Document Version:** 1.0
**Compatibility:** V9_3 and later
**Author:** Claude Code (Sonnet 4.5)
