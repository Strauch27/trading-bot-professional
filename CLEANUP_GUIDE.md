# Trading Bot - Cleanup Guide

## Overview

Der Bot hat **zwei abgestufte Cleanup-Scripte** für verschiedene Szenarien:

| Script | Zweck | Löscht | Behält | Sicherheit |
|--------|-------|--------|--------|------------|
| **cleanup_soft.py** | Leichter Cleanup | Logs, Cache | State, Sessions, Anchors | ✅ Sicher |
| **cleanup_hard.py** | Vollständiger Reset | ALLES | Nichts | ⚠️ DESTRUKTIV |

---

## 🟢 cleanup_soft.py - Leichter Cleanup

**Verwendung:** Für normale Betriebsunterbrechungen, Log-Cleanup, Performance.

### Was wird gelöscht:
- ✗ Logs (alle .log, .jsonl Dateien)
- ✗ Python Cache (`__pycache__`, `*.pyc`)
- ✗ Stuck Orders (optional mit `--orders`)

### Was bleibt erhalten:
- ✅ State (FSM Snapshots, Ledger DB)
- ✅ Sessions (historische Daten)
- ✅ Anchors & Drop Windows (Markt-Kontext)

### Beispiele:

```bash
# Interactive mode (empfohlen)
python3 cleanup_soft.py

# Auto-confirm (für Scripts)
python3 cleanup_soft.py -y

# Mit Stuck-Order-Cleanup
python3 cleanup_soft.py --orders

# Auto + Orders
python3 cleanup_soft.py -y --orders

# Verbose output
python3 cleanup_soft.py -v
```

### Wann verwenden:
- ✓ Logs sind zu groß geworden
- ✓ Performance-Probleme durch Cache
- ✓ Bot neu starten ohne State zu verlieren
- ✓ Stuck orders bereinigen (mit `--orders`)

---

## 🔴 cleanup_hard.py - Vollständiger Reset

**Verwendung:** Für kompletten Neustart, nach größeren Code-Änderungen, bei State-Korruption.

⚠️ **WARNUNG:** Dies ist DESTRUKTIV und löscht ALLE Daten!

### Was wird gelöscht:
- ✗ Logs (alle Log-Dateien)
- ✗ Python Cache
- ✗ Sessions (FSM Snapshots, historische Daten)
- ✗ State (Ledger DB, Idempotency DB)
- ✗ Anchors & Drop Windows (komplett)
- ✗ Stuck Orders
- ✗ Portfolio (optional mit `--portfolio-reset`)

### Beispiele:

```bash
# Interactive mode (EMPFOHLEN! Zeigt Warnung)
python3 cleanup_hard.py

# Dry-run (zeigt nur was gelöscht würde)
python3 cleanup_hard.py --dry-run

# Auto-confirm (GEFÄHRLICH! Überspringt ALLE Prompts!)
python3 cleanup_hard.py -y

# Mit Portfolio-Reset (verkauft ALLE Assets!)
python3 cleanup_hard.py --portfolio-reset

# Verbose output
python3 cleanup_hard.py -v
```

### Wann verwenden:
- ✓ Komplett frischer Start nötig
- ✓ State ist korrupt oder inkonsistent
- ✓ Nach größeren Code-Änderungen
- ✓ Vor Major-Version-Upgrade
- ✓ Test-Setup zurücksetzen

### Sicherheits-Prompts:
1. **File Cleanup:** Eingabe von `DELETE ALL` erforderlich
2. **Portfolio Reset:** Eingabe von `SELL ALL` erforderlich

Diese doppelte Bestätigung verhindert versehentliche Datenverluste!

---

## Vergleich mit alten Scripte

### Ersetzt folgende alte Scripte:

| Alt | Neu | Notizen |
|-----|-----|---------|
| `tools/clean_bot.py` | `cleanup_hard.py` | Mehr Features, Portfolio-Reset |
| `tools/clean_all.py` | `cleanup_hard.py -y` | Gleiche Funktionalität |
| `clear_anchors.py` | `cleanup_hard.py` (Teil davon) | Hard reset inkludiert Anchors |
| `cleanup_stuck_orders.py` | `cleanup_soft.py --orders` | Soft cleanup mit Orders-Option |

### Behalten (spezielle Zwecke):
- `tools/cleanup_debug_safe.py` - Spezifisch für Debug-Code-Cleanup
- `clean.sh` - Environment/venv Cleanup
- `trading/portfolio_reset.py` - Python-Module (nicht direkt ausführbar)

---

## Workflow-Empfehlungen

### Tägliche Verwendung:
```bash
# Logs clearen, State behalten
python3 cleanup_soft.py -y
```

### Wöchentliche Wartung:
```bash
# Logs + Stuck Orders
python3 cleanup_soft.py -y --orders
```

### Nach Code-Updates:
```bash
# Dry-run check
python3 cleanup_hard.py --dry-run

# Wenn OK, komplett resetten
python3 cleanup_hard.py
```

### Bei Problemen:
```bash
# 1. Versuche erst Soft-Cleanup
python3 cleanup_soft.py -y --orders

# 2. Wenn Problem bleibt, Hard-Reset
python3 cleanup_hard.py

# 3. Als letztes Mittel: Portfolio-Reset
python3 cleanup_hard.py --portfolio-reset
```

---

## Recovery nach Hard Reset

Nach einem Hard Reset:

1. **Budget wird automatisch reconciled** beim Bot-Start
2. **Anchors werden neu erstellt** innerhalb von ~5min
3. **State wird frisch aufgebaut**
4. **Kein manueller Eingriff nötig**

Der Bot ist nach dem Reset voll funktionsfähig!

---

## FAQ

**Q: Verliere ich mein Portfolio bei cleanup_soft.py?**
A: Nein! Soft cleanup behält den kompletten State. Portfolio bleibt unverändert.

**Q: Was passiert mit offenen Positionen bei cleanup_hard.py?**
A: Ohne `--portfolio-reset`: Positionen bleiben auf Exchange, State wird neu erstellt.
Mit `--portfolio-reset`: ALLE Assets werden verkauft!

**Q: Kann ich den Hard Reset rückgängig machen?**
A: Nein! Daten sind unwiederbringlich gelöscht. Daher die strengen Prompts.

**Q: Welches Script für normale Log-Cleanups?**
A: `cleanup_soft.py -y` - schnell, sicher, nicht-destruktiv.

**Q: Muss ich nach cleanup den Bot neu starten?**
A: Ja, beide Scripte erfordern einen Bot-Neustart danach.

---

## Sicherheits-Checkliste

Vor cleanup_hard.py:

- [ ] Bot ist gestoppt
- [ ] Keine offenen Positionen (oder `--portfolio-reset` beabsichtigt)
- [ ] Backup von wichtigen Daten erstellt
- [ ] Verstehe dass ALLE Daten gelöscht werden
- [ ] Habe Zeit für vollständigen Neustart (~10min)

---

## Support

Bei Problemen:
1. Prüfe Logs in `logs/` (vor cleanup!)
2. Versuche erst `cleanup_soft.py --orders`
3. Wenn nötig `cleanup_hard.py --dry-run` für Preview
4. Check Github Issues für bekannte Probleme

**Entwickler-Modus:**
Für debugging mit verbose output: `-v` Flag bei beiden Scripten.
