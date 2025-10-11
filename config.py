# config.py – Trading Bot Konfiguration
# ======================================
# ⚠️ WICHTIGE HINWEISE:
# - Abschnitt 1 enthält die Hauptparameter für einfache Anpassung
# - Potenzielle Konflikte sind mit ⚠️ markiert
# - Aliase für Rückwärtskompatibilität am Ende (nicht ändern!)

import os
import uuid
import shutil
from datetime import datetime, timezone

# =============================================================================
# ABSCHNITT 0: RUN IDENTITY & DIRECTORIES
# =============================================================================

# UTC als einzige Zeitquelle (verhindert Drift zwischen verschiedenen Formaten)
_now_utc = datetime.now(timezone.utc)
run_timestamp_utc = _now_utc.strftime('%Y-%m-%d_%H-%M-%S')
run_timestamp = _now_utc.strftime('%Y%m%d_%H%M%S')
run_timestamp_readable = run_timestamp_utc  # eine Quelle, ein Format (UTC)
run_id = str(uuid.uuid4())[:8]

# Base und Session Directories
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
SESSION_DIR_NAME = f"session_{run_timestamp}"
SESSION_DIR = os.path.join(BASE_DIR, "sessions", SESSION_DIR_NAME)

# Session-spezifische Verzeichnisse
LOG_DIR = os.path.join(SESSION_DIR, "logs")
STATE_DIR = os.path.join(SESSION_DIR, "state")
REPORTS_DIR = os.path.join(SESSION_DIR, "reports")
SNAPSHOTS_DIR = os.path.join(SESSION_DIR, "snapshots")

# WICHTIG: Keine Verzeichnis-Erstellung beim Import!
# Verzeichnisse werden in main.py/engine.py zur Laufzeit erstellt
# Dies verhindert Side-Effects beim Import für Tests, Tools, Linter, IDE-Index

# Optionale Versionierung/Migration
CONFIG_VERSION = 1
MIGRATIONS = {
    # künftige Umbenennungen hier abbilden, z.B. "OLD_KEY": "NEW_KEY"
}

# =============================================================================
# ABSCHNITT 1: HAUPTPARAMETER (für einfache Konfiguration)
# =============================================================================

# --- 1.1 MASTER SWITCHES ---
# ⚠️ ACHTUNG: GLOBAL_TRADING = False verhindert ALLE Trades!
# GLOBAL_TRADING ist der Hauptschalter für den Bot:
# - True  = Bot kann echte Trades ausführen (Live-Trading)
# - False = Bot läuft im Beobachtungsmodus (analysiert nur, kauft/verkauft nicht)
# Empfehlung für Anfänger: Erst mit False starten und beobachten!
GLOBAL_TRADING = True

# --- 1.2 EXIT STRATEGIE ---
# Diese Werte bestimmen, wann der Bot eine Position schließt:

# TAKE_PROFIT_THRESHOLD: Gewinnziel
# 1.018 = 101.8% vom Kaufpreis = +1.8% Gewinn
# Beispiel: Gekauft bei 100€ → Verkauf bei 101.80€
TAKE_PROFIT_THRESHOLD = 1.0030  # +0.3% Take Profit (eng für Testzwecke)

# STOP_LOSS_THRESHOLD: Maximaler akzeptierter Verlust
# 0.985 = 98.5% vom Kaufpreis = -1.5% Verlust
# Beispiel: Gekauft bei 100€ → Verkauf spätestens bei 98.50€
STOP_LOSS_THRESHOLD = 0.9980  # -0.2% Stop Loss (eng für Testzwecke)

# SWITCH_TO_SL_THRESHOLD: Umschaltpunkt der Exit-Strategie
# 0.992 = 99.2% vom Kaufpreis = -0.8% Verlust
# Wenn Preis unter diesen Wert fällt, fokussiert Bot auf Stop-Loss statt Take-Profit
SWITCH_TO_SL_THRESHOLD = 0.999  # bei -0.1% auf SL umschalten (eng für Testzwecke)

# SWITCH_TO_TP_THRESHOLD: Rück-Umschaltpunkt zu Take-Profit
# 1.0050 = 100.50% vom Kaufpreis = +0.50% Gewinn
# Wenn Preis wieder über diesen Wert steigt, wechselt Bot zurück zu Take-Profit
SWITCH_TO_TP_THRESHOLD = 1.0020  # bei +0.2% zurück zu TP (eng für Testzwecke)

# SWITCH_COOLDOWN_S: Minimale Wartezeit zwischen Umschaltungen
# 20 = Warte mindestens 20 Sekunden zwischen TP/SL Wechseln
# Verhindert Flip-Flop bei volatilen Märkten
SWITCH_COOLDOWN_S = 20

# ATR-basierte dynamische Exits (Fortgeschrittene Alternative)
# ATR = Average True Range, misst die Volatilität (Preisschwankungen)

# USE_ATR_BASED_EXITS: Umschalten zwischen zwei Exit-Methoden
# False = Nutze feste Prozentwerte von oben (einfacher, vorhersehbar)
# True  = Passe Exit-Levels an aktuelle Marktvolatilität an (adaptiv)
USE_ATR_BASED_EXITS = False

# ATR_PERIOD: Zeitraum für Volatilitätsberechnung (14 = letzte 14 Kerzen)
ATR_PERIOD = 14

# ATR_SL_MULTIPLIER: Stop-Loss = Kaufpreis - (ATR × 0.6)
# Kleinerer Wert = engerer Stop-Loss (weniger Risiko, aber mehr Fehlauslösungen)
ATR_SL_MULTIPLIER = 0.6

# ATR_TP_MULTIPLIER: Take-Profit = Kaufpreis + (ATR × 1.6)  
# Größerer Wert = höheres Gewinnziel (mehr Gewinn, aber seltener erreicht)
ATR_TP_MULTIPLIER = 1.6

# ATR_MIN_SAMPLES: Mindestanzahl Datenpunkte für ATR-Berechnung
ATR_MIN_SAMPLES = 15

# --- 1.3 ENTRY STRATEGIE ---
# Der Bot kauft, wenn der Preis stark gefallen ist ("Buy the Dip" Strategie)

# DROP_TRIGGER_VALUE: Wie tief muss der Preis fallen für ein Kaufsignal?
# 0.995 = 99.5% vom Hochpunkt = -0.5% Preisrückgang
# Beispiel: Coin war bei 100€ (Hoch) → Bot kauft wenn Preis auf 99.50€ fällt
# Kleinerer Wert = größerer Rückgang nötig (0.99 = -1%, 0.995 = -0.5%)
DROP_TRIGGER_VALUE = 0.995  # -0.5% Drop (für Testzwecke)

# --- V9_3 STYLE: Trigger, Lookback, Mode ---
LOOKBACK_S = 300             # 5m Impulsfenster (ok)
MODE = 2                     # Impulsmodus (ok)

# Stabilisierung / Entprellen (V9_3 aggressiver, aber kontrolliert)
CONFIRM_TICKS = 0          # sofort scharf (aggressiver)
HYSTERESIS_BPS = 0         # kein Hysteresis-Puffer (aggressiver)
DEBOUNCE_S = 3             # minimale Entprellung

# Ordermodus für Mode-2 (Impuls): IOC statt Post-Only
USE_IOC_FOR_MODE2 = True

# --- Marktdaten: robustes Orderbuch statt Basic-Fetch ---
USE_ROBUST_MARKET_FETCH = True

# DROP_TRIGGER_MODE: Referenzhoch für den Drop-Trigger
# Mode 1: Höchster Preis seit Bot-Start (einfach, aber kann veralten)
# Mode 2: Höchster Preis der letzten X Minuten (dynamisch, kurzsichtig)
# Mode 3: Der höhere Wert aus Mode 1 und 2 (Kombination)
# Mode 4: Wie Mode 1, aber Reset nach jedem abgeschlossenen Trade (empfohlen)
# Empfehlung: Mode 2 für Rolling-High (frische Marktstruktur)
DROP_TRIGGER_MODE = 1        # Rolling High als Referenz

# DROP_TRIGGER_LOOKBACK_MIN: Zeitfenster für Mode 2/3
# 5 = Betrachte die letzten 5 Minuten für Hochpunkt-Suche (Minimum)
# Größerer Wert = längerer Rückblick (träger, aber stabiler)
DROP_TRIGGER_LOOKBACK_MIN = 10

# Drop Anchor System: Speichert Hochpunkte dauerhaft (überlebt Bot-Neustarts)

# USE_DROP_ANCHOR: Hochpunkte in Datei speichern?
# True = Hochpunkte werden gespeichert und bei Neustart wiederhergestellt
# False = Hochpunkte nur im Arbeitsspeicher (gehen bei Neustart verloren)
# WICHTIG: Mode 4 braucht True oder BACKFILL_MINUTES > 0!
USE_DROP_ANCHOR = True

# ANCHOR_UPDATES_WHEN_FLAT: Soll Anker auch ohne Position steigen?
# True = Anker folgt neuen Hochs auch wenn keine Position offen ist
# False = Anker bleibt fix bis neue Position eröffnet wird
ANCHOR_UPDATES_WHEN_FLAT = True

# --- 1.4 POSITION MANAGEMENT ---
# Risikomanagement: Wie viel investiert der Bot?

# MAX_TRADES: Maximale Anzahl verschiedener Coins gleichzeitig
# 3 = Bot kann maximal 3 verschiedene Coins gleichzeitig halten
# Hilft Risiko zu streuen (Diversifikation)
MAX_TRADES = 3

# POSITION_SIZE_USDT: Wie viel USDT pro Kauf einsetzen
# 25.0 = Jeder Kauf verwendet 25 USDT (ca. 25 Dollar)
# Bei MAX_TRADES=3 → Maximales Risiko = 3 × 25 = 75 USDT
# Viele Märkte benötigen nach Rundung >16 USDT Notional.
# 25 USDT ist ein robuster Untergrenzwert; bei Bedarf weiter erhöhen.
POSITION_SIZE_USDT = 25.0

# --- Auto-Upsize bei knapper Rundung (MinNotional) ---
ALLOW_AUTO_SIZE_UP = True          # darf die Menge leicht erhöht werden?
MAX_AUTO_SIZE_UP_BPS = 25          # max. +25 bps (~0.25%) Notional-Erhöhung
MAX_AUTO_SIZE_UP_ABS_USDT = 0.30   # oder absolut gedeckelt, z.B. +0.30 USDT

# MAX_PER_SYMBOL_USD: Maximales Investment pro einzelnem Coin
# 60.0 = Maximal 60 USDT in einem Coin (Konzentrationslimit)
# Verhindert dass Bot alles in einen Coin steckt
MAX_PER_SYMBOL_USD = 60.0

# TRADE_TTL_MIN: Time-To-Live - Maximale Haltedauer in MINUTEN
# 120 = Nach 120 Minuten (2 Stunden) wird Position zwangsgeschlossen
# Schützt vor "Bag Holding" (zu lange an Verlustpositionen festhalten)
TRADE_TTL_MIN = 120

# COOLDOWN_MIN: Wartezeit nach Verkauf bevor Coin wieder gekauft wird in MINUTEN
# 15 = 15 Minuten Pause nach Verkauf (schnellere Re-Entries)
# 0 = Keine Wartezeit (Coin kann sofort wieder gekauft werden)
COOLDOWN_MIN = 15

# --- 1.5 GUARDS (Qualitätsfilter) ---
# Guards sind Sicherheitschecks die schlechte Trades verhindern
# ⚠️ ACHTUNG: Zu strikte Guards können ALLE Trades blockieren!

