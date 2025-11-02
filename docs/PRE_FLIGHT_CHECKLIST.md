# PRE-FLIGHT CHECKLIST - Production Readiness
## FSM Trading Bot - Go/No-Go Decision

**Purpose:** Systematically verify all critical fixes are in place and tested before enabling FSM in production.

**Status Convention:**
- ☐ Not Started
- 🔄 In Progress
- ✅ Complete
- ❌ Failed/Blocked

---

## PHASE 1: CODE FIXES VERIFICATION

### P0 Fixes (Critical - MUST PASS)

- [ ] **Fix #1: Legacy Preflight Validation**
  - File: `services/buy_service.py:412-460`
  - Verification: `grep -A20 "CRITICAL FIX (P0 Issue #1)" services/buy_service.py`
  - Expected: Preflight validation runs before every order
  - Test: Scenario A (BLESS min_notional)

- [ ] **Fix #2: Legacy PARTIAL Fill Handling**
  - File: `services/buy_service.py:467-557`
  - Verification: `grep -A10 "CRITICAL FIX (P0 Issue #2)" services/buy_service.py`
  - Expected: PARTIAL fills update portfolio incrementally
  - Test: Scenario B (PARTIAL fill weighted average)

### P1 Fixes (High Priority - MUST PASS)

- [ ] **Fix #3: FSM Preflight Error Transition**
  - Files: `core/fsm/exceptions.py`, `core/fsm/fsm_machine.py:86-128`, `core/fsm/actions.py:96-111`
  - Verification: `grep -n "FSMTransitionAbort" core/fsm/exceptions.py`
  - Expected: Preflight failures abort to IDLE with cooldown
  - Test: Manual trigger with invalid order size

- [ ] **Fix #4: COID Retry Duplicate Check**
  - File: `services/order_service.py:403-428, 597-645`
  - Verification: `grep -A15 "CRITICAL FIX (P1 Issue #4)" services/order_service.py`
  - Expected: Duplicate errors trigger COID fetch/recovery
  - Test: Simulate network timeout after order placement

- [ ] **Fix #8: Sell Slippage Pre-Check**
  - File: `core/fsm/actions.py:418-498`
  - Verification: `grep -A30 "CRITICAL FIX (P1 Issue #8)" core/fsm/actions.py`
  - Expected: Flash crash protection blocks sells > 10% loss
  - Test: Manual price manipulation (paper trading only)

- [ ] **Fix #9: Portfolio Cleanup Atomicity**
  - File: `engine/exit_handler.py:200-231`
  - Verification: `grep -A20 "CRITICAL FIX (P1 Issue #9)" engine/exit_handler.py`
  - Expected: Atomic cleanup with lock
  - Test: Concurrent sell + new buy signal

### P2 Fixes (Medium Priority - SHOULD PASS)

- [ ] **Fix #7: Position TTL Enforcement**
  - Files: `core/fsm/timeouts.py:133-173`, `core/fsm/transitions.py:107`
  - Verification: `grep -n "check_position_ttl" core/fsm/timeouts.py`
  - Expected: Positions force-closed after TRADE_TTL_MIN
  - Test: Hold position > TTL, verify force exit

- [ ] **Fix #6: Exit Fee Tracking Multi-Leg**
  - Files: `services/exits.py:42-62`, `engine/exit_handler.py:100-143`
  - Verification: `grep -A10 "all_orders" services/exits.py`
  - Expected: All IOC ladder fees aggregated
  - Test: Multi-step exit ladder, verify total fees

### P3 Fixes (Low Priority - NICE TO HAVE)

- [ ] **Fix #10: Decision-ID Propagation**
  - Files: `services/buy_service.py:90-98`, `services/exits.py` (multiple locations)
  - Verification: `grep -n "decision_id.*Propagate" services/buy_service.py services/exits.py`
  - Expected: End-to-end tracing from signal to close
  - Test: Check logs for consistent decision_id

---

## PHASE 2: CONFIGURATION

### Critical Config Settings (GO/NO-GO)

- [ ] **FSM_ENABLED**
  - Current: `grep "^FSM_ENABLED" config.py`
  - Required: `True` (only after all tests pass)
  - **⚠️ DO NOT set to True until this checklist is complete**

- [ ] **NEVER_MARKET_SELLS**
  - Current: `grep "^NEVER_MARKET_SELLS" config.py`
  - Required: `True` (until exit ladder verified)
  - Action: Update to `True` in config.py

- [ ] **MAX_SLIPPAGE_BPS_EXIT**
  - Current: `grep "^MAX_SLIPPAGE_BPS_EXIT" config.py`
  - Required: `500` (5% max exit slippage)
  - Action: Update from `12` to `500`

- [ ] **ENTRY_BLOCK_COOLDOWN_S**
  - Current: `grep "^ENTRY_BLOCK_COOLDOWN_S" config.py`
  - Required: `60` (minimum 60 seconds)
  - Action: **ADD** to config.py if missing

### Timeout Settings

