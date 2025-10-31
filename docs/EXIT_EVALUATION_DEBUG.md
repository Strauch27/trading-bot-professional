# Exit Evaluation System - Debug Analysis

**Datum:** 2025-10-26
**Problem:** Exit-Evaluationen laufen nicht, obwohl 3 Positionen gehalten werden
**Status:** Debug-Logging hinzugefügt, bereit für Test

---

## Problemzusammenfassung

### Aktuelle Situation
- **Bot hält 3 Positionen:**
  - KGEN/USDT: 89.28 @ $0.2805
  - 4/USDT: 208.54 @ $0.12019
  - FTN/USDT: 12.91 @ $1.9355

- **Keine Exit-Evaluationen in den Logs:**
  - ❌ Keine `POSITION_MGMT_START` Events
  - ❌ Keine `position_updated` Events
  - ❌ Keine `sell_trigger_eval` Events
  - ❌ Keine Exit-Signale werden generiert

### User-Frage
User fragte: "Warum wird RECALL/USDT mit +1.62% nicht verkauft?"

**Antwort:** RECALL/USDT ist NICHT in `held_assets.json` - der Bot hält diese Position nicht. Der User hat vermutlich ein anderes Interface oder eine andere Exchange angeschaut.

---

## System-Architektur: Exit-Evaluation

### 1. Hauptschleife (engine/engine.py:850-862)
```python
# Position Management (alle 2 Sekunden)
if cycle_start - self.last_position_check > 2.0:
    self.position_manager.manage_positions()  # <-- Sollte alle 2s laufen
    self.last_position_check = cycle_start
```

### 2. Position Manager (engine/position_manager.py:40-85)
```python
def manage_positions(self):
    for symbol, data in self.engine.positions.items():
        # 1. Update trailing stops
        # 2. Restore exit protections
        # 3. Evaluate exit conditions  <-- HIER SOLLTEN EXITS GEPRÜFT WERDEN
        if self.engine.config.enable_auto_exits:
            self.evaluate_position_exits(symbol, data, current_price)
        # 4. Update unrealized PnL
```

### 3. Exit Evaluation (engine/position_manager.py:106-348)
```python
def evaluate_position_exits(self, symbol, data, current_price):
    # Prüft:
    # - Take Profit (TP > 1.005 = +0.5%)
    # - Stop Loss (SL < 0.990 = -1.0%)
    # - ATR-basierte Exits
    # - Trailing Stops
    # - TTL (Time-to-Live) Emergency Exits
```

### 4. Konfiguration
```python
# engine/engine_config.py:82
enable_auto_exits=True  # ✅ Hardcoded aktiviert
```

---

## Hinzugefügtes Debug-Logging

### A. Hauptschleife (engine.py)

**NEU:** Logging bei jedem Zyklus:
```python
[ENGINE] Position management triggered (time_since_last=X.Xs, positions=3)
[ENGINE] Position management completed
[ENGINE] Position management skipped (time_since_last=X.Xs < 2.0s)
```

**Events:**
- `ENGINE_POSITION_CHECK_TRIGGER` - Position Management wurde aufgerufen
- `ENGINE_POSITION_CHECK_DONE` - Position Management abgeschlossen
- `ENGINE_POSITION_CHECK_SKIP` - Übersprungen (< 2s seit letztem Check)

### B. Position Manager (position_manager.py)

**NEU:** Detailliertes Logging für jeden Schritt:

1. **Start:**
   ```
   [POSITION_MGMT] Starting position management - positions count: 3
   ```
   Event: `POSITION_MGMT_START`

2. **Pro Symbol:**
   ```
   [POSITION_MGMT] Processing KGEN/USDT
   [POSITION_MGMT] KGEN/USDT current_price=0.2805
   [POSITION_MGMT] Updating trailing stops for KGEN/USDT
   [POSITION_MGMT] Restoring exit protections for KGEN/USDT
   [POSITION_MGMT] Evaluating exits for KGEN/USDT (enable_auto_exits=True)
   [POSITION_MGMT] Updating PnL for KGEN/USDT
   ```

3. **Fehlerfall:**
   ```
   [POSITION_MGMT] No price available for KGEN/USDT
   [POSITION_MGMT] Exit evaluation SKIPPED (enable_auto_exits=False)
   ```

**Events:**
- `POSITION_MGMT_START` - Beginn des Position Management
- `POSITION_MGMT_SYMBOL` - Verarbeitung eines Symbols
- `POSITION_MGMT_PRICE` - Preis verfügbar
- `POSITION_MGMT_NO_PRICE` - Kein Preis verfügbar
- `POSITION_MGMT_TRAILING` - Trailing Stops Update
- `POSITION_MGMT_PROTECTIONS` - Exit Protections Restore
- `POSITION_MGMT_EXIT_EVAL_START` - Exit Evaluation gestartet
- `POSITION_MGMT_EXIT_EVAL_DISABLED` - Exit Evaluation deaktiviert
- `POSITION_MGMT_PNL` - PnL Update

---

## Test-Anleitung

### 1. Bot mit Debug-Logging starten
```bash
cd "/Users/stenrauch/Downloads/Trading Bot Professional Git/trading-bot-professional"
./venv/bin/python3 main.py
```

### 2. Logs überwachen
```bash
# In Echtzeit die Position Management Events sehen:
tail -f sessions/session_*/logs/system_*.jsonl | grep -E "POSITION_MGMT|ENGINE_POSITION"
```

