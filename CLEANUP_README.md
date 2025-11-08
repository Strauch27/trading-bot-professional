# Trading Bot - Cleanup Scripte

## Übersicht

Der Bot hat **6 separate Cleanup-Scripte** für verschiedene Szenarien:

```
🟢 SICHER (Soft Cleanups)
├── cleanup_logs.py                  # Nur Logs
├── cleanup_logs_and_cache.py       # Logs + Cache
├── cleanup_stuck_orders.py         # Nur Stuck Orders
└── cleanup_soft_complete.py        # Logs + Cache + Orders

🔴 DESTRUKTIV (Hard Resets)
├── cleanup_full_reset.py           # Alles löschen (kein Portfolio!)
└── cleanup_full_reset_with_portfolio.py  # Alles + Portfolio verkaufen!
```

---

## 🟢 Sichere Cleanups (Soft)

### 1. `cleanup_logs.py`
**Zweck:** Nur Log-Dateien löschen

**Löscht:**
- ✗ Alle Logs (.log, .jsonl)

**Behält:**
- ✓ State, Sessions, Anchors, Cache

**Verwendung:**
```bash
python3 cleanup_logs.py
```

**Wann:**
- Tägliche Log-Rotation
- Logs sind zu groß geworden
- Festplatte fast voll

---

### 2. `cleanup_logs_and_cache.py`
**Zweck:** Logs + Python Cache löschen

**Löscht:**
- ✗ Alle Logs
- ✗ Python Cache (`__pycache__`, `*.pyc`)

**Behält:**
- ✓ State, Sessions, Anchors

**Verwendung:**
```bash
python3 cleanup_logs_and_cache.py
```

**Wann:**
- Wöchentliche Wartung
- Performance-Probleme
- Nach Python-Updates

---

### 3. `cleanup_stuck_orders.py`
**Zweck:** Nur Stuck Orders bereinigen

**Löscht:**
- ✗ Stuck Orders aus `open_buy_orders.json`

**Behält:**
- ✓ Logs, State, Sessions, Anchors, Cache

**Verwendung:**
```bash
python3 cleanup_stuck_orders.py
```

**Wann:**
- Budget ist blockiert
- "Duplicate blocked" Fehler
- Nach Bot-Crash

---

### 4. `cleanup_soft_complete.py`
**Zweck:** Kompletter Soft-Cleanup (Logs + Cache + Orders)

**Löscht:**
- ✗ Alle Logs
- ✗ Python Cache
- ✗ Stuck Orders

**Behält:**
- ✓ State (FSM Snapshots, Ledger)
- ✓ Sessions (Historie)
- ✓ Anchors & Drop Windows

**Verwendung:**
```bash
python3 cleanup_soft_complete.py
```

**Wann:**
- Wöchentliche Komplett-Wartung
- Vor Bot-Neustart
- Nach Problemen

---

## 🔴 Destruktive Resets (Hard)

### 5. `cleanup_full_reset.py`
⚠️ **DESTRUKTIV!** Löscht ALLE Bot-Daten!

**Löscht:**
- ✗ Logs
- ✗ Sessions (FSM Snapshots)
- ✗ State (Ledger DB, etc.)
- ✗ Anchors & Drop Windows
- ✗ Python Cache
- ✗ Stuck Orders

**Behält:**
- ✓ Portfolio auf Exchange (verkauft NICHTS!)

**Verwendung:**
```bash
python3 cleanup_full_reset.py
```

**Prompts:**
1. Eingabe: `reset`
2. Eingabe: `DELETE ALL`

**Wann:**
- Kompletter Neustart
- State ist korrupt
- Nach Major-Code-Updates
- Test-Setup zurücksetzen

---

### 6. `cleanup_full_reset_with_portfolio.py`
⚠️ **⚠️ MAXIMUM DESTRUKTIV! ⚠️ ⚠️**

**Löscht:**
- ✗ **ALLES** (wie cleanup_full_reset.py)
- ✗ **Verkauft ALLE ASSETS** auf Exchange!

**Behält:**
- Nur USDT übrig

**Verwendung:**
```bash
python3 cleanup_full_reset_with_portfolio.py
```

**Prompts:**
1. Eingabe: `liquidate`
2. Eingabe: `SELL ALL ASSETS`

**Wann:**
- Kompletter Neustart von Null
- Wechsel zu anderem Trading-Setup
- Ende des Bot-Betriebs

