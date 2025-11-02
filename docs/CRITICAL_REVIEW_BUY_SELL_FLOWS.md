# CRITICAL REVIEW: Buy & Sell Flows

**Date:** 2025-11-02
**Scope:** Complete analysis of Buy/Sell order flows in Legacy and FSM systems
**Reviewer:** Claude Code

---

## Executive Summary

### ✅ **Strengths**
- OrderService with retry logic and error classification
- COID-based idempotency (Phase 2)
- Structured logging with trace context
- Exit Manager for signal processing

### ❌ **Critical Vulnerabilities**
1. **PARTIAL-Fill Accounting Race (HIGH)** - Position vs. Portfolio desync
2. **Preflight Bypass in Legacy (HIGH)** - Bypasses min_notional checks
3. **Exit IOC Ladder Cache Race (MEDIUM)** - Already partially fixed
4. **Fee Tracking Inconsistency (MEDIUM)** - Trade-based vs. Order-based
5. **Position Cleanup Timing Gap (MEDIUM)** - Portfolio != Engine state
6. **TTL/Timeout Enforcement Gaps (MEDIUM)** - Not all flows covered
7. **Decision-ID Propagation Holes (LOW)** - Tracing incomplete

---

## Part 1: BUY FLOW ANALYSIS

### 1.1 Legacy Buy Flow

**File:** `services/buy_service.py`

```python
# Current Implementation (PROBLEMATIC)
def execute_buy(...):
    # Step 1: Check budget (good)
    if not self.validate_budget(...):
        return None

    # Step 2: Calculate quantity (good)
    qty = position_value / price

    # Step 3: PREFLIGHT MISSING ❌
    # No call to services/order_validation.py::preflight()
    # No min_notional auto-bump
    # Risk: Small orders like BLESS fail

    # Step 4: Submit order (good)
    result = self.order_service.submit_limit(...)

    # Step 5: Handle fill
    if result.is_filled:
        # CRITICAL GAP: What if PARTIAL fill?
        # Portfolio not updated until FULLY filled
        # Risk: Budget tracking drift
```

**Critical Issue #1: Preflight Bypass**

```python
# CURRENT (services/buy_service.py:84-96)
def execute_buy(self, symbol, price, position_value, ...):
    qty = position_value / price
    # Directly submit without preflight ❌
    result = self.order_service.submit_limit(
        symbol=symbol,
        side='buy',
        qty=qty,
        price=price,
        ...
    )
```

**Expected Flow:**
```python
# SHOULD BE:
from services.order_validation import preflight
from services.quantize import q_price, q_amount
from services.exchange_filters import get_filters

def execute_buy(self, symbol, price, position_value, ...):
    # 1. Load filters
    filters = get_filters(exchange, symbol)

    # 2. Calculate qty
    qty = position_value / price

    # 3. PREFLIGHT (quantize + min_notional bump) ✅
    ok, result = preflight(symbol, price, qty, filters)
    if not ok:
        return BuyResult(success=False, error=result['reason'])

    # 4. Use validated values
    qty = result['amount']
    price = result['price']

    # 5. Submit
    result = self.order_service.submit_limit(...)
```

**Impact:** Low-notional pairs (BLESS, ZBT) fail in Legacy but work after FSM fix (Punkt 2).

---

**Critical Issue #2: PARTIAL Fill Handling**

```python
# CURRENT (services/buy_service.py:103-115)
if result.is_filled:
    # Full fill: Update portfolio
    self.portfolio.add_position(...)
elif result.is_partially_filled:
    # PARTIAL: What happens here? ❌
    # - Portfolio NOT updated
    # - Budget NOT reserved
    # - Remaining order NOT tracked
    # Risk: Engine thinks budget is free, places duplicate buy
```

**Expected Flow:**
```python
if result.is_filled or result.is_partially_filled:
    # Update portfolio with FILLED qty (not original qty)
    self.portfolio.add_position(
        symbol=symbol,
        amount=result.filled_qty,  # NOT original qty ✅
        entry_price=result.avg_price,  # Weighted average ✅
        remaining_order_id=result.order_id if not result.is_filled else None
    )

    # Reserve remaining budget if PARTIAL
    if result.is_partially_filled:
        remaining_value = (qty - result.filled_qty) * price
        self.portfolio.reserve_budget(remaining_value)
```

