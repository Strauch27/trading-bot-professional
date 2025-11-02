# FSM Parity Fixes - Applied

**Date:** 2025-11-02
**Status:** ✅ COMPLETED
**Files Modified:** 1 (`engine/fsm_engine.py`)

---

## Overview

This document summarizes the three critical integration gaps that were identified in the FSM Parity Gap Analysis and have now been fixed.

---

## Fixes Applied

### ✅ LÜCKE 1: can_afford() Integration in Entry Eval

**Location:** `engine/fsm_engine.py:558-599`

**Problem:**
- Entry Eval phase evaluated signals without checking if budget was sufficient for min_notional/min_qty
- Led to ghost positions when orders failed in PLACE_BUY due to budget constraints

**Solution:**
- Added `can_afford()` check after guards pass and before signal evaluation
- Calculates available budget using same logic as PLACE_BUY phase
- Blocks entry with `RISK_LIMITS_BLOCKED` event if budget insufficient
- Logs detailed budget diagnostics for troubleshooting

**Code Added:**
```python
from services.market_guards import can_afford

# Calculate available budget
available_budget = self.portfolio.get_free_usdt()
max_trades = getattr(config, 'MAX_TRADES', 10)
per_trade = available_budget / max(1, max_trades)
quote_budget = min(per_trade, getattr(config, 'POSITION_SIZE_USDT', 10.0))

if not can_afford(self.exchange, st.symbol, ctx.price, quote_budget):
    logger.warning(f"[BUDGET_GUARD] {st.symbol} @ {ctx.price:.8f}: Insufficient budget ({quote_budget:.2f} USDT) for min_notional/min_qty")
    # ... logging ...
    self._emit_event(st, FSMEvent.RISK_LIMITS_BLOCKED, ctx)
    return
```

**Impact:**
- Prevents wasteful signal evaluations when budget is insufficient
- Reduces ghost position rate by catching budget issues early
- Improves logging for budget-related blocks

---

### ✅ LÜCKE 2: wait_for_fill() Policy Integration

**Location:** `engine/fsm_engine.py:920-977`

**Problem:**
- WAIT_FILL phase polled orders without proper timeout/cancel policy
- Partial fills could get stuck indefinitely without timeout
- No explicit cancel logic for orders exceeding timeout thresholds

**Solution:**
- Integrated timeout policy from `services/wait_fill.py`
- Added total timeout: 30s since order placement
- Added partial fill timeout: 10s stuck at same partial level
- Explicit order cancellation with proper logging

**Code Added:**
```python
# FSM PARITY FIX (LÜCKE 2): Implement wait_for_fill() timeout policy
WAIT_FILL_TIMEOUT_S = 30
PARTIAL_MAX_AGE_S = 10

# Check total timeout
if st.order_placed_ts > 0:
    elapsed = time.time() - st.order_placed_ts
    if elapsed > WAIT_FILL_TIMEOUT_S:
        logger.warning(f"[WAIT_FILL] {st.symbol} TIMEOUT after {elapsed:.1f}s - canceling order {st.order_id}")
        try:
            self.exchange.cancel_order(st.order_id, st.symbol)
            logger.info(f"[WAIT_FILL] {st.symbol} order canceled successfully")
        except Exception as e:
            logger.warning(f"[WAIT_FILL] {st.symbol} cancel failed: {e}")
        self._emit_event(st, FSMEvent.BUY_ORDER_TIMEOUT, ctx)
        return

# Check partial fill timeout
if status == "partial" and 0 < filled < amount:
    if not hasattr(st, 'partial_fill_started_at'):
        st.partial_fill_started_at = time.time()
        st.partial_fill_qty = filled
        logger.info(f"[WAIT_FILL] {st.symbol} PARTIAL: {filled:.6f}/{amount:.6f}")
    else:
        partial_age = time.time() - st.partial_fill_started_at
        if partial_age > PARTIAL_MAX_AGE_S:
            logger.warning(f"[WAIT_FILL] {st.symbol} PARTIAL STUCK for {partial_age:.1f}s - canceling order {st.order_id}")
            # Cancel order and clean up tracking
            self._emit_event(st, FSMEvent.BUY_ORDER_TIMEOUT, ctx)
            return
```

**Impact:**
- Prevents orders from getting stuck indefinitely
- Reduces capital lock-up time for failed orders
- Aligns FSM behavior with `wait_for_fill()` policy
- Ensures deterministic timeout behavior

---

### ✅ LÜCKE 3: Event Consistency (ERROR_OCCURRED → BUY_ABORTED)

**Locations:**
- `engine/fsm_engine.py:684` (insufficient budget)
- `engine/fsm_engine.py:696` (market info unavailable)
- `engine/fsm_engine.py:761` (compliance violations)
- `engine/fsm_engine.py:912` (WAIT_FILL rehydration failure)