# SMA Guard: Simple Moving Average - Trendfilter
# TEST: komplett deaktiviert
USE_SMA_GUARD = False
# (Ratio bleibt ohne Wirkung, weil Guard off)
SMA_GUARD_MIN_RATIO = 0.992

# SMA_GUARD_WINDOW: Zeitraum für Durchschnittsberechnung
# 50 = Durchschnitt der letzten 50 Kerzen (50 Minuten bei 1-Min-Kerzen)
# Größerer Wert = langsamerer, stabilerer Trend
SMA_GUARD_WINDOW = 50

# Volume Guard: Handelsvolumen-Filter (Liquidität)
# TEST: komplett deaktiviert
USE_VOLUME_GUARD = False

# VOLUME_GUARD_WINDOW: Zeitraum für Volumen-Durchschnitt
# 15 = Durchschnitt der letzten 15 Minuten
VOLUME_GUARD_WINDOW = 15

# VOLUME_GUARD_FACTOR: Mindest-Volumen im Vergleich zum Durchschnitt
# 1.020 = Aktuelles Volumen muss 102% des Durchschnitts sein (+2% über Normal)
# Werte > 1.0 können zu viele Trades blockieren!
VOLUME_GUARD_FACTOR = 1.020

# MIN_24HUSD_VOLUME: Mindest-24h-Volumen in USDT
# 150000 = Mindestens 150.000 USDT Handelsvolumen in 24h (reduzierte Volumenhürde)
MIN_24HUSD_VOLUME = 150000        # ignoriert, weil Guard off

# Spread Guard: Prüft Differenz zwischen Kauf- und Verkaufspreis
# TEST: komplett deaktiviert
USE_SPREAD_GUARD = False

# GUARD_MAX_SPREAD_BPS: Maximaler erlaubter Spread
# 25 = Maximal 25 Basispunkte = 0.25% Differenz
# Beispiel: Bid=99.75€, Ask=100€ → Spread=0.25% → Trade erlaubt
# bps = Basispunkte (1 bps = 0.01%)
GUARD_MAX_SPREAD_BPS = 35               # ignoriert, weil Guard off

# Volatilitäts-Guard: Prüft ob genug Preisbewegung vorhanden ist
# TEST: komplett deaktiviert
USE_VOL_SIGMA_GUARD = False

# VOL_SIGMA_WINDOW: Zeitraum für Volatilitätsmessung
# 30 = Berechne Volatilität der letzten 30 Minuten
VOL_SIGMA_WINDOW = 30

# REQUIRE_VOL_SIGMA_BPS_MIN: Mindest-Volatilität für Trade
# 10 = Mindestens 10 bps (0.1%) Standardabweichung nötig
# Höherer Wert = Mehr Bewegung nötig (weniger Trades in ruhigen Phasen)
REQUIRE_VOL_SIGMA_BPS_MIN = 10

# --- 1.6 MAKRO-FILTER ---
# Marktweite Filter: Prüfen die Gesamtmarkt-Stimmung
# ⚠️ ACHTUNG: Diese Filter können bei Bärenmärkten ALLE Trades verhindern!

# Bitcoin-Filter: Bitcoin als Markt-Indikator
# TEST: komplett deaktiviert
USE_BTC_FILTER = False

# BTC_CHANGE_THRESHOLD: Wie stark darf BTC fallen?
# TEST: auf None gesetzt für komplette Deaktivierung
BTC_CHANGE_THRESHOLD = None    # None/0.0 ⇒ deaktiviert

# Falling-Coins-Filter: Prüft wie viele Coins fallen
# TEST: komplett deaktiviert
USE_FALLING_COINS_FILTER = False

# FALLING_COINS_THRESHOLD: Wie viele Coins dürfen maximal fallen?
# 0.55 = Maximal 55% der Coins dürfen im Minus sein
# Wenn mehr als 55% fallen → Markt zu schwach → keine Käufe
FALLING_COINS_THRESHOLD = 0.55

# BTC-Trend-Guard vollständig aus (statisch, linter-freundlich)
USE_BTC_TREND_GUARD = False

# --- 1.7 MACHINE LEARNING ---
# KI-basierte Trade-Vorhersage (Fortgeschritten)
# ⚠️ ACHTUNG: USE_ML_GATEKEEPER = True ohne trainierte Modelle blockiert ALLE Trades!

# USE_ML_GATEKEEPER: Soll KI-Modell Trades genehmigen?
# TEST: sicherheitshalber aus, damit nichts blockiert
USE_ML_GATEKEEPER = False

# ML_BUY_THRESHOLD: Mindest-Konfidenz der KI für Kauf
# 0.65 = KI muss zu 65% sicher sein dass Trade profitabel wird
# Höherer Wert = Weniger aber sicherere Trades
ML_BUY_THRESHOLD = 0.65

# MODEL_DIR: Ordner mit trainierten KI-Modellen
# "models" = Suche Modelle im Unterordner "models"
MODEL_DIR = "models"

# =============================================================================
# ABSCHNITT 2: RISIKO & BUDGET MANAGEMENT
# =============================================================================

# Portfolio-Verhalten beim Start
# RESET_PORTFOLIO_ON_START: Was passiert mit bestehenden Positionen beim Start?
# True = Verkaufe alle Coins beim Bot-Start (sauberer Neustart)
# False = Behalte bestehende Positionen (Fortsetzung)
RESET_PORTFOLIO_ON_START = True

# Budget und Reserven
# ⚠️ ACHTUNG: Bot startet nicht wenn Budget < SAFE_MIN_BUDGET!

# SAFE_MIN_BUDGET: Mindest-Guthaben zum Starten
# 10.0 = Bot benötigt mindestens 10 USDT um zu starten
# Schützt vor Trading mit zu wenig Kapital
SAFE_MIN_BUDGET = 10.0

# CASH_RESERVE_USDT: Sicherheitsreserve die nie verwendet wird
# 0.0 = Keine Reserve (alles verfügbare Geld kann gehandelt werden)
# 50.0 = 50 USDT bleiben immer unangetastet (Notgroschen)
CASH_RESERVE_USDT = 0.0

# ON_INSUFFICIENT_BUDGET: Was tun bei zu wenig Budget?
# "observe" = Bot läuft weiter aber nur beobachtend (kein Trading)
# "wait" = Bot wartet bis genügend Budget vorhanden ist
# "stop" = Bot stoppt komplett
ON_INSUFFICIENT_BUDGET = "wait"

# Circuit Breaker: Notbremse bei Verlusten
# MAX_LOSSES_IN_ROW: Anzahl Verlust-Trades bis Bot pausiert
# 5 = Nach 5 Verlusten in Folge stoppt Bot automatisch
# Schützt vor Verlustspiralen in schlechten Marktphasen
MAX_LOSSES_IN_ROW = 5

# CB_WINDOW_MIN: Zeitfenster für Verlust-Zählung
# 60 = Zähle nur Verluste der letzten 60 Minuten
# Verluste außerhalb dieses Fensters werden ignoriert
CB_WINDOW_MIN = 60

# Tages-Limits (Fortgeschritten, optional)
# MAX_DAILY_DRAWDOWN_PCT: Maximaler Tagesverlust
# 0.08 = Stoppe wenn 8% des Startkapitals verloren wurden
# Beispiel: Start mit 1000 USDT → Stop bei 920 USDT (-80 USDT)
MAX_DAILY_DRAWDOWN_PCT = 0.08

# MAX_TRADES_PER_DAY: Maximale Trades pro Tag
# 120 = Maximal 120 Trades in 24 Stunden
# Verhindert Overtrading und schützt vor Gebühren
MAX_TRADES_PER_DAY = 120

# Gebühren und Kosten
# FEE_RATE: Handelsgebühr der Börse
# 0.001 = 0.1% pro Trade (typisch für MEXC und andere Börsen)
# Beispiel: 100 USDT Trade → 0.10 USDT Gebühr
FEE_RATE = 0.001

# SELL_SLIPPAGE_PCT: Sicherheitspuffer für Verkaufspreise
# 0.001 = 0.1% unter Marktpreis anbieten
# Erhöht Chance dass Verkauf durchgeht (besonders bei illiquiden Coins)
SELL_SLIPPAGE_PCT = 0.001

# =============================================================================
# ABSCHNITT 3: ORDER EXECUTION
# =============================================================================

# Buy Execution: Wie kauft der Bot?

# BUY_MODE: Kaufstrategie
# "PREDICTIVE" = Kaufe leicht über Marktpreis (schneller, teurer)
# "ESCALATION" = Stufenweise erhöhen bis Kauf klappt
# "CLASSIC" = Einfache Limit-Order zum Marktpreis
BUY_MODE = "PREDICTIVE"

# PREDICTIVE_BUY_ZONE_BPS: Aufschlag für schnellen Kauf (Step-1)
# 5 = 5 Basispunkte = 0.05% über aktuellem Preis
# Beispiel: Marktpreis 100€ → Kauforder bei 100.05€
# Balanciert: schnell füllen aber nicht übertreiben
PREDICTIVE_BUY_ZONE_BPS = 3

# USE_PREDICTIVE_BUYS: Predictive-Mode aktivieren?
# True = Nutze Aufschlag für schnellere Ausführung
USE_PREDICTIVE_BUYS = True

# Buy Escalation: Stufenweise Preiserhöhung für Kauf

# BUY_LIMIT_PREMIUM_BPS: Standard-Aufschlag für Limit-Orders
# 8 = 8 bps = 0.08% über Marktpreis
BUY_LIMIT_PREMIUM_BPS = 8

# BUY_GTC_WAIT_SECS: Wartezeit für Good-Till-Cancel Orders
# 4.0 = Warte 4 Sekunden auf Ausführung
BUY_GTC_WAIT_SECS = 4.0

# BUY_GTC_MIN_PARTIAL: Mindest-Teilausführung akzeptieren
# 0.25 = Akzeptiere wenn mindestens 25% ausgeführt wurden
BUY_GTC_MIN_PARTIAL = 0.25

# BUY_GTC_WAIT_DYNAMIC: Dynamische Wartezeit?
# True = Passe Wartezeit an Marktbedingungen an
BUY_GTC_WAIT_DYNAMIC = True

# USE_BUY_ESCALATION: Eskalationsstufen nutzen?
# True = Erhöhe Preis stufenweise bis Kauf klappt
USE_BUY_ESCALATION = True

# BUY_ESCALATION_STEPS: Balancierte 3-Stufen-Leiter (5→10→15 bps)
# IOC = Immediate-Or-Cancel (sofort oder abbrechen)
# Stufe 1: +0.05% Aufschlag (schnell füllen)
# Stufe 2: +0.10% Aufschlag (Mikro-Gap Puffer)
# Stufe 3: +0.15% Aufschlag (Hard Cap, sauber gedeckelt)
BUY_ESCALATION_STEPS = [
    {"tif": "IOC", "premium_bps": 10, "max_attempts": 1},
    {"tif": "IOC", "premium_bps": 30, "max_attempts": 1},
    {"tif": "IOC", "premium_bps": 60, "max_attempts": 1},
]

# IOC_ORDER_TTL_MS: Timeout für IOC-Orders
# 600 = 600ms ausreichend Zeit je Stufe für V9_3-Style
IOC_ORDER_TTL_MS = 600

# Sell Execution: Wie verkauft der Bot?
# ⚠️ ACHTUNG: NEVER_MARKET_SELLS mit zu engen EXIT_LADDER_BPS kann Exits verhindern!

