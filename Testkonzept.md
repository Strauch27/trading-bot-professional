# Testkonzept – Trading Bot Professional

Dieses Dokument beschreibt die geplanten automatisierten Tests für die kritischen Handelsabläufe **Buy** und **Exit** innerhalb der `TradingEngine`.

---

## 1. Gemeinsame Rahmenbedingungen
- Testframework: `pytest`.
- Abhängigkeiten werden mit `unittest.mock` / `pytest-mock` gespiesed; externe IOs (Exchange, Telegram, Netzwerk) bleiben deaktiviert.
- `config_module` wird je Test via `monkeypatch` minimal konfiguriert (z. B. `GLOBAL_TRADING=True`, Guards aus, passende Schwellenwerte).
- EventBus-, MarketData- und Telegram-Komponenten laufen als Mocks/Spies.
- Ziel: End-to-End-Durchstich vom Signal bzw. Exit-Trigger bis zur finalen Portfolio- und PnL-Aktualisierung.

---

## 2. Exit-Flow-Test (`tests/engine/test_exit_flow.py`)

### Zielsetzung
Verifizieren, dass ein ausgelöstes Exit-Signal den kompletten FSM-Verkaufspfad korrekt durchläuft und Positionen sauber entfernt werden.

### Setup
- TradingEngine-Fixture mit realer Initialisierung, jedoch:
  - `ExchangeWrapper`, `OrderRouter`, `Reconciler`, Telegram als Mocks.
  - Portfolio enthält vorbereitete Position (z. B. `ETH/USDT`, Menge > 0).
  - `engine.snapshots` stellt Preisdaten bereit, die einen Hard-Take-Profit auslösen.
- `ExitEngine.choose("ETH/USDT")` erzeugt ein ExitSignal; `decision.exit_assembler.assemble` baut daraus den Intent.

### Testablauf
1. Intent per `engine._on_order_intent` einspeisen.
2. Gemockter `OrderRouter.handle_intent` ruft `_publish_filled` und damit `order.filled` aus.
3. `engine._on_order_filled` nutzt den `Reconciler`-Mock (`summary={'state': 'closed', 'qty_delta': -…}`) und triggert `ExitHandler.handle_exit_fill`.

### Verifikation
- `portfolio.positions` enthält das Symbol nicht mehr; `portfolio.held_assets` synchronisiert.
- `pnl_service.record_fill` wurde für den Sell-Aufruf getriggert.
- `engine.session_digest['sells']` besitzt einen Eintrag.
- `event_bus.publish("order.intent", ...)` wurde genau einmal ausgelöst.

---

## 3. Buy-Flow-Test (`tests/engine/test_buy_flow.py`)

### Zielsetzung
Sicherstellen, dass ein positives Kaufsignal zu einer bestehenden Position führt und alle beteiligten Komponenten korrekt interagieren.

### Setup
- TradingEngine-Fixture mit:
  - Leerem Portfolio und ausreichendem Budget (`portfolio.my_budget` ≥ Konfiguration).
  - `engine.topcoins` enthält z. B. `{"ETH/USDT": deque(...)}`.
  - Markt-Daten-Funktionen (`engine.market_data.get_price`, etc.) liefern stabile Preise.
  - `BuyDecisionHandler.evaluate_buy_signal` liefert für `ETH/USDT` ein positives Signal.
  - `decision.buy_assembler.assemble` generiert einen gültigen Intent.

### Testablauf
1. `engine._evaluate_buy_opportunities()` ausführen.
2. `event_bus.publish("order.intent", ...)` wird erwartet; der Mock `OrderRouter.handle_intent` erzeugt synchron `order.filled`.
3. `engine._on_order_filled` nutzt den `Reconciler`-Mock (`summary={'state': 'opened', 'qty_delta': …}`) und ruft `buy_decision_handler.handle_router_fill`.

### Verifikation
- `portfolio.positions` enthält das Symbol; `portfolio.is_holding(symbol)` ist wahr.
- `pnl_service.record_fill` registriert den Buy.
- `engine.session_digest['buys']` besitzt einen Eintrag.
- Budget-Reservierung/-Freigabe bzw. relevante Portfolio-Methoden wurden vom Router-Mock angesprochen (über Spies prüfen).

---

## 4. Erweiterungen & Hinweise
- Tests sollen auch Grenzfälle berücksichtigen (z. B. fehlende Marktdaten, Reservierungsfehler), sobald Grundpfad steht.
- Optional: Validierung der JSONL-Logs (`order_audit`, `orders.jsonl`) durch Mock-Schreiber für zusätzliche Sicherung.
- Beide Tests bilden die Basis für spätere Regression-Suites und Continuous-Integration-Läufe.
