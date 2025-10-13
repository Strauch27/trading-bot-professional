# Baseline Report - Order Flow Analysis

**Date**: 2025-10-13
**Branch**: feat/order-flow-hardening
**Status**: Pre-Implementation Baseline

---

## Executive Summary

This baseline documents the **current state** of order flow before implementing Order Flow Hardening (Option A). All findings are based on code analysis of `buy_service.py` and `sell_service.py`.

---

## 1. Order Flow Architecture (Current)

### 1.1 Order Creation Points

**Direct Exchange Calls Identified:**

| File | Line | Call | Type |
|------|------|------|------|
| `buy_service.py` | 57 | `exchange.create_limit_order()` | Limit-IOC BUY |
| `sell_service.py` | 57 | `exchange.create_limit_order()` | Limit-IOC SELL |
| `sell_service.py` | 79 | `exchange.create_market_order()` | Market-IOC SELL |

**❌ Problem**: No centralized OrderService - direct exchange calls scattered across services.

### 1.2 Client Order ID (COID) Generation

**Current Implementation:**
```python
# buy_service.py:45 & sell_service.py:45/68
coid = next_client_order_id(symbol, side)
```

**Source**: `core.utils.next_client_order_id()`

**Analysis:**
- ✅ **COID generation** exists
- ❌ **No persistence** - not stored in KV-store
- ❌ **No idempotency** - new COID on every retry
- ❌ **No reconciliation** after restart

**Risk**: Duplicate orders on network errors + retry

---

## 2. Error Handling & Retry Logic

### 2.1 Buy Service

```python
# buy_service.py:56-63
try:
    order = self.exchange.create_limit_order(...)
    return order
except Exception as e:
    log_event("BUY_ERROR", level="ERROR", ...)
    return None
```

**Analysis:**
- ❌ **No retry logic**
- ❌ **No error classification** (NetworkError vs InsufficientFunds)
- ❌ **Generic exception catch** - treats all errors the same
- ✅ **Logging** exists

### 2.2 Sell Service

```python
# sell_service.py:56-63 & 78-85
try:
    order = self.exchange.create_limit_order/create_market_order(...)
    return order
except Exception as e:
    log_event("EXIT_ERROR", level="ERROR", ...)
    return None
```

**Analysis:** Same issues as BuyService

**Risk**: Transient network errors cause false "ORDER_FAILED" without retry

---

## 3. Partial Fills Handling

### 3.1 Buy Service

```python
# buy_service.py:148-166
trades = order.get("trades")
if trades is None:
    trades = self.exchange.fetch_my_trades(...)

avg_px, filled_qty, _proceeds_q, buy_fees_q = compute_avg_fill_and_fees(order, trades)
buy_fee_per_unit = (buy_fees_q / filled_qty) if filled_qty > 0 else 0.0

self.portfolio.add_held_asset(symbol, {
    "amount": filled_qty,
    "entry_price": avg_px,
    "buy_fee_quote_per_unit": buy_fee_per_unit,
    ...
})
```

**Analysis:**
- ✅ **Partial fills handled** - fetches trades, calculates fees
- ✅ **Fee-per-unit tracking** - critical for PnL calculation
- ❌ **No FSM** - no explicit NONE → PARTIAL → FULL states
- ❌ **No partial-fill logging** - no JSONL event for forensics

### 3.2 Sell Service

```python
# sell_service.py:136-167
trades = order.get("trades")
if trades is None:
    trades = self.exchange.fetch_my_trades(...)

pnl_ctx = compute_realized_pnl_net_sell(symbol, order, trades, entry_price, buy_fee_per_unit)
exit_filled(symbol, "timeout", pnl=pnl_ctx.get("pnl_net_quote", 0.0), ...)
```

**Analysis:**
- ✅ **PnL calculation includes fees** - uses `buy_fee_per_unit` from portfolio
- ✅ **Trades fetched** if not in order response
- ❌ **No incremental fill tracking** - assumes full fill or nothing
- ❌ **No partial-fill telemetry**

**Risk**: Partial fills at multiple price levels not individually logged

---

## 4. Slippage & Guards

### 4.1 Entry Slippage Guard

**Status**: ❌ **NOT IMPLEMENTED**

**Current Behavior:**
```python
# buy_service.py:112-120
for idx, (px, step_config) in enumerate(price_tiers):
    if idx > 1:  # Refresh ticker for ladder steps
        fresh_ticker = fetch_ticker_cached(self.exchange, symbol)
        fresh_ask = float(fresh_ticker.get("ask") or ask)
        if fresh_ask > 0:
            premium_bps = step_config.get("premium_bps", 0)
            px = fresh_ask * (1 + premium_bps / 10_000.0)
```