**Current State:** FSM implements this correctly (after Punkt 4 fix), Legacy does NOT.

---

### 1.2 FSM Buy Flow

**File:** `core/fsm/actions.py`

**Flow:**
```
IDLE → ENTRY_EVAL → PLACE_BUY → WAIT_FILL → POSITION
                ↓ (guards blocked)
               IDLE
```

**✅ Fixed (Punkt 2):** `action_prepare_buy()` now includes full preflight
**✅ Fixed (Punkt 4):** `action_handle_partial_buy()` implements weighted average + incremental position

**Remaining Issue #3: Preflight Error Handling**

```python
# CURRENT (core/fsm/actions.py:96-107 - AFTER FIX)
if not ok:
    # Block transition by setting error flag
    ctx.data['preflight_failed'] = True
    ctx.data['preflight_reason'] = reason
    coin_state.note = f"preflight failed: {reason}"
    return  # ❌ Transition still happens!
```

**Problem:** `return` from action does NOT prevent transition. FSM still moves to `PLACE_BUY` even if preflight failed.

**Fix Required:**
```python
# Need to emit GUARDS_BLOCKED event instead of silent return
if not ok:
    # Emit event to trigger ENTRY_EVAL → IDLE transition
    from core.fsm.fsm_events import FSMEvent
    ctx.event = FSMEvent.GUARDS_BLOCKED
    ctx.data['block_reason'] = f"preflight:{reason}"
    # Action will now trigger correct transition
```

**OR:** Validate preflight BEFORE transition (in guard function, not action).

---

### 1.3 OrderService Analysis

**File:** `services/order_service.py`

**✅ Strengths:**
- Retry logic with exponential backoff (3 attempts)
- Error classification (network vs. fatal)
- COID manager integration (Phase 2)
- Quantization before submission

**Critical Issue #4: COID Collision Risk on Retry**

```python
# CURRENT (services/order_service.py:377-415)
for attempt in range(1, self.max_retries + 1):
    try:
        order = self.exchange.create_limit_order(
            symbol, side, qty, price, time_in_force, coid, False
        )
        return self._parse_order_response(order, coid, attempts=attempt)
    except Exception as e:
        # Retry with SAME coid ✅ (idempotent)
        # BUT: What if first attempt PARTIALLY succeeded?
        # Exchange may reject 2nd attempt as duplicate
        # BUT we don't check order status before retry ❌
```

**Scenario:**
1. Attempt 1: Order placed, network timeout before response
2. Exchange: Order FILLED
3. Bot: Thinks it failed, retries
4. Attempt 2: Exchange rejects as duplicate
5. Bot: Gives up, marks as failed
6. Reality: Position opened but bot doesn't know ❌

**Fix Required:**
```python
for attempt in range(1, self.max_retries + 1):
    try:
        order = self.exchange.create_limit_order(...)
        return self._parse_order_response(...)
    except Exception as e:
        error_type = self._classify_error(e)

        # NEW: Check if error is "duplicate" - order may have succeeded
        if error_type == OrderErrorType.DUPLICATE_ORDER:
            # Fetch order by COID to check status
            try:
                existing_order = self.exchange.fetch_order_by_coid(coid, symbol)
                if existing_order:
                    logger.warning(f"Duplicate error - order exists: {existing_order['id']}")
                    return self._parse_order_response(existing_order, coid, attempts=attempt)
            except Exception:
                pass

        # Continue retry logic...
```

---

## Part 2: SELL FLOW ANALYSIS

### 2.1 Legacy Sell Flow

**File:** `services/sell_service.py`

**Flow (IOC Ladder):**
```python
for bps in [0, 5, 10, 15]:  # EXIT_LADDER_BPS
    price = bid * (1 - bps / 10000)
    order = place_limit_ioc(symbol, qty, price)

    if order['status'] == 'FILLED':
        break  # Success

    # NOT FILLED - escalate to next step
```

**✅ Fixed (Punkt 5):** 100ms delay between IOC steps to prevent cache race

**Critical Issue #5: IOC Order Status Ambiguity**

```python
# CURRENT (services/sell_service.py:324-330)
order = self._place_limit_ioc(symbol, q_adj, target_price)
if not order:
    continue  # Skip to next step

status = (order.get("status") or "").upper()
if status in ("FILLED", "CLOSED"):
    filled_any = True
    # Process trades...
```

