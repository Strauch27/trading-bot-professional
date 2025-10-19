# Intent-System & Order-Flow-Architektur

**Version:** V12
**Status:** Production-Ready
**Datum:** 2025-10-19

---

## 📋 Überblick

Das Intent-System trennt **Trading-Entscheidungen** (Decision Layer) von der **Order-Ausführung** (Execution Layer) durch ein FSM-basiertes Routing-System. Dies ermöglicht:

- ✅ Idempotente Order-Platzierung
- ✅ Vollständiger Audit-Trail
- ✅ State-Persistenz für Crash-Recovery
- ✅ Latency-Tracking mit Breakdown
- ✅ Budget-Leak-Prevention

---

## 🏗️ Architektur

```
┌─────────────────┐     ┌────────────────┐     ┌─────────────┐
│  Decision Layer │────▶│  OrderRouter   │────▶│  Exchange   │
│  (BuyDecision)  │     │     (FSM)      │     │   (CCXT)    │
└─────────────────┘     └────────────────┘     └─────────────┘
         │                      │                       │
         │ order.intent         │ order.filled          │
         ▼                      ▼                       │
  ┌─────────────┐       ┌──────────────┐              │
  │  EventBus   │──────▶│ Reconciler   │◀─────────────┘
  │             │       │ (Fetch Fills) │
  └─────────────┘       └──────────────┘
                               │
                               ▼
                        ┌──────────────┐
                        │  Portfolio   │
                        │   Manager    │
                        └──────────────┘
```

---

## 🔄 Event-Flow

### 1. Intent Creation (Decision Layer)

```python
# buy_decision.py - execute_buy_order()

intent = assemble_intent(signal_payload, guards_payload, risk_payload)

intent_metadata = {
    "decision_id": decision_id,
    "symbol": symbol,
    "signal": signal,
    "quote_budget": quote_budget,
    "intended_price": limit_price,
    "amount": amount,
    "start_ts": time.time(),  # P2: Latency tracking
    "market_snapshot": {...}
}

engine.pending_buy_intents[intent.intent_id] = intent_metadata
engine._persist_intent_state()  # P1: Debounced persistence

event_bus.publish("order.intent", intent.to_dict())
```

**Log Events:**
- `order_intent` (Phase 1 Logging)
- `order_sent` (JSONL)

---

### 2. Order Execution (Router FSM)

```python
# order_router.py - handle_intent()

# State: RESERVED
portfolio.reserve_budget(quote_budget, symbol, side="buy")

# State: SENT
order = exchange.place_limit_order(symbol, side, qty, limit_price, client_order_id)
_order_meta[order_id] = {**base_meta, "attempt": attempt}
_persist_meta_state()  # P1: Debounced persistence

# State: FILLED (after wait_for_fill)
_publish_filled(symbol, order_id, filled_qty, avg_price)
```

**FSM States:**
- NEW → RESERVED → SENT → FILLED / PARTIAL / CANCELED / FAILED

---

### 3. Reconciliation & Fill Processing

```python
# reconciler.py

def _on_order_filled_event(self, event):
    summary = self.reconcile_single_order(symbol, order_id)

    # Publish reconciled fill
    event_bus.publish("order.reconciled", {
        "intent_id": intent_id,
        "summary": summary  # qty_delta, notional, fees, state
    })
```

---

### 4. Portfolio Update (Buy Handler)

```python
# buy_decision.py - handle_router_fill()

metadata = engine.pending_buy_intents.pop(intent_id)
engine._persist_intent_state()  # P1: Remove from state

# P2: Latency tracking
intent_to_fill_ms = (time.time() - metadata["start_ts"]) * 1000
latency_breakdown = {
    "total_ms": intent_to_fill_ms,
    "placement_ms": event_data.get("placement_latency_ms", 0),
    "exchange_fill_ms": event_data.get("fill_latency_ms", 0),
    "reconcile_ms": event_data.get("reconcile_latency_ms", 0),
    "fetch_order_ms": event_data.get("fetch_order_latency_ms", 0)
}

rolling_stats.add_latency("intent_to_fill", intent_to_fill_ms)

# Update PnL, Portfolio, Logs
pnl_tracker.on_fill(symbol, "BUY", avg_price, filled_amount)
pnl_service.record_fill(...)
jsonl_logger.order_filled(...)
```

**Log Events:**
- `INTENT_LATENCY` (P2)
- `order_done` (Phase 1, mit latency_breakdown)
- `position_opened` (Phase 1)

---

## 📁 State-Persistenz (P1)

### Engine Transient State

**Datei:** `state/engine_transient.json`
**Interval:** 10s (debounced)
**Inhalt:**
```json
{
  "pending_buy_intents": {
    "1697123456789-BTCUSDT-a3f5c2d1": {
      "decision_id": "...",
      "symbol": "BTC/USDT",
      "signal": "DROP_TRIGGER",
      "quote_budget": 50.0,
      "intended_price": 50000.0,
      "amount": 0.001,
      "start_ts": 1697123456.789,
      "market_snapshot": {...}
    }
  },
  "last_update": 1697123466.789
}
```

### OrderRouter Metadata