**⚠️ WARNUNG:** Verkauft wirklich ALLE Coins! Nicht rückgängig!

---

## Entscheidungshilfe

```
┌─ Problem: Logs zu groß
│  └─ cleanup_logs.py

┌─ Problem: Performance
│  └─ cleanup_logs_and_cache.py

┌─ Problem: Budget blockiert / Stuck orders
│  └─ cleanup_stuck_orders.py

┌─ Problem: Mehrere Probleme gleichzeitig
│  └─ cleanup_soft_complete.py

┌─ Problem: State ist korrupt / Neustart nötig
│  └─ cleanup_full_reset.py

┌─ Problem: Will komplett von vorne starten
│  └─ cleanup_full_reset_with_portfolio.py
```

---

## Workflow-Empfehlungen

### Tägliche Routine:
```bash
python3 cleanup_logs.py
```

### Wöchentliche Wartung:
```bash
python3 cleanup_soft_complete.py
```

### Nach Bot-Crash:
```bash
# 1. Stuck Orders clearen
python3 cleanup_stuck_orders.py

# 2. Bot neu starten
python3 main.py
```

### Bei State-Korruption:
```bash
# 1. Erst versuchen: Soft cleanup
python3 cleanup_soft_complete.py

# 2. Wenn Problem bleibt: Full reset
python3 cleanup_full_reset.py

# 3. Bot neu starten
python3 main.py
```

### Kompletter Neustart:
```bash
# Ohne Portfolio zu verkaufen
python3 cleanup_full_reset.py

# Mit Portfolio-Liquidation (GEFÄHRLICH!)
python3 cleanup_full_reset_with_portfolio.py
```

---

## Sicherheits-Features

### Soft Cleanups:
- ✓ Einfache yes/no Bestätigung
- ✓ Kein Datenverlust von State
- ✓ Schnell rückgängig machbar

### Hard Resets:
- ⚠️ Doppelte Bestätigung erforderlich
- ⚠️ Spezifische Prompts (`DELETE ALL`, `SELL ALL ASSETS`)
- ⚠️ Nicht rückgängig machbar!

---

## After-Cleanup Checklist

Nach jedem Cleanup:

- [ ] Bot neu starten: `python3 main.py`
- [ ] Prüfe Budget reconciliation im Log
- [ ] Prüfe dass keine Errors auftreten
- [ ] Warte ~5min bis Anchors neu erstellt sind

Nach Full Reset:
- [ ] Budget wird automatisch reconciled
- [ ] Anchors werden automatisch neu erstellt
- [ ] State wird frisch aufgebaut
- [ ] Kein manueller Eingriff nötig!

---

## FAQ

**Q: Verliere ich Positionen bei Soft Cleanup?**
A: Nein! Soft Cleanups behalten den kompletten State.

**Q: Was ist der Unterschied zwischen full_reset und full_reset_with_portfolio?**
A:
- `full_reset`: Löscht nur Bot-Daten, Portfolio bleibt
- `full_reset_with_portfolio`: Löscht Daten UND verkauft alle Assets!

**Q: Kann ich full_reset rückgängig machen?**
A: Nein! Daten sind unwiederbringlich gelöscht.

**Q: Welches Script für "Budget stuck"?**
A: `cleanup_stuck_orders.py` - schnell und sicher!

**Q: Muss der Bot gestoppt sein?**
A: Ja, **immer** Bot stoppen vor Cleanup!

**Q: Was nach Cleanup?**
A: Bot neu starten mit `python3 main.py`

---

## Alte Scripte (deprecated)

Diese Scripte können gelöscht werden:

- `tools/clean_bot.py` → ersetzt durch `cleanup_full_reset.py`
- `tools/clean_all.py` → ersetzt durch `cleanup_full_reset.py`
- `clear_anchors.py` → Teil von `cleanup_full_reset.py`
- `cleanup_soft.py` → ersetzt durch neue Scripte
- `cleanup_hard.py` → ersetzt durch neue Scripte

---

## Support

Bei Problemen:
1. Prüfe Logs vor dem Cleanup!
2. Versuche erst Soft-Cleanup
3. Nur wenn nötig: Hard-Reset
4. Check Github Issues

**Entwickler:** Verwende `-v` Flags nicht mehr nötig, alle Scripte sind bereits verbose.
