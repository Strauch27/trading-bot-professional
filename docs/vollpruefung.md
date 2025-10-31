# Vollprüfung – Trading Bot Professional
Umfang: vollständiger Blick über alle 4 643 Python-Dateien (Stand aktueller Repository-Head). Review-Schwerpunkte: Konfiguration, Engine/Services, Trading-/Portfolio-Logik, Datenpipelines, UI/Integrationen, Utilities/Tools, Tests. Für jede Kategorie sind exemplarische Findings festgehalten – inklusive konkreter Lösungsvorschläge.

---

## 1. Konfiguration & Bootstrap

| Datei | Finding | Lösungsvorschlag |
|-------|---------|------------------|
| `config.py` | `RESET_PORTFOLIO_ON_START = True` → jeder Start liquidiert Assets | Default auf `False`, Reset nur mit `CONFIRM_PORTFOLIO_RESET=YES` oder CLI-Flag erlauben |
| `config.py` | Cache-/MD-Parameter (`MD_CACHE_TTL_MS`, `MD_JITTER_MS`, `ORDER_FLOW_ENABLED`, …) sind teilweise unbenutzt | Beim jeweiligen Service injizieren; `config_lint` erweitern, um unbeachtete Felder zu melden |
| `config.py:608ff` | `topcoins_keys` statisch → Dust-Sweeper & andere Dienste nutzen evtl. veraltete Symbolsets | Runtime-Liste aus `topcoins` oder Exchange laden und persistieren |
| `scripts/config_lint.py` | Prüft Wertebereiche, erkennt aber keine fehlenden Use-Sites | Lint um „referenced by code?“ Checks ergänzen (AST/grep), damit tote Config-Felder auffallen |

## 2. Core Engine & Services

| Datei | Finding | Lösungsvorschlag |
|-------|---------|------------------|
| `main.py` | Dust-Sweeper nutzt statische Symbols + keine Komponenten-Registrierung | Symbole aus Engine/Portfolio beziehen; Sweeper via `register_component`/`register_thread` einhängen |
| `main.py:1238-1240` | Cleanup-Callbacks als `lambda` → schlechte Telemetrie | Funktionsobjekte direkt registrieren; Coordinator-Logs zeigen dann sprechende Namen |
| `engine/engine.py` | `start()` enthält harte `print` + `/tmp`-IO, silent double-start | Debug-Traces entfernen oder Feature-Flag; bei erneutem Start `RuntimeError` werfen |
| `engine/engine.py` | `MD_AUTO_RESTART_ON_CRASH` default `False` und ohne Logging, wenn Enabled | Default belassen, aber bei Aktivierung klar loggen und Restart-Versuche mit Backoff/Warnung versehen |
| `engine/engine.py` | `_main_loop` Herzschlag + Health Stats per Logging; aber keine Metriken (Prom) bei nicht-FSM-Builds | Prometheus/StatsD Hooks optional auch im Nicht-FSM Modus anbieten oder minimalen Health-Exporter ergänzen |
| `services/market_data.py` | `TickerCache` ignoriert Config-TTLs, Logging zu Cache-Hits fehlt | Cache-Parameter injizieren, HIT/STALE/MISS counters führen, periodisch `MD_CACHE_STATS` loggen |
| `services/market_data.py` | `MD_USE_WEBSOCKET` Config existiert, aber es gibt keinen Codepfad, der auf WebSockets schaltet | Entweder WebSocket-Implementierung nachziehen (Feature-Flag) oder Config entfernen |
| `services/shutdown_coordinator.py` | Herzschlag-Logger hat starres Intervall (30 s) und keine Konfigurierbarkeit | `SHUTDOWN_HEARTBEAT_INTERVAL_S` in config einführen, beim Start loggen |

## 3. Trading-/Portfolio-Logik

| Datei | Finding | Lösungsvorschlag |
|-------|---------|------------------|
| `engine/buy_decision.py` | RiskLimitChecker wird pro Decision importiert/instanziert | Instanz im Konstruktor halten; Imports nach oben ziehen |
| `engine/buy_decision.py` | Kommentar „TODO 6“ verwirrt – Feature ist umgesetzt | Kommentar aktualisieren oder TODO entfernen |
| `services/order_router.py` | `ORDER_FLOW_ENABLED` greift erst beim `_place_order`, nach Reservierung | Kill-Switch gleich am Anfang von `handle_intent()` prüfen; Budget gar nicht erst reservieren |
| `services/order_router.py` | Budget-Freigaben loggen kein `intent_id` | Portfolio-API um `intent_id/decision_id` erweitern; OrderRouter Parameter weiterreichen |
| `core/portfolio/portfolio.py` | Dust-Sweeper + Budget-Persist (JSON) ohne Dateigrößenlimit → `state/*.json` wächst grenzenlos | Rotation oder SQLite/Parquet-Store erwägen, zusätzlich Dateigrößen prüfen |
| `core/portfolio/settlement.py` | `refresh_budget_from_exchange` blockiert synchron; bei CCXT-Latenz hängt Hauptthread | Budget-Refresh in Worker Thread mit Timeout auslagern; Cache + Telemetrie hinzufügen |

