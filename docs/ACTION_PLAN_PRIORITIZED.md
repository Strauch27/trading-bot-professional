# Prioritized Action Plan - Trading Bot Professional
**Erstellt:** 2025-10-31
**Basis:** Comprehensive Code Review + Deep Dive Analysis
**Total Issues:** 30+ identifiziert
**Total Dokumentation:** 3,948 Zeilen Review-Dokumentation

---

## Executive Summary

**Gesamtaufwand:** ~30 Stunden für alle HIGH priority items
**Empfohlene Execution:** 3 Phasen über 3 Wochen
**ROI:** Maximum bei Phase 1 (2.5h → massive production safety improvement)

**Current Status:** ✅ Production-ready BUT should fix Phase 1 critical items first

---

## Table of Contents

1. [Phase 1: CRITICAL - Sofort (< 3 Stunden)](#phase-1-critical---sofort--3-stunden)
2. [Phase 2: HIGH - Diese Woche (< 15 Stunden)](#phase-2-high---diese-woche--15-stunden)
3. [Phase 3: MEDIUM - Dieser Monat (< 12 Stunden)](#phase-3-medium---dieser-monat--12-stunden)
4. [Phase 4: LOW - Nächster Monat (Ongoing)](#phase-4-low---nächster-monat-ongoing)
5. [Quick Reference Summary](#quick-reference-summary)

---

## Phase 1: CRITICAL - Sofort (< 3 Stunden)

**Ziel:** Maximale Production-Sicherheit mit minimalem Aufwand
**Deadline:** Heute/Morgen
**Total Effort:** 2.5 Stunden

---

### ACTION 1.1: Enforce SWITCH_COOLDOWN_S ⚡⚡⚡

**Priority:** CRITICAL
**Effort:** 15 Minuten
**Impact:** Verhindert rapid switching, spart Fees

**Problem:**
```
Config: SWITCH_COOLDOWN_S = 20 (definiert)
Code: Lädt config aber prüft nie! (position_manager.py:184)
Result: Rapid switching möglich, unnecessary fees
```

**Lösung:**

**File:** `engine/position_manager.py`

**Step 1: Locate switch logic (Line ~184-287)**
```python
# Current:
def dynamic_tp_sl_switch(self, symbol: str, data: Dict, current_price: float):
    # ...
    switch_cooldown_s = getattr(config, 'SWITCH_COOLDOWN_S', 20)  # Loaded but unused!

    # SWITCH TO SL
    if pnl_ratio < switch_to_sl_threshold and current_protection == 'TP':
        # FIX C2: Set intermediate state FIRST
        data['active_protection_type'] = 'SWITCHING_TO_SL'
        # ... proceeds immediately without cooldown check!
```

**Step 2: Add cooldown check BEFORE switch**
```python
def dynamic_tp_sl_switch(self, symbol: str, data: Dict, current_price: float):
    # ... existing code until line ~184 ...

    switch_cooldown_s = getattr(config, 'SWITCH_COOLDOWN_S', 20)

    # FIX: Check cooldown BEFORE attempting switch
    last_switch_time = data.get('last_protection_switch_time', 0)
    time_since_last_switch = time.time() - last_switch_time

    if time_since_last_switch < switch_cooldown_s:
        logger.debug(
            f"Switch cooldown active for {symbol}: "
            f"{time_since_last_switch:.1f}s < {switch_cooldown_s}s | "
            f"Skipping switch request",
            extra={
                'event_type': 'SWITCH_COOLDOWN_ACTIVE',
                'symbol': symbol,
                'time_since_last_s': time_since_last_switch,
                'cooldown_s': switch_cooldown_s
            }
        )
        return  # Exit early - cooldown still active

    # Rest of existing switch logic continues...
    # SWITCH TO SL
    if pnl_ratio < switch_to_sl_threshold and current_protection == 'TP':
        # ... existing code ...
```

**Step 3: Verify last_protection_switch_time is set**

Check lines ~271, 334, 342 where switches complete - should already have:
```python
data['last_protection_switch_time'] = time.time()
```

**Already present in C2 fix!** ✅

**Step 4: Test**
```bash
# Syntax check
python3 -m py_compile engine/position_manager.py

# Search for confirmation
grep "last_protection_switch_time" engine/position_manager.py
```

**Verification:**
- Cooldown check added before switch
- Uses config.SWITCH_COOLDOWN_S
- Logs cooldown events
- Returns early if cooldown active

---

### ACTION 1.2: Block Exits on Low Liquidity ⚡⚡⚡

**Priority:** CRITICAL
**Effort:** 45 Minuten
**Impact:** Verhindert predictable "Oversold" failures

**Problem:**
```
Code: Checks spread > 10% → Logs warning → Continues anyway!
Result: Order placed in illiquid market → "Oversold" error → Wasted retries
```

**Lösung:**

**File:** `services/exits.py`

**Step 1: Add config parameters**

**File:** `config.py` (add after EXIT_ESCALATION_BPS around line 383)
```python
# Exit Liquidity Protection
EXIT_MIN_LIQUIDITY_SPREAD_PCT = 10.0  # Block exit if spread > 10%
EXIT_LOW_LIQUIDITY_ACTION = "skip"    # "skip" = return error, "market" = use market order, "wait" = requeue
EXIT_LOW_LIQUIDITY_REQUEUE_DELAY_S = 60  # Delay before retry if action="wait"
```

**Step 2: Modify liquidity check in _try_limit_ioc_exit**

**File:** `services/exits.py` (around line 557-571)

**Current:**
```python
# Line 557-571:
if spread_pct > 5.0:
    logger.warning(f"Low liquidity detected...")

if spread_pct > 10.0:
    logger.error(f"CRITICAL: Extreme low liquidity...")

# ❌ Continues anyway - no action!
```

**Replace with:**
```python
# Check spread-based liquidity
if spread_pct > 5.0:
    logger.warning(
        f"Low liquidity detected for {context.symbol}: "
        f"Spread={spread_pct:.2f}%, Bid={bid:.8f}, Ask={ask:.8f}",
        extra={'event_type': 'LOW_LIQUIDITY_WARNING', 'symbol': context.symbol, 'spread_pct': spread_pct}
    )

# FIX: Block exit if liquidity is critically low
if spread_pct > config.EXIT_MIN_LIQUIDITY_SPREAD_PCT:
    logger.error(
        f"CRITICAL: Spread too wide for safe exit: {spread_pct:.2f}% > {config.EXIT_MIN_LIQUIDITY_SPREAD_PCT}%",
        extra={
            'event_type': 'EXIT_BLOCKED_LOW_LIQUIDITY',
            'symbol': context.symbol,
            'spread_pct': spread_pct,
            'threshold': config.EXIT_MIN_LIQUIDITY_SPREAD_PCT
        }
    )

    # Handle based on configured action
    action = getattr(config, 'EXIT_LOW_LIQUIDITY_ACTION', 'skip')

    if action == "skip":
        return ExitResult(
            success=False,
            reason="low_liquidity",
            error=f"Spread too wide: {spread_pct:.2f}% > {config.EXIT_MIN_LIQUIDITY_SPREAD_PCT}%"
        )

    elif action == "wait":
        # Requeue exit signal for later retry
        delay_s = getattr(config, 'EXIT_LOW_LIQUIDITY_REQUEUE_DELAY_S', 60)
        logger.info(f"Requeuing exit for {context.symbol} after {delay_s}s due to low liquidity")

        # NOTE: This requires signal_manager to have requeue capability
        # For now, just return error - manual intervention needed
        return ExitResult(
            success=False,
            reason="low_liquidity_wait",
            error=f"Requeue needed - spread too wide"
        )

    elif action == "market":
        logger.warning(f"Proceeding with market order despite low liquidity for {context.symbol}")
        # Fall through to market order logic below

    # If action == "market" or unknown, continue with order placement
```

**Step 3: Test**
```bash
# Syntax check
python3 -m py_compile services/exits.py config.py

# Verify config added
grep "EXIT_MIN_LIQUIDITY_SPREAD_PCT" config.py

# Verify logic added
grep -A 10 "EXIT_BLOCKED_LOW_LIQUIDITY" services/exits.py
```

**Verification:**
- Config parameters added
- Spread check blocks order
- Action configurable (skip/market/wait)
- Proper logging

---

### ACTION 1.3: Implement MD_AUTO_RESTART ⚡⚡⚡

**Priority:** CRITICAL
**Effort:** 30 Minuten
**Impact:** Market data thread crash recovery

**Problem:**
```
Config: MD_AUTO_RESTART_ON_CRASH = False (definiert)
Code: Keine Implementierung!
Result: Thread crash → Bot needs manual restart
```

**Lösung:**

**File:** `services/market_data.py`

**Step 1: Find the run() or _loop() method**

Search for: `def run(self):` or `def _loop(self):`

**Step 2: Wrap in auto-restart logic**

**Current pattern (approximate):**
```python
def run(self):
    """Main market data thread loop"""
    while self._running:
        try:
            # Fetch batch of tickers
            self._fetch_batch()
            time.sleep(interval)
        except Exception as e:
            logger.error(f"Market data error: {e}")
            # Currently: No recovery! Thread might die
```

**New pattern:**
```python
def run(self):
    """Main market data thread loop with auto-restart capability"""
    import config

    restart_count = 0
    max_restarts = getattr(config, 'MD_MAX_AUTO_RESTARTS', 5)
    restart_delay_s = getattr(config, 'MD_AUTO_RESTART_DELAY_S', 5.0)

    while self._running:
        try:
            # Main fetch loop
            self._fetch_loop()

            # If we get here, loop exited normally
            logger.info("Market data loop exited normally")
            break

        except Exception as e:
            logger.error(
                f"Market data thread crashed: {e}",
                extra={
                    'event_type': 'MD_THREAD_CRASH',
                    'error': str(e),
                    'restart_count': restart_count
                },
                exc_info=True
            )

            # Check if auto-restart is enabled
            if not getattr(config, 'MD_AUTO_RESTART_ON_CRASH', False):
                logger.critical(
                    "Market data thread crashed and auto-restart is disabled - thread exiting",
                    extra={'event_type': 'MD_THREAD_EXIT_NO_RESTART'}
                )
                self._running = False
                break

            # Check restart limit
            restart_count += 1
            if restart_count > max_restarts:
                logger.critical(
                    f"Market data thread exceeded max auto-restarts ({max_restarts}) - giving up",
                    extra={'event_type': 'MD_THREAD_MAX_RESTARTS_EXCEEDED', 'restart_count': restart_count}
                )
                self._running = False
                break

            # Auto-restart with delay
            logger.warning(
                f"Auto-restarting market data thread in {restart_delay_s}s (restart #{restart_count})",
                extra={'event_type': 'MD_THREAD_AUTO_RESTART', 'restart_count': restart_count, 'delay_s': restart_delay_s}
            )

            time.sleep(restart_delay_s)
            # Loop continues - thread restarts

def _fetch_loop(self):
    """Inner fetch loop (extracted for auto-restart)"""
    while self._running:
        try:
            # All existing fetch logic here
            # ... batch processing ...
            # ... ticker fetching ...
            time.sleep(interval)
        except Exception as e:
            # Let outer loop handle crashes
            raise
```

**Step 3: Add config parameters**

**File:** `config.py` (add after MD_AUTO_RESTART_ON_CRASH around line 252)
```python
MD_AUTO_RESTART_ON_CRASH = True  # Changed default to True for production
MD_MAX_AUTO_RESTARTS = 5  # Maximum restart attempts before giving up
MD_AUTO_RESTART_DELAY_S = 5.0  # Delay between restart attempts
```

**Step 4: Test**
```bash
# Syntax check
python3 -m py_compile services/market_data.py config.py

# Simulate crash (in test environment)
# - Force exception in market data thread
# - Verify auto-restart occurs
# - Verify max restart limit works
```

**Verification:**
- Config defaults to True
- Thread restarts on crash
- Max restart limit prevents infinite loop
- Proper logging for monitoring

---

### ACTION 1.4: Add SNAPSHOT_STALE_TTL_S to Config ⚡

**Priority:** HIGH
**Effort:** 5 Minuten
**Impact:** Konfigurierbarkeit, consistency

**Problem:**
```
Used in: buy_decision.py:89, dashboard.py:261,465
Config: Doesn't exist!
Current: Always uses default (30.0)
```

**Lösung:**

**File:** `config.py`

**Step 1: Add to config (after SNAPSHOT_MIN_PERIOD_MS around line 284)**
```python
# Snapshot Staleness Detection
SNAPSHOT_STALE_TTL_S = 30.0  # Maximum age for snapshot data (seconds)
SNAPSHOT_REQUIRED_FOR_BUY = False  # Block buy decisions if no fresh snapshot (strict mode)
```

**Step 2: Update usages to remove getattr**

**File:** `engine/buy_decision.py:89`
```python
# Before:
stale_ttl = getattr(config, 'SNAPSHOT_STALE_TTL_S', 30.0)

# After:
stale_ttl = config.SNAPSHOT_STALE_TTL_S
```

**File:** `ui/dashboard.py:261,465`
```python
# Before:
stale_ttl = getattr(config_module, 'SNAPSHOT_STALE_TTL_S', 30.0)

# After:
stale_ttl = config_module.SNAPSHOT_STALE_TTL_S
```

**Step 3: Test**
```bash
python3 -m py_compile config.py engine/buy_decision.py ui/dashboard.py
```

---

### ACTION 1.5: Make State Writes Atomic ⚡⚡

**Priority:** CRITICAL
**Effort:** 30 Minuten
**Impact:** Verhindert corrupt state bei crash

**Problem:**
```
Current: JSON write direkt → Crash mid-write → Corrupt file
Impact: Bot can't restart, manual state recovery needed
```

**Lösung:**

**File:** `core/portfolio/portfolio.py`

**Step 1: Create atomic write helper**

Add to portfolio.py (around line 90, after imports):
```python
import tempfile
import shutil

def _atomic_write_json(filepath: str, data: dict, indent: int = 2):
    """
    Atomic JSON file write using temporary file + rename.

    Prevents corruption if crash occurs during write.

    Args:
        filepath: Destination file path
        data: Dict to serialize
        indent: JSON indentation

    Raises:
        IOError: If write fails
    """
    # Create temp file in same directory (ensures same filesystem for atomic rename)
    dir_path = os.path.dirname(filepath)
    fd, tmp_path = tempfile.mkstemp(dir=dir_path, suffix='.tmp', prefix='state_')

    try:
        # Write to temp file
        with os.fdopen(fd, 'w') as f:
            json.dump(data, f, indent=indent)
            f.flush()
            os.fsync(f.fileno())  # Force write to disk

        # Atomic rename (POSIX guarantee)
        shutil.move(tmp_path, filepath)

        logger.debug(f"Atomic write successful: {filepath}")

    except Exception as e:
        # Cleanup temp file on error
        try:
            os.unlink(tmp_path)
        except:
            pass
        raise IOError(f"Atomic write failed for {filepath}: {e}")
```

**Step 2: Replace all JSON writes**

**Find all writes:**
```bash
grep -n "json.dump" core/portfolio/portfolio.py
```

**Replace pattern:**
```python
# Before:
with open(STATE_FILE_HELD, 'w') as f:
    json.dump(data, f, indent=2)

# After:
_atomic_write_json(STATE_FILE_HELD, data)
```

**Locations to update:**
- `_persist_held()` method
- `_persist_open_buys()` method
- Any other state persistence

**Step 3: Test**
```bash
# Syntax check
python3 -m py_compile core/portfolio/portfolio.py

# Integration test:
# 1. Start bot
# 2. Kill -9 during state write (hard to test)
# 3. Verify no .tmp files left
# 4. Verify state intact
```

**Alternative (simpler):**

Use existing `save_state_safe()` function if it already does atomic writes:
```bash
grep -A 20 "def save_state_safe" core/utils.py
```

If it exists and does atomic writes, use it instead of creating new function.

---

### ACTION 1.6: Fix Dashboard MAX_TRADES Usage ⚡

**Priority:** MEDIUM (but trivial, so include in Phase 1)
**Effort:** 5 Minuten
**Impact:** Correct display value

**Problem:**
```
Dashboard: Uses MAX_TRADES (deprecated)
Backend: May use MAX_CONCURRENT_POSITIONS
Result: Displayed value wrong
```

**Lösung:**

**File:** `ui/dashboard.py`

**Step 1: Line 203**
```python
# Before:
"MAX_TRADES": getattr(config_module, 'MAX_TRADES', 0),

# After:
"MAX_POSITIONS": getattr(config_module, 'MAX_CONCURRENT_POSITIONS', 10),
```

**Step 2: Line 500**
```python
# Before:
(f"MAX TRADES: {config_data.get('MAX_TRADES', 0)}", "cyan"),

# After:
(f"MAX POS: {config_data.get('MAX_POSITIONS', 0)}", "cyan"),
```

**Step 3: Test**
```bash
python3 -m py_compile ui/dashboard.py
grep "MAX_TRADES" ui/dashboard.py  # Should return 0 results
```

---

## Phase 1 Summary

**Total Time:** 2.5 Stunden
**Actions:** 6 fixes
**Files Modified:** 4 files (position_manager.py, exits.py, config.py, dashboard.py)

**Verification Script:**
```bash
#!/bin/bash
echo "=== Phase 1 Verification ==="

echo "1. Checking syntax..."
python3 -m py_compile engine/position_manager.py services/exits.py config.py ui/dashboard.py
echo "✅ Syntax OK"

echo "2. Checking SWITCH_COOLDOWN_S enforcement..."
grep -q "time_since_last_switch < switch_cooldown_s" engine/position_manager.py && echo "✅ Cooldown enforced" || echo "❌ Not found"

echo "3. Checking liquidity blocking..."
grep -q "EXIT_BLOCKED_LOW_LIQUIDITY" services/exits.py && echo "✅ Liquidity check blocks" || echo "❌ Not found"

echo "4. Checking MD_AUTO_RESTART..."
grep -q "MD_AUTO_RESTART_ON_CRASH" services/market_data.py && echo "✅ Auto-restart implemented" || echo "❌ Not found"

echo "5. Checking SNAPSHOT_STALE_TTL_S config..."
grep -q "SNAPSHOT_STALE_TTL_S = " config.py && echo "✅ Config added" || echo "❌ Not found"

echo "6. Checking atomic writes..."
grep -q "_atomic_write_json\|save_state_safe" core/portfolio/portfolio.py && echo "✅ Atomic writes used" || echo "❌ Not found"

echo "7. Checking dashboard MAX_TRADES..."
grep -q "MAX_TRADES" ui/dashboard.py && echo "❌ Still uses deprecated" || echo "✅ Migrated to MAX_POSITIONS"

echo ""
echo "=== Phase 1 Complete ==="
```

---

## Phase 2: HIGH - Diese Woche (< 15 Stunden)

**Ziel:** Code quality, thread safety, maintainability
**Deadline:** Ende der Woche
**Total Effort:** 11.5 Stunden

---

### ACTION 2.1: Remove ULTRA DEBUG Code ⚡⚡

**Priority:** HIGH
**Effort:** 1 Stunde
**Impact:** Performance, code cleanliness

**Problem:**
```
27x "ULTRA DEBUG" code in market_data.py
Includes file I/O, bare except, temp file writes
Performance impact, code clutter
```

**Lösung:**

**File:** `services/market_data.py`

**Step 1: Find all ULTRA DEBUG code**
```bash
grep -n "ULTRA DEBUG" services/market_data.py > /tmp/ultra_debug_lines.txt
cat /tmp/ultra_debug_lines.txt
```

**Step 2: Review each occurrence**

**Pattern to remove:**
```python
# ULTRA DEBUG: Write to file
try:
    with open("/tmp/market_data_start.txt", "a") as f:
        f.write(f"{datetime.now()} - Some debug info\n")
        f.flush()
except:
    pass
```

**Decision for each:**
- If genuinely useful → Convert to proper logging
- If debugging leftover → Remove entirely

**Step 3: Replace with proper logging (if needed)**
```python
# Before:
# ULTRA DEBUG: Write to file
try:
    with open("/tmp/market_data_start.txt", "a") as f:
        f.write(f"{datetime.now()} - MarketDataProvider.start() ENTRY\n")
except:
    pass

# After (if genuinely useful):
logger.debug(
    "Market data provider start() called",
    extra={'event_type': 'MD_START_CALLED'}
)

# Or remove if not useful
```

**Step 4: Automated cleanup**
```python
# Create script to help:
cat > /tmp/cleanup_ultra_debug.py << 'EOF'
import re

with open('services/market_data.py', 'r') as f:
    content = f.read()

# Remove ULTRA DEBUG blocks
# Pattern: # ULTRA DEBUG\n try:\n with open... except:\n pass
pattern = r'# ULTRA DEBUG.*?\n.*?try:.*?with open.*?except:.*?pass'
cleaned = re.sub(pattern, '', content, flags=re.DOTALL)

# Show diff
import difflib
diff = difflib.unified_diff(
    content.splitlines(keepends=True),
    cleaned.splitlines(keepends=True),
    lineterm=''
)
print(''.join(diff))
EOF

python3 /tmp/cleanup_ultra_debug.py > /tmp/debug_cleanup_diff.txt
```

**Manual review required!** Don't auto-apply, review diff first.

**Step 5: Test after cleanup**
```bash
python3 -m py_compile services/market_data.py
./venv/bin/ruff check services/market_data.py
python3 main.py --help  # Smoke test
```

---

### ACTION 2.2: Standardize Price Access Pattern ⚡⚡

**Priority:** HIGH
**Effort:** 3 Stunden
**Impact:** Data consistency, thread safety

**Problem:**
```
4 different patterns to get current price:
1. engine.get_current_price() - Not thread-safe
2. engine.get_snapshot_entry() - Thread-safe but complex
3. market_data.get_ticker() - Thread-safe, preferred
4. portfolio.last_prices - Deprecated

Result: Inconsistent data, potential stale prices
```

**Lösung:**

**Step 1: Create standardized price getter**

**File:** Create `core/price_provider.py` (new file)
```python
#!/usr/bin/env python3
"""
Centralized Price Provider
Single source of truth for current prices with thread-safe access
"""

import logging
import time
from typing import Optional

logger = logging.getLogger(__name__)


class PriceProvider:
    """
    Centralized price provider with fallback chain.

    Priority:
    1. Market Data Service (fresh, thread-safe)
    2. Snapshot Store (cached, thread-safe)
    3. Fallback value (if provided)
    """

    def __init__(self, market_data_service, engine):
        self.market_data = market_data_service
        self.engine = engine

    def get_current_price(self, symbol: str, fallback: Optional[float] = None) -> Optional[float]:
        """
        Get current price with fallback chain.

        Args:
            symbol: Trading pair (e.g., "BTC/USDT")
            fallback: Fallback value if no price available

        Returns:
            Current price or fallback, None if no data
        """
        # Priority 1: Market data service (most recent)
        try:
            ticker = self.market_data.get_ticker(symbol)
            if ticker and 'last' in ticker and ticker['last']:
                return float(ticker['last'])
        except Exception as e:
            logger.debug(f"Market data fetch failed for {symbol}: {e}")

        # Priority 2: Snapshot store (cached but consistent)
        try:
            if hasattr(self.engine, 'get_snapshot_entry'):
                snapshot, snap_ts = self.engine.get_snapshot_entry(symbol)
                if snapshot:
                    price_data = snapshot.get('price', {})
                    last_price = price_data.get('last')
                    if last_price:
                        # Check if snapshot is recent enough
                        import config
                        max_age = getattr(config, 'SNAPSHOT_STALE_TTL_S', 30.0)
                        if snap_ts and (time.time() - snap_ts) < max_age:
                            return float(last_price)
        except Exception as e:
            logger.debug(f"Snapshot fetch failed for {symbol}: {e}")

        # Priority 3: Fallback
        if fallback is not None:
            return fallback

        # No price available
        logger.warning(
            f"No price available for {symbol}",
            extra={'event_type': 'PRICE_NOT_AVAILABLE', 'symbol': symbol}
        )
        return None

    def get_safe_price(self, symbol: str, default: float = 0.0) -> float:
        """
        Get price with guaranteed return (never None).

        Args:
            symbol: Trading pair
            default: Default value if no price available

        Returns:
            Current price or default (never None)
        """
        price = self.get_current_price(symbol, fallback=default)
        return price if price is not None else default
```

**Step 2: Integrate into engine**

**File:** `engine/engine.py` (in __init__)
```python
from core.price_provider import PriceProvider

class TradingEngine:
    def __init__(self, ...):
        # ... existing initialization ...

        # Add price provider
        self.price_provider = PriceProvider(
            market_data_service=self.market_data,
            engine=self
        )
```

**Step 3: Migrate all price access**

**Files to update:**
- `ui/dashboard.py:220` - Use `engine.price_provider.get_safe_price()`
- `engine/buy_decision.py` - Use price_provider
- `engine/position_manager.py` - Use price_provider

**Example migration:**
```python
# Before:
current_price = engine.get_current_price(symbol)
if not current_price:
    snap, snap_ts = engine.get_snapshot_entry(symbol)
    if snap:
        current_price = snap.get('price', {}).get('last')
if not current_price:
    current_price = 0.0

# After:
current_price = engine.price_provider.get_safe_price(symbol, default=0.0)
```

**Step 4: Mark engine.get_current_price() as deprecated**
```python
def get_current_price(self, symbol):
    """
    DEPRECATED: Use engine.price_provider.get_safe_price() instead

    This method will be removed in future version.
    """
    logger.warning(f"get_current_price() is deprecated, use price_provider")
    return self.price_provider.get_current_price(symbol)
```

**Step 5: Test**
```bash
python3 -m py_compile core/price_provider.py engine/engine.py ui/dashboard.py

# Run bot briefly to verify no errors
python3 main.py &
BOT_PID=$!
sleep 30
kill $BOT_PID

# Check logs for price provider usage
grep "PRICE_NOT_AVAILABLE" sessions/*/logs/*.jsonl
```

---

### ACTION 2.3: Fix Portfolio Access Patterns ⚡⚡

**Priority:** HIGH
**Effort:** 3 Stunden
**Impact:** Thread safety

**Problem:**
```
Mixed patterns:
- portfolio.positions (direct, unsafe)
- portfolio.get_position() (safe)
- portfolio.get_all_positions() (safe)
- portfolio.held_assets (legacy)
```

**Lösung:**

**Step 1: Audit all portfolio.positions accesses**
```bash
grep -rn "portfolio\.positions" --include="*.py" --exclude-dir=venv . | grep -v "def " > /tmp/portfolio_access.txt
cat /tmp/portfolio_access.txt
```

**Step 2: Categorize each usage**
```
Read single:    portfolio.positions.get(symbol)     → migrate to portfolio.get_position(symbol)
Read all:       portfolio.positions.keys()          → migrate to portfolio.get_all_positions().keys()
Iteration:      for s in portfolio.positions:       → migrate to for s in portfolio.get_all_positions():
Direct write:   portfolio.positions[s] = {...}      → Need setter!
```

**Step 3: Add position setter to Portfolio class**

**File:** `core/portfolio/portfolio.py`

```python
@synchronized
def set_position(self, symbol: str, position: Position):
    """
    Thread-safe position setter.

    Args:
        symbol: Trading pair
        position: Position object

    Note: Prefer using open_position() for new positions
    """
    self.positions[symbol] = position
    self._persist_state()

@synchronized
def update_position_meta(self, symbol: str, **meta_updates):
    """
    Thread-safe position metadata update.

    Args:
        symbol: Trading pair
        **meta_updates: Key-value pairs to update in position.meta

    Returns:
        True if updated, False if position not found
    """
    position = self.positions.get(symbol)
    if position:
        position.meta.update(meta_updates)
        self._persist_state()
        return True
    return False
```

**Step 4: Migrate each unsafe access**

**Example:**
```python
# Before:
portfolio.positions[symbol] = position_data

# After:
portfolio.set_position(symbol, position_data)

# Before:
portfolio_position = portfolio.positions.get(symbol)
portfolio_position.meta['tp_order_id'] = tp_order_id

# After:
portfolio.update_position_meta(symbol, tp_order_id=tp_order_id)
```

**Step 5: Update recent fixes (H2, C2)**

**Files:** `engine/buy_decision.py`, `engine/position_manager.py`

Replace direct meta updates with:
```python
# Before:
portfolio_position.meta['tp_order_id'] = tp_order_id
portfolio_position.meta['current_protection'] = 'TP'

# After:
portfolio.update_position_meta(
    symbol,
    tp_order_id=tp_order_id,
    current_protection='TP'
)
```

**Step 6: Test**
```bash
python3 -m py_compile core/portfolio/portfolio.py engine/buy_decision.py engine/position_manager.py

# Verify no direct .positions access remains (except in Portfolio class itself)
grep -rn "portfolio\.positions\[" --include="*.py" --exclude-dir=venv . | grep -v "portfolio.py:"
```

---

### ACTION 2.4: Add Portfolio Setters (Encapsulation) ⚡

**Priority:** HIGH
**Effort:** 2 Stunden
**Impact:** Thread safety, encapsulation

**Problem:**
```
portfolio.my_budget = 100.0  # Direct write, not thread-safe
engine.positions[symbol] = data  # Direct write, not thread-safe
```

**Lösung:**

**File:** `core/portfolio/portfolio.py`

**Add setters:**
```python
@synchronized_budget
def set_budget(self, amount: float):
    """
    Thread-safe budget setter.

    Args:
        amount: New budget amount (USDT)
    """
    old_budget = self.my_budget
    self.my_budget = float(amount)

    logger.info(
        f"Budget updated: {old_budget:.2f} → {amount:.2f}",
        extra={
            'event_type': 'BUDGET_UPDATED',
            'old_budget': old_budget,
            'new_budget': amount
        }
    )

    self._persist_state()

@synchronized_budget
def adjust_budget(self, delta: float, reason: str = "adjustment"):
    """
    Thread-safe budget adjustment.

    Args:
        delta: Amount to add (positive) or subtract (negative)
        reason: Reason for adjustment (for logging)
    """
    old_budget = self.my_budget
    self.my_budget += delta

    logger.info(
        f"Budget adjusted: {old_budget:.2f} {delta:+.2f} = {self.my_budget:.2f} (reason: {reason})",
        extra={
            'event_type': 'BUDGET_ADJUSTED',
            'old_budget': old_budget,
            'delta': delta,
            'new_budget': self.my_budget,
            'reason': reason
        }
    )

    self._persist_state()
```

**Find all direct budget writes:**
```bash
grep -rn "portfolio\.my_budget\s*=" --include="*.py" --exclude-dir=venv .
grep -rn "self\.my_budget\s*=" --include="*.py" core/portfolio/
```

**Migrate each:**
```python
# Before:
portfolio.my_budget = 100.0

# After:
portfolio.set_budget(100.0)

# Before:
portfolio.my_budget += pnl

# After:
portfolio.adjust_budget(pnl, reason="realized_pnl")
```

**Test:**
```bash
python3 -m py_compile core/portfolio/portfolio.py

# Verify no direct writes remain (except in Portfolio class)
grep -rn "portfolio\.my_budget\s*[+\-*/]?=" --include="*.py" --exclude-dir=venv . | grep -v "portfolio.py:"
```

---

### ACTION 2.5: Migrate Deprecated Variables ⚡

**Priority:** MEDIUM (but easy, so include)
**Effort:** 30 Minuten
**Impact:** Code consistency

**Problem:**
```
MODE → 6 usages (should use DROP_TRIGGER_MODE)
POLL_MS → 5 usages (should use MD_POLL_MS)
MAX_TRADES → 13 usages! (should use MAX_CONCURRENT_POSITIONS)
```

**Lösung:**

**Step 1: Create migration script**
```bash
cat > tools/migrate_deprecated_vars.sh << 'EOF'
#!/bin/bash
# Migrate deprecated config variables

echo "=== Migrating Deprecated Variables ==="

# Backup first
git stash
git checkout -b feature/migrate-deprecated-vars

# Replace MODE with DROP_TRIGGER_MODE
echo "1. Migrating MODE → DROP_TRIGGER_MODE..."
find . -name "*.py" -not -path "./venv/*" -not -path "./sessions/*" -not -path "./.git/*" \
  -exec sed -i '' 's/\bconfig\.MODE\b/config.DROP_TRIGGER_MODE/g' {} \;
find . -name "*.py" -not -path "./venv/*" -not -path "./sessions/*" -not -path "./.git/*" \
  -exec sed -i '' 's/getattr(config, *["\']MODE["\']/getattr(config, "DROP_TRIGGER_MODE"/g' {} \;

# Replace POLL_MS with MD_POLL_MS
echo "2. Migrating POLL_MS → MD_POLL_MS..."
find . -name "*.py" -not -path "./venv/*" -not -path "./sessions/*" -not -path "./.git/*" \
  -exec sed -i '' 's/\bconfig\.POLL_MS\b/config.MD_POLL_MS/g' {} \;
find . -name "*.py" -not -path "./venv/*" -not -path "./sessions/*" -not -path "./.git/*" \
  -exec sed -i '' 's/getattr(config, *["\']POLL_MS["\']/getattr(config, "MD_POLL_MS"/g' {} \;

# Replace MAX_TRADES with MAX_CONCURRENT_POSITIONS
echo "3. Migrating MAX_TRADES → MAX_CONCURRENT_POSITIONS..."
find . -name "*.py" -not -path "./venv/*" -not -path "./sessions/*" -not -path "./.git/*" \
  -exec sed -i '' 's/\bconfig\.MAX_TRADES\b/config.MAX_CONCURRENT_POSITIONS/g' {} \;
find . -name "*.py" -not -path "./venv/*" -not -path "./sessions/*" -not -path "./.git/*" \
  -exec sed -i '' 's/getattr(config, *["\']MAX_TRADES["\']/getattr(config, "MAX_CONCURRENT_POSITIONS"/g' {} \;

# Verify
echo ""
echo "=== Verification ==="
echo "MODE usages remaining:"
grep -r "\bMODE\b" --include="*.py" --exclude-dir=venv . | grep -v "DROP_TRIGGER_MODE" | wc -l

echo "POLL_MS usages remaining:"
grep -r "\bPOLL_MS\b" --include="*.py" --exclude-dir=venv . | grep -v "MD_POLL_MS" | wc -l

echo "MAX_TRADES usages remaining:"
grep -r "\bMAX_TRADES\b" --include="*.py" --exclude-dir=venv . | grep -v "MAX_CONCURRENT_POSITIONS" | wc -l

echo ""
echo "Done! Review changes with: git diff"
EOF

chmod +x tools/migrate_deprecated_vars.sh
```

**Step 2: Run migration (AFTER backup)**
```bash
# Backup current state
git add -A
git commit -m "Backup before deprecated var migration"

# Run migration
./tools/migrate_deprecated_vars.sh

# Review changes
git diff

# Test
python3 -m py_compile $(git diff --name-only | grep "\.py$")
```

**Step 3: Manual review**

Check each change, especially:
- Variable names in strings (shouldn't be replaced)
- Comments (can be replaced)
- Code logic (should be replaced)

**Step 4: Update config.py comments**
```python
# config.py - Update deprecation notices:
MODE = DROP_TRIGGER_MODE  # DEPRECATED (removed in v2.0): Use DROP_TRIGGER_MODE
POLL_MS = MD_POLL_MS  # DEPRECATED (removed in v2.0): Use MD_POLL_MS
MAX_TRADES = MAX_CONCURRENT_POSITIONS  # DEPRECATED (removed in v2.0): Use MAX_CONCURRENT_POSITIONS
```

---

### ACTION 2.6: Fix Position Iteration Safety ⚡

**Priority:** HIGH
**Effort:** 2 Stunden
**Impact:** Prevents KeyError crashes

**Problem:**
```python
for symbol in portfolio.positions:  # ❌ Dict can change during iteration
    position = portfolio.positions[symbol]  # ❌ KeyError if removed
```

**Lösung:**

**Step 1: Find all position iterations**
```bash
grep -rn "for.*portfolio\.positions" --include="*.py" --exclude-dir=venv . > /tmp/position_iterations.txt
grep -rn "for.*engine\.positions" --include="*.py" --exclude-dir=venv . >> /tmp/position_iterations.txt
cat /tmp/position_iterations.txt
```

**Step 2: Replace with safe pattern**

**Pattern A: Iteration with processing**
```python
# Before (UNSAFE):
for symbol in portfolio.positions:
    position = portfolio.positions[symbol]
    process(position)

# After (SAFE):
positions = portfolio.get_all_positions()  # Returns locked copy
for symbol, position in positions.items():
    process(position)
```

**Pattern B: Iteration with keys only**
```python
# Before (UNSAFE):
for symbol in portfolio.positions.keys():
    do_something(symbol)

# After (SAFE):
symbols = list(portfolio.get_all_positions().keys())  # Snapshot
for symbol in symbols:
    do_something(symbol)
```

**Pattern C: Iteration with removal**
```python
# Before (UNSAFE):
for symbol in portfolio.positions:
    if should_remove(symbol):
        del portfolio.positions[symbol]  # ❌ Modifying during iteration!

# After (SAFE):
positions = portfolio.get_all_positions()
symbols_to_remove = [s for s, p in positions.items() if should_remove(s)]
for symbol in symbols_to_remove:
    portfolio.close_position(symbol)  # Thread-safe removal
```

**Step 3: Update each location**

Go through `/tmp/position_iterations.txt` and apply appropriate pattern.

**Step 4: Test**
```bash
# Syntax check all modified files
python3 -m py_compile $(cat /tmp/position_iterations.txt | cut -d: -f1 | sort -u)

# Run bot with multiple positions to test iteration
python3 main.py
# Monitor for KeyError or RuntimeError
```

---

## Phase 2 Summary

**Total Time:** 11.5 Stunden
**Actions:** 6 major fixes
**Files Modified:** ~15 files

**Verification Script:**
```bash
#!/bin/bash
echo "=== Phase 2 Verification ==="

echo "1. ULTRA DEBUG removed?"
count=$(grep -c "ULTRA DEBUG" services/market_data.py)
[ $count -eq 0 ] && echo "✅ All removed" || echo "❌ Still $count remaining"

echo "2. Price provider created?"
[ -f "core/price_provider.py" ] && echo "✅ Created" || echo "❌ Not found"

echo "3. Portfolio setters added?"
grep -q "def set_budget\|def set_position" core/portfolio/portfolio.py && echo "✅ Added" || echo "❌ Missing"

echo "4. Deprecated vars migrated?"
mode_count=$(grep -r "\bconfig\.MODE\b" --include="*.py" --exclude-dir=venv . | grep -v "DROP_TRIGGER_MODE" | wc -l)
[ $mode_count -eq 0 ] && echo "✅ MODE migrated" || echo "❌ $mode_count usages remain"

echo "5. Safe iteration patterns?"
unsafe=$(grep -r "for.*portfolio\.positions\b" --include="*.py" --exclude-dir=venv . | wc -l)
[ $unsafe -eq 0 ] && echo "✅ All safe" || echo "⚠️ $unsafe possibly unsafe"

echo ""
echo "=== Phase 2 Complete ==="
```

---

## Phase 3: MEDIUM - Dieser Monat (< 12 Stunden)

**Ziel:** Feature completeness, validation
**Deadline:** Ende des Monats
**Total Effort:** 6-7 Stunden

---

### ACTION 3.1: Add Missing Config Parameters

**Priority:** MEDIUM
**Effort:** 2 Stunden
**Impact:** Configurability

**Add to config.py:**

```python
# =============================================================================
# SECTION: Advanced Tuning Parameters (Auto-generated from code review)
# =============================================================================

# Buy Flow
MAX_PENDING_BUY_INTENTS = 100  # Maximum pending buy intents before eviction
PENDING_INTENT_TTL_S = 300  # Auto-clear intents older than 5 minutes

# Exit Flow
EXIT_INTENT_TTL_S = 300  # Auto-clear stuck exit intents after 5 minutes
EXIT_MIN_LIQUIDITY_SPREAD_PCT = 10.0  # Block exit if spread exceeds this %
EXIT_LOW_LIQUIDITY_ACTION = "skip"  # "skip", "market", or "wait"
EXIT_LOW_LIQUIDITY_REQUEUE_DELAY_S = 60  # Requeue delay if action="wait"

# Position Management
POSITION_LOCK_TIMEOUT_S = 30  # Max wait time for position lock (deadlock prevention)

# Order Router Cleanup (from H5 fix - previously hardcoded)
ROUTER_CLEANUP_INTERVAL_S = 3600  # Run cleanup every hour
ROUTER_COMPLETED_ORDER_TTL_S = 7200  # Keep completed orders for 2 hours

# Market Data Thread
MD_MAX_AUTO_RESTARTS = 5  # Maximum auto-restart attempts
MD_AUTO_RESTART_DELAY_S = 5.0  # Delay between restart attempts
MD_ULTRA_DEBUG_ENABLED = False  # Enable ultra-verbose debugging (dev only)
MD_DEBUG_FILE_PATH = "/tmp/market_data_debug.txt"  # Debug file location

# Snapshot Management
SNAPSHOT_STALE_TTL_S = 30.0  # Maximum age for snapshot data
SNAPSHOT_REQUIRED_FOR_BUY = False  # Block buys if no fresh snapshot
```

**Update code to use these:**

**File:** `services/order_router.py:106-107`
```python
# Before:
self._cleanup_interval_s = 3600  # Hardcoded
self._completed_order_ttl_s = 7200  # Hardcoded

# After:
self._cleanup_interval_s = getattr(config, 'ROUTER_CLEANUP_INTERVAL_S', 3600)
self._completed_order_ttl_s = getattr(config, 'ROUTER_COMPLETED_ORDER_TTL_S', 7200)
```

**File:** `engine/buy_decision.py:579`
```python
# Before:
MAX_PENDING_INTENTS = 100  # Hardcoded

# After:
MAX_PENDING_INTENTS = getattr(config, 'MAX_PENDING_BUY_INTENTS', 100)
```

**Similar for all other new configs.**

---

### ACTION 3.2: Implement Budget Reservation in Buy Flow

**Priority:** MEDIUM
**Effort:** 1 Stunde
**Impact:** Prevents budget race condition

**Lösung:**

**File:** `engine/buy_decision.py`

**Step 1: Reserve budget before order**

Find where order is placed (around line 560-600):
```python
# Before:
quote_budget = self._calculate_quote_budget(symbol, current_price, coin_data)
if not quote_budget:
    return

# ... prepare order ...
engine.place_order(...)

# After:
quote_budget = self._calculate_quote_budget(symbol, current_price, coin_data)
if not quote_budget:
    return

# FIX: Reserve budget atomically
reservation_id = self.engine.portfolio.reserve_budget(quote_budget, timeout_s=30.0)
if not reservation_id:
    logger.warning(
        f"Failed to reserve budget for {symbol}: {quote_budget:.2f} USDT",
        extra={'event_type': 'BUDGET_RESERVATION_FAILED', 'symbol': symbol, 'amount': quote_budget}
    )
    return

# Store reservation ID in intent metadata
intent_metadata['budget_reservation_id'] = reservation_id

try:
    # ... prepare and place order ...
    self.engine.place_order(...)

    # Success - reservation will be committed when order fills

except Exception as e:
    # Failure - release reservation
    self.engine.portfolio.release_reservation(reservation_id)
    logger.error(f"Order placement failed for {symbol}, budget reservation released: {e}")
    raise
```

**Step 2: Commit reservation on fill**

Find fill handler (around line 1000+):
```python
def handle_router_fill(self, intent, filled_qty, avg_price, fees):
    # ... existing code ...

    # FIX: Commit budget reservation
    reservation_id = intent.metadata.get('budget_reservation_id')
    if reservation_id:
        self.engine.portfolio.commit_reservation(reservation_id)
        logger.debug(f"Budget reservation committed: {reservation_id}")
```

**Step 3: Release on failure**

Find failure handler:
```python
def handle_router_failure(self, intent, error):
    # ... existing code ...

    # FIX: Release budget reservation
    reservation_id = intent.metadata.get('budget_reservation_id')
    if reservation_id:
        self.engine.portfolio.release_reservation(reservation_id)
        logger.debug(f"Budget reservation released due to failure: {reservation_id}")
```

---

### ACTION 3.3: Add Cross-Parameter Validation

**Priority:** MEDIUM
**Effort:** 1 Stunde
**Impact:** Prevents illogical config

**Lösung:**

**File:** `config.py`

**Step 1: Find validate_config() function (around line 885)**

**Step 2: Add validation section**
```python
def validate_config():
    """Validate configuration with cross-parameter checks"""

    # ... existing validations ...

    # ========================================
    # Cross-Parameter Validation
    # ========================================

    # TP/SL Threshold Logic
    if not (STOP_LOSS_THRESHOLD < 1.0 < TAKE_PROFIT_THRESHOLD):
        raise ValueError(
            f"Invalid TP/SL thresholds: SL({STOP_LOSS_THRESHOLD}) must be < 1.0 < TP({TAKE_PROFIT_THRESHOLD})"
        )

    if not (STOP_LOSS_THRESHOLD < SWITCH_TO_SL_THRESHOLD < 1.0):
        raise ValueError(
            f"Invalid switch thresholds: SL({STOP_LOSS_THRESHOLD}) < SWITCH_TO_SL({SWITCH_TO_SL_THRESHOLD}) < 1.0"
        )

    if not (1.0 < SWITCH_TO_TP_THRESHOLD < TAKE_PROFIT_THRESHOLD):
        raise ValueError(
            f"Invalid switch thresholds: 1.0 < SWITCH_TO_TP({SWITCH_TO_TP_THRESHOLD}) < TP({TAKE_PROFIT_THRESHOLD})"
        )

    # Market Data TTL Logic
    if MD_CACHE_SOFT_TTL_MS >= MD_CACHE_TTL_MS:
        raise ValueError(
            f"Soft TTL must be less than hard TTL: "
            f"soft({MD_CACHE_SOFT_TTL_MS}) >= hard({MD_CACHE_TTL_MS})"
        )

    if MD_PORTFOLIO_SOFT_TTL_MS >= MD_PORTFOLIO_TTL_MS:
        raise ValueError(
            f"Portfolio soft TTL must be less than hard TTL: "
            f"soft({MD_PORTFOLIO_SOFT_TTL_MS}) >= hard({MD_PORTFOLIO_TTL_MS})"
        )

    # Position Limits
    if MAX_PER_SYMBOL_USD < POSITION_SIZE_USDT:
        raise ValueError(
            f"MAX_PER_SYMBOL_USD({MAX_PER_SYMBOL_USD}) must be >= POSITION_SIZE_USDT({POSITION_SIZE_USDT})"
        )

    # Budget vs Position Size
    if SAFE_MIN_BUDGET < POSITION_SIZE_USDT:
        logger.warning(
            f"SAFE_MIN_BUDGET({SAFE_MIN_BUDGET}) < POSITION_SIZE_USDT({POSITION_SIZE_USDT}) - "
            "Bot may not be able to open positions"
        )

    # Drop Trigger Logic
    if DROP_TRIGGER_VALUE >= 1.0:
        raise ValueError(
            f"DROP_TRIGGER_VALUE({DROP_TRIGGER_VALUE}) must be < 1.0 (represents drop from peak)"
        )

    # Anchor Clamps
    if ANCHOR_MAX_START_DROP_PCT < 0:
        raise ValueError(f"ANCHOR_MAX_START_DROP_PCT must be >= 0")

    if ANCHOR_CLAMP_MAX_ABOVE_PEAK_PCT < 0:
        raise ValueError(f"ANCHOR_CLAMP_MAX_ABOVE_PEAK_PCT must be >= 0")

    # Success
    logger.info("✅ Cross-parameter validation passed")
```

**Step 3: Test with invalid configs**
```python
# Test script:
cat > test_config_validation.py << 'EOF'
import config

# Save original values
orig_tp = config.TAKE_PROFIT_THRESHOLD
orig_sl = config.STOP_LOSS_THRESHOLD

# Test: TP < SL (should fail)
config.TAKE_PROFIT_THRESHOLD = 0.95
config.STOP_LOSS_THRESHOLD = 0.99

try:
    config.validate_config()
    print("❌ Should have failed!")
except ValueError as e:
    print(f"✅ Validation correctly failed: {e}")

# Restore
config.TAKE_PROFIT_THRESHOLD = orig_tp
config.STOP_LOSS_THRESHOLD = orig_sl
EOF

python3 test_config_validation.py
```

---

### ACTION 3.4: Callback Signature Consistency

**Priority:** MEDIUM
**Effort:** 1-2 Stunden
**Impact:** Tracing completeness

**Lösung:**

**Step 1: Standardize callback signatures**

**Create callback interface:**
```python
# core/callback_types.py (new file)
from typing import Any, Dict, Optional, Protocol

class OrderCallback(Protocol):
    """Standard callback interface for order events"""

    def on_order_placed(self,
                       intent_id: str,
                       symbol: str,
                       side: str,
                       decision_id: Optional[str] = None,
                       metadata: Optional[Dict] = None):
        """Called when order is placed"""
        ...

    def on_order_filled(self,
                       intent_id: str,
                       symbol: str,
                       filled_qty: float,
                       avg_price: float,
                       fees: float,
                       decision_id: Optional[str] = None):
        """Called when order is filled"""
        ...

    def on_order_failed(self,
                       intent_id: str,
                       symbol: str,
                       error: str,
                       decision_id: Optional[str] = None):
        """Called when order fails"""
        ...
```

**Step 2: Update all callbacks to include decision_id**

Example:
```python
# Before:
def handle_router_fill(self, intent, filled_qty, avg_price, fees):
    # decision_id might be buried in intent.metadata

# After:
def handle_router_fill(self,
                      intent,
                      filled_qty: float,
                      avg_price: float,
                      fees: float,
                      decision_id: Optional[str] = None):
    """
    Handle order fill with consistent signature.

    Args:
        intent: Order intent object
        filled_qty: Filled quantity
        avg_price: Average fill price
        fees: Fees paid
        decision_id: Decision ID for tracing (extracted from intent.metadata if None)
    """
    # Extract decision_id if not provided
    if decision_id is None:
        decision_id = intent.metadata.get('decision_id')

    # Now all callbacks have decision_id available consistently
    logger.info(
        f"Order filled: {intent.symbol}",
        extra={'decision_id': decision_id, ...}
    )
```

---

## Phase 3 Summary

**Total Time:** 6-7 Stunden
**Actions:** 4 improvements
**Impact:** Feature completeness, maintainability

---

## Phase 4: LOW - Nächster Monat (Ongoing)

**Ziel:** Long-term code quality
**Deadline:** Flexibel
**Effort:** Ongoing

---

### ACTION 4.1: Remove Duplicate faulthandler Import

**File:** `main.py`
**Line:** 15
**Action:** Delete line `import faulthandler` (already imported on line 1)
**Effort:** 10 seconds

---

### ACTION 4.2: Remove Unused Import

**File:** `core/logging/logger_setup.py`
**Line:** 42
**Import:** `adaptive_logger.should_log_performance_metric`
**Action:** Remove or use it
**Effort:** 1 minute

---

### ACTION 4.3: Implement USE_ATR_BASED_EXITS OR Remove

**Priority:** LOW (feature decision needed)
**Effort:** 2-3 Tage (implement) ODER 15 Minuten (remove)

**Option A: Remove (RECOMMENDED for now)**
```python
# config.py - Comment out:
# USE_ATR_BASED_EXITS = False  # Not yet implemented - planned for future release
# ATR_PERIOD = 14
# ATR_SL_MULTIPLIER = 0.6
# ATR_TP_MULTIPLIER = 1.6
# ATR_MIN_SAMPLES = 15

# Add note:
# Note: ATR-based exits are planned but not yet implemented.
# Current implementation uses fixed percentage TP/SL thresholds.
```

**Option B: Implement (if feature is needed)**

Requires:
1. ATR calculation module
2. Integration in position_manager.py
3. Dynamic TP/SL calculation based on volatility
4. Testing with various market conditions

**Estimated:** 2-3 days full implementation

---

### ACTION 4.4: Code Style Cleanup

**E501 Line Length:** ~150 violations

**Automated fix:**
```bash
# Use black formatter
pip install black
black --line-length 120 engine/ services/ core/ ui/

# Or manual:
# Configure editor to warn at 120 chars
# Fix on edit gradually
```

**N802 Naming Violations:**

`core/logger_factory.py` - DECISION_LOG() functions

**Option A: Keep and document**
```python
# These are intentionally uppercase as they act like pseudo-constants
# returning logger instances. Documented deviation from PEP8.
def DECISION_LOG():
    """Get decision logger (uppercase intentional for constant-like usage)"""
    return get_logger("decision")
```

**Option B: Refactor**
```python
# Change to constants:
DECISION_LOGGER = get_logger("decision")
ORDER_LOGGER = get_logger("order")
# ... use throughout code
```

---

## Quick Reference Summary

### Critical Path (Phase 1) - DO THIS FIRST

| # | Action | File | Effort | Why Critical |
|---|--------|------|--------|--------------|
| 1.1 | Enforce cooldown | position_manager.py | 15min | Prevents fee waste |
| 1.2 | Block low liquidity | exits.py, config.py | 45min | Prevents failures |
| 1.3 | Auto-restart MD | market_data.py, config.py | 30min | Reliability |
| 1.4 | Add SNAPSHOT_STALE_TTL_S | config.py | 5min | Consistency |
| 1.5 | Atomic state writes | portfolio.py | 30min | Data integrity |
| 1.6 | Fix dashboard MAX_TRADES | dashboard.py | 5min | Correct display |

**Total:** 2.5 hours → Maximum safety improvement

---

### High Priority (Phase 2) - THIS WEEK

| # | Action | Effort | Impact |
|---|--------|--------|--------|
| 2.1 | Remove ULTRA DEBUG | 1hr | Performance |
| 2.2 | Standardize price access | 3hr | Consistency |
| 2.3 | Fix portfolio access | 3hr | Thread safety |
| 2.4 | Add portfolio setters | 2hr | Encapsulation |
| 2.5 | Migrate deprecated vars | 30min | Cleanup |
| 2.6 | Fix position iteration | 2hr | Crash prevention |

**Total:** 11.5 hours → Code quality & safety

---

### Medium Priority (Phase 3) - THIS MONTH

| # | Action | Effort | Impact |
|---|--------|--------|--------|
| 3.1 | Add missing configs | 2hr | Tunability |
| 3.2 | Budget reservation | 1hr | Race prevention |
| 3.3 | Cross-validation | 1hr | Config safety |
| 3.4 | Callback consistency | 1-2hr | Tracing |

**Total:** 5-6 hours → Feature completeness

---

### Low Priority (Phase 4) - NEXT MONTH

| # | Action | Effort | Impact |
|---|--------|--------|--------|
| 4.1 | Remove duplicate imports | 1min | Cleanup |
| 4.2 | Remove unused imports | 1min | Cleanup |
| 4.3 | ATR implementation OR removal | 15min-3days | Feature decision |
| 4.4 | Code style cleanup | Ongoing | Readability |

---

## Implementation Order Recommendation

### Week 1: Critical + Quick Wins

**Monday (3 hours):**
- Phase 1 complete (2.5h)
- Verify and test (0.5h)

**Tuesday (2 hours):**
- Remove ULTRA DEBUG (1h)
- Migrate deprecated vars (30min)
- Fix dashboard (5min)
- Buffer time (25min)

### Week 2: Thread Safety

**Day 1-2 (6 hours):**
- Standardize price access (3h)
- Fix portfolio access (3h)

**Day 3 (4 hours):**
- Add portfolio setters (2h)
- Fix position iteration (2h)

### Week 3: Polish

**Day 1-2 (6 hours):**
- Add missing configs (2h)
- Budget reservation (1h)
- Cross-validation (1h)
- Callback consistency (2h)

---

## Testing Strategy for Each Phase

### Phase 1 Testing

```bash
# After each fix:
1. Syntax check: python3 -m py_compile <file>
2. Linting: ./venv/bin/ruff check <file>
3. Unit test: pytest tests/ -k <relevant_test>
4. Integration: Run bot for 5 minutes
5. Monitor logs for new errors
```

### Phase 2 Testing

```bash
# After all Phase 2 fixes:
1. Full syntax check: python3 -m py_compile $(find . -name "*.py" -not -path "./venv/*")
2. Run bot for 30 minutes
3. Monitor for:
   - Price fetch errors
   - Position iteration errors
   - Budget reservation issues
4. Check performance (should improve after ULTRA DEBUG removal)
```

### Phase 3 Testing

```bash
# After all Phase 3 fixes:
1. Config validation test with invalid values
2. Full 2-hour bot run
3. Verify all new configs are honored
4. Performance baseline
```

---

## Rollback Plan

For each phase:

**Before starting:**
```bash
git add -A
git commit -m "Checkpoint before Phase X"
git tag phase-X-checkpoint
```

**If issues arise:**
```bash
git reset --hard phase-X-checkpoint
git tag -d phase-X-checkpoint
```

**For production:**
```bash
# Keep old version running
# Deploy new version in parallel
# Monitor for 1 hour
# Switch traffic OR rollback
```

---

## Success Metrics

### Phase 1 Success Criteria

- [ ] SWITCH_COOLDOWN_S events in logs
- [ ] EXIT_BLOCKED_LOW_LIQUIDITY events when spread high
- [ ] MD thread auto-restart working (simulate crash)
- [ ] No corrupt state files after crash test
- [ ] Dashboard displays MAX_POSITIONS correctly

### Phase 2 Success Criteria

- [ ] Zero ULTRA DEBUG in code
- [ ] Price access through single provider
- [ ] Zero unsafe portfolio iteration
- [ ] Budget setters used everywhere
- [ ] Deprecated variable usage = 0

### Phase 3 Success Criteria

- [ ] All new configs working
- [ ] Budget reservation prevents double-spend
- [ ] Invalid configs rejected at startup
- [ ] Callbacks have consistent signatures

---

## Dependencies Between Actions

**Must do in order:**
1. Phase 1 before production
2. 1.4 (SNAPSHOT_STALE_TTL_S) before 2.2 (price access)
3. 2.4 (portfolio setters) before 2.3 (portfolio access fix)
4. 3.1 (missing configs) before 3.2 (budget reservation)

**Can do in parallel:**
- 1.1, 1.2, 1.3 (independent)
- 2.1, 2.5 (independent)
- All Phase 3 actions (independent)

---

## Total Effort Summary

| Phase | Hours | Impact | When |
|-------|-------|--------|------|
| Phase 1 | 2.5 | ⚡⚡⚡ Maximum | TODAY |
| Phase 2 | 11.5 | ⚡⚡ High | This Week |
| Phase 3 | 6-7 | ⚡ Medium | This Month |
| Phase 4 | Ongoing | ℹ️ Low | Next Month |

**Total:** ~20-21 hours for all critical/high items

---

## Final Recommendation

**START WITH:**
1. Enforce SWITCH_COOLDOWN_S (15 min)
2. Block low liquidity exits (45 min)
3. Implement MD_AUTO_RESTART (30 min)

**These 3 fixes = 90 minutes = Massive production safety improvement!**

After these, the bot is significantly more robust for production use.

---

**Document Version:** 1.0
**Last Updated:** 2025-10-31
**Total Actions:** 20+
**Priority Focus:** Production Safety → Code Quality → Features