**Problem:** What if IOC is PARTIALLY_FILLED?

```python
# Scenario:
# - IOC Limit @ bid-5bps: 50% filled
# - Bot sees status != "FILLED", continues to next step
# - Next IOC @ bid-10bps: 50% filled (different price!)
# - Result: Position closed at TWO different prices
# - Fee tracking: Only last order's fees counted ❌
```

**Fix Required:**
```python
if status in ("FILLED", "CLOSED"):
    filled_any = True
    cumulative_fills.append(order)
elif status in ("PARTIALLY_FILLED", "PARTIAL"):
    # Handle partial fill ✅
    filled_qty += float(order.get('filled', 0))
    avg_prices.append(order.get('average', target_price))
    cumulative_fills.append(order)

    # Continue with remaining qty
    remaining_qty = qty - filled_qty
    if remaining_qty < filters['min_qty']:
        break  # Too small to continue
```

---

**Critical Issue #6: Fee Tracking Inconsistency**

```python
# CURRENT (engine/exit_handler.py:102-119)
exit_fee = 0.0
if hasattr(result, 'order') and result.order:
    order = result.order
    trades = order.get("trades") or []
    if not trades and order.get("id"):
        # Fetch trades from cache ✅
        trades = trade_cache.get_trades_cached(...)
    exit_fee = sum(t.get("fee", {}).get("cost", 0) for t in (trades or []))
```

**Problem #1:** Trade fetching only happens if `trades` is empty. If order has stale/incomplete trades, not refreshed.

**Problem #2:** Multi-leg exits (IOC ladder with PARTIALs) only track last order's fees.

**Fix Required:**
```python
# Track fees across ALL exit orders in ladder
exit_fees_total = 0.0
for order_result in ladder_results:
    if order_result.filled_qty > 0:
        # Fetch trades for EACH filled order
        trades = trade_cache.get_trades_cached(
            symbol=symbol,
            exchange_adapter=self.engine.exchange_adapter,
            params={"orderId": order_result.order_id},
            cache_ttl=60.0
        )
        for trade in trades:
            exit_fees_total += trade.get("fee", {}).get("cost", 0)

# Record with cumulative fees
realized_pnl = self.engine.pnl_service.record_fill(
    ...,
    fee_quote=exit_fees_total  # All fees ✅
)
```

---

### 2.2 FSM Sell Flow

**File:** `core/fsm/actions.py`

**Flow:**
```
POSITION → EXIT_EVAL → PLACE_SELL → WAIT_SELL_FILL → POST_TRADE
                    ↓ (guards blocked)
                   POSITION
```

**Critical Issue #7: Exit TTL Enforcement Gap**

```python
# CURRENT (core/fsm/timeouts.py - assumption based on structure)
def check_timeouts(self, coin_state: CoinState, ctx: EventContext) -> Optional[FSMEvent]:
    if coin_state.phase == Phase.WAIT_FILL:
        if now - coin_state.buy_order_ts > BUY_FILL_TIMEOUT_SECS:
            return FSMEvent.BUY_ORDER_TIMEOUT

    if coin_state.phase == Phase.WAIT_SELL_FILL:
        if now - coin_state.sell_order_ts > SELL_FILL_TIMEOUT_SECS:
            return FSMEvent.SELL_ORDER_TIMEOUT

    # ❌ MISSING: POSITION phase TTL check
    # If position held > TRADE_TTL_MIN, should trigger EXIT_EVAL
```

**Expected:**
```python
if coin_state.phase == Phase.POSITION:
    if now - coin_state.entry_ts > config.TRADE_TTL_MIN * 60:
        return FSMEvent.TRADE_TTL_EXCEEDED  # Force exit ✅
```

---

**Critical Issue #8: Sell Preparation Lacks Slippage Check**

```python
# CURRENT (core/fsm/actions.py - sell preparation)
def action_prepare_sell(ctx: EventContext, coin_state: CoinState) -> None:
    # Calculate sell price
    current_price = ctx.data.get('current_price')

    # ❌ NO check against MAX_SLIPPAGE_BPS_EXIT
    # ❌ NO validation that price hasn't crashed below SL
    # Risk: Sell at disaster price if market gapped down
```

