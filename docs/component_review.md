# Component Review
Stand: aktueller `main`‑Branch vom Trading Bot Professional. Fokus auf praxisrelevante Komponenten – jeweils mit Beobachtungen und konkreten Lösungsvorschlägen.

---

## config.py
- **Risky default:** `RESET_PORTFOLIO_ON_START` steht weiterhin auf `True` (`config.py:54`). Jeder Bot-Start liquidiert dadurch sämtliche Nicht‑USDT‑Assets – selbst im Live-Betrieb.
  - *Vorschläge zur Umsetzung:*
    1. Default auf `False` setzen.
    2. In `main.py` vor `portfolio.perform_startup_reset` einen Schutz einbauen: nur wenn `os.getenv("CONFIRM_PORTFOLIO_RESET") == "YES"` oder ein CLI‑Flag gesetzt ist, den Reset tatsächlich ausführen – sonst `logger.error` + `sys.exit(1)`.
    3. Im Startbanner deutlich anzeigen, ob ein Reset vorgesehen ist und wie er bestätigt wurde.
- **Unbenutzter Cache-Parameter:** `MD_CACHE_TTL_MS` (`config.py:120`) wird nirgends ausgewertet; der Ticker-Cache in `services/market_data.py` verwendet fest verdrahtete 5 s bzw. 2 s TTLs.
  - *Vorschläge zur Umsetzung:*
    1. Neben `MD_CACHE_TTL_MS` auch `MD_CACHE_SOFT_TTL_MS` und `MD_CACHE_MAX_SIZE` definieren.
    2. Den `TickerCache` im Market-Data-Service mit diesen Werten initialisieren:
       ```python
       cache = TickerCache(
           default_ttl=config.MD_CACHE_TTL_MS / 1000,
           soft_ttl=config.MD_CACHE_SOFT_TTL_MS / 1000,
           max_size=config.MD_CACHE_MAX_SIZE,
       )
       ```
    3. Beim Start ein Telemetrie-Event `MD_CACHE_CFG` loggen, damit jeder Bot-Run dokumentiert, welche TTLs tatsächlich aktiv waren.

## main.py
- **Dust-Sweep Symbolquelle:** Der periodische Dust-Sweeper greift auf `config_module.topcoins_keys` zurück (`main.py:514-517`). Diese statische Liste weicht häufig von den zur Laufzeit geladenen Märkten (`topcoins`) ab, wodurch relevante Assets ignoriert werden.
  - *Vorschläge zur Umsetzung:*
    1. Beim Start dem Dust-Loop `list(topcoins.keys())` übergeben.
    2. Innerhalb der Schleife regelmäßig `symbols = list(self.portfolio.held_assets.keys()) + list(self.engine.topcoins.keys())` ermitteln, damit neue Märkte automatisch berücksichtigt werden.
    3. Den Sweeper beim `ShutdownCoordinator` via `register_component("dust_sweeper", dust_sweeper)` und `register_thread(dust_sweep_thread)` anmelden, damit er im Heartbeat/Shutdown sichtbar ist.
- **Cleanup-Callbacks:** Beim Registrieren der Shutdown-Cleanups werden anonyme `lambda`‑Wrapper verwendet (`main.py:1238-1240`). Im Shutdown-Log tauchen dadurch lediglich `<lambda>`‑Namen auf, was Troubleshooting erschwert.
  - *Vorschlag:* Die Funktionsobjekte direkt übergeben:
    ```python
    shutdown_coordinator.add_cleanup_callback(_telegram_shutdown_cleanup)
    ```
    So erscheinen die echten Funktionsnamen im `SHUTDOWN_HEARTBEAT`.

## engine/engine.py
- **Leftover Debugging Artifacts:** `start()` schreibt immer noch in `/tmp/engine_start_called.txt` und produziert `print()`‑Ausgaben (`engine/engine.py:586-626`). Dadurch entsteht IO-Lärm außerhalb des Logging-Subsystems und in Container-Umgebungen ggf. kein Zugriff auf `/tmp`.
  - *Vorschläge zur Umsetzung:*
    1. Alle `print()` und File-Writes entfernen oder hinter ein Config-Flag `ENGINE_DEBUG_TRACE` stellen.
    2. Falls persistente Traces benötigt werden, stattdessen `logger.debug("ENGINE_START_TRACE", extra={...})` verwenden.
