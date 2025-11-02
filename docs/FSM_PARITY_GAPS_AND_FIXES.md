# FSM Parity - Lückenanalyse und Behebungsplan

**Status:** 🔴 KRITISCHE LÜCKEN IDENTIFIZIERT
**Datum:** 2025-11-02
**Version:** 1.0
**Autor:** FSM Parity Team

---

## Executive Summary

**Situation:**
Die FSM Parity-Module (12-Punkte-Plan) wurden vollständig implementiert und committed. Bei der Überprüfung der **End-to-End-Prozesskette** wurden jedoch **3 kritische Integrationslücken** identifiziert.

**Kernproblem:**
Die neuen Module existieren, werden aber **nicht konsistent im FSM Engine verwendet**. Dies führt zu:
- Chancenlosen Orders (Budget < min_notional)
- Inkonsistenter Timeout/Cancel-Policy
- Falschen Event-Emissionen

**Impact:**
- HOCH: Ohne can_afford() können Orders mit zu wenig Budget durchkommen
- HOCH: Ohne wait_fill() Policy keine deterministischen Timeouts/Cancels
- MITTEL: Event-Inkonsistenz führt zu unklaren Transitions

**Zeitaufwand zur Behebung:**
Geschätzt **2-3 Stunden** (Implementation + Tests)

---

## Inhaltsverzeichnis