### 3. Nach 1 Minute: Logs analysieren
```bash
# Alle Position Management Events zählen:
grep "POSITION_MGMT_START" sessions/session_*/logs/system_*.jsonl | wc -l

# Erwartung: Mindestens 30 Events (alle 2s für 1 Minute)
# Wenn 0: Position Management läuft NICHT
```

---

## Diagnose-Szenarien

### Szenario 1: Keine ENGINE_POSITION_CHECK_TRIGGER Events
**Symptom:** Kein `[ENGINE] Position management triggered` in den Logs

**Ursache:** Die Hauptschleife läuft nicht oder ist blockiert

**Lösungen:**
- Main Loop crashed/stopped
- Thread Deadlock
- Unbehandelte Exception in vorherigem Schleifenteil

### Szenario 2: ENGINE_POSITION_CHECK_TRIGGER aber keine POSITION_MGMT_START
**Symptom:** `ENGINE_POSITION_CHECK_TRIGGER` vorhanden, aber kein `POSITION_MGMT_START`

**Ursache:** `manage_positions()` wird aufgerufen, aber die Funktion crasht sofort

**Lösungen:**
- Exception beim Lock-Erwerb (`self.engine._lock`)
- Exception beim Zugriff auf `self.engine.positions`
- Check Exception-Logs mit `exc_info=True`

### Szenario 3: POSITION_MGMT_START aber keine POSITION_MGMT_SYMBOL
**Symptom:** `POSITION_MGMT_START` mit `positions count: 0`

**Ursache:** `self.engine.positions` ist leer (obwohl `held_assets.json` Positionen hat)

**Lösungen:**
- Positions werden nicht korrekt in Engine geladen
- Synchronisationsproblem zwischen `held_assets.json` und `engine.positions`

### Szenario 4: POSITION_MGMT_SYMBOL aber POSITION_MGMT_NO_PRICE
**Symptom:** Symbol wird verarbeitet, aber `No price available`

**Ursache:** `self.engine.get_current_price(symbol)` gibt `None` zurück

**Lösungen:**
- Market Data Provider liefert keine Preise
- Symbol nicht in Market Data Cache
- MD_POLL läuft nicht korrekt

### Szenario 5: POSITION_MGMT_EXIT_EVAL_DISABLED
**Symptom:** `enable_auto_exits=False` obwohl es hardcoded `True` sein sollte

**Ursache:** Konfiguration wurde zur Laufzeit überschrieben

**Lösungen:**
- Check `self.engine.config.enable_auto_exits` Wert
- Suche nach `set_config_override` Aufrufen

---

## Erwartete Log-Ausgabe (Normal)

```
[ENGINE] Position management triggered (time_since_last=2.1s, positions=3)
[POSITION_MGMT] Starting position management - positions count: 3
[POSITION_MGMT] Processing KGEN/USDT
[POSITION_MGMT] KGEN/USDT current_price=0.2805
[POSITION_MGMT] Updating trailing stops for KGEN/USDT
[POSITION_MGMT] Restoring exit protections for KGEN/USDT
[POSITION_MGMT] Evaluating exits for KGEN/USDT (enable_auto_exits=True)
[POSITION_MGMT] Updating PnL for KGEN/USDT
[POSITION_MGMT] Processing 4/USDT
[POSITION_MGMT] 4/USDT current_price=0.12019
[POSITION_MGMT] Updating trailing stops for 4/USDT
[POSITION_MGMT] Restoring exit protections for 4/USDT
[POSITION_MGMT] Evaluating exits for 4/USDT (enable_auto_exits=True)
[POSITION_MGMT] Updating PnL for 4/USDT
[POSITION_MGMT] Processing FTN/USDT
[POSITION_MGMT] FTN/USDT current_price=1.9355
[POSITION_MGMT] Updating trailing stops for FTN/USDT
[POSITION_MGMT] Restoring exit protections for FTN/USDT
[POSITION_MGMT] Evaluating exits for FTN/USDT (enable_auto_exits=True)
[POSITION_MGMT] Updating PnL for FTN/USDT
[ENGINE] Position management completed
```

**Wiederholung:** Alle 2 Sekunden

---

## Nächste Schritte

1. **Bot starten** mit Debug-Logging
2. **1 Minute warten** und Logs sammeln
3. **Logs analysieren** nach oben genannten Szenarien
4. **Root Cause identifizieren** basierend auf fehlendem Event
5. **Fix implementieren** je nach Ursache

---

## Dateien mit Debug-Logging

- `engine/engine.py` (Zeilen 850-862): Hauptschleife Position Check
- `engine/position_manager.py` (Zeilen 40-85): Position Management Funktion

**Commit-Message:**
```
debug: Add comprehensive logging for exit evaluation system

- Add ENGINE_POSITION_CHECK_TRIGGER/SKIP/DONE events in main loop
- Add POSITION_MGMT_* events for all position management steps
- Add symbol-level logging for price, trailing, protections, exits, PnL
- Add explicit enable_auto_exits flag logging
- Add exception logging with exc_info=True for full traceback

This will help diagnose why exit evaluations are not running
despite 3 positions being held (KGEN/USDT, 4/USDT, FTN/USDT).

Files modified:
- engine/engine.py: Main loop position management trigger
- engine/position_manager.py: Position management execution
```
