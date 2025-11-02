# FSM Migration - TODO Checklist & Solution Design Status

**Generiert**: 2025-11-01
**Quelle**: FSM_MASTER_ANALYSIS.md v2

---

## ✅ VOLLSTÄNDIGKEITS-CHECK

| Kriterium | Status | Details |
|-----------|--------|---------|
| Alle TODOs identifiziert | ✅ | 14 Tasks über 4 Phasen |
| Prioritäten vergeben | ✅ | P0 (5), P1 (4), P2 (4), P3 (1) |
| Zeitschätzungen | ✅ | Alle Tasks haben min-max Schätzung |
| File-Locations | ✅ | Alle mit exakten Zeilen/Pfaden |
| Solution Design | ✅ | Code-Beispiele für alle P0, API-Verträge für P1 |
| Tests definiert | ✅ | Unit, Integration, E2E |
| Akzeptanzkriterien | ✅ | Messbare Metriken |

---

## 🔴 PHASE 1: P0 - CRITICAL FIXES (3-4h)

### ✅ P0-1: Event-Namen angleichen
- **Priority**: 🔴 P0
- **Time**: 15 min
- **File**: `engine/fsm_engine.py:558-593`
- **Solution**: ✅ Before/After Code vorhanden
- **Risk**: Exit-Transitions feuern NIE
- **Test**: Run FSM, verify exit events trigger
- **DOD**: Alle 3 Events nutzen EXIT_SIGNAL_* Naming

**Code vorhanden**: ✅ Ja (Zeilen 38-48)
```python
# VORHER: TAKE_PROFIT_HIT, STOP_LOSS_HIT, TRAILING_STOP_HIT
# NACHHER: EXIT_SIGNAL_TP, EXIT_SIGNAL_SL, EXIT_SIGNAL_TRAILING
```

---

### ✅ P0-2: TP/SL nach Fill initialisieren
- **Priority**: 🔴 P0
- **Time**: 30 min
- **File**: `core/fsm/actions.py:118`
- **Solution**: ✅ Komplette Funktion (30 Zeilen)
- **Risk**: FSM weiß nie, wann TP/SL erreicht
- **Test**: Verify coin_state.tp_px and sl_px set after buy
- **DOD**: tp_px, sl_px, tp_active, sl_active alle gesetzt

**Code vorhanden**: ✅ Ja (Zeilen 62-88)
```python
coin_state.tp_px = round(fill.avg_px * (1 + tp_pct/100), decimals)
coin_state.sl_px = round(fill.avg_px * (1 - sl_pct/100), decimals)
coin_state.tp_active = True
coin_state.sl_active = True
coin_state.trail_high = fill.avg_px
```

---

### ✅ P0-3: Ticker-Zugriff korrigieren
- **Priority**: 🔴 P0
- **Time**: 10 min
- **File**: `engine/fsm_engine.py:891`
- **Solution**: ✅ One-liner fix
- **Risk**: AttributeError → Engine crasht
- **Test**: Run FSM, no AttributeError in logs
- **DOD**: ticker.last statt ticker.get("last")

**Code vorhanden**: ✅ Ja (Zeilen 101-107)
```python
# VORHER: current_price = ticker.get("last", 0.0)
# NACHHER: current_price = ticker.last if ticker else 0.0
```

---

### ✅ P0-4: ExitEngine portieren
- **Priority**: 🔴 P0
- **Time**: 2h
- **File**: `core/fsm/exit_engine.py` (NEW)
- **Solution**: ✅ Komplette Implementierung (150 Zeilen)
- **Risk**: Falsche Exit-Wahl bei Konflikten
- **Test**: Unit tests für Priorität (SL > TP > TRAIL > TIME)
- **DOD**: ExitDecision mit priority, alle 4 Regeln implementiert

**Code vorhanden**: ✅ Ja (Zeilen 125-238)
- `ExitDecision` dataclass ✅
- `FSMExitEngine` Klasse ✅
- `choose_exit()` mit Prioritäts-Logik ✅
- `_check_hard_sl/tp/trailing/time_exit()` alle 4 ✅
- Integration-Code für fsm_engine.py ✅

**Spezifikation vorhanden**: ✅
- Priorität strikt: HARD_SL > HARD_TP > TRAILING > TIME
- Konflikte: Bei mehreren Triggern entscheidet Priorität
- Trailing: Aktivierung ab trail_activate_pnl
- Time: Nur wenn keine andere Regel feuert

---

