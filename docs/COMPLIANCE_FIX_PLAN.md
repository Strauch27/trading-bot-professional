# Exchange Compliance Fix Plan

## Problem Analysis (Session 2025-11-02 10:41)

**Symptom:** ZBT/USDT buy signals triggered but all failed pre-flight validation.

**Root Cause:**
- `tick_size_violation`: Price not properly quantized to 0.0001
- `amount_step_violation`: Amount not properly quantized to 0.01
- Current code uses `size_buy_from_quote()` but validation fails

**Impact:** No trades executed despite valid signals

## Solution Architecture

### Phase 1: Core Modules ✅ COMPLETED

1. **`core/exchange_compliance.py`** ✅
   - `quantize_and_validate()`: Auto-fix price/amount to tick/step
   - `ComplianceResult`: Structured result with violations + auto_fixed flag
   - Min-cost auto-bump: Increases amount if notional < min_cost

2. **`state/ghost_store.py`** ✅
   - Tracks rejected buy intents
   - Thread-safe, TTL-based expiration (24h default)
   - Does NOT affect PnL or budget
   - Purely informational for UI transparency

### Phase 2: Integration (TODO)

#### 2.1 FSM Engine Initialization
```python
# engine/fsm_engine.py __init__
from state.ghost_store import GhostStore

class FSMTradingEngine:
    def __init__(self, ...):
        ...
        self.ghost_store = GhostStore(ttl_sec=86400)
```

#### 2.2 Buy Flow Patch
```python
# engine/fsm_engine.py _process_place_buy()
from core.exchange_compliance import quantize_and_validate
import uuid

def _process_place_buy(self, st, ctx):
    # Generate intent_id
    intent_id = str(uuid.uuid4())

    # Log BUY_INTENT event
    logger.info(f"[BUY_INTENT] {st.symbol} intent={intent_id[:8]} raw_price={ctx.price:.8f} raw_amount={amount:.6f}")

    # Quantize and validate
    market_info = self.exchange.market(st.symbol)
    comp = quantize_and_validate(ctx.price, amount, market_info)

    # Log quantization result
    logger.info(f"[BUY_INTENT_QUANTIZED] {st.symbol} intent={intent_id[:8]} q_price={comp.price:.8f} q_amount={comp.amount:.6f} violations={comp.violations}")

    # Check validity
    if not comp.is_valid():
        abort_reason = "precision_non_compliant" if "invalid_amount_after_quantize" in comp.violations else "min_cost_violation"

        # Log abort
        logger.warning(f"[BUY_INTENT_ABORTED] {st.symbol} intent={intent_id[:8]} reason={abort_reason} violations={comp.violations}")

        # Create ghost position
        self.ghost_store.create(
            intent_id=intent_id,
            symbol=st.symbol,
            q_price=comp.price,
            q_amount=comp.amount,
            violations=comp.violations,
            abort_reason=abort_reason,
            raw_price=ctx.price,
            raw_amount=amount,
            market_precision={
                "tick": market_info.get("precision", {}).get("price"),
                "step": market_info.get("precision", {}).get("amount"),
                "min_cost": market_info.get("limits", {}).get("cost", {}).get("min")
            }
        )

        self._emit_event(st, FSMEvent.ORDER_PLACEMENT_FAILED, ctx)
        return

    # Use quantized values for order
    price, amount = comp.price, comp.amount
    logger.info(f"[PRE_FLIGHT] {st.symbol} PASSED (q_price={price:.8f}, q_amount={amount:.6f})")

    # Place order (existing code)
    result = self.order_router.submit(...)

    if result.success:
        # Remove ghost if exists (edge case: retry after abort)
        self.ghost_store.remove_by_intent(intent_id)
```

#### 2.3 Portfolio Integration
```python
# core/portfolio/portfolio.py

def get_positions_with_ghosts(self, engine=None):
    positions = self.get_positions()

    if engine and hasattr(engine, 'ghost_store'):
        ghosts = engine.ghost_store.list_active()

        for g in ghosts:
            positions.append({
                "id": f"ghost:{g['id']}",
                "symbol": g["symbol"],
                "size": 0,
                "avg_price": g["q_price"],
                "exposure_usdt": 0,
                "unrealized_pnl_usdt": None,
                "state": "GHOST",
                "note": g["abort_reason"],
                "violations": g["violations"],
                "_ghost": True
            })

    return positions
```

### Phase 3: Frontend (TODO)

#### 3.1 Intent Lane Component
- Shows recent BUY_INTENT events
- Color-coded by outcome (PLACED=green, ABORTED=red, QUANTIZED=yellow)
- Displays violations and abort reasons