**Analysis:**
- ✅ **Dynamic repricing** - ladder steps reprice based on fresh ticker
- ❌ **No slippage validation** - no check if `avg_fill_price` vs. `ref_price` exceeds threshold
- ❌ **No ladder abort** on excessive slippage

**Risk**: Expensive entries during volatile market conditions

### 4.2 Exit Slippage Guard

**Status**: ⚠️ **PARTIALLY IMPLEMENTED**

```python
# config.py
MAX_SLIPPAGE_BPS_EXIT = 50  # Config exists but not enforced
```

**Analysis:**
- ✅ **Config parameter exists** (`MAX_SLIPPAGE_BPS_EXIT`)
- ❌ **Not checked in sell flow** - no validation in `sell_service.py`

### 4.3 Spread/Depth Guards

**Status**: ❌ **NOT IMPLEMENTED**

No checks for:
- Spread width (illiquid symbols)
- Orderbook depth (thin markets)
- Predictive buy zone capping

---

## 5. Risk Guards Integration

### 5.1 Current Guard Checks

**Location**: Not found in `buy_service.py` or `sell_service.py`

**Analysis:**
- ❌ **No risk_guards.py integration** in order flow
- ❌ **Guards might exist elsewhere** but not called before order submission
- ❌ **No GUARD_BLOCK logging** before buy

**Search Result:**
```bash
$ grep -r "risk_guard" services/
# No results
```

**Risk**: Orders might be placed despite cooldowns, slot limits, or volatility conditions

---

## 6. Telemetry & Observability

### 6.1 Current Logging

**Buy Flow:**
```python
log_event("BUY_ERROR", level="ERROR", ...)
decision_start(dec_id, ...)
decision_end(dec_id, outcome="ORDER_PLACED" | "ORDER_FAILED", ...)
report_buy_sizing(symbol, {...})
```

**Sell Flow:**
```python
log_event("EXIT_ERROR", level="ERROR", ...)
log_event("EXIT_SKIP" | "EXIT_BELOW_MIN_NOTIONAL", ...)
exit_placed(symbol, reason, ...)
exit_filled(symbol, reason, pnl, ...)
```

**Analysis:**
- ✅ **Decision logging** exists (`decision_start`, `decision_end`)
- ✅ **Exit logging** exists (`exit_placed`, `exit_filled`)
- ❌ **No fill-level telemetry** - no `on_trade_fill()` callback
- ❌ **No slippage metrics** - no tracking of `mid_at_submit` vs. `avg_fill_price`
- ❌ **No rolling metrics** - no mean/95p slippage, fill-latency, partial-rate

### 6.2 Decision ID Propagation

**Analysis:**
```python
# buy_service.py:81
dec_id = new_decision_id()  # Generated in BuyService

# ❌ Not passed to _place_limit_ioc()
# ❌ Not logged in "BUY_ERROR" event
# ❌ Not attached to COID
```

**Problem**: Decision IDs exist but not consistently propagated through order lifecycle

---

## 7. Trailing Stop Integration

**Current State**: Trailing stop controller exists but integration unclear.

**Files Found:**
- `trailing/trailing_stop_controller.py` (assumed)
- No direct reference in `sell_service.py`

**Analysis:**
- ⚠️ **Separate exit path** likely exists for trailing stops
- ❌ **Not consolidated** with normal exit flow

**Risk**: Different logging schemas for trailing vs. TTL exits

---

## 8. Symbol Locking

**Status**: ❌ **NOT IMPLEMENTED**

**Analysis:**
```bash
$ grep -r "Lock\|lock" services/buy_service.py services/sell_service.py
# No threading locks found
```

**Risk**: Race conditions possible:
- Buy + Sell same symbol simultaneously
- Trailing stop + TTL exit collide
- Portfolio mutations not atomic

---

## 9. Quantization & Validation

### 9.1 Current Implementation

```python
# buy_service.py:46-54 & sell_service.py:46-54
px = float(self.exchange.price_to_precision(symbol, price))
qty = float(self.exchange.amount_to_precision(symbol, qty))

if qty <= 0 or px <= 0:
    log_event("BUY_QUANTIZATION_ERROR", level="ERROR", ...)
    return None
```

**Analysis:**
- ✅ **Quantization happens** before order submission
- ✅ **Post-quantization validation** catches invalid values
- ✅ **Logged as error** if quantization fails

---

## 10. Issues Summary

### 10.1 Critical Issues (P0)

| Issue | Impact | Current State |
|-------|--------|---------------|
| **No OrderService** | Code duplication, inconsistent error handling | ❌ Multiple direct exchange calls |
| **No COID persistence** | Duplicate orders on retry | ❌ Ephemeral COIDs |
| **No retry logic** | False failures on network errors | ❌ Single-shot submissions |
| **No error classification** | Cannot distinguish recoverable vs. fatal | ❌ Generic `Exception` catch |

### 10.2 High-Priority Issues (P1)