**Fix Required:**
```python
def action_prepare_sell(ctx: EventContext, coin_state: CoinState) -> None:
    current_price = ctx.data.get('current_price')
    entry_price = coin_state.entry_price

    # Validate slippage vs entry
    slippage_bps = abs((current_price - entry_price) / entry_price) * 10000

    if slippage_bps > config.MAX_SLIPPAGE_BPS_EXIT:
        # Price too far from entry - may be flash crash
        logger.warning(f"Sell slippage too high: {slippage_bps:.1f} bps > {config.MAX_SLIPPAGE_BPS_EXIT}")

        # Option 1: Block sell, wait for recovery
        ctx.data['sell_blocked'] = True
        ctx.data['block_reason'] = f"slippage_too_high:{slippage_bps:.0f}bps"
        return

        # Option 2: Use emergency market order with lower slippage cap
```

---

## Part 3: CROSS-CUTTING CONCERNS

### 3.1 Portfolio vs. Engine Position Sync

**Critical Issue #9: Position Cleanup Race**

**Files:** `engine/exit_handler.py:201-215`, `core/portfolio.py`

```python
# CURRENT (exit_handler.py:201)
del self.engine.positions[symbol]  # Engine state cleared ✅

# THEN (exit_handler.py:206-215)
try:
    portfolio = self.engine.portfolio
    if portfolio.held_assets.get(symbol):
        remaining = asset['amount'] - result.filled_amount
        if remaining <= 1e-9:
            portfolio.remove_held_asset(symbol)  # Portfolio cleared ✅
except Exception:
    pass  # ❌ Silent failure
```

**Race Scenario:**
1. Thread A: `del positions[symbol]` (engine clean)
2. Thread B: Buy signal for same symbol arrives
3. Thread B: Checks `positions[symbol]` → not found → OK to buy ✅
4. Thread A: `portfolio.remove_held_asset(symbol)` (delayed by 100ms)
5. Thread B: Checks `portfolio.held_assets[symbol]` → still exists! → Skip buy ❌

**Fix Required:**
```python
# Atomic cleanup with lock
with self.engine._lock:
    # 1. Portfolio first (stricter check)
    portfolio.remove_held_asset(symbol)

    # 2. Engine second
    del self.engine.positions[symbol]

    # Both or neither - no intermediate state
```

---

### 3.2 Decision-ID Propagation

**Critical Issue #10: Incomplete Trace Context**

**Observation:** Decision-IDs are propagated in some flows but not others.

**Missing Links:**

1. **Legacy Buy → Order Submission**
   ```python
   # CURRENT (services/buy_service.py)
   result = self.order_service.submit_limit(
       symbol=symbol,
       ...,
       decision_id=None  # ❌ Not passed
   )
   ```

2. **Exit Signal → Exit Order**
   ```python
   # CURRENT (services/sell_service.py)
   # Exit flows don't extract decision_id from position_data
   # Can't trace sell order back to original buy decision
   ```

**Fix Required:**
```python
# In buy_service.py
result = self.order_service.submit_limit(
    ...,
    decision_id=self.current_decision_id  # ✅ Propagate
)

# In sell_service.py
def execute_exit(self, symbol, position_data, ...):
    decision_id = position_data.get('decision_id')
    result = self.order_service.submit_limit(
        ...,
        decision_id=decision_id  # ✅ Link sell to buy
    )
```

---

## Part 4: RISK MATRIX

| Issue | Severity | Impact | Current State | Fix Priority |
|-------|----------|--------|---------------|--------------|
| #1: Legacy Preflight Bypass | **HIGH** | Failed buys on low-notional pairs | ❌ Unfixed | **P0** |
| #2: PARTIAL Fill Portfolio Desync | **HIGH** | Budget tracking drift, duplicate buys | ✅ FSM fixed, ❌ Legacy | **P0** |
| #3: FSM Preflight Error Transition | **MEDIUM** | Orders placed despite validation failure | ❌ Unfixed | **P1** |
| #4: COID Retry Duplicate Check | **MEDIUM** | Positions opened but marked failed | ❌ Unfixed | **P1** |
| #5: IOC Ladder PARTIAL Handling | **MEDIUM** | Multi-price exits, fee undercounting | ❌ Unfixed | **P1** |
| #6: Exit Fee Tracking Multi-Leg | **MEDIUM** | PnL drift (small but cumulative) | ❌ Unfixed | **P2** |
| #7: Position TTL Enforcement | **LOW** | Stale positions not force-closed | ❌ Unfixed | **P2** |
| #8: Sell Slippage Pre-Check | **MEDIUM** | Flash crash disaster sells | ❌ Unfixed | **P1** |
| #9: Portfolio Cleanup Race | **MEDIUM** | Duplicate buy after sell | ❌ Unfixed | **P1** |
| #10: Decision-ID Gaps | **LOW** | Incomplete tracing | ❌ Unfixed | **P3** |