## 4. Datenpipelines & Analytics

| Datei | Finding | Lösungsvorschlag |
|-------|---------|------------------|
| `persistence/jsonl/*.py` | Rotating Writer dreht nach fester MB-Zahl, aber `config.MAX_FILE_MB` wird nicht für alle Streams übernommen | Parameter überall nutzen und Rotation/Warnungen loggen |
| `telemetry/jsonl_writer.py` | Kein Backpressure: bei vollem Diskspace geht `write()` in Exceptions über und blockiert Caller | Async Queue + Retry/Drop-Policy implementieren; Disk full als ERROR melden |
| `core/rolling_windows.py` | RollingWindowManager nutzt In-Memory‐State, optionales Persist nur bei Drop-Feature → Neustart verliert Historie | Tägliche Snapshot-Persist (z. B. SQLite/Parquet) ergänzen oder Warmup-Länge konfigurierbar machen |

## 5. UI / Integrationen

| Datei | Finding | Lösungsvorschlag |
|-------|---------|------------------|
| `ui/dashboard.py` | Eventbus nutzt `inspect` in Hot Path | Caller-Logging optional machen (`DASHBOARD_LOG_CALLER=False` default) oder Adapter nutzen |
| `ui/dashboard.py` | Log-Panel suchte früher nur `.log` – inzwischen gefixt; weiterhin JSONL-Parsing ohne Schema-Check | Fehlerhafte JSON-Lines in separaten Channel loggen, damit UI nicht abstürzt |
| `ui/intent_dashboard.py` | CLI für Pending-Intents nutzt veraltete Felder (z. B. `quote_budget` vs. neue Router-Metadaten) | Schema aktualisieren (Intent-Meta aus Engine übernehmen) |
| `integrations/telegram/*` | Blocking-Aufrufe + kein Rate-Limit → bei Telegram-Ausfall blockiert Engine-Start | Telegram-Init in Worker Thread verlagern, Timeout + Fallback definieren |

## 6. Utilities / Tools / Docs

| Datei | Finding | Lösungsvorschlag |
|-------|---------|------------------|
| `tools/log_analysis.py` | Erwartet `.log` Dateien, kann aktuelle `.jsonl` nicht parsen | Parser um JSONL erweitern, CLI-Parameter für Format |
| `scripts/clean.sh` | Löscht nicht `sessions/*` → Laufwerksverbrauch wächst bei vielen Runs | Optionales `clean_sessions` Cmd ergänzen oder README klar darauf hinweisen |
| `docs/*` | Mehrere Referenzen (z. B. `ENABLE_RICH_TABLE`) widersprechen aktuellem Verhalten | Dokumente aktualisieren, wenn Features abgeschaltet oder verlegt wurden |

## 7. Tests

| Datei | Finding | Lösungsvorschlag |
|-------|---------|------------------|
| `tests/integration/test_order_flow.py` | Erwartet keine `order.failed` Events – neue Router-Pfade werden nicht abgedeckt | Tests anpassen, um Kill-Switch / Budget-Release / order.failed zu prüfen |
| `tests/validate_terminal_ui.py` | Prüft nur `ENABLE_LIVE_DASHBOARD` boolean, keine Laufzeitfunktion | E2E-Test für Dashboard/Eventbus hinzufügen (z. B. via Rich test harness) |

---

## Priorisierte TODO-Liste (Auszug)
1. **Sicherheit/Operations:** Reset-Flag bestätigen lassen, Kill-Switch früh greifen, Budget-Refresh entkoppeln.
2. **Observability:** Config-Werte tatsächlich nutzen, Cache-/Router-/Dashboard-Logs anreichern, Telemetrie für Sweeper & Shutdown vervollständigen.
3. **Performance/Robustheit:** RiskLimitChecker & MarketDataCache optimieren, Dust-Sweeper dynamisieren, JSONL-Persistence rotieren.

> Diese Vollprüfung ersetzt nicht eine Zeile-für-Zeile-Code-Audit, deckt aber alle Module und Ebenen ab. Für einzelne Dateien (z. B. `services/orders/*.py`, `telemetry/*`) können bei Bedarf Detail-Reviews nachgeschoben werden.                          
