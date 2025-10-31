# Session Analysis: session_20251026_180621

**Session Start:** 2025-10-26 18:06:21
**Session Duration:** ~9.5 minutes
**Run ID:** d588d1dc

---

## CRITICAL ISSUES

### 1. ❌ CRITICAL: JsonlWriter AttributeError - Order Flow Crash
**Severity:** CRITICAL
**Location:** `services/order_router.py:783`
**Timestamp:** 2025-10-26T18:08:15.911Z

**Error:**
```
AttributeError: 'JsonlWriter' object has no attribute 'order_failed'
```

**Stack Trace:**
```
File "/engine/engine.py", line 1188, in _on_order_intent
    self.order_router.handle_intent(intent_data)
File "/services/order_router.py", line 783, in handle_intent
    self.tl.order_failed(...)
AttributeError: 'JsonlWriter' object has no attribute 'order_failed'
```

**Context:**
- Order was canceled: `C02__611193389798432768094` (filled 0.0/616.067)
- Order Router tried to log failure via telemetry writer
- JsonlWriter class does not have `order_failed()` method

**Impact:**
- Order flow crashes when orders are canceled
- Prevents proper error handling and logging
- Could lead to stale order intents in system

**Root Cause:**
- Mismatch between OrderRouter's expected telemetry interface and JsonlWriter's actual interface
- OrderRouter expects a method `order_failed()` that doesn't exist

**Recommended Fix:**
1. Add `order_failed()` method to JsonlWriter class
2. OR: Update OrderRouter to use correct JsonlWriter method name
3. Add defensive try/except around telemetry calls in OrderRouter

---

### 2. ⚠️ CRITICAL: Persistent MD_POLL_OVERRUN - Market Data Pipeline Bottleneck
**Severity:** CRITICAL
**Frequency:** 98+ occurrences in 9.5 minutes (~10.3/min)
**Average Overrun:** 5.2 seconds (target: 1.5s)

**Statistics:**
- **Target Interval:** 1.5 seconds (MD_POLL_MS=1500)
- **Observed Cycle Time:** 3.3s - 21.5s
- **Average Cycle Time:** ~6.5s (4.3x slower than target)
- **Worst Case:** 21.51s (14.3x slower)

**Impact:**
- Market data is 4-6 seconds stale on average
- Drop detection delays (missing fast price movements)
- Reduced trading opportunities
- System cannot keep up with configured poll rate

**Root Cause Analysis:**
Based on RCA Session_20251026_171254:
1. ✅ FIXED: `get_ohlcv_history()` AttributeError
2. ✅ FIXED: XPLUS/USDT BadSymbol retries (18 symbols × retries)
3. Still present: Slow API responses or network latency
4. Still present: Large watchlist (130 symbols) overwhelming 1.5s target

**Current Watchlist Size:** 130 symbols after XPLUS/USDT removal

**Remaining Issues:**
- Batch size may be too small (MD_BATCH_SIZE=13 symbols)
- API rate limiting or slow exchange responses
- Too many concurrent API calls
- Network latency issues

**Recommended Fixes:**
1. **Increase Poll Interval:**
   - Change `MD_POLL_MS` from 1500 to 3000-4000ms
   - This matches observed 6.5s average better than 1.5s

2. **Optimize Batching:**
   - Increase `MD_BATCH_SIZE` from 13 to 20-25
   - Adjust `MD_BATCH_INTERVAL_MS` from 150ms to 100ms

3. **Implement Tiered Polling:**
   - Hot symbols (recently dropped): Poll every 1.5s
   - Warm symbols (active positions): Poll every 3s
   - Cold symbols (watchlist): Poll every 5-10s

4. **Add Performance Monitoring:**
   - Log per-symbol fetch latency
   - Identify slow symbols and degrade temporarily
   - Track exchange API response times

---

### 3. ⚠️ HIGH: Heartbeat Timeout Warnings
**Severity:** HIGH
**Frequency:** Every 30 seconds after 5:21 minutes
**Location:** Shutdown coordinator heartbeat monitor

**Errors:**
```
Heartbeat timeout: 314.4s > 300.0s (warn-only mode) | no beat history available
Heartbeat timeout: 344.4s > 300.0s (warn-only mode) | no beat history available
...
Heartbeat timeout: 554.4s > 300.0s (warn-only mode) | no beat history available
```

**Timeline:**
- First timeout: 18:11:35 (314.4s = 5m 14s after start)
- Subsequent timeouts: Every ~30s
- Last timeout: 18:15:35 (554.4s = 9m 14s after start)

**Root Cause:**
- Heartbeat monitor expects heartbeat every 300s (5 minutes)
- System has no beat history available
- Either heartbeat thread not running OR not publishing beats

**Impact:**
- Could indicate thread deadlock or starvation
- Suggests system health monitoring is broken
- In production mode (not warn-only), this would trigger shutdown

**Recommended Fix:**
1. Check if heartbeat thread is started properly
2. Verify heartbeat publishing mechanism
3. Check for thread starvation due to MD_POLL_OVERRUN
4. Add heartbeat history initialization at startup