#### 3.2 Portfolio Ghost Rows
- Toggle "Show Ghosts" checkbox
- Ghost rows with gray background
- Tooltip showing violations and raw vs quantized values

#### 3.3 Header Stats
- Rejected counter by reason
- Example: "Aborted: 5 (precision_non_compliant: 3, min_cost_violation: 2)"

### Phase 4: Testing

#### 4.1 Unit Tests
```python
# tests/test_compliance.py

def test_zbt_usdt_quantization():
    """Test ZBT/USDT case from logs."""
    market = {
        "precision": {"price": 0.0001, "amount": 0.01},
        "limits": {"cost": {"min": 1.0}}
    }

    # Simulate raw values from size_buy_from_quote
    raw_price = 0.012345
    raw_amount = 123.456

    result = quantize_and_validate(raw_price, raw_amount, market)

    assert result.price == 0.0123  # Floored to tick
    assert result.amount == 123.45  # Floored to step
    assert "tick_size_violation" in result.violations
    assert "amount_step_violation" in result.violations
    assert result.auto_fixed == False  # No min_cost fix needed
    assert result.is_valid() == True  # Should pass after quantization


def test_min_cost_auto_bump():
    """Test min_cost auto-fix."""
    market = {
        "precision": {"price": 0.1, "amount": 1},
        "limits": {"cost": {"min": 5.0}}
    }

    # Would give notional = 0.1 * 10 = 1.0 < 5.0
    result = quantize_and_validate(0.1, 10, market)

    assert result.amount >= 50  # Should bump to 50 (0.1 * 50 = 5.0)
    assert "min_cost_auto_fixed" in result.violations
    assert result.auto_fixed == True
```

#### 4.2 Integration Tests
- Run bot for 1h with compliance logging
- Verify ghost positions created for precision violations
- Verify successful orders after quantization

### Phase 5: Deployment

1. Deploy backend changes
2. Run for 24h, monitor ghost_store statistics
3. Deploy frontend after backend stability confirmed
4. Add alerts for high rejection rates

## Event Schema

### BUY_INTENT
```json
{
  "type": "BUY_INTENT",
  "ts": 1730563200.123,
  "intent_id": "f47ac10b-58cc-4372-a567-0e02b2c3d479",
  "symbol": "ZBT/USDT",
  "raw_price": 0.012345,
  "raw_amount": 123.456,
  "market_precision": {
    "tick": 0.0001,
    "step": 0.01,
    "min_cost": 1.0
  },
  "budget_usdt": 10.0,
  "engine_mode": "FSM",
  "anchor_mode": "MODE_4"
}
```

### BUY_INTENT_QUANTIZED
```json
{
  "type": "BUY_INTENT_QUANTIZED",
  "ts": 1730563200.124,
  "intent_id": "f47ac10b-...",
  "symbol": "ZBT/USDT",
  "q_price": 0.0123,
  "q_amount": 123.45,
  "violations": ["tick_size_violation", "amount_step_violation"]
}
```

### BUY_INTENT_ABORTED
```json
{
  "type": "BUY_INTENT_ABORTED",
  "ts": 1730563200.125,
  "intent_id": "f47ac10b-...",
  "symbol": "ZBT/USDT",
  "violations": ["invalid_amount_after_quantize"],
  "abort_reason": "precision_non_compliant"
}
```

### BUY_ORDER_PLACED
```json
{
  "type": "BUY_ORDER_PLACED",
  "ts": 1730563200.126,
  "intent_id": "f47ac10b-...",
  "symbol": "ZBT/USDT",
  "order": {
    "id": "1234567890",
    "price": 0.0123,
    "amount": 123.45,
    "status": "open"
  }
}
```

## Rollout Checklist

- [x] Create `core/exchange_compliance.py`
- [x] Create `state/ghost_store.py`
- [ ] Add ghost_store to FSM Engine `__init__`
- [ ] Patch `_process_place_buy` with quantize_and_validate
- [ ] Add intent event logging
- [ ] Update portfolio to include ghosts
- [ ] Write unit tests
- [ ] Document event schema
- [ ] Deploy backend
- [ ] Monitor for 24h
- [ ] Implement frontend IntentLane
- [ ] Implement frontend ghost rows
- [ ] Add header rejected stats

## Success Metrics

- **Before:** 100% of ZBT/USDT signals failed pre-flight
- **After:** 0% pre-flight failures (auto-quantized)
- **Ghost Rate:** <5% (only invalid markets or insufficient budget)
- **Order Success Rate:** >95% after quantization

## References

- Session logs: `sessions/session_20251102_104145/`
- Original issue: Pre-flight tick/step violations
- Market data: `load_markets.json` → ZBT/USDT precision