**Datei:** `state/order_router_meta.json`
**Interval:** 10s (debounced)
**Inhalt:**
```json
{
  "order_meta": {
    "exchange-order-456": {
      "intent_id": "...",
      "symbol": "BTC/USDT",
      "side": "buy",
      "attempt": 1,
      "filled_qty": 0.001,
      "start_ts": 1697123456.789
    }
  },
  "last_update": 1697123466.789
}
```

### Recovery-Flow

```
Engine Start
    ↓
DebouncedStateWriter.init()
    ↓
Load state from disk
    ↓
Filter stale intents (age > INTENT_STALE_THRESHOLD_S)
    ↓
Restore pending_buy_intents
    ↓
Engine runs with recovered state
    ↓
Debounced writes every 10s
    ↓
Engine Stop → Final Flush
```

---

## ⚙️ Konfiguration

### Core Settings (`config.py`)

```python
# State Persistence
ENGINE_TRANSIENT_STATE_FILE = os.path.join(STATE_DIR, "engine_transient.json")
ORDER_ROUTER_META_FILE = os.path.join(STATE_DIR, "order_router_meta.json")
STATE_PERSIST_INTERVAL_S = 10.0  # Debounce interval
STATE_PERSIST_ON_SHUTDOWN = True  # Final flush on clean shutdown
INTENT_STALE_THRESHOLD_S = 60  # Intent considered stale after 60s
ORDER_META_MAX_AGE_S = 86400  # Clean up metadata older than 24h

# Order Router
ROUTER_MAX_RETRIES = 3
ROUTER_BACKOFF_MS = 400
ROUTER_TIF = "IOC"
ROUTER_SLIPPAGE_BPS = 20
ROUTER_MIN_NOTIONAL = 5.0
ROUTER_FETCH_ORDER_ON_FILL = False  # P2: Performance mode (default)

# Stale Intent Monitoring (P4)
STALE_INTENT_CHECK_ENABLED = True
STALE_INTENT_TELEGRAM_ALERTS = False  # Enable in production only
```

---

## 📊 Performance-Optimierungen (P2)

### fetch_order Optional

**Problem:** `fetch_order()` nach jedem Fill verursacht ~30-50ms Latency.

**Lösung:**
```python
ROUTER_FETCH_ORDER_ON_FILL = False  # Default: Performance-First
```

**Impact:**
- **False:** Spart ~30-50ms pro Fill, nutzt wait_for_fill() Daten
- **True:** Vollständige Order-Details für Debugging (langsamer)

### Latency-Tracking mit Breakdown

```python
# Logged in order_done event
latency_breakdown = {
    "total_ms": 245.3,
    "placement_ms": 12.5,
    "exchange_fill_ms": 180.2,
    "reconcile_ms": 45.1,
    "fetch_order_ms": 0.0  # 0 wenn disabled
}
```

**Metrics:**
- `rolling_stats.avg_latency("intent_to_fill", seconds=300)`
- `rolling_stats.p95_latency("intent_to_fill", seconds=300)`

---

## 🧪 Testing (P3)

### Integration-Tests

**Datei:** `tests/integration/test_order_flow.py`

```bash
pytest tests/integration/test_order_flow.py -v
```

**Tests:**
- ✅ Buy Intent → Router → Fill → Position (Success-Case)
- ✅ Partial Fill → Retry Handling
- ✅ Intent Failure → Cleanup
- ✅ Latency Tracking Complete
- ✅ State Persistence on Intent Lifecycle

### Unit-Tests

**Datei:** `tests/unit/test_portfolio_apply_fills.py`

```bash
pytest tests/unit/test_portfolio_apply_fills.py -v
```

**Tests:**
- ✅ Buy Aggregation (WAC)
- ✅ Sell Reduction (Realized PnL)
- ✅ Position State Transitions (NEW → OPEN → PARTIAL_EXIT → CLOSED)
- ✅ Fee Accumulation
- ✅ Budget Reconciliation

---

## 📈 Monitoring & Observability (P4)

### Dashboard-Widget

**Datei:** `ui/intent_dashboard.py`

```python
from ui.intent_dashboard import render_intent_panel

# In main dashboard loop:
intent_panel = render_intent_panel(
    engine.pending_buy_intents,
    engine.rolling_stats
)
print(intent_panel)
```

**Output:**
```
================================================================================
📊 INTENT TRACKING | 2 pending
================================================================================
🟢 FRESH  BTC/USDT      | DROP_TRIGGER        |  $50.00 |  Age:    15.3s | 169712345...
🟡 PENDING ETH/USDT     | DROP_TRIGGER        |  $75.00 |  Age:    45.7s | 169712346...
--------------------------------------------------------------------------------
Latency Metrics (last 5 min):
  Intent→Fill: avg=245.3ms, p95=380.2ms
================================================================================
```

### Log-Analysis Tools

**Datei:** `tools/log_analysis.py`

```bash
# Trace Intent Flow
python tools/log_analysis.py <intent_id>

# Show Recent Intents
python tools/log_analysis.py --recent 10

# Latency Report
python tools/log_analysis.py --latency-report
```