---

### 4. ⚠️ MEDIUM: Log Cleanup Errors
**Severity:** MEDIUM
**Frequency:** 2 occurrences
**Timestamps:** 18:06:33, 18:11:33

**Errors:**
```
Could not ensure base log dir: argument should be a str or an os.PathLike object where __fspath__ returns a str, not 'NoneType'
Log cleanup error: expected str, bytes or os.PathLike object, not NoneType
```

**Root Cause:**
- Log directory path is None/uninitialized
- Lazy initialization issue or config not loaded
- Could be related to earlier phase 1-6 fixes

**Impact:**
- Log rotation/cleanup may fail
- Could lead to disk space issues over time
- Non-critical but indicates config initialization problem

**Recommended Fix:**
1. Ensure SESSION_DIR is initialized before log setup
2. Add defensive checks in log cleanup code
3. Verify lazy initialization order (config.init_runtime_config())

---

### 5. ⚠️ MEDIUM: Debug Tracer File Handler Setup Failed
**Severity:** MEDIUM
**Timestamp:** 2025-10-26T18:08:14.357Z

**Error:**
```
Debug tracer file handler setup failed: expected str, bytes or os.PathLike object, not NoneType
```

**Root Cause:**
- Similar to log cleanup error
- Debug tracer path is None/uninitialized
- ENGINE_DEBUG_TRACE_FILE or related path not set

**Impact:**
- Debug tracing disabled (may be intentional)
- Cannot capture detailed engine traces
- Non-critical for normal operation

**Recommended Fix:**
1. Check ENGINE_DEBUG_TRACE configuration
2. Add fallback path if None
3. Make debug tracer optional/graceful degradation

---

## PERFORMANCE ANALYSIS

### Market Data Pipeline Performance

**Configured vs Actual:**
- **Target Poll Interval:** 1.5 seconds
- **Actual Average:** 6.5 seconds (4.3x slower)
- **Success Rate:** Unknown (no MD_HEALTH events logged)

**Cycle Duration Distribution:**
- Min: 3.31s
- Max: 21.51s
- P50: ~6.0s
- P95: ~8.5s
- P99: ~21.5s

**Overrun Severity:**
- < 5s: 18% of cycles
- 5-7s: 64% of cycles
- 7-10s: 15% of cycles
- > 10s: 3% of cycles (worst case 21.51s)

### Expected Improvements from Recent Fixes

Based on commit `ec5460e` (RCA fixes):

1. **get_ohlcv_history() Fix:**
   - Before: Crashed on every market conditions update
   - After: Should work (not observed in logs - may not be called often)

2. **XPLUS/USDT Removal:**
   - Before: 18 BadSymbol retries per cycle
   - After: 0 BadSymbol retries
   - Expected speedup: ~0.5-1.0s per cycle (if retries were slow)

3. **BadSymbol Skip-List:**
   - Before: BadSymbol errors degraded for 30s, then retried
   - After: Permanent skip (no retries)
   - Expected: Prevents future BadSymbol waste

4. **MD_POLL_OVERRUN Event Logging:**
   - Before: Only warned in logs
   - After: Events with metrics (confirmed working)

**Verdict:** Fixes are working but MD_POLL still overruns severely. Root cause is likely:
- API latency (exchange responses slow)
- Too many symbols for 1.5s target
- Network issues

---

## TRADING ACTIVITY

**Activity:** None observed
**Positions:** 0 held assets
**Orders:** 0 open buy orders
**State:** Bot running but no trades executed

**Events:**
- Budget reserved/released (2 cycles observed)
- Budget committed (1 occurrence)
- No buy intents generated
- No sell intents generated

**Possible Reasons:**
1. No drop triggers detected (MD_POLL too slow?)
2. Guards blocking all opportunities
3. Market conditions not suitable
4. Drop detection pipeline working but no qualifying drops

---

## CONFIGURATION SNAPSHOT

**Key Settings:**
- `MD_POLL_MS`: 1500 (1.5s target - **TOO AGGRESSIVE**)
- `DROP_TRIGGER_VALUE`: 0.985 (-1.5% drop)
- `DROP_TRIGGER_MODE`: 4 (Anchor Reset)
- `DROP_TRIGGER_LOOKBACK_MIN`: 5 minutes
- `POSITION_SIZE_USDT`: 25.0
- `MAX_CONCURRENT_POSITIONS`: 10
- `GLOBAL_TRADING`: true
- `ORDER_FLOW_ENABLED`: true
- `Watchlist Size`: 130 symbols (after XPLUS/USDT removal)

**Bottlenecks:**
- MD_POLL_MS=1500 is unrealistic for 130 symbols
- Actual cycle time 6.5s means effective poll rate is ~4.3x slower

---

## RECOMMENDATIONS

### IMMEDIATE FIXES (Priority 1 - CRITICAL)

#### 1. Fix JsonlWriter AttributeError
**File:** `services/order_router.py:783` or `telemetry/jsonl_writer.py`

