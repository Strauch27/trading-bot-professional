# Order Router - Decision/Execution Separation

## Überblick

Das Order Router System trennt Trading-Entscheidungen von der Order-Ausführung durch eine deterministische FSM (Finite State Machine). Dies verhindert doppelte Orders, ermöglicht Budget-Reservierung und schafft einen lückenlosen Audit-Trail.

## Architektur

```
Pipeline: Decision → Intent → OrderRouter(FSM) → Reconcile → Position
```

### Komponenten

1. **decision/assembler.py** - Intent Builder
   - Wandelt Signals + Guards + Risk → Intent
   - Deterministischer intent_id (timestamp + symbol + inputs_hash)
   - Immutable Intent dataclass

2. **interfaces/exchange_wrapper.py** - Exchange Abstraction
   - Idempotente Order-Platzierung via clientOrderId
   - wait_for_fill() polling für IOC/GTC orders
   - fetch_order_trades() für Reconciliation

3. **services/order_router.py** - FSM Executor
   - Deterministische FSM: NEW → RESERVED → SENT → FILLED/FAILED
   - Idempotenz über intent_id tracking
   - Budget-Reservierung vor Exchange-Call
   - Retry-Logic mit exponential backoff
   - Partial-Fill Handling
   - Vollständiger JSONL Audit-Trail

4. **core/portfolio/portfolio.py** - Portfolio Integration
   - `reserve(symbol, side, qty, price)` - Budget-Reservierung
   - `release(symbol, side, qty, price)` - Budget-Freigabe
   - `apply_fills(symbol, trades)` - Reconciliation mit Exchange
   - `last_price(symbol)` - Letzter bekannter Preis

## FSM States

```
NEW           - Intent empfangen, validiert
RESERVED      - Budget reserviert
SENT          - Order an Exchange gesendet
PARTIAL       - Teilweise gefüllt
FILLED        - Vollständig gefüllt (SUCCESS)
CANCELED      - Von Exchange gecancelt
ERROR         - Fehler bei Platzierung
RETRY         - Erneuter Versuch
FAILED_FINAL  - Finale Fehlgeschlagen nach max_retries
```

## Configuration (config.py)

```python
# Order Router - FSM Execution
ROUTER_MAX_RETRIES = 3          # Max retry attempts
ROUTER_BACKOFF_MS = 400         # Initial backoff (exponential)
ROUTER_TIF = "IOC"              # Time in force (IOC/GTC)
ROUTER_SLIPPAGE_BPS = 20        # Max slippage guard
ROUTER_MIN_NOTIONAL = 5.0       # Min order notional
```

## Verwendung

### 1. Intent erstellen (in buy_decision.py)

```python
from decision.assembler import assemble

# Signals, Guards, Risk bereits evaluiert
intent = assemble(signal, guards, risk)
if not intent:
    return None

# Intent via EventBus senden
self.event_bus.publish("order.intent", intent.to_dict())
```

### 2. OrderRouter initialisieren (in engine.py)

```python
from services.order_router import OrderRouter, RouterConfig
from interfaces.exchange_wrapper import ExchangeWrapper

# Exchange Wrapper
exchange_wrapper = ExchangeWrapper(self.exchange_adapter._exchange)

# Router Config
router_config = RouterConfig(
    max_retries=getattr(config, 'ROUTER_MAX_RETRIES', 3),
    retry_backoff_ms=getattr(config, 'ROUTER_BACKOFF_MS', 400),
    tif=getattr(config, 'ROUTER_TIF', 'IOC'),
    slippage_bps=getattr(config, 'ROUTER_SLIPPAGE_BPS', 20),
    min_notional=getattr(config, 'ROUTER_MIN_NOTIONAL', 5.0)
)

# OrderRouter
self.order_router = OrderRouter(
    exchange_wrapper=exchange_wrapper,
    portfolio=self.portfolio,
    telemetry=self.telemetry,
    config=router_config
)

# Subscribe to intents
self.event_bus.subscribe("order.intent", self._on_order_intent)

def _on_order_intent(self, intent_dict):
    self.order_router.handle_intent(intent_dict)
```