**Output:**
```
================================================================================
Intent Flow: 1697123456789-BTCUSDT-a3f5c2d1
================================================================================

 1. [15:30:45.123] order_intent            | BTC/USDT
    → qty=0.00100000, limit_price=50000.00000000

 2. [15:30:45.135] SENT                    | BTC/USDT
    → order_id=exchange-order-456

 3. [15:30:45.320] FILLED                  | BTC/USDT
    → filled_qty=0.00100000, avg_price=50000.00000000

 4. [15:30:45.365] order_done              | BTC/USDT
    → status=filled, latency=242ms
    → breakdown: placement=12.5ms, exchange=180.2ms, reconcile=45.1ms

================================================================================
Total Events: 4
================================================================================
```

### Stale-Intent-Detection

**Auto-Cleanup:** Alle 30s (zusammen mit Heartbeat)

**Logging:**
```
WARNING: Cleaned up 1 stale intents (age > 60s)
  {
    "event_type": "STALE_INTENT_CLEANUP",
    "count": 1,
    "intents": [
      {
        "intent_id": "...",
        "symbol": "BTC/USDT",
        "age_s": 75.3,
        "quote_budget": 50.0,
        "signal": "DROP_TRIGGER"
      }
    ]
  }
```

**Telegram-Alert (Optional):**
```
⚠️ Stale Intent Cleanup: 1 intents
• BTC/USDT: $50.00 (age: 75s)
```

---

## 🏥 Budget-Health-Metriken (P5)

### Health-Check

```python
health = portfolio.get_budget_health_metrics()

# Returns:
{
  "my_budget": 1000.0,
  "reserved_budget": 150.0,
  "verified_budget": 850.0,
  "reserved_quote_total": 150.0,
  "reserved_base_total": 0.05,
  "expected_available": 850.0,
  "actual_available": 850.0,
  "budget_drift": 0.0,
  "drift_pct": 0.0,
  "active_reservations_count": 3,
  "old_reservations": [],  # Reservations >5min
  "health": "HEALTHY"  # HEALTHY | WARNING
}
```

### Integration mit cleanup_stale_reservations

```python
# Automatic health check after stale cleanup
def cleanup_stale_reservations(self):
    # ... release stale reservations

    # P5: Log budget health after cleanup
    health_metrics = self.get_budget_health_metrics()
    logger.info(
        f"Budget health: drift={health_metrics['drift_pct']:.2f}%, "
        f"active_reservations={health_metrics['active_reservations_count']}",
        extra={"event_type": "BUDGET_HEALTH_CHECK", **health_metrics}
    )
```

**Health States:**
- **HEALTHY:** `drift_pct < 1%` AND `old_reservations == []`
- **WARNING:** `drift_pct >= 1%` OR `old_reservations > 0`

---

## 🚀 Deployment-Checklist

### Pre-Production

- [ ] State-Directory existiert (`STATE_DIR`)
- [ ] `STATE_PERSIST_INTERVAL_S` konfiguriert (default: 10s)
- [ ] `INTENT_STALE_THRESHOLD_S` angepasst (default: 60s)
- [ ] `ROUTER_FETCH_ORDER_ON_FILL` = False (Performance)
- [ ] `STALE_INTENT_TELEGRAM_ALERTS` aktiviert (optional)

### Testing

- [ ] Integration-Tests laufen durch
- [ ] Unit-Tests validiert
- [ ] Recovery-Test durchgeführt (Restart während Intent pending)
- [ ] Stale-Intent-Test durchgeführt (>60s Wartezeit)
- [ ] Budget-Health-Metriken validiert

### Monitoring

- [ ] Dashboard zeigt Intent-Tracking
- [ ] Log-Analysis-Tool funktioniert
- [ ] Latency-Metriken werden getrackt
- [ ] Telegram-Alerts getestet (falls aktiviert)

---

## 🔍 Troubleshooting

### Problem: Intents verschwinden nach Restart

**Ursache:** State-File nicht persistiert oder zu alt.

**Lösung:**
- Check `STATE_PERSIST_ON_SHUTDOWN = True`
- Check `engine_transient.json` existiert
- Check Intent-Alter < `INTENT_STALE_THRESHOLD_S`

### Problem: Hohe Intent→Fill Latency

**Ursache:** `fetch_order` aktiviert.

**Lösung:**
```python
ROUTER_FETCH_ORDER_ON_FILL = False
```

### Problem: Budget-Leaks

**Ursache:** Stale reservations nicht bereinigt.

**Lösung:**
- Check `cleanup_stale_reservations()` läuft periodisch
- Check Budget-Health-Metriken:
  ```python
  health = portfolio.get_budget_health_metrics()
  print(health["health"])  # Should be "HEALTHY"
  ```

---

## 📚 Weitere Ressourcen

- **Architektur-Diagramm:** `docs/architecture/order_flow.png`
- **Event-Schema:** `core/event_schemas.py`
- **FSM-Dokumentation:** `services/order_router.py`
- **Reconciliation-Logik:** `services/reconciler.py`

---

**Maintainer:** Trading Bot V12 Team
**Last Updated:** 2025-10-19