# NEVER_MARKET_SELLS: Market-Orders verbieten?
# True = Nutze nur Limit-Orders (kontrollierter, aber langsamer)
# False = Erlaube Market-Orders (schneller, aber unkontrollierter Preis)
NEVER_MARKET_SELLS = False

# EXIT_LADDER_BPS: Preisstufen für Verkaufsversuche
# [10, 30, 70, 120] = Versuche mit -0.1%, -0.3%, -0.7%, -1.2% Abschlag
# Bot versucht erst mit kleinem Abschlag, dann immer größer
EXIT_LADDER_BPS = [10, 30, 70, 120]

# EXIT_ESCALATION_BPS: Alternative Eskalationsstufen
# [0, 10] = Erst zum Marktpreis, dann -0.1% darunter
EXIT_ESCALATION_BPS = [0, 10]

# ALLOW_MARKET_FALLBACK_TTL: Market-Order bei Zeitablauf?
# False = Kein Market-Fallback bei TTL (nur Limit-IOC Leiter)
# Konservative Policy für kontrollierte Exits
ALLOW_MARKET_FALLBACK_TTL = False

# Order Management: Verwaltung offener Orders

# BUY_ORDER_TIMEOUT_MINUTES: Timeout für Kauforders
# 3 = Breche Kauforder nach 3 Minuten ab wenn nicht ausgeführt
# Verhindert dass Orders ewig hängen bleiben
BUY_ORDER_TIMEOUT_MINUTES = 3

# BUY_ORDER_CANCEL_THRESHOLD_PCT: Abbruch bei Preisanstieg
# 1.03 = Breche ab wenn Preis über 103% des Order-Preises steigt
# Schützt vor Kauf zu ungünstigen Preisen
BUY_ORDER_CANCEL_THRESHOLD_PCT = 1.03

# STALE_ORDER_CLEANUP_INTERVAL: Prüfintervall für alte Orders
# 60 = Prüfe alle 60 Sekunden auf veraltete Orders
STALE_ORDER_CLEANUP_INTERVAL = 60

# STALE_ORDER_MAX_AGE: Maximales Alter für Orders
# 60 = Orders älter als 60 Minuten werden abgebrochen
# Aufräumen von vergessenen/hängenden Orders
STALE_ORDER_MAX_AGE = 60

# =============================================================================
# ABSCHNITT 4: TRAILING & ADVANCED EXITS
# =============================================================================

# Trailing Stop Loss: Nachlaufender Stop-Loss (Fortgeschritten)
# USE_TRAILING_STOP: Aktiviere nachlaufenden Stop-Loss?
# False = Fester Stop-Loss (einfacher)
# True = Stop-Loss folgt dem Preis nach oben (sichert Gewinne)
USE_TRAILING_STOP = True

# TRAILING_STOP_ACTIVATION_PCT: Ab wann aktiviert sich der Trailing Stop?
# 1.0010 = Aktiviere wenn Preis 100.1% vom Kaufpreis erreicht (+0.1% Gewinn)
# Erst wenn dieser Gewinn erreicht ist, beginnt der Stop nachzulaufen
TRAILING_STOP_ACTIVATION_PCT = 1.001  # bei +0.1% aktivieren (eng für Testzwecke)

# TRAILING_STOP_DISTANCE_PCT: Abstand des Stops vom Höchstpreis
# 0.9980 = Stop liegt bei 99.80% vom erreichten Hoch (-0.20%)
# Beispiel: Hoch bei 101€ → Stop bei 100.80€
TRAILING_STOP_DISTANCE_PCT = 0.999  # nur 0.1% Abstand (eng für Testzwecke)

# Trailing Take Profit: Nachlaufende Gewinnmitnahme
# USE_TRAILING_TP: Aktiviere nachlaufende Gewinnmitnahme?
# False = Feste Gewinnziele (einfacher)
# True = Gewinnziel steigt mit dem Preis (mehr Gewinnpotential)
USE_TRAILING_TP = False

# TRAILING_TP_ACTIVATION_PCT: Ab wann aktiviert sich Trailing TP?
# 1.0040 = Aktiviere bei 100.4% vom Kaufpreis (+0.4% Gewinn)
TRAILING_TP_ACTIVATION_PCT = 1.0040

# TRAILING_TP_STEP_BP: Schrittweite für TP-Erhöhung
# 5 = Erhöhe TP um 5 bps (0.05%) bei jedem neuen Hoch
TRAILING_TP_STEP_BP = 5

# TRAILING_TP_UNDER_HIGH_BP: Abstand des TP vom Hoch
# 10 = TP liegt 10 bps (0.10%) unter dem Hoch
TRAILING_TP_UNDER_HIGH_BP = 10

# =============================================================================
# ABSCHNITT 4.1: RELATIVE TRAILING INPUTS (OPT-IN)
# =============================================================================
# Wenn aktiv, werden TRAILING_* Werte automatisch aus TP/SL abgeleitet.
USE_RELATIVE_TRAILING = True   # auf False setzen, wenn du absolute Werte nutzen willst

# --- Steuergrößen (Fraktionen) ---
# Ab wann TSL aktiv wird: Anteil des Weges Entry -> TP (z.B. 0.60 = 60% des TP-Wegs)
TSL_ACTIVATE_FRAC_OF_TP = 0.60

# TSL-Abstand: Anteil der SL-Lücke (Entry → fester SL). 0.30 = 30% der SL-Lücke
TSL_DISTANCE_FRAC_OF_SL_GAP = 0.30

# Trailing-TP: Aktivierung als Anteil des TP-Wegs
TTP_ACTIVATE_FRAC_OF_TP = 0.60

# TP liegt unter Hoch um einen Anteil der TP-Breite (in bp abgeleitet), z.B. 0.20 = 20%
TTP_UNDER_HIGH_FRAC_OF_TP = 0.20

# Wie viele Schritte bis zum vollen TP-Weg? → wird in STEP_BPS übersetzt
TTP_STEPS_PER_TP = 6

def _clamp(x, lo, hi):
    return max(lo, min(hi, x))