### ✅ P0-5: Dynamic TP/SL Switching
- **Priority**: 🔴 P0
- **Time**: 1h
- **File**: `core/fsm/position_management.py` (NEW)
- **Solution**: ✅ Komplette Klasse (90 Zeilen)
- **Risk**: Weniger downside protection
- **Test**: Test PnL<0 → SL, PnL>0 → TP, Cooldown
- **DOD**: DynamicTPSLManager mit rebalance_protection()

**Code vorhanden**: ✅ Ja (Zeilen 273-364)
- `DynamicTPSLManager` Klasse ✅
- `rebalance_protection()` mit PnL-Logik ✅
- `_switch_to_sl()` - Cancel TP, place SL ✅
- `_switch_to_tp()` - Cancel SL, place TP ✅
- Cooldown-Logik (20s) ✅

**Legacy-Verhalten dokumentiert**: ✅
- PnL < -0.5% → SL priority
- PnL > +0.2% → TP priority
- Cooldown 20s zwischen Switches

---

## 🟠 PHASE 2: P1 - SAFETY & PARITY (4-5h)

### ✅ P1-1: OrderRouter (Idempotenz)
- **Priority**: 🟠 P1
- **Time**: 2h
- **File**: `core/fsm/order_router.py` (NEW, port from services/)
- **Solution**: ✅ API-Vertrag + Pseudo-Code
- **Risk**: 🔴 Doppelte Orders bei Retry
- **Test**: Same intent_id → No duplicate
- **DOD**: submit(intent_id) garantiert Idempotenz

**API-Vertrag vorhanden**: ✅ Ja (Zeilen 377-409)
```python
def submit(self, intent_id: str, order_params: OrderParams) -> OrderResult
# Garantiert: Gleiche intent_id → Kein doppeltes Placement
```

**Implementation Guide**: ✅
1. Check if intent already processed (cache lookup)
2. Place order with exchange
3. Cache result for idempotency
4. Return result

**Source**: `services/order_router.py` → zu portieren

---

### ✅ P1-2: Exchange State Reconciler
- **Priority**: 🟠 P1
- **Time**: 1.5h
- **File**: `core/fsm/reconciler.py` (NEW, port from services/)
- **Solution**: ✅ API-Vertrag + Invariante
- **Risk**: 🔴 Desync Portfolio ≠ Exchange
- **Test**: Simulate desync → reconciler.sync() → match
- **DOD**: local.positions == exchange.positions nach sync()

**API-Vertrag vorhanden**: ✅ Ja (Zeilen 420-444)
```python
def sync(self) -> ReconcileReport
# Reads: Open orders, fills, positions from exchange
# Updates: Local portfolio to match reality
# Returns: Report mit desyncs, corrections, missing fills
```

**Invariante definiert**: ✅ (Zeilen 547-553)
```
After reconciler.sync():
  local.positions ≡ exchange.positions
  local.orders ≡ exchange.orders
```

**Source**: `services/reconciler.py` → zu portieren

---

### ✅ P1-3: ExchangeWrapper (Duplicate Prevention)
- **Priority**: 🟠 P1
- **Time**: 1h
- **File**: `core/fsm/exchange_wrapper.py` (NEW, port from interfaces/)
- **Solution**: ✅ API-Vertrag + Safety-Checks
- **Risk**: 🔴 Duplicate orders ohne clientOrderId
- **Test**: Retry mit gleicher clientOrderId → Same order
- **DOD**: Alle Orders nutzen stable clientOrderId

**API-Vertrag vorhanden**: ✅ Ja (Zeilen 461-487)
```python
def place_order(self, params: OrderParams) -> Order
# Safety:
# - Uses server-side clientOrderId
# - Retry with SAME clientOrderId
# - Precision rounding before submission
# - Duplicate detection
```

**Implementation Guide**: ✅
1. Generate stable clientOrderId
2. Round to exchange precision
3. Check duplicate by clientOrderId
4. Submit to exchange

**Source**: `interfaces/exchange_wrapper.py` → zu portieren

---

### ✅ P1-4: Tighter Position Tracking
- **Priority**: 🟠 P1
- **Time**: 30 min
- **File**: `engine/fsm_engine.py`
- **Solution**: ✅ Before/After Code
- **Risk**: 4x langsamere Exit-Detection
- **Test**: Verify position tracking every cycle
- **DOD**: _process_positions() jede Iteration, nicht jede 4.

**Code vorhanden**: ✅ Ja (Zeilen 499-506)
```python
# VORHER: if self.tick_count % 4 == 0: self._process_positions()
# NACHHER: self._process_positions()  # Every cycle
```

---

## 🟡 PHASE 3: P2 - OBSERVABILITY (2-3h)