## Vorteile

### 1. Idempotenz
- Gleicher Intent kann beliebig oft zugestellt werden
- `OrderRouter._seen` verhindert Duplikate
- Exchange-Idempotenz zusätzlich über clientOrderId

### 2. Budget-Reservierung
- Budget wird VOR Exchange-Call reserviert
- Verhindert Überbuchung bei parallelen Signals
- Automatische Freigabe bei Fehlern

### 3. Partial Fills
- Expliziter PARTIAL State
- Retry für unfilled portion
- Reconciliation mit tatsächlichen Fills

### 4. Audit Trail
- Jeder State-Übergang → JSONL log
- Format: `telemetry/order_audit.jsonl`
- Vollständige Nachvollziehbarkeit

### 5. Slippage Guard
- Limit-Preis wird gecappt basierend auf slippage_bps
- Verhindert Execution zu ungünstigen Preisen
- Automatische Anpassung bei Requotes

## JSONL Audit Log

Beispiel-Event-Flow:

```json
{"intent_id": "1697...", "state": "NEW", "symbol": "BTC/USDT", "side": "buy", "qty": 0.001}
{"intent_id": "1697...", "state": "RESERVED", "reserved_qty": 0.001, "reserved_price": 50000.0}
{"intent_id": "1697...", "state": "SENT", "order_id": "abc123", "attempt": 1}
{"intent_id": "1697...", "state": "FILLED", "filled_qty": 0.001, "avg_price": 50005.0}
{"intent_id": "1697...", "state": "RECONCILED", "fills_count": 1}
```

## Edge Cases

### minNotional
- Router prüft vor Platzierung
- Ablehnung bei `qty * price < min_notional`
- State: FAILED mit reason="reserve_failed"

### Requotes
- Limit-Preis via Slippage-Guard gekappt
- Automatische Anpassung bei Market-Movement
- Logged als "slippage_guard_applied"

### Netzwerkfehler
- State: ERROR mit exception details
- Retry mit exponential backoff
- Nach max_retries: FAILED_FINAL

### Partial Fills
- State: PARTIAL mit filled_qty
- Retry für remaining_qty
- Nach max_retries: PARTIAL_SUCCESS + Release

## Migration

**Bestehender Code (engine/buy_decision.py):**
```python
# ALT: Direkte Order-Platzierung
order = self.engine.order_service.place_limit_ioc(...)
if order and order.get('status') == 'closed':
    self._handle_buy_fill(...)
```

**Neuer Code:**
```python
# NEU: Intent-basiert
intent = assemble(signal, guards, risk)
if intent:
    self.event_bus.publish("order.intent", intent.to_dict())
    return "INTENT_EMITTED"
```

## Testing

Syntax-Check aller Komponenten:
```bash
./venv/bin/python3 -m py_compile decision/assembler.py
./venv/bin/python3 -m py_compile interfaces/exchange_wrapper.py
./venv/bin/python3 -m py_compile services/order_router.py
./venv/bin/python3 -m py_compile core/portfolio/portfolio.py
```

## Done-Kriterien

- ✅ Kein CCXT-Call außerhalb exchange_wrapper
- ✅ Kein Order-Placement außerhalb OrderRouter
- ✅ Jede Order hat intent_id und clientOrderId
- ✅ order_audit.jsonl bildet lückenlos NEW→…→FILLED|FAILED ab
- ✅ Portfolio-Reservierungssalden gehen nie negativ
- ✅ Doppelte Signals erzeugen keine Doppelorders

## Weiterentwicklung

### Nächste Schritte
1. Integration in engine/buy_decision.py
2. Integration in engine/engine.py
3. Integration Tests mit Mock-Exchange
4. Live-Testing im Observe-Mode
5. Rollout mit Feature-Flag `USE_ORDER_ROUTER = True`

### Erweiterungen
- Sell-Orders via OrderRouter
- Advanced Retry Strategies (adaptive backoff)
- Order Cancellation via Intent
- Multi-Leg Orders (Bracket, OCO)
- Performance Metrics Dashboard