def _derive_trailing_from_relatives():
    global TRAILING_STOP_ACTIVATION_PCT, TRAILING_STOP_DISTANCE_PCT
    global TRAILING_TP_ACTIVATION_PCT, TRAILING_TP_UNDER_HIGH_BP, TRAILING_TP_STEP_BP

    # TP/SL-Breiten relativ zum Entry
    tp_gap = max(0.0, TAKE_PROFIT_THRESHOLD - 1.0)   # z.B. 0.006 bei +0.60%
    sl_gap = max(0.0, 1.0 - STOP_LOSS_THRESHOLD)     # z.B. 0.010 bei -1.00%

    # 1) Trailing SL: Aktivierung relativ zum TP-Weg
    act_frac = _clamp(TSL_ACTIVATE_FRAC_OF_TP, 0.0, 1.0)
    TRAILING_STOP_ACTIVATION_PCT = 1.0 + tp_gap * act_frac

    # 2) Trailing SL: Distanz relativ zur SL-Lücke (nie besser als Worst-Case-SL)
    dist_frac = _clamp(TSL_DISTANCE_FRAC_OF_SL_GAP, 0.05, 1.0)
    TRAILING_STOP_DISTANCE_PCT = 1.0 - sl_gap * dist_frac
    TRAILING_STOP_DISTANCE_PCT = max(TRAILING_STOP_DISTANCE_PCT, STOP_LOSS_THRESHOLD)

    # 3) Trailing TP: Aktivierung relativ zum TP-Weg (nicht über festem TP)
    ttp_act_frac = _clamp(TTP_ACTIVATE_FRAC_OF_TP, 0.0, 1.0)
    TRAILING_TP_ACTIVATION_PCT = 1.0 + tp_gap * ttp_act_frac
    TRAILING_TP_ACTIVATION_PCT = min(TRAILING_TP_ACTIVATION_PCT, TAKE_PROFIT_THRESHOLD)

    # 4) Trailing TP: Unter-Hoch-Abstand & Step-Größe aus TP-Breite ableiten
    total_tp_bps = int(round(tp_gap * 10_000))  # z.B. 60 bps bei +0.60%
    under_high_frac = _clamp(TTP_UNDER_HIGH_FRAC_OF_TP, 0.05, 0.90)
    TRAILING_TP_UNDER_HIGH_BP = max(1, int(round(total_tp_bps * under_high_frac)))

    steps = max(1, int(TTP_STEPS_PER_TP))
    TRAILING_TP_STEP_BP = max(1, total_tp_bps // steps)

# Ableitung anwenden (muss vor den Alias-Zuweisungen passieren)
if USE_RELATIVE_TRAILING:
    _derive_trailing_from_relatives()

# Breakeven: Absicherung bei Gewinnzone
# BE_ACTIVATION_PCT: Ab wann auf Breakeven umschalten?
# None = Breakeven aus (aggressiver)
# Stop-Loss wird dann auf Kaufpreis gesetzt (kein Verlust mehr möglich)
BE_ACTIVATION_PCT = None

# =============================================================================
# ABSCHNITT 5: EXCHANGE & TECHNISCHE LIMITS
# =============================================================================

# Trading Universum: Welche Coins handelt der Bot?

# UNIVERSE_TOP_N_BY_VOL: Anzahl der Top-Coins nach Volumen
# 60 = Handle nur die 60 Coins mit höchstem Handelsvolumen
# Fokus auf liquide Coins (bessere Preise, weniger Manipulation)
UNIVERSE_TOP_N_BY_VOL = 72

# MIN_NOTIONAL_USDT: Mindest-Handelsvolumen eines Coins
# 5.0 = Coin muss mindestens 5 USDT Volumen haben
# Filtert Micro-Coins raus
MIN_NOTIONAL_USDT = 5.0

# EXCLUDE_SYMBOL_PREFIXES: Diese Coin-Typen ausschließen
# Leveraged Tokens sind riskant und unvorhersehbar
# BULL/BEAR = 2x gehebelt, 3L/3S = 3x gehebelt, UP/DOWN = variabel
EXCLUDE_SYMBOL_PREFIXES = ["BULL/", "BEAR/", "3L/", "3S/", "UP/", "DOWN/"]

# Exchange Limits: Technische Beschränkungen der Börse

# MIN_ORDER_VALUE: Mindest-Ordergröße der Börse
# 5.1 = Orders müssen mindestens 5.1 USDT wert sein
# MEXC Minimum ist 5.0, wir nehmen 5.1 als Sicherheitspuffer
MIN_ORDER_VALUE = 5.1

# MIN_ORDER_BUFFER: Sicherheitsfaktor für Mindestorder
# 1.02 = Addiere 2% Puffer zur Mindestgröße
# Verhindert Ablehnung durch minimale Preisschwankungen
MIN_ORDER_BUFFER = 0.005

# DUST_FACTOR: Faktor für Dust-Erkennung
# 0.9995 = 99.95% der Position (0.05% Toleranz)
# Kleine Reste werden als "Dust" (Staub) erkannt
DUST_FACTOR = 0.9995

# MAX_HISTORY_LEN: Maximale Länge der Preishistorie
# 1440 = Speichere maximal 1440 Datenpunkte (24h bei 1-Min-Kerzen)
# Begrenzt Speicherverbrauch
MAX_HISTORY_LEN = 1440

# Dust Sweeper: Automatische Bereinigung kleiner Reste

# DUST_SWEEP_ENABLED: Dust-Bereinigung aktivieren?
# True = Verkaufe automatisch kleine Coin-Reste
# False = Lasse kleine Reste liegen
# TEMPORÄR DEAKTIVIERT: Verhindert Windows TLS-Crashes durch fetch_tickers() Flut
DUST_SWEEP_ENABLED = False

# DUST_SWEEP_INTERVAL_MIN: Prüfintervall für Dust
# 30 = Prüfe alle 30 Minuten auf Dust
DUST_SWEEP_INTERVAL_MIN = 30

# DUST_MIN_COST_USD: Ab wann ist es Dust?
# 6.0 = Reste unter 6 USDT werden als Dust behandelt
# Alles darunter ist zu klein zum normalen Handeln
DUST_MIN_COST_USD = 6.0

# DUST_FORCE_MARKET_IOC: Sofort zum Marktpreis verkaufen?
# True = Verkaufe Dust sofort (IOC = Immediate-Or-Cancel)
# Dust soll schnell weg, Preis ist zweitrangig
DUST_FORCE_MARKET_IOC = True

# DUST_TARGET_QUOTE: In welche Währung konvertieren?
# "USDT" = Konvertiere allen Dust zu USDT (Stablecoin)
DUST_TARGET_QUOTE = "USDT"

# Settlement: Abwicklung und Bestätigung von Trades

# SETTLEMENT_TIMEOUT: Maximale Wartezeit auf Tradebestätigung
# 240 = Warte maximal 240 Sekunden (4 Minuten) auf Bestätigung
SETTLEMENT_TIMEOUT = 240

# SETTLEMENT_TOLERANCE: Toleranz für Mengenabweichungen
# 0.995 = Akzeptiere wenn 99.5% der Order ausgeführt wurde
# Kleine Abweichungen durch Rundung sind OK
SETTLEMENT_TOLERANCE = 0.995

# SETTLEMENT_CHECK_INTERVAL: Prüfintervall für Settlements
# 30 = Prüfe alle 30 Sekunden den Status
SETTLEMENT_CHECK_INTERVAL = 30

# SETTLEMENT_MAX_ATTEMPTS: Maximale Versuche für Settlement
# 8 = Versuche maximal 8 Mal die Abwicklung (240s / 30s = 8 Versuche)
SETTLEMENT_MAX_ATTEMPTS = 8

# SETTLEMENT_POLL_INTERVAL_S: Wartezeit zwischen Settlement-Prüfungen
# 20 = Prüfe alle 20 Sekunden (schnellere Bestätigung)
SETTLEMENT_POLL_INTERVAL_S = 20

# SAFETY_BUFFER_PCT: Sicherheitspuffer für Berechnungen
# 0.02 = 2% Puffer für Rundungsfehler
SAFETY_BUFFER_PCT = 0.02

# SAFETY_BUFFER_MIN: Minimaler Sicherheitspuffer
# 0.5 = Mindestens 0.5 USDT als Puffer
SAFETY_BUFFER_MIN = 0.5

# MIN_SLOT_USDT: Mindestgröße für Trading-Slots
# 5.0 = Mindestens 5 USDT pro Trade (verhindert Mikro-Trades)
MIN_SLOT_USDT = 5.0

# SKIP_TRADE_IF_EXIT_UNDER_MIN: Trade überspringen wenn Exit unter Minimum
# True = Verhindert Trades die später kein TP/SL setzen können
SKIP_TRADE_IF_EXIT_UNDER_MIN = True

# API Fehler-Handling: Umgang mit API-Problemen

# API_BLOCK_TTL_MINUTES: Sperrzeit nach API-Fehler
# 120 = Sperre fehlerhaften Coin für 120 Minuten (2 Stunden)
# Verhindert wiederholte Fehler beim gleichen Coin
API_BLOCK_TTL_MINUTES = 120

# PERMANENT_API_BLOCKLIST: Dauerhaft gesperrte Coins
# set() = Leere Liste (keine dauerhaften Sperren)
# Hier können problematische Coins eingetragen werden
# Beispiel: {"LUNA/USDT", "FTT/USDT"}
# ⚠️ Diese Symbole werden NIE gehandelt!
PERMANENT_API_BLOCKLIST = {"FLOW/USDT"}  # Temporär blockiert wegen schwacher Performance

# Retry Settings: Wiederholungsversuche

# RETRY_REDUCTION_PCT: Mengenreduzierung bei Wiederholung
# 0.97 = Reduziere Menge auf 97% bei jedem Retry
# Hilft wenn Order wegen Größe fehlschlägt
RETRY_REDUCTION_PCT = 0.97

# BACKFILL_MINUTES: Historie beim Start nachladen
# 0 = Keine Historie, Drops werden von Session-Start berechnet
# 90 = Lade 90 Minuten Historie für BTC 60m % Berechnung
BACKFILL_MINUTES = 90

# BACKFILL_TIMEFRAME: Zeitrahmen für Historie
# '1m' = 1-Minuten-Kerzen (Standard)
# Mögliche Werte: '1m', '5m', '15m', '1h'
BACKFILL_TIMEFRAME = '1m'

# =============================================================================
# ABSCHNITT 6: LOGGING & MONITORING
# =============================================================================

# LOG_LEVEL: Detailgrad der Log-Ausgaben
# "DEBUG" = Sehr detailliert (für Fehlersuche)
# "INFO" = Normal (empfohlen für Produktion)
# "WARNING" = Nur Warnungen und Fehler
# "ERROR" = Nur Fehler
LOG_LEVEL = "DEBUG"

# LOG_SCHEMA_VERSION: Version des Log-Formats
# 4 = Aktuelle Version (nicht ändern)
LOG_SCHEMA_VERSION = 4

# Terminal Transparency & Debug Features
# ENABLE_PRETTY_TRADE_LOGS: Schöne Trade-Log-Formatierung
ENABLE_PRETTY_TRADE_LOGS = True

# --- Console / Terminal Einstellungen ---
# CONSOLE_LEVEL: Separater Log-Level für Terminal (DEBUG zeigt Heartbeat-Details)
CONSOLE_LEVEL = "DEBUG"

# SHOW_EVENT_TYPE_IN_CONSOLE: Event-Types im Terminal anzeigen
SHOW_EVENT_TYPE_IN_CONSOLE = True

# SHOW_THREAD_NAME_IN_CONSOLE: Thread-Namen für Heartbeat-Diagnose anzeigen
SHOW_THREAD_NAME_IN_CONSOLE = True

# ENABLE_PNL_MONITOR: PnL-Monitoring aktivieren
ENABLE_PNL_MONITOR = True

# VERBOSE_GUARD_LOGS: Ausführliche Guard-Logs mit Summaries
VERBOSE_GUARD_LOGS = True  # LOG_GUARD_BLOCKS equivalent

# LOG_MAX_BYTES: Maximale Größe einer Log-Datei
# 50_000_000 = 50 MB pro Datei
# Bei Überschreitung wird neue Datei angelegt
LOG_MAX_BYTES = 50_000_000

# LOG_BACKUP_COUNT: Anzahl alter Log-Dateien behalten
# 5 = Behalte 5 alte Log-Dateien (dann löschen)
LOG_BACKUP_COUNT = 5

# WRITE_SNAPSHOTS: Regelmäßige Zustands-Schnappschüsse speichern?
# True = Speichere Snapshots für spätere Analyse
# False = Keine Snapshots (spart Speicherplatz)
WRITE_SNAPSHOTS = True

# Log-Dateien: Verschiedene Logs für verschiedene Zwecke

# LOG_FILE: Haupt-Log mit allen Bot-Aktivitäten
# Enthält: Trades, Entscheidungen, Fehler
LOG_FILE = os.path.join(LOG_DIR, f"bot_log_{run_timestamp_readable}.jsonl")

# EVENTS_LOG: Wichtige Ereignisse (strukturiert)
# Enthält: Käufe, Verkäufe, Starts, Stops
EVENTS_LOG = os.path.join(LOG_DIR, f"events_{run_timestamp}.jsonl")

# METRICS_LOG: Leistungsmetriken und Statistiken
# Enthält: Spreads, Volatilität, Latenz
METRICS_LOG = os.path.join(LOG_DIR, f"metrics_{run_timestamp}.jsonl")

# MEXC_ORDERS_LOG: Alle Order-Aktivitäten mit MEXC
# Enthält: Order-IDs, Statusänderungen, Füllungen
MEXC_ORDERS_LOG = os.path.join(LOG_DIR, f"mexc_orders_{run_timestamp}.jsonl")

# AUDIT_EVENTS_LOG: Prüfprotokoll für Compliance
# Enthält: Kritische Entscheidungen, Änderungen
AUDIT_EVENTS_LOG = os.path.join(LOG_DIR, f"audit_events_{run_timestamp}.jsonl")

# --- DropTrigger Minute Audit ---
ENABLE_DROP_TRIGGER_MINUTELY = True        # Ein/Aus
DROP_AUDIT_INTERVAL_S = 60                 # 1x pro Minute (läuft über den Top-Drops-Ticker)
DROP_AUDIT_LOG = os.path.join(LOG_DIR, f"drop_trigger_{run_timestamp}.jsonl")  # LOG_TRIGGER_AUDIT equivalent

# =============================================================================
# ABSCHNITT: ADAPTIVE DEBUG CONFIGURATION
# =============================================================================

# DEBUG_MODE: 3-Stufen Logging Verbosity
# FULL = Maximales Debug (alle OHLCV, alle Metrics, alle Events)
# TRADING = Trading-fokussiert (nur trading-relevante Events, Sampling bei Market Data)
# MINIMAL = Nur Kritisches (Errors, Trades, wichtige Entscheidungen)
DEBUG_MODE = "TRADING"

# AUTO_ESCALATE: Automatischer Switch zu FULL bei Problemen
DEBUG_AUTO_ESCALATE = True

# STARTUP_FULL_MINUTES: Zeit in FULL-Mode nach Bot-Start (für initiale Verifikation)
DEBUG_STARTUP_FULL_MINUTES = 30

# POST_TRADE_FULL_MINUTES: Zeit in FULL-Mode nach Trades (für Trade-Debugging)
DEBUG_POST_TRADE_FULL_MINUTES = 5

# MARKET_DATA_SAMPLING: Bei TRADING-Mode, logge nur jeden N-ten Market-Data-Event
DEBUG_MARKET_DATA_SAMPLING = 10

# PERFORMANCE_AGGREGATION_SECONDS: Aggregiere Performance-Metrics über X Sekunden
DEBUG_PERFORMANCE_AGGREGATION_SECONDS = 60

# ENABLE_LOG_COMPRESSION: Komprimiere abgeschlossene Log-Files
DEBUG_ENABLE_LOG_COMPRESSION = True

# LOG_RETENTION_DAYS: Behalte Logs für X Tage (danach automatisch löschen)
DEBUG_LOG_RETENTION_DAYS = 30

# SNAPSHOTS_PARQUET: Komprimierte Zustands-Snapshots
# Format: Parquet (effizient für große Datenmengen)
SNAPSHOTS_PARQUET = os.path.join(SNAPSHOTS_DIR, f"snapshots_{run_timestamp}.parquet")

# RUN_SUMMARY_JSON: Zusammenfassung des Bot-Laufs
# Enthält: Statistiken, Gewinn/Verlust, Fehler
RUN_SUMMARY_JSON = os.path.join(REPORTS_DIR, f"run_summary_{run_timestamp}.json")

# RECONCILE_REPORT: Abgleichsbericht Portfolio vs Exchange
# Zeigt Differenzen zwischen erwartet und tatsächlich
RECONCILE_REPORT = os.path.join(REPORTS_DIR, f"reconcile_{run_timestamp}.json")

# Monitoring: Überwachung der Bot-Performance

# HEARTBEAT_INTERVAL_S: Lebenszeichen-Intervall
# 60 = Sende alle 60 Sekunden "Ich lebe noch" Signal
# Hilft bei Überwachung ob Bot noch läuft
HEARTBEAT_INTERVAL_S = 60

# METRIC_DECISION_LATENCY: Entscheidungszeit messen?
# True = Messe wie lange Bot für Entscheidungen braucht
METRIC_DECISION_LATENCY = True

# METRIC_OHLCV_FETCH_MS: API-Abrufzeit messen?
# True = Messe wie schnell Preisdaten kommen
METRIC_OHLCV_FETCH_MS = True

# METRIC_API_RETRY_COUNT: API-Wiederholungen zählen?
# True = Zähle wie oft API-Calls wiederholt werden müssen
METRIC_API_RETRY_COUNT = True

# EMIT_DECISION_EVAL: Entscheidungsgründe loggen?
# True = Speichere warum Bot kauft/nicht kauft
EMIT_DECISION_EVAL = True

# EMIT_GUARD_BLOCK_REASON: Guard-Blockierungen loggen?
# True = Speichere warum Guards Trades blockieren
EMIT_GUARD_BLOCK_REASON = True

# EMIT_EXIT_REASON: Exit-Gründe loggen?
# True = Speichere warum Positionen geschlossen werden
EMIT_EXIT_REASON = True

# USE_CLIENT_ORDER_ID: Eigene Order-IDs verwenden?
# True = Verwende eigene IDs für besseres Tracking
USE_CLIENT_ORDER_ID = True

# Snapshot Settings: Einstellungen für Zustands-Schnappschüsse

# SNAPSHOT_CHUNK_ROWS: Zeilen pro Snapshot-Chunk
# 1500 = Schreibe 1500 Zeilen auf einmal
# Kleinerer Wert = Weniger Speicherverbrauch
SNAPSHOT_CHUNK_ROWS = 1500

# SNAPSHOT_WRITE_INTERVAL_S: Snapshot-Intervall
# 60 = Erstelle alle 60 Sekunden einen Snapshot
SNAPSHOT_WRITE_INTERVAL_S = 60

# Order Update Settings: Verwaltung von Order-Updates

# ORDER_UPDATE_MIN_INTERVAL_S: Mindest-Update-Intervall
# 60 = Update Orders maximal alle 60 Sekunden
# Verhindert zu häufige Updates
ORDER_UPDATE_MIN_INTERVAL_S = 3

# ORDER_UPDATE_MIN_DELTA_FILLED: Mindest-Änderung für Update
# 1e-8 = 0.00000001 (sehr kleine Änderungen ignorieren)
ORDER_UPDATE_MIN_DELTA_FILLED = 1e-8

# ACTIVE_ORDER_SYNC_INTERVAL_S: Sync-Intervall für aktive Orders
# 60 = Synchronisiere aktive Orders alle 60 Sekunden
ACTIVE_ORDER_SYNC_INTERVAL_S = 60

# ACTIVE_ORDER_SYNC_JITTER_S: Zufällige Verzögerung
# 5 = Füge 0-5 Sekunden Zufall hinzu (verhindert Patterns)
ACTIVE_ORDER_SYNC_JITTER_S = 5

# IOC Settings: Immediate-Or-Cancel Order-Einstellungen

# IOC_SELL_BUFFER_PCT: Preispuffer für IOC-Verkäufe in PROZENTPUNKTEN
# Beispiel: 0.10 = 0.10% unter Marktpreis, 0.50 = 0.50% unter Marktpreis
# WICHTIG: Werte sind bereits in Prozent! (0.10 = 0.10%, NICHT 10%)
IOC_SELL_BUFFER_PCT = 0.10

# IOC_TIME_IN_FORCE: Order-Typ für IOC
# "IOC" = Immediate-Or-Cancel (sofort oder abbrechen)
# Order wird sofort ausgeführt oder gelöscht
IOC_TIME_IN_FORCE = "IOC"

# IOC_PRICE_BUFFERS_BPS: Preisstufen für IOC-Versuche
# [5, 12, 20, 35, 60, 90] = Versuche mit verschiedenen Abschlägen
# Erste Stufe: -0.05%, letzte Stufe: -0.90%
IOC_PRICE_BUFFERS_BPS = [5, 12, 20, 35, 60, 90]

# IOC_RETRY_SLEEP_S: Pause zwischen IOC-Versuchen
# 0.4 = Warte 0.4 Sekunden zwischen Versuchen
IOC_RETRY_SLEEP_S = 0.4

# POST_ONLY_REST_TTL_S: Lebensdauer für Post-Only-Rest-Orders
# 8 = Nach 8 Sekunden werden nicht-gefüllte Post-Only-Orders gecancelt
POST_ONLY_REST_TTL_S = 8

# EXIT_IOC_TTL_MS: Timeout für Exit-IOC-Orders in Millisekunden
# 500 = Exit-IOC-Orders timeout nach 500ms für schnelle Exits
EXIT_IOC_TTL_MS = 500

# POST_ONLY_REST_TTL_S: Timeout für Post-Only Orders
# 8 = Warte maximal 8 Sekunden auf Ausführung
# Post-Only = Order wird nur ins Orderbuch gestellt (Maker)
POST_ONLY_REST_TTL_S = 8

# POST_ONLY_undershoot: Veraltet (nicht mehr verwendet)
POST_ONLY_undershoot = None  # deprecated alias placeholder

# POST_ONLY_UNDERSHOOT_BPS: Abschlag für Post-Only
# 3 = 3 bps = 0.03% unter Marktpreis
# Stellt sicher dass Order im Buch landet
POST_ONLY_UNDERSHOOT_BPS = 3

# EXIT_LADDER_SLEEP_MS: Pause zwischen Exit-Stufen
# 0 = Keine Pause (so schnell wie möglich)
EXIT_LADDER_SLEEP_MS = 0

# EXIT_IOC_TTL_MS: Timeout für Exit-IOC Orders
# 500 = Warte maximal 500ms (0.5 Sekunden)
EXIT_IOC_TTL_MS = 500

# =============================================================================
# ABSCHNITT 7: STATE FILES & PERSISTENCE
# =============================================================================

# STATE_FILE_HELD: Speichert gehaltene Positionen
# Enthält: Alle Coins die Bot besitzt mit Kaufpreisen
# Überlebt Neustarts (Persistenz)
STATE_FILE_HELD = os.path.join(BASE_DIR, "held_assets.json")

# STATE_FILE_OPEN_BUYS: Speichert offene Kauforders
# Enthält: Alle noch nicht ausgeführten Kauforders
# Wird nach Neustart geprüft und bereinigt
STATE_FILE_OPEN_BUYS = os.path.join(BASE_DIR, "open_buy_orders.json")

# HISTORY_FILE: Trade-Historie als CSV
# Enthält: Alle abgeschlossenen Trades für Analyse
# Format: CSV für Excel/Sheets Import
HISTORY_FILE = os.path.join(BASE_DIR, "trade_history.csv")

# DROP_ANCHORS_FILE: Speichert Hochpunkte pro Coin
# Enthält: Höchstpreise als Referenz für Drop-Trigger
# Wichtig für Mode 4 (Reset nach Trade)
DROP_ANCHORS_FILE = os.path.join(BASE_DIR, "drop_anchors.json")

# CONFIG_BACKUP_PATH: Backup dieser Config-Datei
# Wird bei Bot-Start erstellt für Nachvollziehbarkeit
# Zeigt exakte Einstellungen des Bot-Laufs
CONFIG_BACKUP_PATH = os.path.join(SESSION_DIR, "config_backup.py")

# =============================================================================
# ABSCHNITT 7.5: SIMULATION / TRACING
# =============================================================================

# === Exchange Tracing System ===
# Protokolliert jeden Exchange-Call als JSONL für Replay/Testing
EXCHANGE_TRACE_ENABLED = True              # Tracer aktivieren/deaktivieren
EXCHANGE_TRACE_PATH = None                 # None = auto unter sessions/<id>/logs/exchange_trace.jsonl
EXCHANGE_TRACE_ORDERBOOK_LEVELS = 10       # Top-N Bids/Asks, die mitgeloggt werden
EXCHANGE_TRACE_SCRUB_IDS = True            # OrderIDs/ClientIDs anonymisieren (Hash)
EXCHANGE_TRACE_MAX_ARGLEN = 2000           # sehr lange Payloads einkürzen (Sicherheit/Performance)

# =============================================================================
# TEST-SETTINGS (OVERRIDES)
# =============================================================================

# --- Positionsgröße & Slot-Deckel ---
POSITION_SIZE_USDT = 25.0        # >=25 USDT → verhindert Sizing-Blocks nach Rundung
MAX_TRADES = 3                    # Deckel auf 3 gleichzeitige Trades (für 2 → 2 setzen)
COOLDOWN_MIN = 0                  # kein Cooldown für schnelle Tests
ALLOW_DUPLICATE_COINS = False     # keine doppelten Positionen im selben Symbol

# --- Drop-Trigger (Entry) → viele Signale, aber kontrolliert ---
DROP_TRIGGER_VALUE = 0.997        # ≈ -0.3% unter Referenzhoch (aggressiv)
DROP_TRIGGER_MODE = 2             # Rolling High als Referenz
DROP_TRIGGER_LOOKBACK_MIN = 10    # kurzes Fenster → reaktiv

# --- Impuls-/Signallogik (Timing) ---
MODE = 2                          # Rolling-Impulse + IOC
LOOKBACK_S = 180                  # 3 Minuten Impulsfenster → reaktiver als 5m
DEBOUNCE_S = 1                    # minimale Entprellung → mehr Auslösungen
CONFIRM_TICKS = 0                 # keine zusätzliche Bestätigung
HYSTERESIS_BPS = 0                # kein Puffer um die Schwelle
USE_IOC_FOR_MODE2 = True          # sofort füllen oder verwerfen

# --- Exits (schnelles Rein/Raus im Test) ---
TAKE_PROFIT_THRESHOLD = 1.003     # +0.3% Take Profit
STOP_LOSS_THRESHOLD = 0.998       # -0.2% Stop Loss

# --- Guards (für Test KOMPLETT AUS) ---
USE_SMA_GUARD = False
SMA_GUARD_MIN_RATIO = 0.994       # ohne Wirkung, Guard aus
USE_SPREAD_GUARD = False
GUARD_MAX_SPREAD_BPS = 35         # ohne Wirkung, Guard aus
USE_VOLUME_GUARD = False
MIN_24HUSD_VOLUME = 150000        # ohne Wirkung, Guard aus
USE_VOL_SIGMA_GUARD = False
USE_FALLING_COINS_FILTER = False
USE_BTC_TREND_GUARD = False
BTC_CHANGE_THRESHOLD = None
USE_ML_GATEKEEPER = False

# --- Sichtbarkeit im Heartbeat / Debug ---
TOP_DROPS_WITHIN_BPS_OF_TRIGGER = 300   # zeigt Kandidaten bis 3.00 pp vom Trigger

# --- Exchange Tracer (für Analyse) ---
EXCHANGE_TRACE_ENABLED = True
EXCHANGE_TRACE_PATH = None              # None = Session-Ordner
EXCHANGE_TRACE_ORDERBOOK_LEVELS = 10
EXCHANGE_TRACE_SCRUB_IDS = True
EXCHANGE_TRACE_MAX_ARGLEN = 2000

# --- Thread-Kontrolle (Services wieder aktiviert) ---
ENABLE_MEMORY_MONITORING = True        # MemoryManager an
ENABLE_TELEGRAM_COMMANDS = True        # TelegramCommands an
ENABLE_HEARTBEAT_TELEMETRY = False     # HeartbeatTelemetry bleibt aus (nicht verwendet)

# --- Concurrency-Limits (TLS-Stabilität) ---
MAX_PARALLEL_UPDATES = 1               # Windows + TLS mögen kein Thread-Gewitter
HTTP_SLOTS_LIMIT = 6                   # Max gleichzeitige TLS-Handshakes

# =============================================================================
# ABSCHNITT 8: ERWEITERTE EINSTELLUNGEN
# =============================================================================
# (ALLOW_DUPLICATE_COINS bereits in TEST-SETTINGS überschrieben)

# TICKER_THREADPOOL_SIZE: Anzahl paralleler Preis-Abrufe
# 6 = Nutze 6 Threads für schnellere API-Abfragen
# Höherer Wert = Schneller aber mehr Last
TICKER_THREADPOOL_SIZE = 6

# SYMBOL_MIN_COST_OVERRIDE: Spezielle Mindestgrößen
# Manche Coins haben abweichende Mindest-Ordergrößen
# "OKB/USDT": 10.0 = OKB benötigt mindestens 10 USDT
SYMBOL_MIN_COST_OVERRIDE = {
    "OKB/USDT": 10.0,
}

# Weitere Runtime-Konstanten (Fortgeschritten)

# MAX_POSITION_SIZE_USD: Absolute Obergrenze pro Position
# 1000 = Niemals mehr als 1000 USDT in eine Position
MAX_POSITION_SIZE_USD = 1000

# MAX_PORTFOLIO_RISK_PCT: Maximales Portfolio-Risiko
# 0.05 = Maximal 5% des Portfolios riskieren
MAX_PORTFOLIO_RISK_PCT = 0.05

# SESSION_GRANULARITY: Zeitauflösung der Session
# "minute" = Minutengenaue Zeitstempel
SESSION_GRANULARITY = "minute"

# BUY_ESCALATION_EXTRA_BPS: Zusätzlicher Aufschlag bei Eskalation
# 20 = Füge 20 bps (0.2%) bei Eskalation hinzu
BUY_ESCALATION_EXTRA_BPS = 20

# ALLOW_MARKET_FALLBACK: Market-Orders als Fallback?
# False = Niemals Market-Orders (außer TTL)
# Separat von TTL-Fallback Einstellung
ALLOW_MARKET_FALLBACK = True  # Ermöglicht Market-Orders als Fallback

# System-Konstanten (nicht ändern)
MAX_TRADES_CONCURRENT = MAX_TRADES  # Alias
MAX_CONCURRENT_POSITIONS = MAX_TRADES  # V11-Style Alias
FEE_RT = FEE_RATE  # Alias
RUN_ID = run_id  # Eindeutige Session-ID
RUN_TIMESTAMP_UTC = run_timestamp_utc  # UTC-Zeit
RUN_TIMESTAMP_LOCAL = run_timestamp  # Lokale Zeit

# Aus BPS die PCT-Variante für Rückwärtskompatibilität ableiten
PREDICTIVE_BUY_ZONE_PCT = 1.0 + (PREDICTIVE_BUY_ZONE_BPS / 10_000.0)

# =============================================================================
# ABSCHNITT 9: WATCHLIST - ALLE HANDELBAREN COINS
# =============================================================================
topcoins_keys = [
    # Major Coins (Top 20)
    'BTCUSDT', 'XRPUSDT', 'SOLUSDT', 'BNBUSDT', 'DOGEUSDT', 'ADAUSDT',
    'TRXUSDT', 'LINKUSDT', 'AVAXUSDT', 'XLMUSDT', 'TONUSDT', 'HBARUSDT',
    'SUIUSDT', 'SHIBUSDT', 'DOTUSDT', 'LTCUSDT', 'BCHUSDT', 'UNIUSDT',
    'NEARUSDT', 'ONDOUSDT',

    # Mid Cap DeFi & Infrastructure
    'AAVEUSDT', 'APTUSDT', 'ICPUSDT', 'XMRUSDT', 'TAOUSDT', 'MNTUSDT',
    'VETUSDT', 'CROUSDT', 'POLUSDT', 'KASUSDT', 'ALGOUSDT', 'RENDERUSDT',
    'FILUSDT', 'ARBUSDT', 'FETUSDT', 'ATOMUSDT', 'TKXUSDT', 'ENAUSDT',
    'TIAUSDT', 'RAYUSDT', 'JUPUSDT', 'IMXUSDT', 'STXUSDT', 'OPUSDT',

    # Trending & Gaming
    'BERAUSDT', 'DEXEUSDT', 'XDCUSDT', 'MOVEUSDT', 'INJUSDT', 'LDOUSDT',
    'WLDUSDT', 'FTNUSDT', 'KCSUSDT', 'GRTUSDT', 'NEXOUSDT', 'QNTUSDT',
    'FLRUSDT', 'SEIUSDT', 'SANDUSDT', 'GALAUSDT', 'XTZUSDT', 'IOTAUSDT',
    'MKRUSDT', 'FLOWUSDT', 'CRVUSDT', 'AXSUSDT', 'MANAUSDT', 'RUNEUSDT',

    # Meme Coins
    'PEPEUSDT', 'BONKUSDT', 'WIFUSDT', 'FLOKIUSDT', 'POPCATUSDT', 'MOGUSDT',
    'PNUTUSDT', 'TURBOUSDT', 'TRUMPUSDT',

    # Additional Alts
    'DAIUSDT', 'EOSUSDT', 'NEOUSDT', 'COREUSDT', 'STRKUSDT', 'JASMYUSDT',
    'MINAUSDT', 'PENDLEUSDT', 'ORDIUSDT', 'RONUSDT', 'SNXUSDT', 'COMPUSDT',
    'ZECUSDT', 'JTOUSDT', 'GRASSUSDT', 'DYDXUSDT', 'ARUSDT'
]

# =============================================================================
# MARKET DATA & RETENTION SETTINGS
# =============================================================================

# Market data flush interval in seconds
MARKET_DATA_FLUSH_INTERVAL_S = 5

# Data retention periods in days
RETENTION = {
    "ticks_days": 60,
    "quotes_days": 60,
    "orderbook_days": 60,
    "ohlc1s_days": 180,
    "ohlc1m_days": 365,
    "logs_days": 365
}

# =============================================================================
# HELPER FUNCTIONS
# =============================================================================
def backup_config():
    """Erstellt eine Kopie der config.py im Session-Ordner"""
    try:
        config_source = os.path.join(BASE_DIR, "config.py")
        shutil.copy2(config_source, CONFIG_BACKUP_PATH)
        print(f"Config backed up to: {CONFIG_BACKUP_PATH}")
        return True
    except Exception as e:
        print(f"Could not backup config: {e}")
        return False

def validate_config():
    """Leichte Konsistenzprüfungen, kann in main() aufgerufen werden."""
    problems = []

    # Budget/Größen
    min_notional_need = MIN_ORDER_VALUE * MIN_ORDER_BUFFER
    if POSITION_SIZE_USDT < min_notional_need:
        problems.append(
            f"POSITION_SIZE_USDT ({POSITION_SIZE_USDT}) < MIN_ORDER_VALUE*BUFFER ({min_notional_need:.2f})"
        )

    if MAX_PER_SYMBOL_USD < POSITION_SIZE_USDT:
        problems.append("MAX_PER_SYMBOL_USD < POSITION_SIZE_USDT")

    # Exits - Policy-Konflikt prüfen
    if NEVER_MARKET_SELLS and ALLOW_MARKET_FALLBACK_TTL:
        problems.append("Policy-Konflikt: NEVER_MARKET_SELLS=True aber ALLOW_MARKET_FALLBACK_TTL=True → Wähle konsistente Policy")

    if any(b <= 0 for b in EXIT_LADDER_BPS) or EXIT_LADDER_BPS != sorted(EXIT_LADDER_BPS):
        problems.append("EXIT_LADDER_BPS müssen >0 und aufsteigend sein")
    
    # --- Trailing/Exit Konsistenz ---
    if USE_TRAILING_STOP:
        if not (1.0 <= TRAILING_STOP_ACTIVATION_PCT <= TAKE_PROFIT_THRESHOLD):
            problems.append("TRAILING_STOP_ACTIVATION_PCT muss zwischen 1.0 und TP liegen")
        if not (STOP_LOSS_THRESHOLD < TRAILING_STOP_DISTANCE_PCT < 1.0):
            problems.append("TRAILING_STOP_DISTANCE_PCT muss zwischen SL und 1.0 liegen")

    if USE_TRAILING_TP:
        if not (1.0 <= TRAILING_TP_ACTIVATION_PCT <= TAKE_PROFIT_THRESHOLD):
            problems.append("TRAILING_TP_ACTIVATION_PCT muss zwischen 1.0 und TP liegen")
        if not (1 <= TRAILING_TP_STEP_BP <= 500):  # 5% obere Plausi-Grenze
            problems.append("TRAILING_TP_STEP_BP unrealistisch")
        max_tp_bps = max(1, int((TAKE_PROFIT_THRESHOLD - 1.0) * 10_000))
        if not (1 <= TRAILING_TP_UNDER_HIGH_BP < max_tp_bps):
            problems.append("TRAILING_TP_UNDER_HIGH_BP muss kleiner als die TP-Breite sein")

    if MARKET_DATA_FLUSH_INTERVAL_S <= 0:
        problems.append("MARKET_DATA_FLUSH_INTERVAL_S muss > 0 sein")

    if any(v <= 0 for v in RETENTION.values()):
        problems.append("RETENTION Werte mussen > 0 sein")

    # Guards
    if USE_SMA_GUARD and SMA_GUARD_MIN_RATIO >= 1.0:
        problems.append("SMA_GUARD_MIN_RATIO >= 1.0 blockiert alle Käufe")

    # Prefix-Excludes auf Plausibilität
    if not isinstance(EXCLUDE_SYMBOL_PREFIXES, (list, tuple)):
        problems.append("EXCLUDE_SYMBOL_PREFIXES muss Liste/Tuple sein")

    # Predictive-Zone: PCT konsistent zu BPS?
    expected_pct = 1.0 + (PREDICTIVE_BUY_ZONE_BPS / 10_000.0)
    if abs(PREDICTIVE_BUY_ZONE_PCT - expected_pct) > 1e-9:
        problems.append("PREDICTIVE_BUY_ZONE_PCT weicht von BPS-Ableitung ab")

    if problems:
        raise ValueError("Config validation failed: " + "; ".join(problems))
    return True

# =============================================================================
# ENV-OVERRIDES (optional für LOG_LEVEL)
# =============================================================================
# GLOBAL_TRADING wird NICHT überschrieben - nur der Wert aus Zeile 55 gilt!
# Entfernt: GLOBAL_TRADING = os.getenv("BOT_TRADING", "1") == "1"
LOG_LEVEL = os.getenv("BOT_LOG_LEVEL", LOG_LEVEL)

# =============================================================================
# ALIASE FÜR RÜCKWÄRTSKOMPATIBILITÄT (NICHT ÄNDERN!)
# =============================================================================
drop_trigger_value = DROP_TRIGGER_VALUE
take_profit_threshold = TAKE_PROFIT_THRESHOLD
stop_loss_threshold = STOP_LOSS_THRESHOLD
switch_to_sl_threshold = SWITCH_TO_SL_THRESHOLD
switch_to_tp_threshold = SWITCH_TO_TP_THRESHOLD
switch_cooldown_s = SWITCH_COOLDOWN_S
max_trades = MAX_TRADES
max_elapsed_minutes = TRADE_TTL_MIN
use_trailing_stop = USE_TRAILING_STOP
use_trailing_take_profit = USE_TRAILING_TP
trailing_stop_activation_pct = TRAILING_STOP_ACTIVATION_PCT
trailing_stop_distance_pct = TRAILING_STOP_DISTANCE_PCT
trailing_tp_activation_pct = TRAILING_TP_ACTIVATION_PCT
trailing_tp_step_bp = TRAILING_TP_STEP_BP
trailing_tp_under_high_bp = TRAILING_TP_UNDER_HIGH_BP
be_activation_pct = BE_ACTIVATION_PCT
use_sma_guard = USE_SMA_GUARD
sma_guard_window = SMA_GUARD_WINDOW
sma_guard_min_ratio = SMA_GUARD_MIN_RATIO
use_volume_guard = USE_VOLUME_GUARD
volume_guard_window = VOLUME_GUARD_WINDOW
volume_guard_factor = VOLUME_GUARD_FACTOR
use_ml_gatekeeper = USE_ML_GATEKEEPER
ml_buy_threshold = ML_BUY_THRESHOLD
use_btc_last60min = USE_BTC_FILTER
btc_last60min_change_threshold = BTC_CHANGE_THRESHOLD
use_falling_coins_percentage_last60min = USE_FALLING_COINS_FILTER
falling_coins_percentage_last60min = FALLING_COINS_THRESHOLD
fee_rate = FEE_RATE
sell_slippage_pct = SELL_SLIPPAGE_PCT
symbol_cooldown_minutes = COOLDOWN_MIN
reset_portfolio_on_start = RESET_PORTFOLIO_ON_START
sell_all_on_restart = RESET_PORTFOLIO_ON_START
safe_min_budget = SAFE_MIN_BUDGET
cash_reserve_usdt = CASH_RESERVE_USDT
on_insufficient_budget = ON_INSUFFICIENT_BUDGET
global_trading = GLOBAL_TRADING
allow_duplicate_coins = ALLOW_DUPLICATE_COINS
never_market_sells = NEVER_MARKET_SELLS
exit_ladder_bps = EXIT_LADDER_BPS
ioc_sell_buffer_pct = IOC_SELL_BUFFER_PCT
ioc_time_in_force = IOC_TIME_IN_FORCE
ioc_price_buffers_bps = IOC_PRICE_BUFFERS_BPS
ioc_retry_sleep_s = IOC_RETRY_SLEEP_S
post_only_rest_ttl_s = POST_ONLY_REST_TTL_S
post_only_undershoot_bps = POST_ONLY_UNDERSHOOT_BPS
exit_ladder_sleep_ms = EXIT_LADDER_SLEEP_MS
buy_order_timeout_minutes = BUY_ORDER_TIMEOUT_MINUTES
buy_order_cancel_threshold_pct = BUY_ORDER_CANCEL_THRESHOLD_PCT
stale_order_cleanup_interval = STALE_ORDER_CLEANUP_INTERVAL
stale_order_max_age = STALE_ORDER_MAX_AGE
market_update_interval = 30
use_drop_anchor_since_last_close = USE_DROP_ANCHOR
anchor_updates_when_flat = ANCHOR_UPDATES_WHEN_FLAT
use_predictive_buys = USE_PREDICTIVE_BUYS
predictive_buy_zone_pct = PREDICTIVE_BUY_ZONE_PCT
predictive_buy_zone_bps = PREDICTIVE_BUY_ZONE_BPS
min_order_buffer = MIN_ORDER_BUFFER
dust_allow_market_fallback = False
guard_log_level = "info"
api_block_ttl_minutes = API_BLOCK_TTL_MINUTES
permanent_api_blocklist = PERMANENT_API_BLOCKLIST

# --- Aliase für Drop-Trigger-Modi ---
drop_trigger_mode = DROP_TRIGGER_MODE
market_data_flush_interval_s = MARKET_DATA_FLUSH_INTERVAL_S
retention = RETENTION
drop_trigger_lookback_min = DROP_TRIGGER_LOOKBACK_MIN

# --- Aliase für neue Guards ---
use_spread_guard = USE_SPREAD_GUARD
guard_max_spread_bps = GUARD_MAX_SPREAD_BPS
use_vol_sigma_guard = USE_VOL_SIGMA_GUARD
vol_sigma_window = VOL_SIGMA_WINDOW
require_vol_sigma_bps_min = REQUIRE_VOL_SIGMA_BPS_MIN

# 🧩 Fehlt bisher in deiner Datei, wird aber vom Code erwartet:
exit_ioc_ttl_ms = EXIT_IOC_TTL_MS
symbol_min_cost_override = SYMBOL_MIN_COST_OVERRIDE
post_only_rest_ttl_s = POST_ONLY_REST_TTL_S
enable_pretty_trade_logs = ENABLE_PRETTY_TRADE_LOGS
show_event_type_in_console = SHOW_EVENT_TYPE_IN_CONSOLE
enable_pnl_monitor = ENABLE_PNL_MONITOR
verbose_guard_logs = VERBOSE_GUARD_LOGS

# =============================================================================
# ABSCHNITT X: Terminal Market Monitor (Top Drops seit Anchor)
# =============================================================================
# Aktiviert eine periodische Terminal-Ausgabe der größten Rückgänge seit Anchor.
# - "since Anchor" spiegelt deinen DROP_TRIGGER_MODE wider (inkl. Mode 4 mit persistentem Anchor)
# - Zusätzlich wird BTC/USDT ausgegeben (Preis + ggf. 60m-%-Change, falls verfügbar)
ENABLE_TOP_DROPS_TICKER = True        # Ein/Aus
TOP_DROPS_INTERVAL_S = 60             # Intervall in Sekunden
TOP_DROPS_LIMIT = 10                  # Anzahl Einträge in der Liste
# Ticker-Visualisierung in der Nähe des Triggers (hilfreich fürs Debuggen)
# 200 bps = 2.00 Prozentpunkte Restweg zum Trigger
TOP_DROPS_WITHIN_BPS_OF_TRIGGER = 200

# (Optional) Aliase, falls du sie im Code kleingeschrieben importierst:
enable_top_drops_ticker = ENABLE_TOP_DROPS_TICKER
top_drops_interval_s = TOP_DROPS_INTERVAL_S
top_drops_limit = TOP_DROPS_LIMIT
top_drops_within_bps_of_trigger = TOP_DROPS_WITHIN_BPS_OF_TRIGGER

# =============================================================================
# ABSCHNITT Y: Pretty Trade Logs (Terminal)
# =============================================================================
# Hübsche, klare Terminal-Zeilen zusätzlich zu JSON-Events:
#  - ✅ BUY FILLED / 🟢 TP / 🔴 SL
#  - mit Preis, Qty, PnL usw.
ENABLE_PRETTY_TRADE_LOGS = True
USE_ANSI_COLORS = True           # Windows 10+ unterstützt ANSI; bei Bedarf auf False setzen
SHOW_EVENT_TYPE_IN_CONSOLE = True

# =============================================================================
# ABSCHNITT Z: PnL Monitor Settings
# =============================================================================
# Zeigt regelmäßig die unrealized und realized PnL im Terminal
ENABLE_PNL_MONITOR = True           # PnL-Übersicht aktivieren
PNL_MONITOR_INTERVAL_S = 120        # Alle 2 Minuten ausgeben
SHOW_INDIVIDUAL_POSITIONS = True    # Einzelne Positionen zeigen
SHOW_TOTAL_SUMMARY = True          # Gesamtsumme zeigen

# =============================================================================
# ABSCHNITT: Depth-Sweep / Synthetic-Market (keine Market-Orders)
# =============================================================================
# Aktiviert Preisfindung über Orderbuch-Tiefe (synthetischer Market mit Limit-IOC)
USE_DEPTH_SWEEP = True

# Wie viele Levels aus dem Orderbuch werden betrachtet (asks/bids)
SWEEP_ORDERBOOK_LEVELS = 20

# Maximale erlaubte Slippage ggü. Best-Ask/Bid in Basispunkten
# Entry (Buy): 15 bps Hard Cap (= 0.15% über Ask)
# Exit (Sell): 12 bps wie gehabt
MAX_SLIPPAGE_BPS_ENTRY = 15
MAX_SLIPPAGE_BPS_EXIT  = 12

# Reprice-Schleife: Anzahl Versuche und Pause
SWEEP_REPRICE_ATTEMPTS = 4
SWEEP_REPRICE_SLEEP_MS = 150  # Millisekunden

# =============================================================================
# SYMBOL-SPEZIFISCHE SPREAD/SLIPPAGE-DECKEL
# =============================================================================

# Maximale erlaubte Spreads pro Symbol (in Basispunkten)
MAX_SPREAD_BP_BY_SYMBOL = {
    "BTC/USDT": 10,  # 0.10%
    "ETH/USDT": 12,  # 0.12%
    "SOL/USDT": 15,  # 0.15%
    "BNB/USDT": 12,  # 0.12%
    "XRP/USDT": 8,   # 0.08%
    "ADA/USDT": 15,  # 0.15%
    "DOGE/USDT": 12, # 0.12%
    "DOT/USDT": 18,  # 0.18%
    "AVAX/USDT": 18, # 0.18%
    "LINK/USDT": 15, # 0.15%
}

# Erlaubte Slippage pro Symbol (in Basispunkten)
SLIPPAGE_BP_ALLOWED_BY_SYMBOL = {
    "BTC/USDT": 15,  # 0.15%
    "ETH/USDT": 18,  # 0.18%
    "SOL/USDT": 25,  # 0.25%
    "BNB/USDT": 18,  # 0.18%
    "XRP/USDT": 12,  # 0.12%
    "ADA/USDT": 25,  # 0.25%
    "DOGE/USDT": 20, # 0.20%
    "DOT/USDT": 30,  # 0.30%
    "AVAX/USDT": 30, # 0.30%
    "LINK/USDT": 25, # 0.25%
}

# Default-Werte für nicht-gelistete Symbole (direkt aus Feedback)
defaults = {"max_spread_bp": 30, "slippage_bp_allowed": 15}
max_spread_bp_by_symbol = MAX_SPREAD_BP_BY_SYMBOL
slippage_bp_allowed_by_symbol = SLIPPAGE_BP_ALLOWED_BY_SYMBOL
DEFAULT_MAX_SPREAD_BPS = 30   # 0.30% (erhöht von 20)
DEFAULT_SLIPPAGE_BPS = 15     # 0.15% (geändert von 25)

# Aliase (lowercase) für konsistenten Import
use_depth_sweep = USE_DEPTH_SWEEP
sweep_orderbook_levels = SWEEP_ORDERBOOK_LEVELS
max_slippage_bps_entry = MAX_SLIPPAGE_BPS_ENTRY
max_slippage_bps_exit  = MAX_SLIPPAGE_BPS_EXIT
sweep_reprice_attempts = SWEEP_REPRICE_ATTEMPTS
sweep_reprice_sleep_ms = SWEEP_REPRICE_SLEEP_MS

# =============================================================================
# TERMINAL UI
# =============================================================================
TERMINAL_PRESET = "verbose"   # "compact" | "verbose"
USE_STATUS_LINE = True
USE_ANSI_COLORS = True
USE_EMOJI_ICONS = False
SHOW_SESSION_TRADES_DIGEST = True

# Globale Icons
ICONS = {
    "buy": "✅" if USE_EMOJI_ICONS else "BUY",
    "sell_filled": "✅" if USE_EMOJI_ICONS else "SELL",
    "sell_tp": "🟢" if USE_EMOJI_ICONS else "TP",
    "sell_sl": "🔴" if USE_EMOJI_ICONS else "SL",
    "placed": "⌛" if USE_EMOJI_ICONS else "...",
    "session": "📊" if USE_EMOJI_ICONS else "[S]",
    "drops": "📉" if USE_EMOJI_ICONS else "[D]",
    "search": "🔎" if USE_EMOJI_ICONS else "[?]",
}

# Preset-Overrides
TERMINAL_PRESETS = {
    "compact": {
        "STATUS_RATE_LIMIT_S": 1.5,
        "PRICE_DECIMALS": 6, "QTY_DECIMALS": 6, "PNL_DECIMALS": 2,
        "SHOW_TIF": False, "SHOW_MAKER": False, "SHOW_DURATION_LONG": False,
        "SHOW_REST_PP": False, "SHOW_LABELS_WIDE": False,
    },
    "verbose": {
        "STATUS_RATE_LIMIT_S": 1.0,
        "PRICE_DECIMALS": 6, "QTY_DECIMALS": 6, "PNL_DECIMALS": 2,
        "SHOW_TIF": True, "SHOW_MAKER": True, "SHOW_DURATION_LONG": True,
        "SHOW_REST_PP": True, "SHOW_LABELS_WIDE": True,
    },
}

# Preset anwenden
for _k, _v in TERMINAL_PRESETS.get(TERMINAL_PRESET, {}).items():
    globals()[_k] = _v

# Optional: Aktualisierungsintervall in Sekunden (die run-Schleife schläft typ. ohnehin 30s)
STATUS_LINE_TICK_S = 30

# Falls nicht bereits vorhanden: angenehme Aliase
try:
    max_trades  # alias vorhanden?
except NameError:
    max_trades = MAX_TRADES

# --- Backward-compat alias for code that expects `Settings` ---
# Ziel: Settings als Klasse anbieten die BEIDE Schreibweisen unterstützt
if "Settings" not in globals():
    if "C" in globals():
        # Wenn du eine Konfigurationsklasse/-namespace C hast, einfach aliasen:
        Settings = C  # type: ignore[name-defined]
    else:
        # Robuste Settings-Klasse die beide Schreibweisen unterstützt
        class Settings:
            """Settings-Klasse die sowohl UPPERCASE als auch lowercase Zugriff erlaubt"""
            def __init__(self):
                # Snapshot aller UPPERCASE-Variablen aus dem Modul
                for k, v in list(globals().items()):
                    if isinstance(k, str) and k.isupper() and not k.startswith('_'):
                        # Setze UPPERCASE Version
                        setattr(self, k, v)
                        # Setze auch lowercase Version für Kompatibilität
                        setattr(self, k.lower(), v)
            
            def to_dict(self):
                """Gibt alle Settings als Dictionary zurück"""
                return {k: v for k, v in self.__dict__.items() 
                        if not k.startswith('_')}# ---- Anchor-Schutz (Mode 4) ----
# ANCHOR_STALE_MINUTES: Anchor älter als X Minuten werden neu initialisiert
ANCHOR_STALE_MINUTES = 60          # älter als 60 Min => neu initialisieren

# ANCHOR_CLAMP_MAX_ABOVE_PEAK_PCT: Anchor darf max. X% über aktuellem Peak liegen
ANCHOR_CLAMP_MAX_ABOVE_PEAK_PCT = 5.0   # max. 5% über aktuellem Peak/Ref erlaubt

# ANCHOR_MAX_START_DROP_PCT: Zu Beginn darf Drop vs. Anchor max. X% sein
ANCHOR_MAX_START_DROP_PCT = 6.0    # zu Beginn darf Drop vs. Anchor max. 6% sein

# ---- Entry-Preisgestaltung ----
# ENTRY_LIMIT_OFFSET_BPS: Offset über dem Ask für bessere Fill-Chance
ENTRY_LIMIT_OFFSET_BPS = 0     # 0 = deaktiviert (nutze BUY_ESCALATION_STEPS stattdessen)

# ENTRY_ORDER_TIF: Time-In-Force für Entry-Orders
ENTRY_ORDER_TIF = "IOC"        # "IOC" bevorzugen für schnelle Fills (oder "GTC")

# =============================================================================
# CONFIG SCHEMA VALIDATION (Fail-Fast)
# =============================================================================

def validate_config_schema():
    """
    Validiert kritische Config-Parameter für fail-fast Verhalten.

    Wirft ValueError bei ungültigen Konfigurationen und liefert
    klare Fehlermeldungen für schnelle Problemdiagnose.
    """
    errors = []

    # Helper für Bereichsvalidierung
    def check_range(name, value, min_val=None, max_val=None, required_type=None):
        if required_type and not isinstance(value, required_type):
            errors.append(f"{name} muss vom Typ {required_type.__name__} sein, ist aber {type(value).__name__}")
            return False
        if min_val is not None and value < min_val:
            errors.append(f"{name} = {value} ist zu klein (min: {min_val})")
            return False
        if max_val is not None and value > max_val:
            errors.append(f"{name} = {value} ist zu groß (max: {max_val})")
            return False
        return True

    # Helper für Enum-Validierung
    def check_enum(name, value, valid_values):
        if value not in valid_values:
            errors.append(f"{name} = {value} ist ungültig. Erlaubt: {valid_values}")
            return False
        return True

    # 1. GLOBAL SWITCHES
    check_range("GLOBAL_TRADING", GLOBAL_TRADING, required_type=bool)

    # 2. EXIT STRATEGIE
    check_range("TAKE_PROFIT_THRESHOLD", TAKE_PROFIT_THRESHOLD, 1.001, 2.0, float)
    check_range("STOP_LOSS_THRESHOLD", STOP_LOSS_THRESHOLD, 0.5, 0.999, float)
    check_range("SWITCH_TO_SL_THRESHOLD", SWITCH_TO_SL_THRESHOLD, 0.5, 1.0, float)
    check_range("SWITCH_TO_TP_THRESHOLD", SWITCH_TO_TP_THRESHOLD, 1.0, 1.1, float)
    check_range("SWITCH_COOLDOWN_S", SWITCH_COOLDOWN_S, 5, 300, (int, float))

    # Exit-Logik Konsistenz
    if STOP_LOSS_THRESHOLD >= TAKE_PROFIT_THRESHOLD:
        errors.append("STOP_LOSS_THRESHOLD muss kleiner als TAKE_PROFIT_THRESHOLD sein")
    if SWITCH_TO_SL_THRESHOLD >= SWITCH_TO_TP_THRESHOLD:
        errors.append("SWITCH_TO_SL_THRESHOLD muss kleiner als SWITCH_TO_TP_THRESHOLD sein")

    # 3. ATR-BASED EXITS
    check_range("USE_ATR_BASED_EXITS", USE_ATR_BASED_EXITS, required_type=bool)
    if USE_ATR_BASED_EXITS:
        check_range("ATR_PERIOD", ATR_PERIOD, 5, 200, int)
        check_range("ATR_SL_MULTIPLIER", ATR_SL_MULTIPLIER, 0.1, 5.0, (int, float))
        check_range("ATR_TP_MULTIPLIER", ATR_TP_MULTIPLIER, 0.5, 10.0, (int, float))
        check_range("ATR_MIN_SAMPLES", ATR_MIN_SAMPLES, 5, 500, int)

    # 4. ENTRY STRATEGIE
    check_range("DROP_TRIGGER_VALUE", DROP_TRIGGER_VALUE, 0.5, 0.999, float)
    check_enum("DROP_TRIGGER_MODE", DROP_TRIGGER_MODE, [1, 2, 3, 4])
    check_range("DROP_TRIGGER_LOOKBACK_MIN", DROP_TRIGGER_LOOKBACK_MIN, 5, 1440, int)

    # 5. POSITION MANAGEMENT
    check_range("MAX_TRADES", MAX_TRADES, 1, 50, int)
    check_range("POSITION_SIZE_USDT", POSITION_SIZE_USDT, 1.0, 10000.0, (int, float))
    check_range("MAX_PER_SYMBOL_USD", MAX_PER_SYMBOL_USD, 1.0, 100000.0, (int, float))
    check_range("TRADE_TTL_MIN", TRADE_TTL_MIN, 5, 10080, (int, float))  # 5min bis 1 Woche
    check_range("COOLDOWN_MIN", COOLDOWN_MIN, 0, 1440, (int, float))  # bis 24h

    # Position Size Konsistenz
    if POSITION_SIZE_USDT > MAX_PER_SYMBOL_USD:
        errors.append("POSITION_SIZE_USDT darf nicht größer als MAX_PER_SYMBOL_USD sein")

    # 6. GUARDS
    check_range("USE_SMA_GUARD", USE_SMA_GUARD, required_type=bool)
    if USE_SMA_GUARD:
        check_range("SMA_GUARD_WINDOW", SMA_GUARD_WINDOW, 3, 500, int)
        check_range("SMA_GUARD_MIN_RATIO", SMA_GUARD_MIN_RATIO, 0.5, 1.5, float)

    check_range("USE_VOLUME_GUARD", USE_VOLUME_GUARD, required_type=bool)
    if USE_VOLUME_GUARD:
        check_range("VOLUME_GUARD_WINDOW", VOLUME_GUARD_WINDOW, 1, 120, int)
        check_range("VOLUME_GUARD_FACTOR", VOLUME_GUARD_FACTOR, 0.1, 10.0, (int, float))

    # 7. EXCHANGE SETTINGS
    if hasattr(globals(), 'MIN_NOTIONAL_USDT'):
        check_range("MIN_NOTIONAL_USDT", MIN_NOTIONAL_USDT, 0.1, 1000.0, (int, float))

    # 8. ANCHOR SYSTEM (wenn aktiviert)
    if USE_DROP_ANCHOR:
        check_range("ANCHOR_STALE_MINUTES", ANCHOR_STALE_MINUTES, 1, 10080, (int, float))
        check_range("ANCHOR_CLAMP_MAX_ABOVE_PEAK_PCT", ANCHOR_CLAMP_MAX_ABOVE_PEAK_PCT, 0.1, 50.0, (int, float))
        check_range("ANCHOR_MAX_START_DROP_PCT", ANCHOR_MAX_START_DROP_PCT, 0.1, 50.0, (int, float))

    # 9. ENTRY ORDER SETTINGS
    if hasattr(globals(), 'ENTRY_LIMIT_OFFSET_BPS'):
        check_range("ENTRY_LIMIT_OFFSET_BPS", ENTRY_LIMIT_OFFSET_BPS, 0, 1000, (int, float))
    if hasattr(globals(), 'ENTRY_ORDER_TIF'):
        check_enum("ENTRY_ORDER_TIF", ENTRY_ORDER_TIF, ["GTC", "IOC", "FOK"])

    # Fehler sammeln und werfen
    if errors:
        error_msg = "[ERROR] CONFIG VALIDATION FAILED!\n" + "\n".join(f"  - {err}" for err in errors)
        error_msg += f"\n\nCheck config.py lines 46-400 for main parameters"
        raise ValueError(error_msg)

    return True

# Automatische Validation beim Import (fail-fast)
if __name__ != "__main__":
    try:
        validate_config_schema()
        print("[OK] Config Schema Validation: PASSED")
    except ValueError as e:
        print(f"[FAIL] Config Schema Validation: FAILED\n{e}")
        raise  # Re-raise für fail-fast Verhalten