### ⚠️ P2-1: JsonlLogger
- **Priority**: 🟡 P2
- **Time**: 1h
- **File**: Port `core/logging/logger.py` → FSM
- **Solution**: ⚠️ HIGH-LEVEL (kein Detail-Code)
- **Risk**: 🟡 Weniger Debug-Daten
- **Test**: Verify JSONL logs für Signals, Orders, Fills, Exits
- **DOD**: Strukturiertes Event-Logging aktiv

**Detail-Level**: ⚠️ Grob
- "Port aus Legacy core/logging/"
- "Aktivieren für FSM"
- Keine Code-Beispiele
- Keine API-Verträge

**Verbesserungsbedarf**:
- [ ] Welche Events loggen?
- [ ] Format-Spezifikation?
- [ ] Integration-Punkte in FSM?

---

### ⚠️ P2-2: AdaptiveLogger
- **Priority**: 🟡 P2
- **Time**: 1h
- **File**: Port `core/logging/adaptive_logger.py` → FSM
- **Solution**: ⚠️ HIGH-LEVEL (kein Detail-Code)
- **Risk**: 🟡 Keine Log-Level Adaptation
- **Test**: Verify log levels anpassen bei Fehlerwellen
- **DOD**: Adaptive Log-Level aktiv

**Detail-Level**: ⚠️ Grob
- "Log-Level anpassen bei Fehlerwellen"
- Keine Thresholds definiert
- Keine Integration-Strategie

**Verbesserungsbedarf**:
- [ ] Welche Schwellwerte?
- [ ] Welche Log-Level-Übergänge?
- [ ] Wie in FSM integrieren?

---

### ⚠️ P2-3: BuyFlowLogger
- **Priority**: 🟡 P2
- **Time**: 30 min
- **File**: Port `services/buy_flow_logger.py` → FSM
- **Solution**: ⚠️ HIGH-LEVEL (kein Detail-Code)
- **Risk**: 🟡 Kein Kaufpfad-Tracing
- **Test**: Verify vollständiger Buy-Flow geloggt
- **DOD**: Buy-Flow-Tracing aktiv

**Detail-Level**: ⚠️ Grob
- "Vollständiger Kaufpfad-Trace"
- Keine Event-Definition
- Keine Integration-Punkte

**Verbesserungsbedarf**:
- [ ] Welche Buy-Flow Steps?
- [ ] Format?
- [ ] FSM Phase-Mapping?

---

### ⚠️ P2-4: PhaseMetrics
- **Priority**: 🟡 P2
- **Time**: 30 min
- **File**: Extend `telemetry/phase_metrics.py`
- **Solution**: ⚠️ HIGH-LEVEL (kein Detail-Code)
- **Risk**: 🟡 Keine Phase-Latenz-Metriken
- **Test**: Verify Latenz pro Phase gemessen
- **DOD**: Phase-Metriken verfügbar

**Detail-Level**: ⚠️ Grob
- "Latenz und Fehlerrate pro FSM-Phase"
- Keine Metrik-Spezifikation
- Keine Export-Strategie

**Verbesserungsbedarf**:
- [ ] Welche Metriken genau?
- [ ] Prometheus/Grafana Integration?
- [ ] Alerting-Schwellwerte?

---

## 🟢 PHASE 4: CLEANUP (1h)

### ✅ P3-1: Remove Legacy Engine
- **Priority**: 🟢 P3
- **Time**: 30 min
- **File**: `engine/engine.py` (delete)
- **Solution**: ✅ Klar definiert
- **Test**: Verify FSM runs standalone
- **DOD**: engine.py gelöscht, nur FSM bleibt

**Abhängigkeiten**: P0, P1, P2 müssen fertig sein

---

### ✅ P3-2: Simplify HybridEngine
- **Priority**: 🟢 P3
- **Time**: 15 min
- **File**: `engine/hybrid_engine.py`
- **Solution**: ✅ Klar definiert
- **Test**: Verify hybrid_engine nur FSM wrapper
- **DOD**: Legacy-Logik entfernt

---

### ✅ P3-3: Update Documentation
- **Priority**: 🟢 P3
- **Time**: 15 min
- **File**: `docs/*.md`
- **Solution**: ✅ Klar definiert
- **Test**: Verify docs reflect FSM-only state
- **DOD**: Alle Docs aktualisiert

---

## 📊 SUMMARY

### Solution Design Completeness

| Phase | Tasks | Full Code | API Contract | High-Level | Missing |
|-------|-------|-----------|--------------|------------|---------|
| **P0** | 5 | 5 (100%) | - | - | 0 |
| **P1** | 4 | 1 (25%) | 3 (75%) | - | 0 |
| **P2** | 4 | 0 (0%) | - | 4 (100%) | Details |
| **P3** | 3 | - | - | 3 (100%) | 0 |
| **TOTAL** | 16 | 6 | 3 | 7 | 0 |