- [ ] **BUY_FILL_TIMEOUT_SECS**
  - Required: `30`
  - Action: ADD to config.py if missing

- [ ] **SELL_FILL_TIMEOUT_SECS**
  - Required: `30`
  - Action: ADD to config.py if missing

- [ ] **EXIT_IOC_TTL_MS**
  - Required: `500`
  - Action: ADD to config.py if missing

- [ ] **TRADE_TTL_MIN**
  - Current: Should be `120`
  - Verify: `grep "^TRADE_TTL_MIN" config.py`

### Parity Settings (FSM ↔ Legacy)

- [ ] **MAX_SPREAD_BPS_ENTRY**
  - Current: `grep "^MAX_SPREAD_BPS_ENTRY" config.py`
  - Required: Same value in FSM and Legacy flows
  - Verify: Check both `config.py` and usage in code

- [ ] **MAX_SLIPPAGE_BPS_ENTRY**
  - Current: `grep "^MAX_SLIPPAGE_BPS_ENTRY" config.py`
  - Required: Same value in FSM and Legacy flows

- [ ] **BUY_ESCALATION_STEPS**
  - Current: `grep -A5 "^BUY_ESCALATION_STEPS" config.py`
  - Required: Identical array in FSM and Legacy

- [ ] **EXIT_LADDER_BPS**
  - Current: `grep "^EXIT_LADDER_BPS" config.py`
  - Required: Matches `sell_service.py` ladder steps

### Paper Trading / Safety Settings

- [ ] **API Keys Isolation**
  - ⚠️ Using paper trading keys OR isolated sub-account?
  - ⚠️ Max balance < $100 for initial tests?
  - **GO/NO-GO:** Must use isolated environment

- [ ] **Position Sizing (Conservative for Testing)**
  - MAX_OPEN_POSITIONS: Reduce to `3`
  - POSITION_SIZE_USDT: Reduce to `15.0`
  - MAX_TOTAL_EXPOSURE_USDT: Reduce to `75.0`
  - Action: Temporarily reduce for smoke tests

- [ ] **Whitelist (Single Symbol)**
  - WHITELIST: Set to `["BLESS/USDT"]` for initial tests
  - Action: Comment out multi-symbol lists

- [ ] **Logging (Debug Mode)**
  - LOG_LEVEL: Set to `"DEBUG"`
  - Action: Enable verbose logging for smoke tests

---

## PHASE 3: SMOKE TESTS

### Test Execution

- [ ] **Run Smoke Test Script**
  ```bash
  python tests/smoke_tests.py --all --symbol BLESS/USDT
  ```
  - Expected: All scenarios (A-E) pass
  - Report: `smoke_test_report.json`

### Individual Scenario Verification

- [ ] **Scenario A: Entry Parity**
  ```bash
  python tests/smoke_tests.py --scenario A
  ```
  - **PASS Criteria:** Preflight auto-bumps min_notional, order places successfully
  - **FAIL Action:** Review `services/buy_service.py` Fix #1

- [ ] **Scenario B: PARTIAL Fill**
  ```bash
  python tests/smoke_tests.py --scenario B
  ```
  - **PASS Criteria:** Weighted average calculated correctly, position increments
  - **FAIL Action:** Review `core/fsm/actions.py` Fix #4

- [ ] **Scenario C: Exit Ladder**
  ```bash
  python tests/smoke_tests.py --scenario C
  ```
  - **PASS Criteria:** Config verified, NEVER_MARKET_SELLS=True
  - **FAIL Action:** Update config.py

- [ ] **Scenario D: Recovery**
  ```bash
  python tests/smoke_tests.py --scenario D
  ```
  - **PASS Criteria:** Recovery infrastructure verified
  - **FAIL Action:** Review `core/fsm/recovery.py` Fix #7

- [ ] **Scenario E: Dashboard**
  ```bash
  python tests/smoke_tests.py --scenario E
  ```
  - **PASS Criteria:** Dashboard flush code detected
  - **FAIL Action:** Review `core/fsm/fsm_machine.py` Fix #9

---

## PHASE 4: MINIMAL START SEQUENCE (Paper Trading)

### Pre-Launch Checklist

- [ ] **Environment Variables Set**
  ```bash
  export MEXC_API_KEY="<paper-trading-key>"
  export MEXC_API_SECRET="<paper-trading-secret>"
  export FSM_ENABLED=true
  ```

- [ ] **Single Symbol Mode**
  - Whitelist contains ONLY `BLESS/USDT`
  - No other symbols active

- [ ] **Position Limits Conservative**
  - MAX_OPEN_POSITIONS = 3
  - POSITION_SIZE_USDT = 15.0
  - Verified in config.py

- [ ] **Logs Directory Writable**
  ```bash
  mkdir -p sessions/
  mkdir -p sessions/session_test/logs
  ```

### Launch Command

```bash
# Dry-run to verify config
python main.py --dry-run --config config.py

# Actual start (paper trading)
python main.py --config config.py --symbol BLESS/USDT
```

### Stop Criteria (Auto-Halt)