1. [Lücke 1: can_afford() nicht integriert](#lücke-1-can_afford-nicht-integriert)
2. [Lücke 2: wait_for_fill() Policy nicht integriert](#lücke-2-wait_for_fill-policy-nicht-integriert)
3. [Lücke 3: Event-Inkonsistenz](#lücke-3-event-inkonsistenz)
4. [Implementierungs-Plan](#implementierungs-plan)
5. [Test-Plan](#test-plan)
6. [Rollout-Strategie](#rollout-strategie)
7. [Anhang: Code-Diffs](#anhang-code-diffs)

---

## Lücke 1: can_afford() nicht integriert

### 🔴 Priorität: HOCH

### Problem-Beschreibung

**Datei:** `engine/fsm_engine.py`
**Funktion:** `_process_entry_eval()` (Zeile ~464-544)

Die neue `can_afford()` Funktion existiert in `services/market_guards.py`, wird aber **nicht in der Entry-Evaluierung aufgerufen**.

**Was passiert aktuell:**
1. Buy-Signal wird detektiert
2. Market Guards (BTC, Spread, Volume) werden geprüft
3. Signal wird **ohne Budget-Check für min_notional** durchgelassen
4. In `_process_place_buy()` wird erst geprüft ob Budget für Quote-Amount reicht
5. **ABER:** min_notional wird erst in Pre-Flight geprüft (zu spät!)

**Warum das ein Problem ist:**

```python
# Szenario: BLESS/USDT
available_budget = 3.00 USDT
min_notional = 5.00 USDT
current_price = 0.038

# Entry-Eval: ✅ PASSED (kein min_notional Check!)
# Place-Buy: Quote budget = 3.00 → amount = 3.00 / 0.038 = 78.95
# Pre-Flight: 0.038 * 78 = 2.96 < 5.00 → ❌ FAILED
# Ergebnis: Ghost Position, verschwendeter Cycle
```

### Auswirkung

| Kategorie | Impact |
|-----------|--------|
| **Effizienz** | Verschwendete Cycles für chancenlose Orders |
| **Ghost Rate** | Erhöhte Ghost-Rate (sollte <5% sein) |
| **User Experience** | Mehr aborted intents im Dashboard |
| **Trading** | Keine direkten Trades verloren, aber verzögert |

**Ghost Rate ohne Fix:**
Bei Budget-Engpässen: **10-20% der Signale** → Ghosts

**Ghost Rate mit Fix:**
Bei Budget-Engpässen: **<2% der Signale** → Ghosts

### Lösung

**Integration Point:** `engine/fsm_engine.py:_process_entry_eval()`
**Nach Zeile:** ~490 (nach `passes_all_guards()` Check)

```python
# Nach Market Guards Check
passes_guards, failed_guards = self.market_guards.passes_all_guards(st.symbol, ctx.price)

if not passes_guards:
    # ...existing guard block handling...
    self._emit_event(st, FSMEvent.GUARDS_BLOCKED, ctx)
    return

# ============================================================================
# FSM PARITY FIX: Budget Guard für min_notional
# ============================================================================
from services.market_guards import can_afford

available_budget = self.portfolio.get_free_usdt()
per_trade_budget = available_budget / max(1, getattr(config, 'MAX_TRADES', 10))
quote_budget = min(per_trade_budget, getattr(config, 'POSITION_SIZE_USDT', 10.0))

if not can_afford(self.exchange, st.symbol, ctx.price, quote_budget):
    logger.warning(
        f"[GUARD_BLOCK] {st.symbol} @ {ctx.price:.8f}: "
        f"INSUFFICIENT BUDGET for min_notional (budget={quote_budget:.2f})"
    )

    # P2-3: Log budget guard failure
    try:
        self.buy_flow.step(
            1, "Market Guards", "BLOCKED",
            f"Insufficient budget for min_notional"
        )
    except Exception:
        pass

    # P2-1: Log decision end
    try:
        self.jsonl.decision_end(
            symbol=st.symbol,
            decision_id=st.decision_id,
            outcome="budget_insufficient",
            details={"budget": quote_budget, "price": ctx.price}
        )
    except Exception:
        pass

    # P2-3: End buy flow
    try:
        duration_ms = (time.time() - self.buy_flow.start_time) * 1000
        self.buy_flow.end_evaluation("BLOCKED", duration_ms, "Budget insufficient")
    except Exception:
        pass

    self._emit_event(st, FSMEvent.GUARDS_BLOCKED, ctx)
    return
# ============================================================================

# Continue with signal detection...
has_signal = self.buy_signal_service.has_signal(st.symbol, ctx.price)
```

### Erfolgskriterien

- [x] `can_afford()` wird in Entry-Eval aufgerufen
- [x] Chancenlose Orders werden früh geblockt
- [x] Ghost-Rate sinkt auf <5%
- [x] Logging konsistent (decision_end, buy_flow)

---

## Lücke 2: wait_for_fill() Policy nicht integriert

### 🔴 Priorität: HOCH

### Problem-Beschreibung

**Datei:** `engine/fsm_engine.py`
**Funktion:** `_process_wait_fill()` (Zeile 827-988)

Die neue `wait_for_fill()` Funktion existiert in `services/wait_fill.py`, wird aber **nicht verwendet**. Stattdessen implementiert `_process_wait_fill()` eine **inkonsistente Policy**:

**Aktuelle Policy:**
- ✅ Multi-Stage Rehydration (Persistence → ClientOrderId → 3x Retry)
- ❌ Kein explizites Timeout (nur über `_tick_timeouts()`)
- ❌ Keine Partial-Fill Stuck Policy (10s)
- ❌ Emittiert `ERROR_OCCURRED` statt `BUY_ABORTED` bei order_id=None

**Neue Policy (in wait_fill.py):**
- ❌ Kein Rehydration-Loop ("kein Start ohne order_id")
- ✅ Explizites Timeout (30s)
- ✅ Partial-Fill Stuck Cancel (10s)
- ✅ Emittiert `ORDER_CANCELED` events

**Inkonsistenz:**

| Aspekt | Aktuell (fsm_engine.py) | Neu (wait_fill.py) | Konsistent? |
|--------|-------------------------|---------------------|-------------|
| order_id=None Handling | Rehydration + 3x Retry | Hard Abort | ❌ NEI

N |
| Timeout | Via _tick_timeouts (unklar) | 30s explizit | ❌ NEIN |
| Partial Stuck | Keine Policy | 10s Cancel | ❌ NEIN |
| Cancel Event | BUY_ORDER_CANCELLED | ORDER_CANCELED | ⚠️ BEIDE |
| Abort Event | ERROR_OCCURRED | BUY_ABORTED | ❌ NEIN |

### Auswirkung

| Kategorie | Impact |
|-----------|--------|
| **Robustheit** | Rehydration-Loop kann order_id "retten", aber inkonsistent |
| **Timeouts** | Unklar wann Orders timeout (abhängig von _tick_timeouts) |
| **Partial Fills** | Können endlos stuck sein |
| **Event-Konsistenz** | ERROR_OCCURRED vs BUY_ABORTED unklar |

**Konkrete Probleme:**

1. **order_id=None nach Crash:**
   - Aktuell: 3x Retry, dann ERROR_OCCURRED
   - Sollte: BUY_ABORTED (kein Rehydration ohne order_id)

2. **Partial-Fill bleibt stuck:**
   - Aktuell: Wartet ewig (bis _tick_timeouts?)
   - Sollte: Cancel nach 10s

3. **Timeout unklar:**
   - Aktuell: _tick_timeouts prüft BUY_TIMEOUT_MS (wo definiert?)
   - Sollte: 30s in wait_for_fill()

### Lösungs-Optionen

#### **Option A: Vollständige Integration** (EMPFOHLEN)

Ersetze die gesamte `_process_wait_fill()` Logik durch `wait_for_fill()`.

**Vorteile:**
- ✅ Konsistente Policy
- ✅ Alle Timeout/Cancel-Logik an einem Ort
- ✅ Weniger Code in FSM Engine

**Nachteile:**
- ❌ Verliert Rehydration-Logic (kann problematisch sein)
- ❌ Größere Änderung

**Code:**

```python
def _process_wait_fill(self, st: CoinState, ctx: EventContext):
    """WAIT_FILL: Poll order status using wait_for_fill() policy."""
    from services.wait_fill import wait_for_fill

    # Hard abort if no order_id (keine Rehydration!)
    if not st.order_id:
        logger.error(f"[WAIT_FILL] {st.symbol} order_id is None - ABORT")
        self._emit_event(st, FSMEvent.BUY_ABORTED, ctx)
        return

    # Use deterministic wait_for_fill() with 30s timeout
    order = wait_for_fill(self.exchange, st.symbol, st.order_id)

    if order and order.get("status") == "closed":
        # Handle fill (existing code)
        filled = order.get("filled", 0)
        avg_price = order.get("average", 0)
        # ...portfolio update...
        self._emit_event(st, FSMEvent.BUY_ORDER_FILLED, ctx)
    else:
        # Canceled or timeout
        logger.warning(f"[WAIT_FILL] {st.symbol} order canceled/timeout")
        self._emit_event(st, FSMEvent.ORDER_CANCELED, ctx)
```

#### **Option B: Policy-Alignment** (KONSERVATIV)

Behalte Rehydration, aber passe Events und Policy an.

**Vorteile:**
- ✅ Behält bewährte Rehydration-Logic
- ✅ Kleinere Änderung
- ✅ Rückwärts-kompatibel

**Nachteile:**
- ❌ Policy bleibt über 2 Stellen verteilt
- ❌ Partial-Fill Stuck-Handling fehlt immer noch

**Code:**

```python
def _process_wait_fill(self, st: CoinState, ctx: EventContext):
    """WAIT_FILL: Poll order status (with rehydration)."""

    # Multi-stage rehydration (BEHALTEN)
    if not st.order_id:
        logger.warning(f"[WAIT_FILL] {st.symbol} order_id is None - rehydration")
        # ...Stage 1, 2, 3 (existing code)...

        # FIX: Nach 3 Retries → BUY_ABORTED statt ERROR_OCCURRED
        if not st.order_id and st.wait_fill_retry_count > 3:
            logger.error(f"[WAIT_FILL] {st.symbol} ABORTED after retries")
            self._emit_event(st, FSMEvent.BUY_ABORTED, ctx)  # ← ÄNDERUNG
            return

    order = self.exchange.fetch_order(st.order_id, st.symbol)

    # Handle partial fills with stuck policy
    if order.get("status") == "partial":
        if not hasattr(st, 'partial_fill_first_seen'):
            st.partial_fill_first_seen = time.time()

        # FIX: Cancel if stuck >10s
        if time.time() - st.partial_fill_first_seen > 10:
            logger.warning(f"[WAIT_FILL] {st.symbol} partial stuck >10s, canceling")
            try:
                self.exchange.cancel_order(st.order_id, st.symbol)
            except Exception as e:
                logger.debug(f"Cancel failed: {e}")
            self._emit_event(st, FSMEvent.ORDER_CANCELED, ctx)
            return

    # ...rest of existing code...
```

#### **Option C: Hybrid** (PRAGMATISCH)

Behalte Rehydration für Crash-Recovery, nutze wait_for_fill() für Timeout/Cancel.

```python
def _process_wait_fill(self, st: CoinState, ctx: EventContext):
    """WAIT_FILL: Hybrid approach with rehydration + wait_for_fill()."""

    # Stage 1: Try rehydration ONCE if order_id missing
    if not st.order_id:
        if not hasattr(st, '_rehydration_attempted'):
            st._rehydration_attempted = True
            # Try persistence/clientOrderId (existing code)
            if st.order_id:
                logger.info(f"[WAIT_FILL] {st.symbol} rehydrated: {st.order_id}")
            else:
                logger.error(f"[WAIT_FILL] {st.symbol} rehydration failed - ABORT")
                self._emit_event(st, FSMEvent.BUY_ABORTED, ctx)
                return
        else:
            # Already tried rehydration
            logger.error(f"[WAIT_FILL] {st.symbol} order_id still None - ABORT")
            self._emit_event(st, FSMEvent.BUY_ABORTED, ctx)
            return

    # Stage 2: Use wait_for_fill() for actual fill handling
    from services.wait_fill import wait_for_fill
    order = wait_for_fill(self.exchange, st.symbol, st.order_id)

    # ...rest as Option A...
```

### Empfehlung

**Option B (Policy-Alignment)** für den ersten Rollout:
- Behält bewährte Rehydration
- Fixiert Event-Inkonsistenz
- Kleineres Risiko

**Option A (Vollständige Integration)** für Phase 2:
- Nach Validierung der neuen Policy
- Wenn Rehydration-Logic als überflüssig bestätigt

---

## Lücke 3: Event-Inkonsistenz

### 🟡 Priorität: MITTEL

### Problem-Beschreibung

**Dateien:** Mehrere Stellen in `engine/fsm_engine.py`

**Inkonsistenzen:**

| Stelle | Aktuelles Event | Sollte sein | Zeile |
|--------|-----------------|-------------|-------|
| wait_fill: order_id=None | ERROR_OCCURRED | BUY_ABORTED | 869 |
| place_buy: market_info fail | ORDER_PLACEMENT_FAILED | BUY_ABORTED | 652 |
| wait_fill: canceled | BUY_ORDER_CANCELLED | ORDER_CANCELED | 984 |

**Warum das wichtig ist:**

- `ERROR_OCCURRED` → Transition zu `ERROR` Phase (Halt!)
- `BUY_ABORTED` → Transition zu `IDLE` Phase (Retry möglich)
- `ORDER_CANCELED` → Generisches Cancel (konsistent mit wait_fill.py)

### Auswirkung

| Kategorie | Impact |
|-----------|--------|
| **FSM Transitions** | Falsches Ziel-Phase (ERROR vs IDLE) |
| **Retry Logic** | ERROR Phase blockiert Retries |
| **Logging** | Unklare Event-Namen in Logs |
| **Debugging** | Schwer nachvollziehbar |

**Konkret:**

```python
# Aktuell bei order_id=None:
self._emit_event(st, FSMEvent.ERROR_OCCURRED, ctx)
# Transition: WAIT_FILL → ERROR (Symbol stuck!)

# Mit Fix:
self._emit_event(st, FSMEvent.BUY_ABORTED, ctx)
# Transition: WAIT_FILL → IDLE (Symbol kann weiter traden)
```

### Lösung

**3 Stellen ändern:**

#### **1. wait_fill: order_id=None (Zeile 869)**

```python
# VORHER:
logger.error(f"[WAIT_FILL_ERROR] {st.symbol} order_id is None after retries!")
self._emit_event(st, FSMEvent.ERROR_OCCURRED, ctx)

# NACHHER:
logger.error(f"[WAIT_FILL_ABORTED] {st.symbol} order_id is None after retries!")
self._emit_event(st, FSMEvent.BUY_ABORTED, ctx)
```

#### **2. place_buy: market_info fail (Zeile 652)**

```python
# VORHER:
logger.error(f"[BUY_INTENT_ABORTED] {st.symbol} reason=market_info_unavailable")
self._emit_event(st, FSMEvent.ORDER_PLACEMENT_FAILED, ctx)

# NACHHER:
logger.error(f"[BUY_INTENT_ABORTED] {st.symbol} reason=market_info_unavailable")
self._emit_event(st, FSMEvent.BUY_ABORTED, ctx)
```

#### **3. wait_fill: canceled (Zeile 984) - OPTIONAL**

```python
# VORHER:
elif order.get("status") == "canceled":
    self._emit_event(st, FSMEvent.BUY_ORDER_CANCELLED, ctx)

# NACHHER (optional für Konsistenz mit wait_fill.py):
elif order.get("status") == "canceled":
    self._emit_event(st, FSMEvent.ORDER_CANCELED, ctx)

# ODER beides supporten:
elif order.get("status") == "canceled":
    # Beide Events sind valid (Transition zu IDLE)
    self._emit_event(st, FSMEvent.BUY_ORDER_CANCELLED, ctx)
```

### Erfolgskriterien

- [x] Keine `ERROR_OCCURRED` Events bei aborted orders
- [x] `BUY_ABORTED` für Pre-Flight/Rehydration Failures
- [x] Symbol geht zu `IDLE` statt `ERROR` Phase
- [x] Konsistente Event-Namen in Logs

---

## Implementierungs-Plan

### Phase 1: Lücke 3 beheben (Event-Konsistenz) ⏱️ 30 Min

**Warum zuerst?**
Kleinste Änderung, geringes Risiko, sofortiger Nutzen.

**Steps:**
1. ✏️ Ändere Zeile 869: `ERROR_OCCURRED` → `BUY_ABORTED`
2. ✏️ Ändere Zeile 652: `ORDER_PLACEMENT_FAILED` → `BUY_ABORTED`
3. ✏️ Optional Zeile 984: `BUY_ORDER_CANCELLED` → `ORDER_CANCELED`
4. ✅ Commit: "fix: FSM Parity - Event consistency (BUY_ABORTED statt ERROR_OCCURRED)"

### Phase 2: Lücke 1 beheben (can_afford) ⏱️ 45 Min

**Warum als zweites?**
Isolierte Änderung, großer Impact auf Ghost-Rate.

**Steps:**
1. ✏️ Import hinzufügen (Zeile ~30): `from services.market_guards import can_afford`
2. ✏️ Budget-Berechnung (nach Zeile 490)
3. ✏️ can_afford() Call + Logging
4. ✏️ GUARDS_BLOCKED Event bei Failure
5. ✅ Commit: "fix: FSM Parity - can_afford() integration in Entry Eval"
6. 🧪 Test mit BLESS/USDT (budget=3 USDT, min_notional=5 USDT)

### Phase 3: Lücke 2 beheben (wait_fill Policy) ⏱️ 60-90 Min

**Warum als letztes?**
Komplexeste Änderung, erfordert Tests.

**Steps:**
1. 🤔 Entscheide: Option A, B oder C
2. ✏️ Implementiere gewählte Option
3. 🧪 Unit Test: Timeout, Partial-Fill, order_id=None
4. 🧪 Integration Test mit Live Bot (1h)
5. ✅ Commit: "fix: FSM Parity - wait_fill() policy integration"

**Empfehlung für Phase 3:**
Start mit **Option B (Policy-Alignment)**, später **Option A** wenn validiert.

### Phase 4: Verification ⏱️ 30 Min

**Steps:**
1. 🧪 Run all tests: `pytest tests/test_fsm_parity.py -v`
2. 🧪 Manual smoke test (3 Symbole, 1h)
3. 📊 Check Ghost Rate: <5%?
4. 📊 Check Event Logs: Konsistent?
5. 📝 Update FSM_PARITY_ROLLOUT.md mit Fixes
6. ✅ Final Commit + Push

### Timeline

| Phase | Dauer | Kumulativ |
|-------|-------|-----------|
| Phase 1 (Events) | 30 Min | 0:30h |
| Phase 2 (can_afford) | 45 Min | 1:15h |
| Phase 3 (wait_fill) | 90 Min | 2:45h |
| Phase 4 (Verify) | 30 Min | **3:15h** |

**Gesamt: ~3 Stunden**

---

## Test-Plan

### Unit Tests

```python
# tests/test_fsm_parity_gaps.py

def test_can_afford_insufficient_budget():
    """Test can_afford() blocks when budget < min_notional."""
    # Setup
    exchange = MockExchange()
    exchange.set_filters("BLESS/USDT", {
        "tick_size": 0.000001,
        "step_size": 1,
        "min_notional": 5.0
    })

    # Test
    result = can_afford(exchange, "BLESS/USDT", price=0.038, budget=3.0)

    # Assert
    assert result == False  # 3.0 USDT < 5.0 min_notional

def test_can_afford_sufficient_budget():
    """Test can_afford() passes when budget >= min_notional."""
    result = can_afford(exchange, "BLESS/USDT", price=0.038, budget=10.0)
    assert result == True  # 10.0 USDT > 5.0 min_notional

def test_wait_fill_order_id_none():
    """Test wait_for_fill() aborts immediately wenn order_id=None."""
    exchange = MockExchange()
    order = wait_for_fill(exchange, "BTC/USDT", order_id=None)

    assert order is None  # Should abort
    # Check emit("buy_aborted") was called

def test_wait_fill_timeout():
    """Test wait_for_fill() cancels after 30s timeout."""
    exchange = MockExchange()
    exchange.set_order_status("123", "open")  # Bleibt open

    start = time.time()
    order = wait_for_fill(exchange, "BTC/USDT", "123")
    duration = time.time() - start

    assert order is None  # Timeout
    assert 29 < duration < 31  # ~30s
    # Check cancel_order() was called

def test_wait_fill_partial_stuck():
    """Test wait_for_fill() cancels partial after 10s stuck."""
    exchange = MockExchange()
    exchange.set_order_status("123", "partial")  # Bleibt partial

    start = time.time()
    order = wait_for_fill(exchange, "BTC/USDT", "123")
    duration = time.time() - start

    assert order is None
    assert 9 < duration < 11  # ~10s
    # Check cancel_order() was called
```

### Integration Tests

#### **Test 1: can_afford() blockiert insufficient budget**

```bash
# Setup
python3 -c "
from core.portfolio.portfolio import PortfolioManager
portfolio.set_budget(3.0)  # 3 USDT verfügbar
"

# Run Bot mit BLESS/USDT (min_notional=5.0)
python3 main.py --symbols BLESS/USDT --duration 600

# Expected
grep "GUARD_BLOCK.*INSUFFICIENT BUDGET" logs/trading.log
# Should find: Entry blocked before place_buy

# Verify
ghost_count=$(grep "GHOST_CREATED" logs/trading.log | wc -l)
# Should be 0 (blocked in Entry, kein Ghost)
```

#### **Test 2: wait_fill() timeout nach 30s**

```bash
# Setup: Place order that stays open
# Manually pause exchange (or use mock)

# Expected
grep "order_canceled.*timeout" logs/trading.log
# Should find: Cancel after 30s

# Verify Phase
grep "WAIT_FILL.*IDLE" logs/phase_events.jsonl
# Should transition to IDLE, not ERROR
```

#### **Test 3: Event-Konsistenz**

```bash
# Run Bot 1h
python3 main.py --duration 3600

# Check Events
grep "ERROR_OCCURRED" logs/trading.log | grep -i "order_id"
# Should be EMPTY (keine ERROR_OCCURRED bei order_id=None)

grep "BUY_ABORTED" logs/trading.log
# Should exist (für order_id=None, Pre-Flight failures)

# Check Phases
grep "→ ERROR" logs/phase_events.jsonl | grep -v "exception"
# Should be EMPTY (kein ERROR Phase wegen order_id=None)
```

---

## Rollout-Strategie

### Rollout-Plan

#### **Stage 1: Dev-Test** (Tag 1)
- Alle 3 Lücken beheben
- Unit Tests grün
- Manual Smoke Test (3h)

#### **Stage 2: Canary** (Tag 2-3)
- Deploy auf Test-Environment
- 3 Symbole live
- 24h laufen lassen
- Metriken:
  - Ghost Rate < 5%?
  - Event-Konsistenz 100%?
  - Keine ERROR Phase wegen order_id=None?

#### **Stage 3: Rollout** (Tag 4)
- Deploy auf Production
- Monitoring:
  - Ghost Rate Dashboard
  - Event-Log Analysis
  - Phase Transition Sanity

### Rollback-Plan

Falls Probleme auftreten:

```bash
# Rollback zu vorherigem Commit
git log --oneline | head -5
git revert <commit-hash>
git push origin main

# Oder: Feature-Flag deaktivieren
# In config.py:
FSM_PARITY_GAPS_FIXED = False

# In fsm_engine.py:
if getattr(config, 'FSM_PARITY_GAPS_FIXED', False):
    # Neue Logik
else:
    # Alte Logik (Fallback)
```

### Success Metrics

| Metrik | Vorher (geschätzt) | Nachher (Ziel) |
|--------|-------------------|---------------|
| **Ghost Rate** | 10-20% bei Budget-Engpass | <5% |
| **Event ERROR_OCCURRED** | ~5% aller aborts | 0% (nur echte Errors) |
| **Chancenlose Orders** | ~10% der Signale | <1% |
| **Partial Stuck** | Unklar (wartet ewig?) | 0% (10s Cancel) |
| **Timeout Clarity** | Unklar | 30s explizit |

---

## Anhang: Code-Diffs

### Diff 1: can_afford() Integration

```diff
*** a/engine/fsm_engine.py
--- b/engine/fsm_engine.py
@@ -30,6 +30,7 @@
 from services.buy_signals import BuySignalService
 from services.exits import ExitManager
 from services.market_guards import MarketGuards
+from services.market_guards import can_afford  # FSM Parity Fix

 # ...

@@ -490,6 +491,35 @@
         passes_guards, failed_guards = self.market_guards.passes_all_guards(st.symbol, ctx.price)
         if not passes_guards:
             # ...existing guard block handling...
             self._emit_event(st, FSMEvent.GUARDS_BLOCKED, ctx)
             return
+
+        # ========================================================================
+        # FSM PARITY FIX: Budget Guard für min_notional
+        # ========================================================================
+        available_budget = self.portfolio.get_free_usdt()
+        max_trades = getattr(config, 'MAX_TRADES', 10)
+        per_trade = available_budget / max(1, max_trades)
+        quote_budget = min(per_trade, getattr(config, 'POSITION_SIZE_USDT', 10.0))
+
+        if not can_afford(self.exchange, st.symbol, ctx.price, quote_budget):
+            logger.warning(
+                f"[GUARD_BLOCK] {st.symbol} @ {ctx.price:.8f}: "
+                f"INSUFFICIENT BUDGET for min_notional (budget={quote_budget:.2f})"
+            )
+
+            try:
+                self.buy_flow.step(1, "Market Guards", "BLOCKED", "Budget insufficient")
+                duration_ms = (time.time() - self.buy_flow.start_time) * 1000
+                self.buy_flow.end_evaluation("BLOCKED", duration_ms, "Budget insufficient")
+            except Exception:
+                pass
+
+            try:
+                self.jsonl.decision_end(st.symbol, st.decision_id, "budget_insufficient")
+            except Exception:
+                pass
+
+            self._emit_event(st, FSMEvent.GUARDS_BLOCKED, ctx)
+            return
+        # ========================================================================

         # Continue with signal detection...
         has_signal = self.buy_signal_service.has_signal(st.symbol, ctx.price)
```

### Diff 2: Event-Konsistenz

```diff
*** a/engine/fsm_engine.py
--- b/engine/fsm_engine.py
@@ -651,7 +651,7 @@
                 market_info = self.exchange.market(st.symbol)
             except Exception as e:
                 logger.error(f"[BUY_INTENT_ABORTED] {st.symbol} reason=market_info_unavailable")
-                self._emit_event(st, FSMEvent.ORDER_PLACEMENT_FAILED, ctx)
+                self._emit_event(st, FSMEvent.BUY_ABORTED, ctx)
                 return

@@ -867,8 +867,8 @@
                     else:
                         # All retries exhausted
-                        logger.error(f"[WAIT_FILL_ERROR] {st.symbol} order_id is None after retries!")
-                        self._emit_event(st, FSMEvent.ERROR_OCCURRED, ctx)
+                        logger.error(f"[WAIT_FILL_ABORTED] {st.symbol} order_id is None after retries!")
+                        self._emit_event(st, FSMEvent.BUY_ABORTED, ctx)
                         return
```

### Diff 3: wait_fill() Policy (Option B)

```diff
*** a/engine/fsm_engine.py
--- b/engine/fsm_engine.py
@@ -876,10 +876,24 @@
             order = self.exchange.fetch_order(st.order_id, st.symbol)

             # Handle open/partial status (order still active)
             status = order.get("status")
             if status in ("open", "partial"):
-                # Order still pending - optionally track partial fills
+                # FSM PARITY FIX: Partial-Fill Stuck Policy (10s)
+                if status == "partial":
+                    if not hasattr(st, 'partial_fill_first_seen'):
+                        st.partial_fill_first_seen = time.time()
+
+                    stuck_duration = time.time() - st.partial_fill_first_seen
+                    if stuck_duration > 10:
+                        logger.warning(f"[WAIT_FILL] {st.symbol} partial stuck {stuck_duration:.1f}s, canceling")
+                        try:
+                            self.exchange.cancel_order(st.order_id, st.symbol)
+                        except Exception as e:
+                            logger.debug(f"Cancel failed: {e}")
+                        self._emit_event(st, FSMEvent.ORDER_CANCELED, ctx)
+                        return
+
                 if status == "partial" and hasattr(self, 'partial_fill_handler'):
                     try:
                         self.partial_fill_handler.on_update(st.symbol, order)
```

---

## Zusammenfassung

**3 Kritische Lücken identifiziert:**
1. 🔴 can_afford() nicht integriert → Chancenlose Orders
2. 🔴 wait_fill() Policy nicht integriert → Inkonsistente Timeouts
3. 🟡 Event-Inkonsistenz → Falsche Phase-Transitions

**Implementierung: ~3 Stunden**
- Phase 1: Event-Konsistenz (30 Min)
- Phase 2: can_afford() (45 Min)
- Phase 3: wait_fill() (90 Min)
- Phase 4: Verification (30 Min)

**Impact nach Fix:**
- Ghost Rate: 10-20% → <5%
- Timeout Clarity: Unklar → 30s explizit
- Event-Konsistenz: ~95% → 100%
- Chancenlose Orders: ~10% → <1%

**Nächste Schritte:**
1. Review dieses Dokuments
2. Implementierung starten (Phase 1-3)
3. Tests durchführen
4. Canary Deployment
5. Production Rollout

---

**Status:** 📝 BEREIT FÜR IMPLEMENTIERUNG
**Reviewer:** _________
**Approved:** _________
**Date:** _________