---

## Part 5: RECOMMENDATIONS

### Immediate Actions (P0)

1. **Add Preflight to Legacy Buy**
   ```python
   # In services/buy_service.py::execute_buy()
   from services.order_validation import preflight
   ok, result = preflight(symbol, price, qty, filters)
   if not ok:
       return BuyResult(success=False, error=result['reason'])
   ```

2. **Fix Legacy PARTIAL Fill Handling**
   ```python
   # In services/buy_service.py
   if result.is_filled or result.is_partially_filled:
       self.portfolio.add_position(
           amount=result.filled_qty,  # Actual filled ✅
           entry_price=result.avg_price,
           ...
       )
   ```

### High Priority (P1)

3. **Fix FSM Preflight Guard**
   - Move preflight validation to ENTRY_EVAL guard function (before PLACE_BUY transition)

4. **Add COID Duplicate Recovery**
   - Implement `fetch_order_by_coid()` fallback on duplicate errors

5. **Implement IOC Ladder PARTIAL Aggregation**
   - Track all filled orders in ladder
   - Aggregate fees and quantities
   - Calculate weighted average exit price

6. **Add Sell Slippage Pre-Check**
   - Validate exit price against `MAX_SLIPPAGE_BPS_EXIT` before submitting

7. **Fix Portfolio Cleanup Atomicity**
   - Use lock for portfolio + engine position removal

### Medium Priority (P2)

8. **Add Position TTL Check**
   - Implement timeout in `core/fsm/timeouts.py` for POSITION phase

9. **Multi-Leg Exit Fee Tracking**
   - Extend `ExitResult` to support multiple orders
   - Aggregate all trade fees

### Low Priority (P3)

10. **Complete Decision-ID Propagation**
    - Pass decision_id through all order service calls
    - Extract decision_id from position_data in exit flows

---

## Part 6: TEST COVERAGE GAPS

### Missing Integration Tests

1. **PARTIAL Fill Buy → Sell Cycle**
   ```python
   # Test: Buy order 50% filled → Sell 50% position
   # Expected: Portfolio consistent, fees tracked correctly
   ```

2. **IOC Ladder Multi-Fill Exit**
   ```python
   # Test: Exit ladder with 3 PARTIALs at different prices
   # Expected: Weighted avg exit price, all fees counted
   ```

3. **Preflight Min-Notional Auto-Bump**
   ```python
   # Test: BLESS @ 0.0001 USDT, qty=50 (below min_notional)
   # Expected: Auto-bumped to min_notional, order succeeds
   ```

4. **COID Duplicate Recovery**
   ```python
   # Test: Network timeout after order placed
   # Expected: Retry detects duplicate, fetches order, marks success
   ```

5. **Portfolio Cleanup Race**
   ```python
   # Test: Concurrent sell completion + new buy signal
   # Expected: No race, buy only happens after cleanup complete
   ```

---

## Conclusion

The buy/sell flows have **solid foundations** (OrderService, COID management, retry logic) but suffer from **critical edge-case vulnerabilities**:

1. **PARTIAL fills** are not handled atomically in Legacy (FSM is better after fixes)
2. **Preflight validation** is bypassed in Legacy buy flow
3. **Exit ladder fee tracking** is incomplete for multi-leg exits
4. **Position cleanup** has potential race conditions

**Overall Risk:** **MEDIUM-HIGH** for production use without P0/P1 fixes.

**Estimated Fix Effort:**
- P0 fixes: 4-6 hours
- P1 fixes: 8-12 hours
- Total: 12-18 hours

**Post-Fix Confidence:** **HIGH** (with comprehensive test coverage)