**Option A:** Add method to JsonlWriter
```python
def order_failed(self, symbol, reason, **kwargs):
    """Log order failure event"""
    self.write({
        'event_type': 'ORDER_FAILED',
        'symbol': symbol,
        'reason': reason,
        **kwargs
    })
```

**Option B:** Fix OrderRouter call
```python
# Instead of:
self.tl.order_failed(...)

# Use:
self.tl.write({'event_type': 'ORDER_FAILED', ...})
```

**Option C:** Add try/except wrapper (defensive)
```python
try:
    self.tl.order_failed(...)
except AttributeError:
    logger.warning(f"Telemetry logging failed for order {coid}")
```

#### 2. Adjust MD_POLL_MS to Realistic Value
**File:** `config.py`

```python
# Current (unrealistic):
MD_POLL_MS = 1500  # 1.5s

# Recommended:
MD_POLL_MS = 6000  # 6s (matches actual performance)
# OR
MD_POLL_MS = 4000  # 4s (optimistic but achievable)
```

**Impact:**
- Eliminates 98+ warnings per 10 minutes
- Reduces system stress
- More honest about actual performance
- Allows headroom for variance

### HIGH PRIORITY FIXES (Priority 2)

#### 3. Fix Heartbeat Timeout
**File:** `core/shutdown_coordinator.py` or similar

**Investigation needed:**
1. Verify heartbeat thread is started
2. Check if `record_heartbeat()` is called
3. Verify heartbeat interval matches timeout (300s)

**Quick Fix:**
```python
# Increase timeout to match actual interval
HEARTBEAT_TIMEOUT_S = 600  # 10 minutes instead of 5
```

#### 4. Fix Log Cleanup NoneType Errors
**File:** `core/logging/log_manager.py` or similar

**Add defensive checks:**
```python
def cleanup_old_logs(log_dir):
    if log_dir is None:
        logger.warning("Log directory not initialized, skipping cleanup")
        return
    # ... rest of cleanup
```

### MEDIUM PRIORITY IMPROVEMENTS (Priority 3)

#### 5. Optimize Market Data Pipeline

**A. Implement Tiered Polling**
```python
# Hot symbols (recent drops, active positions): 2s
# Warm symbols (watchlist monitored): 5s
# Cold symbols (background monitoring): 10s
```

**B. Increase Batch Size**
```python
MD_BATCH_SIZE = 20  # Increased from 13
MD_BATCH_INTERVAL_MS = 100  # Reduced from 150
```

**C. Add Performance Metrics**
```python
# Log per-symbol fetch latency
# Track slow symbols
# Auto-degrade problematic symbols
```

#### 6. Add BadSymbol Detection Logging
**Monitor effectiveness of skip-list:**
```python
# Log when symbols are added to skip-list
# Report skip-list size periodically
# Alert if skip-list grows unexpectedly
```

### MONITORING & VALIDATION

#### Post-Fix Validation Steps:

1. **Run 30-minute test session**
   - Monitor MD_POLL_OVERRUN count (should be 0)
   - Verify no JsonlWriter errors
   - Check heartbeat timeouts resolved

2. **Check Performance Metrics**
   - Cycle duration should match MD_POLL_MS ± 0.5s
   - Success rate should be > 95%
   - No degraded symbols (unless legitimately bad)

3. **Validate Order Flow**
   - Trigger a test order
   - Verify telemetry logging works
   - Check order_failed() path doesn't crash

4. **Log Analysis**
   - No ERROR or CRITICAL events (except expected)
   - No NoneType path errors
   - Heartbeat history populates correctly

---

## SUMMARY

### Critical Issues Found: 5

1. ❌ **JsonlWriter AttributeError** - Crashes order flow
2. ⚠️ **MD_POLL_OVERRUN** - 4.3x slower than configured (98+ warnings)
3. ⚠️ **Heartbeat Timeout** - Health monitoring broken
4. ⚠️ **Log Cleanup Errors** - NoneType path issues (2 occurrences)
5. ⚠️ **Debug Tracer Setup Failed** - NoneType path issue

### Fixes Implemented (Commit ec5460e):
✅ Market Conditions API fixed (get_ohlcv_history → fetch_ohlcv)
✅ XPLUS/USDT removed from watchlist
✅ BadSymbol skip-list implemented
✅ MD_POLL_OVERRUN event logging added

### Outstanding Issues:
❌ JsonlWriter.order_failed() missing
❌ MD_POLL_MS too aggressive for actual performance
❌ Heartbeat timeout warnings
❌ NoneType path errors in log cleanup

### Next Steps:
1. Fix JsonlWriter interface mismatch (CRITICAL)
2. Adjust MD_POLL_MS to 4000-6000ms (HIGH)
3. Investigate heartbeat timeout (HIGH)
4. Fix NoneType path errors (MEDIUM)
5. Implement tiered polling for scalability (MEDIUM)

---

**Analysis Generated:** 2025-10-26
**Session Analyzed:** session_20251026_180621
**Total Runtime:** ~9.5 minutes
**Errors Found:** 5 categories, 100+ occurrences
**Trading Activity:** 0 trades executed