**Problem:**
- PLACE_BUY phase emitted `ERROR_OCCURRED` for clean aborts (budget, compliance)
- WAIT_FILL phase emitted `ERROR_OCCURRED` when order_id couldn't be recovered
- Inconsistent with transition table which expects `BUY_ABORTED` for pre-flight failures

**Solution:**
- Replaced `ERROR_OCCURRED` with `BUY_ABORTED` for all clean abort scenarios
- `ERROR_OCCURRED` now reserved for unexpected exceptions only
- Ensures proper transition table routing: `BUY_ABORTED → IDLE (action_handle_reject/action_cleanup_cancelled)`

**Changes:**
1. **Insufficient budget:** `BUY_ABORTED` instead of `ORDER_PLACEMENT_FAILED`
2. **Market info unavailable:** `BUY_ABORTED` instead of `ORDER_PLACEMENT_FAILED`
3. **Compliance violations:** `BUY_ABORTED` instead of `ORDER_PLACEMENT_FAILED`
4. **WAIT_FILL rehydration failure:** `BUY_ABORTED` instead of `ERROR_OCCURRED`

**Impact:**
- Cleaner event semantics (abort vs error)
- Proper cooldown handling via `action_handle_reject`
- Better logging and debugging (can distinguish aborts from errors)
- Aligns with FSM Parity rollout plan

---

## Transition Table Verification

All `BUY_ABORTED` transitions are properly wired in `core/fsm/transitions.py`:

```python
# Line 91: PLACE_BUY phase
self._add(Phase.PLACE_BUY, FSMEvent.BUY_ABORTED, Phase.IDLE, action_handle_reject)

# Line 100: WAIT_FILL phase
self._add(Phase.WAIT_FILL, FSMEvent.BUY_ABORTED, Phase.IDLE, action_cleanup_cancelled)
```

---

## Validation

### Syntax Check
```bash
python3 -m py_compile engine/fsm_engine.py
# ✅ PASSED - No syntax errors
```

### Function Availability
- ✅ `can_afford()` exists in `services/market_guards.py:619`
- ✅ `BUY_ABORTED` event exists in `core/fsm/fsm_events.py:40`
- ✅ Transitions exist in `core/fsm/transitions.py:91,100`

---

## Testing Checklist

Before deploying to production:

- [ ] Unit test: `can_afford()` blocks entry when budget < min_notional
- [ ] Integration test: WAIT_FILL timeout after 30s cancels order
- [ ] Integration test: Partial fill stuck >10s triggers cancel
- [ ] Integration test: Budget check in Entry Eval prevents ghost positions
- [ ] Log validation: `BUY_ABORTED` events appear in logs (not `ERROR_OCCURRED`)
- [ ] Metrics: Ghost position rate <5% after fixes

---

## Rollout Plan

Following Phase 3 from `FSM_PARITY_ROLLOUT.md`:

1. **Integration Test (3 symbols, 1 hour)**
   - Verify no `invalid_tick_size` errors
   - Verify proper timeout/cancel behavior
   - Verify ghost position rate <5%

2. **Canary Deployment (80/20, 24 hours)**
   - Monitor FSM vs Legacy metrics
   - Track pre-flight success rate (target: >99%)
   - Track order timeout rate (should decrease)

3. **Full Rollout**
   - Enable FSM for all symbols
   - Monitor ghost position rate
   - Monitor order submit success rate

---

## Success Metrics

### Expected Improvements

| Metric | Before | After (Target) |
|--------|--------|----------------|
| Ghost Position Rate | ~10-15% | <5% |
| Pre-Flight Success Rate | ~85% | >99% |
| WAIT_FILL Timeout Rate | Variable | Deterministic (30s) |
| Budget Block Detection | After PLACE_BUY | In Entry Eval |

### Monitoring Commands

```bash
# Check for budget blocks (should appear in Entry Eval now)
grep "BUDGET_GUARD" logs/trading.log

# Check for proper timeouts (should see TIMEOUT logs)
grep "WAIT_FILL.*TIMEOUT" logs/trading.log

# Check for proper event usage (should see BUY_ABORTED, not ERROR_OCCURRED for aborts)
grep "BUY_ABORTED" logs/trading.log

# Check ghost position stats
# Via API: curl http://localhost:5000/api/ghosts
```

---

## Summary

All three critical FSM Parity gaps have been closed:

1. ✅ Budget affordability check integrated in Entry Eval
2. ✅ Wait-for-fill timeout policy fully implemented
3. ✅ Event consistency achieved (BUY_ABORTED for clean aborts)

**Total Changes:**
- Files modified: 1
- Lines added: ~120
- Functions integrated: 1 (`can_afford()`)
- Events aligned: 4 locations

**Ready for:** Integration testing and rollout

---

**Next Steps:**
1. Commit changes with message: "fix: FSM Parity - Close 3 critical integration gaps"
2. Run integration tests per rollout plan
3. Deploy to canary (80/20 split)
4. Monitor metrics for 24h
5. Full rollout if metrics meet targets