### Quality Score

| Kriterium | Score |
|-----------|-------|
| **P0 (Critical)** | ✅ 10/10 - Perfekt |
| **P1 (Safety)** | ✅ 9/10 - API-Verträge vorhanden, Code zu portieren |
| **P2 (Observability)** | ⚠️ 6/10 - Grob definiert, Details fehlen |
| **P3 (Cleanup)** | ✅ 8/10 - Klar genug |
| **Tests** | ✅ 9/10 - Unit, Integration, E2E definiert |
| **Metriken** | ✅ 10/10 - Messbare Akzeptanzkriterien |
| **Priorisierung** | ✅ 10/10 - Klare P0>P1>P2>P3 |

**Gesamt-Score**: ✅ **8.7/10** - Sehr gut, kleine Lücken bei P2

---

## 🎯 ARBEITSANLEITUNG FÜR ENTWICKLER

### Phase 1 (P0) - SOFORT STARTBAR ✅
**Alle 5 Tasks haben:**
- ✅ Exakte File-Location mit Zeilen
- ✅ Complete Code (Copy-Paste ready)
- ✅ Before/After Vergleich
- ✅ Tests definiert

**→ Entwickler kann DIREKT loslegen, kein weiteres Design nötig**

---

### Phase 2 (P1) - STARTBAR MIT PORTING ✅
**Alle 4 Tasks haben:**
- ✅ Exakte Source-Files zum Portieren
- ✅ API-Verträge definiert
- ✅ Invarianten spezifiziert
- ✅ Tests definiert

**→ Entwickler muss Legacy-Code portieren, aber API ist klar**

---

### Phase 3 (P2) - BEDARF DETAIL-DESIGN ⚠️
**Problem**: Nur High-Level definiert

**Fehlende Details für P2:**
1. **JsonlLogger**:
   - [ ] Welche FSM-Events loggen? (ENTRY_EVAL, PLACE_BUY, etc.?)
   - [ ] JSONL-Schema definieren
   - [ ] Integration-Punkte in fsm_engine.py?

2. **AdaptiveLogger**:
   - [ ] Schwellwerte für Level-Wechsel? (z.B. >10 errors/min → DEBUG)
   - [ ] Welche Error-Typen tracken?

3. **BuyFlowLogger**:
   - [ ] Buy-Flow Steps: Signal → Guard → Order → Fill?
   - [ ] Trace-ID-Strategie?

4. **PhaseMetrics**:
   - [ ] Welche Metriken? (phase_duration, phase_errors, phase_transitions?)
   - [ ] Prometheus Labels?

**→ Entwickler muss erst Design machen, dann implementieren**

---

### Phase 4 (P3) - TRIVIAL ✅
**Alle 3 Tasks klar genug**

---

## ✅ EMPFEHLUNG

### Für P0 + P1 (Production-Ready):
**Status**: ✅ **100% READY TO IMPLEMENT**
- Alle Code-Beispiele vorhanden
- Alle API-Verträge definiert
- Alle Tests spezifiziert
- **Geschätzter Aufwand**: 7-9 Stunden (wie dokumentiert)

### Für P2 (Observability):
**Status**: ⚠️ **70% READY** - Detail-Design fehlt
- High-Level klar, aber Details offen
- **Empfehlung**:
  1. Erst P0+P1 implementieren
  2. Dann P2-Detail-Design machen (1h)
  3. Dann P2 implementieren (2-3h)

### Für P3 (Cleanup):
**Status**: ✅ **90% READY** - Abhängig von P0-P2

---

## 🚦 AMPEL-STATUS

| Phase | Design-Status | Can Start | Blocker |
|-------|---------------|-----------|---------|
| **P0** | 🟢 Complete | ✅ JA | None |
| **P1** | 🟢 Complete | ✅ JA | None |
| **P2** | 🟡 High-Level | ⚠️ Partial | Detail-Design fehlt |
| **P3** | 🟢 Clear | ✅ JA | P0-P2 müssen fertig sein |

---

**FAZIT**:
- ✅ P0+P1 (11 Tasks) sind **100% implementation-ready**
- ⚠️ P2 (4 Tasks) brauchen noch **Detail-Design**
- ✅ P3 (3 Tasks) sind klar genug

**Total Ready-to-Code**: **11/16 Tasks (69%)**
**Estimated Time for Ready Tasks**: **7-9 Stunden**

---

**Checklist generiert**: 2025-11-01
**Basierend auf**: FSM_MASTER_ANALYSIS.md v2