- [ ] **Halt Conditions Configured**
  - HALT_ON_RECONCILE_MISMATCH = True
  - HALT_ON_ORDER_RACE_DETECTED = True
  - MAX_CONSECUTIVE_ERRORS = 3

- [ ] **Manual Stop Triggers Defined**
  - Any RECONCILE_MISMATCH → immediate stop
  - Order cache race detected → immediate stop
  - 3+ consecutive order failures → immediate stop

---

## PHASE 5: LIVE MONITORING (First 24 Hours)

### Real-Time Checks

- [ ] **Dashboard Lag < 200ms**
  - Monitor: Phase transitions appear instantly
  - Metric: `dashboard_flush_latency_ms` in logs

- [ ] **No Orphaned Orders**
  - Monitor: Order cache vs. exchange reality
  - Check: `reconcile_report.json` after each session

- [ ] **PARTIAL Fills Handled**
  - Monitor: Portfolio qty matches filled_qty (not order_qty)
  - Check: Logs for `PARTIAL_UPDATE` events

- [ ] **No Guard Loops**
  - Monitor: GUARDS_BLOCKED followed by 60s+ cooldown
  - Check: No rapid IDLE→ENTRY_EVAL→IDLE cycles

### Log Analysis

- [ ] **Review Decision Logs**
  ```bash
  grep "DECISION" sessions/session_*/logs/events_*.jsonl | jq .
  ```
  - Look for: consistent decision_ids, no orphaned decisions

- [ ] **Review Order Logs**
  ```bash
  grep "ORDER" sessions/session_*/logs/events_*.jsonl | jq .
  ```
  - Look for: all orders have decision_id, no duplicates

- [ ] **Review FSM Transitions**
  ```bash
  grep "fsm_transition" sessions/session_*/logs/*.jsonl | jq .
  ```
  - Look for: clean transitions, no stuck states

---

## PHASE 6: ACCEPTANCE CRITERIA (Go-Live Decision)

### Quantitative Metrics (MUST MEET)

- [ ] **Entry Parity: 10/10**
  - Run 10 identical signals through FSM and Legacy
  - Expected: 10/10 decisions identical
  - Actual: ____/10
  - **GO/NO-GO:** Must be 10/10

- [ ] **Zero Orphaned Orders**
  - After 24h paper trading
  - Expected: 0 orphaned orders (reconcile report)
  - Actual: ____
  - **GO/NO-GO:** Must be 0

- [ ] **PARTIAL Fill Accuracy**
  - All PARTIAL fills result in correct PnL
  - Expected: PnL diff < 0.1% vs. manual calculation
  - Actual: ____%
  - **GO/NO-GO:** Must be < 0.1%

- [ ] **Restart Idempotency**
  - Restart bot with open positions/orders
  - Expected: No drift after recovery
  - Actual: Pass/Fail
  - **GO/NO-GO:** Must Pass

- [ ] **Dashboard Responsiveness**
  - All phase transitions visible within 200ms
  - Expected: 100% within 200ms
  - Actual: ____%
  - **GO/NO-GO:** Must be > 95%

### Qualitative Checks (SHOULD MEET)

- [ ] **Code Review Complete**
  - All P0-P3 fixes reviewed by second person
  - No critical TODOs in codebase

- [ ] **Documentation Complete**
  - Critical Review Doc finalized
  - Config Parity Doc updated
  - Runbook for common issues

- [ ] **Rollback Plan Ready**
  - Can revert to Legacy mode in < 5 minutes
  - FSM_ENABLED=False tested and verified

---

## FINAL GO/NO-GO DECISION

### GO Criteria (ALL must be TRUE)

- ✅ All P0 fixes verified and tested
- ✅ All P1 fixes verified and tested
- ✅ Config matches `config_production_ready.patch`
- ✅ All smoke tests (A-E) pass
- ✅ 24h paper trading with 0 critical errors
- ✅ All acceptance criteria met (10/10, 0 orphans, etc.)
- ✅ Dashboard lag < 200ms
- ✅ Rollback plan tested

### NO-GO Criteria (ANY triggers halt)

- ❌ Any P0 fix failing
- ❌ Smoke tests failing
- ❌ Orphaned orders detected
- ❌ Reconcile mismatches
- ❌ Order cache races
- ❌ Dashboard lag > 500ms
- ❌ PARTIAL fill PnL drift > 0.1%

---

## DECISION LOG

**Date:** ______________

**Tester:** ______________

**Final Decision:** ☐ GO  ☐ NO-GO

**Signature:** ______________

**Notes:**
```
[Record any observations, warnings, or conditions for go-live]




```

---

## POST-GO-LIVE MONITORING

If decision is GO, monitor for 7 days:

- Daily reconciliation reports reviewed
- Order cache health checked
- Dashboard lag metrics tracked
- PnL accuracy verified against manual calculations
- Rollback readiness maintained

**Escalation:** Any critical issue → immediate rollback to Legacy mode.

---

**Revision:** 1.0
**Last Updated:** 2025-11-02
**Owner:** Trading Bot Team
