# Trading Bot - Konfigurations-Handbuch

Vollständige Dokumentation aller Konfigurationsparameter für den Trading Bot.

## 📋 Inhaltsverzeichnis

1. [Schnellstart](#schnellstart)
2. [USER SETTINGS - Häufig geänderte Parameter](#user-settings)
3. [SYSTEM DEFAULTS - Selten geänderte Parameter](#system-defaults)
4. [Troubleshooting](#troubleshooting)
5. [Advanced Topics](#advanced-topics)

---

## Schnellstart

### Die 5 wichtigsten Einstellungen

Für 90% der Anpassungen reichen diese 5 Parameter:

```python
GLOBAL_TRADING = True          # True = Live Trading, False = Nur Beobachten
DROP_TRIGGER_VALUE = 0.997     # -0.3% Drop → Kaufsignal
TAKE_PROFIT_THRESHOLD = 1.005  # +0.5% → Verkauf mit Gewinn
STOP_LOSS_THRESHOLD = 0.990    # -1.0% → Verkauf mit Verlust
MAX_TRADES = 10                # Maximal 10 Positionen gleichzeitig
```

### Sicherheitshinweise

⚠️ **Bevor du GLOBAL_TRADING = True setzt:**
- Teste mit `False` im Beobachtungsmodus
- Prüfe dein Budget (`SAFE_MIN_BUDGET`)
- Verstehe die Guards (können ALLE Trades blockieren)
- Sichere deine API Keys in `.env`

---

## USER SETTINGS

Häufig geänderte Parameter für tägliche Anpassungen.

### 1. MASTER SWITCH

#### GLOBAL_TRADING
**Hauptschalter für den Bot**

- `True` = Bot kann echte Trades ausführen (Live-Trading)
- `False` = Bot läuft im Beobachtungsmodus (analysiert nur, kauft/verkauft nicht)

**Empfehlung für Anfänger:** Erst mit `False` starten und beobachten!

---

### 2. EXIT STRATEGIE

Bestimmt wann der Bot Positionen schließt.

#### TAKE_PROFIT_THRESHOLD
**Gewinnziel**

- `1.005` = 100.5% vom Kaufpreis = **+0.5% Gewinn**
- Beispiel: Gekauft bei 100€ → Verkauf bei 100.50€

**Typische Werte:**
- Konservativ: `1.003` (+0.3%)
- Standard: `1.005` (+0.5%)
- Aggressiv: `1.010` (+1.0%)

#### STOP_LOSS_THRESHOLD
**Maximaler akzeptierter Verlust**

- `0.990` = 99.0% vom Kaufpreis = **-1.0% Verlust**
- Beispiel: Gekauft bei 100€ → Verkauf spätestens bei 99.00€

**Typische Werte:**
- Eng: `0.995` (-0.5%)
- Standard: `0.990` (-1.0%)
- Weit: `0.985` (-1.5%)

#### SWITCH_TO_SL_THRESHOLD & SWITCH_TO_TP_THRESHOLD
**Umschaltpunkte der Exit-Strategie**

- `SWITCH_TO_SL_THRESHOLD = 0.995`: Bei -0.5% fokussiert Bot auf Stop-Loss
- `SWITCH_TO_TP_THRESHOLD = 1.002`: Bei +0.2% wechselt Bot zurück zu Take-Profit
- `SWITCH_COOLDOWN_S = 20`: Mindestens 20 Sekunden zwischen Umschaltungen

**Verhindert:** Flip-Flop bei volatilen Märkten

#### ATR-basierte dynamische Exits (Fortgeschritten)

**USE_ATR_BASED_EXITS**
- `False` = Nutze feste Prozentwerte (einfacher, vorhersehbar)
- `True` = Passe Exit-Levels an aktuelle Marktvolatilität an (adaptiv)

**ATR = Average True Range** - misst die Volatilität (Preisschwankungen)

Wenn aktiviert:
- `ATR_PERIOD = 14`: Letzte 14 Kerzen für Berechnung
- `ATR_SL_MULTIPLIER = 0.6`: Stop-Loss = Kaufpreis - (ATR × 0.6)
- `ATR_TP_MULTIPLIER = 1.6`: Take-Profit = Kaufpreis + (ATR × 1.6)

---

### 3. ENTRY STRATEGIE

Definiert wann der Bot kauft ("Buy the Dip" Strategie).

#### DROP_TRIGGER_VALUE
**Wie tief muss der Preis fallen für ein Kaufsignal?**

- `0.997` = 99.7% vom Hochpunkt = **-0.3% Preisrückgang**
- Beispiel: Coin war bei 100€ (Hoch) → Bot kauft bei 99.70€

**Einstellung:**
- Kleinerer Wert (0.99) = Größerer Rückgang nötig → Weniger Trades
- Größerer Wert (0.998) = Kleinerer Rückgang nötig → Mehr Trades

#### DROP_TRIGGER_MODE
**Referenzhoch für den Drop-Trigger**

- **Mode 1**: Höchster Preis seit Bot-Start (einfach, aber kann veralten)
- **Mode 2**: Höchster Preis der letzten X Minuten (dynamisch, kurzsichtig)
- **Mode 3**: Der höhere Wert aus Mode 1 und 2 (Kombination)
- **Mode 4**: Wie Mode 1, aber Reset nach jedem Trade ✅ **Empfohlen**

**DROP_TRIGGER_LOOKBACK_MIN** (nur für Mode 2/3):
- `2` = Betrachte die letzten 2 Minuten für Hochpunkt-Suche
- Größerer Wert = Längerer Rückblick (träger, aber stabiler)

#### Drop Anchor System

**USE_DROP_ANCHOR**
- `True` = Hochpunkte werden gespeichert und bei Neustart wiederhergestellt
- `False` = Hochpunkte nur im Arbeitsspeicher (gehen bei Neustart verloren)

**WICHTIG:** Mode 4 braucht `True` oder `BACKFILL_MINUTES > 0`!

**ANCHOR_UPDATES_WHEN_FLAT**
- `True` = Anker folgt neuen Hochs auch ohne Position
- `False` = Anker bleibt fix bis neue Position eröffnet wird

**Anchor-Schutz (Mode 4):**
- `ANCHOR_STALE_MINUTES = 60`: Anker älter als 60 Min werden neu initialisiert
- `ANCHOR_CLAMP_MAX_ABOVE_PEAK_PCT = 5.0`: Max 5% über aktuellem Peak erlaubt
- `ANCHOR_MAX_START_DROP_PCT = 6.0`: Zu Beginn darf Drop vs. Anchor max. 6% sein

---

### 4. POSITION MANAGEMENT

Risikomanagement - Wie viel investiert der Bot?

#### MAX_TRADES
**Maximale Anzahl verschiedener Coins gleichzeitig**

- `10` = Bot kann maximal 10 verschiedene Coins gleichzeitig halten
- Hilft Risiko zu streuen (Diversifikation)

#### POSITION_SIZE_USDT
**Wie viel USDT pro Kauf einsetzen**

- `25.0` = Jeder Kauf verwendet 25 USDT (ca. 25 Dollar)
- Bei MAX_TRADES=10 → Maximales Risiko = 10 × 25 = 250 USDT

**Hinweis:** 25 USDT ist ein robuster Wert. Viele Märkte benötigen nach Rundung >16 USDT Notional.

**Auto-Upsize bei knapper Rundung:**
- `ALLOW_AUTO_SIZE_UP = True`: Darf Menge leicht erhöht werden?
- `MAX_AUTO_SIZE_UP_BPS = 25`: Max. +25 bps (~0.25%) Notional-Erhöhung
- `MAX_AUTO_SIZE_UP_ABS_USDT = 0.30`: Absolut gedeckelt bei +0.30 USDT

#### MAX_PER_SYMBOL_USD
**Maximales Investment pro einzelnem Coin**

- `60.0` = Maximal 60 USDT in einem Coin (Konzentrationslimit)
- Verhindert dass Bot alles in einen Coin steckt

#### TRADE_TTL_MIN
**Time-To-Live - Maximale Haltedauer in MINUTEN**

- `120` = Nach 120 Minuten (2 Stunden) wird Position zwangsgeschlossen
- Schützt vor "Bag Holding" (zu lange an Verlustpositionen festhalten)

#### COOLDOWN_MIN
**Wartezeit nach Verkauf bevor Coin wieder gekauft wird**

- `15` = 15 Minuten Pause nach Verkauf
- `0` = Keine Wartezeit (Coin kann sofort wieder gekauft werden)

#### ALLOW_DUPLICATE_COINS
**Dürfen mehrere Positionen im gleichen Symbol eröffnet werden?**

- `False` = Nur eine Position pro Symbol ✅ **Empfohlen**
- `True` = Mehrere Positionen erlaubt (höheres Risiko)

---

### 5. GUARDS - Qualitätsfilter

Guards sind Sicherheitschecks die schlechte Trades verhindern.

⚠️ **ACHTUNG:** Zu strikte Guards können ALLE Trades blockieren!

#### SMA Guard (Simple Moving Average - Trendfilter)

**USE_SMA_GUARD**
- `False` = Deaktiviert ✅ **Standard**
- `True` = Prüfe Preis vs. gleitenden Durchschnitt

Wenn aktiviert:
- `SMA_GUARD_WINDOW = 50`: Durchschnitt der letzten 50 Kerzen
- `SMA_GUARD_MIN_RATIO = 0.992`: Preis muss mindestens 99.2% des SMA sein

**Problem:** Ratio < 1.0 blockiert Käufe bei Abwärtstrends (wo Drop-Trigger eigentlich kaufen will!)

#### Volume Guard (Handelsvolumen-Filter)

**USE_VOLUME_GUARD**
- `False` = Deaktiviert ✅ **Standard**
- `True` = Prüfe Liquidität

Wenn aktiviert:
- `VOLUME_GUARD_WINDOW = 15`: Durchschnitt der letzten 15 Minuten
- `VOLUME_GUARD_FACTOR = 1.020`: Aktuelles Volumen muss 102% des Durchschnitts sein
- `MIN_24HUSD_VOLUME = 150000`: Mindestens 150.000 USDT in 24h

**Problem:** Factor > 1.0 kann zu viele Trades blockieren!

#### Spread Guard (Bid-Ask Differenz)

**USE_SPREAD_GUARD**
- `False` = Deaktiviert ✅ **Standard**
- `True` = Prüfe Spread

Wenn aktiviert:
- `GUARD_MAX_SPREAD_BPS = 35`: Maximal 35 bps (0.35%) Differenz

**Beispiel:** Bid=99.65€, Ask=100€ → Spread=0.35% → Trade erlaubt

#### Volatilitäts-Guard

**USE_VOL_SIGMA_GUARD**
- `False` = Deaktiviert ✅ **Standard**
- `True` = Prüfe Preisbewegung

Wenn aktiviert:
- `VOL_SIGMA_WINDOW = 30`: Volatilität der letzten 30 Minuten
- `REQUIRE_VOL_SIGMA_BPS_MIN = 10`: Mindestens 10 bps (0.1%) Standardabweichung

#### Makro-Filter (Marktweite Stimmung)

⚠️ **Diese Filter können bei Bärenmärkten ALLE Trades verhindern!**

**Bitcoin-Filter:**
- `USE_BTC_FILTER = False`: Deaktiviert ✅ **Standard**
- `BTC_CHANGE_THRESHOLD = None`: Wenn aktiviert, wie stark darf BTC fallen?

**Falling-Coins-Filter:**
- `USE_FALLING_COINS_FILTER = False`: Deaktiviert ✅ **Standard**
- `FALLING_COINS_THRESHOLD = 0.55`: Maximal 55% der Coins dürfen fallen

#### Machine Learning Guard

**USE_ML_GATEKEEPER**
- `False` = Deaktiviert ✅ **Standard**
- `True` = KI-Modell muss Trades genehmigen

⚠️ **ACHTUNG:** Ohne trainierte Modelle blockiert dies ALLE Trades!

Wenn aktiviert:
- `ML_BUY_THRESHOLD = 0.65`: KI muss zu 65% sicher sein
- `MODEL_DIR = "models"`: Ordner mit trainierten Modellen

---

### 6. ORDER EXECUTION

Wie werden Orders platziert?

#### Buy Execution

**BUY_MODE**
- `"PREDICTIVE"` = Kaufe leicht über Marktpreis (schneller, teurer) ✅ **Standard**
- `"ESCALATION"` = Stufenweise erhöhen bis Kauf klappt
- `"CLASSIC"` = Einfache Limit-Order zum Marktpreis

**PREDICTIVE_BUY_ZONE_BPS**
- `3` = 3 Basispunkte = 0.03% über aktuellem Preis
- Beispiel: Marktpreis 100€ → Kauforder bei 100.03€
- Balanciert: schnell füllen aber nicht übertreiben

**BUY_ESCALATION_STEPS** (wenn BUY_MODE = "ESCALATION"):
```python
[
    {"tif": "IOC", "premium_bps": 10, "max_attempts": 1},  # +0.10%
    {"tif": "IOC", "premium_bps": 30, "max_attempts": 1},  # +0.30%
    {"tif": "IOC", "premium_bps": 60, "max_attempts": 1},  # +0.60%
]
```
IOC = Immediate-Or-Cancel (sofort oder abbrechen)

#### Sell Execution

**NEVER_MARKET_SELLS**
- `False` = Erlaube Market-Orders ✅ **Standard** (schneller)
- `True` = Nur Limit-Orders (kontrollierter, aber langsamer)

⚠️ **ACHTUNG:** `True` mit zu engen `EXIT_LADDER_BPS` kann Exits verhindern!

**EXIT_LADDER_BPS**
- `[10, 30, 70, 120]` = Versuche mit -0.1%, -0.3%, -0.7%, -1.2% Abschlag
- Bot versucht erst mit kleinem Abschlag, dann immer größer

**ALLOW_MARKET_FALLBACK_TTL**
- `False` = Kein Market-Fallback bei Zeitablauf ✅ **Konservativ**
- `True` = Market-Order wenn TTL erreicht

#### Depth-Sweep (Synthetischer Market mit Limit-IOC)

**USE_DEPTH_SWEEP**
- `True` = Preisfindung über Orderbuch-Tiefe ✅ **Empfohlen**
- `False` = Einfache Market-Orders

Wenn aktiviert:
- `SWEEP_ORDERBOOK_LEVELS = 20`: Top-20 Bids/Asks betrachten
- `MAX_SLIPPAGE_BPS_ENTRY = 15`: Entry max. 0.15% über Ask
- `MAX_SLIPPAGE_BPS_EXIT = 12`: Exit max. 0.12% unter Bid
- `SWEEP_REPRICE_ATTEMPTS = 4`: 4 Versuche
- `SWEEP_REPRICE_SLEEP_MS = 150`: 150ms Pause zwischen Versuchen

---

### 7. TRAILING & ADVANCED EXITS

#### Trailing Stop Loss (Nachlaufender Stop-Loss)

**USE_TRAILING_STOP**
- `True` = Stop-Loss folgt dem Preis nach oben ✅ **Sichert Gewinne**
- `False` = Fester Stop-Loss (einfacher)

**TRAILING_STOP_ACTIVATION_PCT**
- `1.001` = Aktiviere wenn Preis +0.1% vom Kaufpreis erreicht
- Erst wenn dieser Gewinn erreicht ist, beginnt der Stop nachzulaufen

**TRAILING_STOP_DISTANCE_PCT**
- `0.999` = Stop liegt bei 99.9% vom erreichten Hoch (-0.1%)
- Beispiel: Hoch bei 101€ → Stop bei 100.90€

#### Trailing Take Profit (Nachlaufende Gewinnmitnahme)

**USE_TRAILING_TP**
- `False` = Feste Gewinnziele ✅ **Standard**
- `True` = Gewinnziel steigt mit dem Preis

Wenn aktiviert:
- `TRAILING_TP_ACTIVATION_PCT = 1.0040`: Bei +0.4% aktivieren
- `TRAILING_TP_STEP_BP = 5`: Erhöhe TP um 5 bps bei jedem neuen Hoch
- `TRAILING_TP_UNDER_HIGH_BP = 10`: TP liegt 10 bps unter dem Hoch

#### Relative Trailing (Automatisch aus TP/SL ableiten)

**USE_RELATIVE_TRAILING**
- `True` = TRAILING_* Werte werden automatisch aus TP/SL berechnet ✅ **Empfohlen**
- `False` = Nutze absolute Werte von oben

**Steuergrößen (Fraktionen):**
- `TSL_ACTIVATE_FRAC_OF_TP = 0.60`: TSL aktiv bei 60% des TP-Wegs
- `TSL_DISTANCE_FRAC_OF_SL_GAP = 0.30`: TSL-Abstand = 30% der SL-Lücke
- `TTP_ACTIVATE_FRAC_OF_TP = 0.60`: TTP aktiv bei 60% des TP-Wegs
- `TTP_UNDER_HIGH_FRAC_OF_TP = 0.20`: TP liegt 20% der TP-Breite unter Hoch
- `TTP_STEPS_PER_TP = 6`: 6 Schritte bis zum vollen TP-Weg

#### Breakeven

**BE_ACTIVATION_PCT**
- `None` = Breakeven aus ✅ **Standard** (aggressiver)
- Wenn gesetzt: Stop-Loss wird auf Kaufpreis gesetzt (kein Verlust mehr möglich)

---

## SYSTEM DEFAULTS

Selten geänderte technische Einstellungen.

### 8. RISIKO & BUDGET MANAGEMENT

#### Portfolio beim Start
- `RESET_PORTFOLIO_ON_START = True`: Verkaufe alle Coins beim Bot-Start (sauberer Neustart)

#### Budget und Reserven
- `SAFE_MIN_BUDGET = 10.0`: Bot benötigt mindestens 10 USDT zum Starten
- `CASH_RESERVE_USDT = 0.0`: Sicherheitsreserve die nie verwendet wird
- `ON_INSUFFICIENT_BUDGET = "wait"`: "observe", "wait" oder "stop"

#### Circuit Breaker (Notbremse)
- `MAX_LOSSES_IN_ROW = 5`: Nach 5 Verlusten in Folge stoppt Bot
- `CB_WINDOW_MIN = 60`: Zähle nur Verluste der letzten 60 Minuten

#### Tages-Limits
- `MAX_DAILY_DRAWDOWN_PCT = 0.08`: Stoppe bei 8% Tagesverlust
- `MAX_TRADES_PER_DAY = 120`: Maximal 120 Trades in 24 Stunden

#### Gebühren
- `FEE_RATE = 0.001`: 0.1% pro Trade (typisch für MEXC)
- `SELL_SLIPPAGE_PCT = 0.001`: 0.1% Sicherheitspuffer für Verkaufspreise

---

### 9. EXCHANGE & TECHNISCHE LIMITS

#### Trading Universum
- `UNIVERSE_TOP_N_BY_VOL = 72`: Handle nur die 72 Coins mit höchstem Volumen
- `MIN_NOTIONAL_USDT = 5.0`: Coin muss mindestens 5 USDT Volumen haben
- `EXCLUDE_SYMBOL_PREFIXES = ["BULL/", "BEAR/", "3L/", "3S/", "UP/", "DOWN/"]`: Leveraged Tokens ausschließen

#### Exchange Limits
- `MIN_ORDER_VALUE = 5.1`: Orders müssen mindestens 5.1 USDT wert sein
- `MIN_ORDER_BUFFER = 0.005`: Sicherheitsfaktor für Mindestorder
- `DUST_FACTOR = 0.9995`: 99.95% der Position (0.05% Toleranz)
- `MAX_HISTORY_LEN = 1440`: Maximal 1440 Datenpunkte (24h bei 1-Min-Kerzen)

#### Dust Sweeper
- `DUST_SWEEP_ENABLED = False`: Automatische Bereinigung kleiner Reste (deaktiviert)
- `DUST_MIN_COST_USD = 6.0`: Reste unter 6 USDT werden als Dust behandelt

#### Settlement
- `SETTLEMENT_TIMEOUT = 240`: Warte maximal 240 Sekunden auf Tradebestätigung
- `SETTLEMENT_TOLERANCE = 0.995`: Akzeptiere wenn 99.5% ausgeführt wurde

#### API Fehler-Handling
- `API_BLOCK_TTL_MINUTES = 120`: Sperre fehlerhaften Coin für 2 Stunden
- `PERMANENT_API_BLOCKLIST = {"FLOW/USDT"}`: Dauerhaft gesperrte Coins

#### Retry Settings
- `RETRY_REDUCTION_PCT = 0.97`: Reduziere Menge auf 97% bei jedem Retry
- `BACKFILL_MINUTES = 90`: Lade 90 Minuten Historie beim Start
- `BACKFILL_TIMEFRAME = '1m'`: 1-Minuten-Kerzen

#### Order Management
- `BUY_ORDER_TIMEOUT_MINUTES = 3`: Breche Kauforder nach 3 Minuten ab
- `BUY_ORDER_CANCEL_THRESHOLD_PCT = 1.03`: Breche ab wenn Preis über 103% steigt
- `STALE_ORDER_CLEANUP_INTERVAL = 60`: Prüfe alle 60 Sekunden auf alte Orders

#### IOC Settings
- `IOC_SELL_BUFFER_PCT = 0.10`: 0.10% unter Marktpreis
- `IOC_TIME_IN_FORCE = "IOC"`: Immediate-Or-Cancel
- `IOC_PRICE_BUFFERS_BPS = [5, 12, 20, 35, 60, 90]`: Preisstufen
- `IOC_RETRY_SLEEP_S = 0.4`: 0.4 Sekunden zwischen Versuchen

---

### 10. LOGGING & MONITORING

#### Log Levels
- `LOG_LEVEL = "DEBUG"`: DEBUG, INFO, WARNING, ERROR
- `CONSOLE_LEVEL = "DEBUG"`: Separater Level für Terminal

#### Debug Mode
- `DEBUG_MODE = "TRADING"`: FULL, TRADING oder MINIMAL
- `DEBUG_AUTO_ESCALATE = True`: Automatischer Switch zu FULL bei Problemen
- `DEBUG_STARTUP_FULL_MINUTES = 30`: 30 Min in FULL-Mode nach Start

#### Log Files
- `LOG_FILE`: Haupt-Log mit allen Bot-Aktivitäten
- `EVENTS_LOG`: Wichtige Ereignisse (Käufe, Verkäufe)
- `METRICS_LOG`: Leistungsmetriken
- `MEXC_ORDERS_LOG`: Order-Aktivitäten
- `AUDIT_EVENTS_LOG`: Prüfprotokoll

#### Monitoring
- `HEARTBEAT_INTERVAL_S = 60`: Lebenszeichen alle 60 Sekunden
- `ENABLE_PNL_MONITOR = True`: PnL-Monitoring aktivieren
- `ENABLE_PRETTY_TRADE_LOGS = True`: Schöne Trade-Log-Formatierung

---

### 11. STATE FILES & PERSISTENCE

Dateien die Bot-Zustand speichern:

- `STATE_FILE_HELD = "held_assets.json"`: Gehaltene Positionen
- `STATE_FILE_OPEN_BUYS = "open_buy_orders.json"`: Offene Kauforders
- `HISTORY_FILE = "trade_history.csv"`: Trade-Historie
- `DROP_ANCHORS_FILE = "drop_anchors.json"`: Hochpunkte pro Coin
- `CONFIG_BACKUP_PATH`: Backup dieser Config im Session-Ordner

---

### 12. WATCHLIST

**topcoins_keys** - Alle handelbaren Coins:
- 20 Major Coins (BTC, XRP, SOL, BNB, ...)
- 24 Mid Cap DeFi & Infrastructure
- 24 Trending & Gaming
- 9 Meme Coins
- 18 Additional Alts

Gesamt: **95 Coins**

---

### 13. TERMINAL UI

#### Terminal Presets
- `TERMINAL_PRESET = "verbose"`: "compact" oder "verbose"
- `USE_ANSI_COLORS = True`: Farbige Ausgabe
- `USE_EMOJI_ICONS = False`: Emojis oder Text

#### Top Drops Ticker
- `ENABLE_TOP_DROPS_TICKER = True`: Zeige größte Rückgänge
- `TOP_DROPS_INTERVAL_S = 60`: Alle 60 Sekunden
- `TOP_DROPS_LIMIT = 10`: Top 10 Drops

#### PnL Monitor
- `PNL_MONITOR_INTERVAL_S = 120`: Alle 2 Minuten
- `SHOW_INDIVIDUAL_POSITIONS = True`: Einzelne Positionen zeigen
- `SHOW_TOTAL_SUMMARY = True`: Gesamtsumme zeigen

---

## Troubleshooting

### Bot macht keine Trades

**1. Prüfe GLOBAL_TRADING**
```python
GLOBAL_TRADING = True  # Muss True sein!
```

**2. Prüfe Guards (deaktiviere alle)**
```python
USE_SMA_GUARD = False
USE_VOLUME_GUARD = False
USE_SPREAD_GUARD = False
USE_VOL_SIGMA_GUARD = False
USE_BTC_FILTER = False
USE_FALLING_COINS_FILTER = False
USE_ML_GATEKEEPER = False
```

**3. Prüfe Budget**
```python
# Logs checken:
# "INSUFFICIENT_BUDGET" → Budget zu niedrig
# "NO_TRADE_SLOT" → MAX_TRADES erreicht
```

**4. Prüfe DROP_TRIGGER_VALUE**
```python
# Zu niedriger Wert = zu seltene Triggers
DROP_TRIGGER_VALUE = 0.997  # Versuche 0.998 oder 0.999
```

---

### Bot kauft zu oft

**1. DROP_TRIGGER_VALUE erhöhen**
```python
DROP_TRIGGER_VALUE = 0.995  # Größerer Drop nötig
```

**2. COOLDOWN_MIN erhöhen**
```python
COOLDOWN_MIN = 30  # 30 Minuten Pause nach Verkauf
```

**3. Guards aktivieren**
```python
USE_VOLUME_GUARD = True
VOLUME_GUARD_FACTOR = 1.5  # Nur bei hohem Volumen kaufen
```

---

### Exits funktionieren nicht

**1. Prüfe NEVER_MARKET_SELLS**
```python
NEVER_MARKET_SELLS = False  # Erlaube Market-Orders
```

**2. EXIT_LADDER_BPS erweitern**
```python
EXIT_LADDER_BPS = [10, 30, 70, 120, 200]  # Mehr Stufen
```

**3. ALLOW_MARKET_FALLBACK_TTL aktivieren**
```python
ALLOW_MARKET_FALLBACK_TTL = True  # Notfall-Market-Order
```

---

### Position bleibt zu lange offen

**1. TRADE_TTL_MIN reduzieren**
```python
TRADE_TTL_MIN = 60  # 1 Stunde statt 2
```

**2. USE_TRAILING_STOP deaktivieren**
```python
USE_TRAILING_STOP = False  # Fester Stop-Loss
```

**3. TAKE_PROFIT_THRESHOLD senken**
```python
TAKE_PROFIT_THRESHOLD = 1.003  # +0.3% statt +0.5%
```

---

## Advanced Topics

### Relative Trailing System

Automatische Berechnung von Trailing-Werten aus TP/SL:

```python
# Aktivieren
USE_RELATIVE_TRAILING = True

# TP = 1.006 (+0.6%), SL = 0.990 (-1.0%)
# → tp_gap = 0.006, sl_gap = 0.010

# Trailing SL aktiviert bei 60% des TP-Wegs:
# TRAILING_STOP_ACTIVATION_PCT = 1.0 + (0.006 * 0.60) = 1.0036 (+0.36%)

# Trailing SL Distanz = 30% der SL-Lücke:
# TRAILING_STOP_DISTANCE_PCT = 1.0 - (0.010 * 0.30) = 0.997 (-0.3%)

# Vorteil: Trailing-Werte passen sich automatisch an TP/SL an!
```

### Custom Guards entwickeln

Eigene Guards in `services/` hinzufügen:

```python
def custom_momentum_guard(symbol, price_history):
    """Prüfe ob Momentum positiv ist"""
    if len(price_history) < 10:
        return False

    recent_change = (price_history[-1] - price_history[-10]) / price_history[-10]
    return recent_change > 0.001  # Mindestens +0.1% in letzten 10 Ticks
```

### Depth Sweep verstehen

**Problem:** Market-Orders haben unkontrollierten Preis (Slippage)

**Lösung:** Depth-Sweep simuliert Market-Order mit Limit-IOC:
1. Lese Top-20 des Orderbooks
2. Berechne durchschnittlichen Fill-Preis
3. Prüfe ob Slippage < MAX_SLIPPAGE_BPS
4. Platziere Limit-IOC Order zu diesem Preis
5. Bei Reject: Reprice und wiederhole (max 4x)

**Vorteil:** Kontrollierter Preis + schnelle Ausführung

### ML Gatekeeper Training

Trainiere eigene ML-Modelle für Trade-Vorhersage:

```python
# 1. Sammle Daten aus trade_history.csv
# 2. Feature Engineering (Drop-Size, Volume, Spread, ...)
# 3. Trainiere Binary Classifier (Profit/Loss)
# 4. Speichere Modell in models/
# 5. Aktiviere ML_GATEKEEPER

USE_ML_GATEKEEPER = True
ML_BUY_THRESHOLD = 0.70  # 70% Konfidenz für Kauf
```

### Exchange Trace Replay

**Exchange Tracing** protokolliert alle API-Calls für Replay/Testing:

```python
EXCHANGE_TRACE_ENABLED = True
# → sessions/<id>/logs/exchange_trace.jsonl

# Replay für Testing:
# 1. Lade exchange_trace.jsonl
# 2. Simuliere Bot-Lauf ohne echte API-Calls
# 3. Vergleiche Ergebnisse
```

---

## Konfiguration sichern

### Backup vor Änderungen

```bash
# Config sichern
cp config.py config_backup_$(date +%Y%m%d).py

# Session-Config wird automatisch gesichert:
# sessions/<timestamp>/config_backup.py
```

### Environment Variables (.env)

Sensible Daten in `.env` statt `config.py`:

```bash
# .env (NICHT in Git!)
TELEGRAM_BOT_TOKEN=your_bot_token
TELEGRAM_CHAT_ID=your_chat_id
BOT_LOG_LEVEL=INFO

# In config.py wird automatisch geladen:
LOG_LEVEL = os.getenv("BOT_LOG_LEVEL", LOG_LEVEL)
```

### Git Best Practices

```bash
# .gitignore sollte enthalten:
.env
config_backup_*.py
sessions/
*.json
*.csv
```

---

## Support & Weitere Hilfe

- **ARCHITECTURE.md** - Codebase-Struktur und Refactoring
- **Logs prüfen** - `sessions/<timestamp>/logs/bot_log_*.jsonl`
- **Config Validation** - Automatisch beim Import, siehe Fehler-Output
- **State Files** - `held_assets.json`, `drop_anchors.json` für Bot-Zustand

---

**Dokumentation erstellt:** 2025-10-11
**Config Version:** 1
