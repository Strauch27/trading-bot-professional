# FSM Configuration Parity Checklist

**Purpose:** Ensure FSM and Legacy trading flows use identical configuration values to prevent behavioral divergence.

## Critical Configuration Variables

### 1. Entry Guards & Limits

```python
# Spread Guard (Entry)
MAX_SPREAD_BPS_ENTRY = 30  # Max spread in basis points for entry
ENABLE_SPREAD_GUARD_ENTRY = True  # Enable spread check before buy

# Slippage Guard (Entry)
MAX_SLIPPAGE_BPS_ENTRY = 25  # Max slippage in basis points for entry
ENABLE_ENTRY_SLIPPAGE_GUARD = True  # Enable slippage check after buy

# Depth Guard (Entry)
DEPTH_MIN_NOTIONAL_USD = 100.0  # Minimum orderbook depth (USD) on ask side
ENABLE_DEPTH_GUARD_ENTRY = False  # Enable depth check (performance impact)

# Predictive Buy Zone
PREDICTIVE_BUY_ZONE_BPS = 10  # Premium over ask in basis points
PREDICTIVE_BUY_ZONE_CAP_BPS = 50  # Max premium cap (prevents runaway)

# Risk Limits
MAX_OPEN_POSITIONS = 5  # Maximum concurrent positions
MAX_POSITION_VALUE_USDT = 100.0  # Max value per position
MAX_TOTAL_EXPOSURE_USDT = 500.0  # Max total portfolio exposure
```

### 2. Buy Escalation

```python
# Buy Ladder Steps (FSM must match Legacy)
BUY_ESCALATION_STEPS = [
    {"premium_bps": 10, "ttl_ms": 1000},
    {"premium_bps": 20, "ttl_ms": 1000},
    {"premium_bps": 30, "ttl_ms": 1000}
]

# Timeouts
BUY_FILL_TIMEOUT_SECS = 30  # Max wait for buy fill
```

### 3. Exit Escalation & TTL

```python
# Exit Ladder (IOC Limit steps)
EXIT_LADDER_BPS = [0, 5, 10, 15]  # BPS below bid for each step
EXIT_ESCALATION_BPS = [0, 5, 10, 15]  # Alias (both must match)

# Exit Slippage Guard
MAX_SLIPPAGE_BPS_EXIT = 50  # Max slippage on exit (higher tolerance)

# Market Fallback Policy
NEVER_MARKET_SELLS = True  # Block market orders on exit
ALLOW_MARKET_FALLBACK_TTL = False  # Allow market after TTL (if NEVER_MARKET_SELLS=False)

# TTL (Time-to-Live)
TRADE_TTL_MIN = 60  # Force exit after X minutes
EXIT_IOC_TTL_MS = 500  # TTL for each IOC limit step

# Exit Timeouts
SELL_FILL_TIMEOUT_SECS = 30  # Max wait for sell fill
```

### 4. Cooldown & State Management

```python
# Cooldown after guard blocks (prevents rapid re-entry loops)
ENTRY_BLOCK_COOLDOWN_S = 30  # Cooldown after GUARDS_BLOCKED or RISK_LIMITS_BLOCKED

# Post-trade cooldown
COOLDOWN_SECS = 60  # Cooldown after trade close (prevents immediate re-entry)
```

### 5. Position Sizing

```python
# Position sizing
POSITION_SIZE_USDT = 25.0  # Target position size in USDT
MIN_SLOT_USDT = 10.0  # Minimum order value (below this = skip)

# Fees & Slippage Buffer
TRADING_FEE_BPS = 10  # Expected trading fee (basis points)
SLIPPAGE_BPS_ALLOWED = 5  # Budget slippage buffer (basis points)
```

### 6. TP/SL (Take Profit / Stop Loss)

```python
# TP/SL percentages (applied after position open)
TP_PCT = 3.0  # Take profit at +3%
SL_PCT = 5.0  # Stop loss at -5%

# TP Distance for auto-TP order placement (Entry Hook)
TP_DISTANCE_PCT = 1.5  # Auto-place TP order at +1.5%
```

## Verification Checklist

### Pre-Flight Validation
- [ ] `services/order_validation.py::preflight()` called in both FSM and Legacy
- [ ] Identical `q_price()` and `q_amount()` quantization (FLOOR strategy)
- [ ] min_notional auto-bump enabled in both flows

### Entry Guards
- [ ] `MAX_SPREAD_BPS_ENTRY` enforced identically
- [ ] `MAX_SLIPPAGE_BPS_ENTRY` checked post-fill in both
- [ ] `DEPTH_MIN_NOTIONAL_USD` applied to same side (ask for buys)
- [ ] `PREDICTIVE_BUY_ZONE_BPS` and `PREDICTIVE_BUY_ZONE_CAP_BPS` applied identically

### Buy Escalation
- [ ] `BUY_ESCALATION_STEPS` array matches between FSM and Legacy
- [ ] Premium calculation: `price * (1 + premium_bps / 10_000)` identical
- [ ] IOC timeout per step matches `ttl_ms` in config

### Exit Logic
- [ ] `EXIT_LADDER_BPS` used in exact same order
- [ ] Slippage cap `MAX_SLIPPAGE_BPS_EXIT` enforced at each step
- [ ] `NEVER_MARKET_SELLS` respected (no market orders if True)
- [ ] Market fallback only after `ALLOW_MARKET_FALLBACK_TTL` if enabled

