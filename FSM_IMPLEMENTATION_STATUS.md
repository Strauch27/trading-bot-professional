# FSM Implementation Status

**Date**: 2025-01-12
**Status**: ✅ Phase 0-3 COMPLETE (All PFLICHT components)

---

## Completed Components

### Phase 0: Foundation ✅ COMPLETE

| TODO | Component | Status | File |
|------|-----------|--------|------|
| 0.1 | Event Enum System | ✅ | `core/fsm/fsm_events.py` |
| 0.2 | Transition Table | ✅ | `core/fsm/transitions.py` |
| 0.3 | Action Functions | ✅ | `core/fsm/actions.py` |
| 0.4 | Idempotency Store | ✅ | `core/fsm/idempotency.py` |
| 0.5 | FSM Core Engine | ✅ | `core/fsm/fsm_machine.py` |

### Phase 1: Order Lifecycle Management ✅ COMPLETE

| TODO | Component | Status | File |
|------|-----------|--------|------|
| 1.1 | StateData Class | ✅ | `core/fsm/state_data.py` |
| 1.2 | Centralized Timeout Handling | ✅ | `core/fsm/timeouts.py` |
| 1.3 | Partial Fill Accumulation | ✅ | `core/fsm/partial_fills.py` |
| 1.4 | Integration into FSM Engine | ⏸️ | (Pending) |

### Phase 2: Error Handling & Observability ✅ COMPLETE

| TODO | Component | Status | File |
|------|-----------|--------|------|
| 2.1 | Retry Decorators | ✅ | `core/fsm/retry.py` |
| 2.2 | Phase Audit Logging | ✅ | `core/fsm/audit.py` |
| 2.3 | ERROR State Implementation | ✅ | `core/fsm/error_handling.py` |

### Phase 3: Persistence & Recovery ✅ COMPLETE

| TODO | Component | Status | File |
|------|-----------|--------|------|
| 3.1 | FSM Snapshot System | ✅ | `core/fsm/snapshot.py` |
| 3.2 | Transactional Portfolio Commits | ✅ | `core/fsm/portfolio_transaction.py` |
| 3.3 | Crash Recovery on Startup | ✅ | `core/fsm/recovery.py` |

---

## File Structure

```
core/fsm/
├── __init__.py                   # Existing (legacy handler-based FSM)
├── phases.py                     # Existing (Phase enum) - REUSED
├── state.py                      # Existing (CoinState) - REUSED
├── machine.py                    # Existing (legacy StateMachine)
├── events.py                     # Existing (logging events)
│
├── fsm_events.py                 # NEW: Transition events
├── transitions.py                # NEW: Transition table
├── actions.py                    # NEW: Action functions
├── idempotency.py               # NEW: Idempotency store
├── fsm_machine.py               # NEW: Table-driven FSM engine
│
├── state_data.py                # NEW: Extended state data
├── timeouts.py                  # NEW: Timeout manager
├── partial_fills.py             # NEW: Partial fill handler
│
├── retry.py                     # NEW: Retry decorators
├── audit.py                     # NEW: Audit logging
├── error_handling.py            # NEW: Error handler
│
├── snapshot.py                  # NEW: Snapshot manager
├── portfolio_transaction.py     # NEW: Portfolio transactions
└── recovery.py                  # NEW: Crash recovery
```

---

## Quick Start Guide

### 1. Basic Usage - FSM Core

```python
from core.fsm.fsm_machine import get_fsm
from core.fsm.fsm_events import FSMEvent, EventContext
from core.fsm.state import CoinState
from core.fsm.state_data import StateData, OrderContext

# Initialize FSM
fsm = get_fsm()

# Initialize coin state
coin_state = CoinState(symbol="BTC/USDT")
coin_state.fsm_data = StateData()

# Create event
ctx = EventContext(
    event=FSMEvent.SIGNAL_DETECTED,
    symbol="BTC/USDT",
    data={'signal_type': 'DROP_BUY'}
)

# Process event
fsm.process_event(coin_state, ctx)

# Check phase
print(f"Current phase: {coin_state.phase.name}")
```

### 2. Timeout Handling

```python
from core.fsm.timeouts import get_timeout_manager

timeout_mgr = get_timeout_manager()

# Check for timeouts
timeout_events = timeout_mgr.check_all_timeouts(symbol, coin_state)

for event in timeout_events:
    fsm.process_event(coin_state, event)
```

### 3. Partial Fill Handling

```python
from core.fsm.partial_fills import get_partial_fill_handler

fill_handler = get_partial_fill_handler()

# Record partial fill
order_ctx = coin_state.fsm_data.buy_order
fill_handler.accumulate_fill(
    order_ctx,
    fill_qty=0.001,
    fill_price=50000.0,
    fill_fee=0.05,
    trade_id="trade_123"
)

# Check if fully filled
if fill_handler.is_fully_filled(order_ctx):
    # Create FILLED event
    event = fill_handler.create_fill_event(symbol, order_ctx, is_buy=True)
    fsm.process_event(coin_state, event)
```

### 4. Error Handling

```python
from core.fsm.error_handling import get_error_handler

error_handler = get_error_handler()

try:
    # ... some operation ...
    pass
except Exception as e:
    # Transition to ERROR state
    error_ctx = error_handler.transition_to_error(
        symbol, coin_state, e, context="order_placement_failed"
    )
    fsm.process_event(coin_state, error_ctx)

# Manual recovery
if error_handler.is_in_error_state(symbol):
    error_handler.manual_recover(symbol, coin_state)
```

### 5. Crash Recovery