| Issue | Impact | Current State |
|-------|--------|---------------|
| **No entry slippage guard** | Expensive entries | ❌ Not implemented |
| **No symbol locks** | Race conditions | ❌ Not implemented |
| **No risk guards enforcement** | Orders despite blocks | ❌ Not called |
| **No fill telemetry** | No slippage metrics | ❌ Not tracked |

### 10.3 Medium-Priority Issues (P2)

| Issue | Impact | Current State |
|-------|--------|---------------|
| **No partial-fill FSM** | Incomplete forensics | ⚠️ Handled but not logged |
| **No spread/depth guards** | Bad fills in illiquid markets | ❌ Not implemented |
| **Trailing not unified** | Inconsistent logging | ⚠️ Separate path |

---

## 11. Metrics Baseline (Estimated)

**Note**: No live data available. Estimates based on code analysis.

| Metric | Estimated Value | Notes |
|--------|----------------|-------|
| **Orders Total** | N/A | No historical data |
| **Partial Fills** | Unknown | Not tracked separately |
| **Order Failures** | Unknown | Mixed with genuine errors |
| **Retry Attempts** | 0 | No retry logic |
| **Duplicate Orders** | Unknown | Risk exists, occurrence unknown |
| **Slippage (Mean)** | Unknown | Not measured |
| **Slippage (95p)** | Unknown | Not measured |
| **Fill Latency** | Unknown | Not measured |
| **Guard Blocks** | Unknown | Not logged |

---

## 12. Log Schema Analysis

### 12.1 Current Events

**Buy Events:**
- `BUY_ERROR` - Generic error catch
- `BUY_SKIP` - No valid ask
- `BUY_QUANTIZATION_ERROR` - Post-quantization validation failed

**Sell Events:**
- `EXIT_ERROR` - Generic error catch
- `EXIT_SKIP` - No valid bid
- `EXIT_BELOW_MIN_NOTIONAL` - Order below exchange minimum
- `EXIT_MARKET_PLACED` - Market order placed
- `SELL_QUANTIZATION_ERROR` - Post-quantization validation failed

**Decision Events:**
- `decision_start(dec_id, ...)` - Decision initiated
- `decision_end(dec_id, outcome, ...)` - Decision completed
- `report_buy_sizing(...)` - Sizing calculation logged
- `exit_placed(symbol, reason, ...)` - Exit order placed
- `exit_filled(symbol, reason, pnl, ...)` - Exit order filled

### 12.2 Missing Events

**Option A will add:**
- `PARTIAL_FILL_BUY` - Incremental buy fill
- `PARTIAL_FILL_SELL` - Incremental sell fill
- `ENTRY_SLIPPAGE_BREACH` - Entry slippage exceeded
- `GUARD_BLOCK` - Risk guard prevented order
- `ORDER_RETRY` - Retry attempt logged

---

## 13. Phase 0 Acceptance Criteria

### ✅ Completed

- [x] Git branch created: `feat/order-flow-hardening`
- [x] Baseline report created: `artifacts/2025-10-13_baseline/baseline_report.md`
- [x] Direct exchange calls identified:
  - `buy_service.py:57` - `create_limit_order()`
  - `sell_service.py:57` - `create_limit_order()`
  - `sell_service.py:79` - `create_market_order()`
- [x] Current error handling documented
- [x] Partial fill handling documented
- [x] Missing features identified

### 📊 Metrics Collection (Deferred)

**Note**: Golden-run (1h Paper-Run) **deferred** until Phase 1 OrderService is implemented. Rationale:
- Current code has no retry logic - metrics would be skewed
- No fill telemetry - cannot measure slippage baseline
- Better to establish baseline **after** Phase 1 centralization

**Next Step**: Proceed to Phase 1 (OrderService implementation)

---

## 14. Recommendations

### 14.1 Immediate Actions (Phase 1)

1. **Centralize order submission** in `services/order_service.py`
2. **Add retry logic** for network errors
3. **Classify exceptions** (Recoverable vs. Fatal)
4. **Propagate decision_id** to all log events

### 14.2 Quick Wins (Phases 4, 8, 9)

1. **Entry slippage guard** - Low complexity, high impact
2. **Risk guards hardening** - Enforce existing guards before buy
3. **Fill telemetry** - Track slippage, latency, partial-rate

### 14.3 Deferred (Future)

1. **COID persistence** - Phase 2 (if duplicate orders observed)
2. **Symbol locks** - Phase 6 (if race conditions occur)
3. **Partial-fill FSM** - Phase 3 (if current logging insufficient)

---

## 15. Change Log

| Date | Phase | Changes |
|------|-------|---------|
| 2025-10-13 | Phase 0 | Baseline established |
| TBD | Phase 1 | OrderService implementation |

---

**Next**: Phase 1 - OrderService Implementation