- **Silent double‑start:** Bei erneutem `engine.start()` wird nur gewarnt und kommentarlos zurückgekehrt (`engine/engine.py:598-603`). Das lässt race conditions unbemerkt.
  - *Vorschläge zur Umsetzung:*
    1. Wenn `self.running` oder `self.main_thread.is_alive()` bereits `True` ist, `raise RuntimeError("TradingEngine already running")`.
    2. Eine separate `restart()`-Methode anbieten, die `stop()` aufruft, auf Thread-Join wartet und dann `start()` erneut ausführt – so bleibt der Control-Flow sauber.

## engine/buy_decision.py
- **Hot-path Imports / Instantiierungen:** Im Buy-Handler wird `RiskLimitChecker` innerhalb jeder Entscheidungsrunde neu importiert und konstruiert (`engine/buy_decision.py:442-449`). Bei 100+ Symbolen kostet das pro Sekunde mehrere Millisekunden.
  - *Vorschläge zur Umsetzung:*
    1. `RiskLimitChecker` im `__init__` des Handlers instantiieren und als `self.risk_checker` hinterlegen.
    2. `RiskLimitsEval` oben einmal importieren, damit pro Decision kein neuer Pydantic-Typ erzeugt wird.
- **Unklarer TODO-Status:** Kommentar „Phase 1 (TODO 6)“ suggeriert unvollständige Implementierung, obwohl der Risk-Check real ausgeführt wird.
  - *Vorschlag:* Kommentar entfernen oder durch eine präzise Beschreibung („Phase 1 implementiert“) ersetzen, damit zukünftige Reviewer nicht nach einem fehlenden Feature suchen.

## services/order_router.py
- **Kill-Switch greift zu spät:** `ORDER_FLOW_ENABLED` wird erst in `_place_order()` geprüft (`services/order_router.py:321-332`). Zu diesem Zeitpunkt sind Budget bereits reserviert und Audit-Einträge geschrieben; der Intent läuft durch alle Retry-Schleifen.
  - *Vorschläge zur Umsetzung:*
    1. Gleich nach dem Intent-Parsing prüfen:
       ```python
       if not getattr(config, 'ORDER_FLOW_ENABLED', True):
           self._emit_order_failed(intent_id, symbol, reason="order_flow_disabled")
           return
       ```
    2. `_emit_order_failed` als Helper bauen, der Audit-Log, `order.failed` Event und Budget-Freigabe erledigt, damit alle Fehlerpfade identisch behandelt werden.

## services/market_data.py
- **Config-Drift:** Obwohl die Config Soft-/Hard-TTLs bereitstellt (`MD_CACHE_TTL_MS`, `MD_JITTER_MS` etc.), nutzt `TickerCache` fixe Standardwerte (`services/market_data.py:118-154`).
  - *Vorschläge zur Umsetzung:*
    1. Die oben genannten Config-Werte an den Market-Data-Service durchreichen (siehe Abschnitt “config.py”).
    2. `MarketDataProvider` alle 60 s ein Event `MD_CACHE_STATS` loggen lassen, das Treffer/Verfehlungen (HIT/STALE/MISS) summarisiert – so kann man bei Debug-Sessions sofort sehen, ob TTLs sinnvoll gewählt sind.

## core/portfolio/portfolio.py
- **Budget-Freigaben ohne Kontext:** `release_budget()` und `commit_budget()` loggen zwar `BUDGET_RELEASED/BUDGET_COMMITTED`, übergeben jedoch keinen `intent_id` (`core/portfolio/portfolio.py:720-759`).
  - *Vorschläge zur Umsetzung:*
    1. Beide Methoden um optionale `intent_id`/`decision_id` Parameter erweitern.
    2. OrderRouter beim Aufruf `self.pf.release(..., intent_id=intent_id)` bzw. `commit(...)` den Intent mitgeben.
    3. Die `log_event`-Payloads um dieses Feld ergänzen, damit sich Budget-Events später eindeutig einer Order zuordnen lassen.

## ui/dashboard.py
- **Event Bus Logging Overhead:** `emit_dashboard_event` ermittelt den Aufrufer via `inspect` (`ui/dashboard.py:58-82`), was bei vielen Events CPU-Zeit kostet.
  - *Vorschläge zur Umsetzung:*
    1. Ein Config-Flag `DASHBOARD_LOG_CALLER` einführen, das die Caller-Ermittlung aktiviert/deaktiviert.
    2. Alternativ `logging.LoggerAdapter` verwenden, bei dem Aufrufer optional `extra={'dashboard_source': '...'}`

---

Diese Punkte decken die wesentlichen Laufzeit-Komponenten ab. Nach Umsetzung werden Konfiguration, Lifecycle-Management und Observability konsistenter, und mehrere noch vorhandene Test-Hilfen verschwinden aus dem Produktionspfad.***