### Cooldown
- [ ] `ENTRY_BLOCK_COOLDOWN_S` set on GUARDS_BLOCKED in FSM
- [ ] `COOLDOWN_SECS` applied after POST_TRADE → COOLDOWN transition
- [ ] IDLE checks `cooldown_until` before SLOT_AVAILABLE event

### Timeouts
- [ ] `BUY_FILL_TIMEOUT_SECS` triggers BUY_ORDER_TIMEOUT event
- [ ] `SELL_FILL_TIMEOUT_SECS` triggers SELL_ORDER_TIMEOUT event
- [ ] TTL enforcement via `core/fsm/timeouts.py` singleton

## Testing Requirements

### Unit Tests (Required)

1. **BLESS/USDT Parity Test** (Low Min-Notional)
   ```python
   # Test that FSM auto-bumps min_notional like Legacy
   def test_bless_min_notional_parity():
       price = 0.0001  # Very low price
       amount = 50  # Below min_notional
       # Expected: Both FSM and Legacy bump to min_notional
       assert fsm_result.amount >= legacy_result.amount
   ```

2. **PARTIAL-Fill Chain Test**
   ```python
   # Test weighted average across multiple PARTIAL fills
   def test_partial_fill_weighted_average():
       # Fill 1: 100 @ 1.0
       # Fill 2: 50 @ 1.1
       # Expected: position = 150 @ 1.0333
       assert abs(position.avg_entry - 1.0333) < 0.0001
   ```

3. **Exit Ladder Without Market Fallback**
   ```python
   # Test that NEVER_MARKET_SELLS is enforced
   def test_exit_ladder_no_market():
       config.NEVER_MARKET_SELLS = True
       result = sell_service.enforce_ttl()
       # Expected: No market orders placed
       assert not any(o['type'] == 'market' for o in result['orders'])
   ```

4. **Guard Block Cooldown**
   ```python
   # Test that guard block sets cooldown
   def test_guard_block_cooldown():
       # Trigger GUARDS_BLOCKED
       fsm.process_event(coin_state, guards_blocked_event)
       # Expected: cooldown_until set
       assert coin_state.cooldown_until > time.time()
       assert coin_state.cooldown_until <= time.time() + config.ENTRY_BLOCK_COOLDOWN_S + 1
   ```

### Integration Tests (Recommended)

1. **Full Buy-Sell Cycle Parity**
   - Run identical signal through FSM and Legacy
   - Compare: entry price, position size, exit price, PnL
   - Expected: Diff < 0.1%

2. **Recovery & Reconciliation**
   - Simulate crash with open WAIT_FILL order
   - Restart and verify exchange reconciliation
   - Expected: State matches exchange reality

3. **Dashboard Lag Test**
   - Trigger rapid phase transitions (IDLE → ENTRY_EVAL → PLACE_BUY)
   - Verify dashboard updates within 100ms
   - Expected: No debounce lag on critical phases

## Common Pitfalls

### ❌ Config Mismatch Examples

**Problem:** FSM uses default, Legacy uses custom config
```python
# Legacy: MAX_SPREAD_BPS_ENTRY = 50
# FSM: Uses default 30 (hardcoded)
# Result: FSM blocks more buys than Legacy
```

**Problem:** Different escalation steps
```python
# Legacy: BUY_ESCALATION_STEPS = [10, 20, 30]
# FSM: Uses EXIT_ESCALATION_BPS = [0, 5, 10] by mistake
# Result: FSM never buys (wrong premium)
```

**Problem:** Cooldown not set on RISK_LIMITS_BLOCKED
```python
# Legacy: Sets 30s cooldown on risk block
# FSM: Only sets cooldown on GUARDS_BLOCKED
# Result: FSM rapid-loops on risk limit hits
```

## Migration Path

### Phase 1: Audit (Complete this first)
- [ ] Run `tools/check_fsm_consistency.py` (if exists)
- [ ] Compare config values in this doc vs. actual `config.py`
- [ ] Identify mismatches

### Phase 2: Fix Config
- [ ] Update `config.py` with values from this doc
- [ ] Ensure all variables have defaults in code (for backward compat)

### Phase 3: Test
- [ ] Run unit tests (above)
- [ ] Run integration tests
- [ ] Dry-run FSM in parallel with Legacy (shadow mode)

### Phase 4: Deploy
- [ ] Enable FSM for low-volume pairs first
- [ ] Monitor discrepancies via dashboards
- [ ] Roll out to all pairs once parity confirmed

## Monitoring

Add these metrics to your dashboard:

```python
# Config Divergence Alerts
- guard_block_rate_fsm vs. guard_block_rate_legacy
- avg_entry_price_fsm vs. avg_entry_price_legacy
- partial_fill_rate_fsm (should be > 0 if working)

# Cooldown Enforcement
- cooldown_loops_prevented (should increase if fix works)

# Dashboard Lag
- dashboard_flush_latency_ms (target < 100ms)
```

## Summary

**Priority Order:**
1. ✅ Preflight (Punkt 2) - Fixes BLESS issue
2. ✅ PARTIAL-Fills (Punkt 4) - Prevents PnL drift
3. ✅ Cooldown (Punkt 3) - Prevents resource exhaustion
4. ✅ Exit Race (Punkt 5) - Prevents order cache corruption
5. ✅ Idempotenz (Punkt 6) - Prevents duplicate processing
6. ✅ Dashboard Flush (Punkt 9) - Improves UX
7. ✅ Recovery (Punkt 7) - Ensures clean restarts
8. [ ] Config Parity (Punkt 10-12) - This document serves as the checklist

**Expected Outcome:**
After applying all fixes, FSM should behave identically to Legacy for all test cases, with improved robustness (recovery, idempotency, timeouts).