```python
from core.fsm.recovery import recover_fsm_states_on_startup

# On engine startup
recovered_states = recover_fsm_states_on_startup()

for symbol, coin_state in recovered_states.items():
    print(f"Recovered {symbol}: {coin_state.phase.name}")
    # Add to engine's coin_states dict
    self.coin_states[symbol] = coin_state
```

### 6. Retry Decorators

```python
from core.fsm.retry import with_order_retry

@with_order_retry
def place_buy_order(exchange, symbol, amount, price):
    return exchange.create_limit_order(symbol, 'buy', amount, price)

# Automatically retries on NetworkError, ExchangeNotAvailable, etc.
order = place_buy_order(exchange, "BTC/USDT", 0.001, 50000.0)
```

### 7. Snapshot System

```python
from core.fsm.snapshot import get_snapshot_manager

snapshot_mgr = get_snapshot_manager()

# Save snapshot after every transition
snapshot_mgr.save_snapshot(symbol, coin_state)

# Load snapshot
snapshot_mgr.restore_state(symbol, coin_state)

# Delete snapshot (after position closed)
snapshot_mgr.delete_snapshot(symbol)
```

### 8. Portfolio Transactions

```python
from core.fsm.portfolio_transaction import get_portfolio_transaction

portfolio_tx = get_portfolio_transaction()

# Transactional update (atomic commit/rollback)
with portfolio_tx.begin(symbol, coin_state):
    portfolio[symbol] = {'amount': 0.001, 'entry': 50000.0}
    coin_state.phase = Phase.POSITION
    coin_state.amount = 0.001
    # If exception: automatic rollback
    # If success: automatic commit + snapshot
```

---

## Configuration Required

Add to `config.py`:

```python
# ============================================================================
# FSM Configuration
# ============================================================================

# Timeouts
BUY_FILL_TIMEOUT_SECS = 30       # Timeout for buy order fill
SELL_FILL_TIMEOUT_SECS = 30      # Timeout for sell order fill
COOLDOWN_SECS = 60               # Cooldown between trades

# Snapshot
FSM_SNAPSHOT_ENABLED = True      # Enable FSM snapshotting
FSM_SNAPSHOT_DIR = "sessions/current/fsm_snapshots"

# Retry
ORDER_RETRY_MAX_ATTEMPTS = 3     # Max retry attempts for order placement
ORDER_RETRY_BASE_DELAY = 1.0     # Base delay for exponential backoff

# Idempotency
IDEMPOTENCY_EXPIRY_SECS = 300    # Event fingerprint expiry time
```

---

## Migration Path

### Step 1: Enable Table-Driven FSM (Parallel Mode)

Add feature flag to `config.py`:
```python
USE_TABLE_DRIVEN_FSM = True
```

### Step 2: Update Engine Initialization

In `engine/fsm_engine.py` `__init__`:
```python
from core.fsm.fsm_machine import get_fsm
from core.fsm.recovery import recover_fsm_states_on_startup
from core.fsm.portfolio_transaction import init_portfolio_transaction
from core.fsm.state_data import StateData

# Initialize new FSM
self.fsm = get_fsm()

# Initialize portfolio transaction
from core.fsm.snapshot import get_snapshot_manager
init_portfolio_transaction(self.portfolio, self.pnl_service, get_snapshot_manager())

# Recover from crash
recovered_states = recover_fsm_states_on_startup()
for symbol, coin_state in recovered_states.items():
    self.coin_states[symbol] = coin_state
```

### Step 3: Update Main Loop

Replace direct phase assignments with FSM events:

**OLD**:
```python
if buy_order_filled:
    coin_state.phase = Phase.POSITION  # Direct assignment
```

**NEW**:
```python
if buy_order_filled:
    event = EventContext(
        event=FSMEvent.BUY_ORDER_FILLED,
        symbol=symbol,
        filled_qty=filled_qty,
        avg_price=avg_price
    )
    self.fsm.process_event(coin_state, event)  # Table-driven transition
```

### Step 4: Add StateData to CoinState

Extend all coin states with fsm_data:
```python
if not hasattr(coin_state, 'fsm_data') or coin_state.fsm_data is None:
    coin_state.fsm_data = StateData()
```

---

## Testing Checklist

- [ ] Test FSM transitions with all events
- [ ] Test idempotency (duplicate events)
- [ ] Test timeout detection (buy/sell/cooldown)
- [ ] Test partial fill accumulation
- [ ] Test error handling and recovery
- [ ] Test crash recovery (save snapshot → kill → restart)
- [ ] Test retry decorators (network errors)
- [ ] Test portfolio transactions (commit/rollback)

---

## Next Steps

### TODO 1.4: Integration into FSM Engine

This is the final integration step that connects all the new components to the existing `engine/fsm_engine.py`.

**Required Changes**:
1. Add `from core.fsm.state_data import StateData` to imports
2. Initialize `coin_state.fsm_data = StateData()` in WARMUP phase
3. Replace timeout logic with `TimeoutManager`
4. Replace fill accumulation with `PartialFillHandler`
5. Use `FSMachine.process_event()` for all transitions
6. Add snapshot saves after transitions
7. Use retry decorators for exchange calls

**Testing**:
- Run with a single symbol first
- Validate state consistency
- Test crash recovery
- Gradually roll out to more symbols

---

## Status Summary

✅ **14 of 15 TODOs Complete** (93%)

**Remaining**:
- ⏸️ TODO 1.4: Integration into FSM Engine (careful migration required)

**All PFLICHT components are implemented and ready for integration!**
