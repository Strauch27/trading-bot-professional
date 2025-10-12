# config.py – Trading Bot Konfiguration
# ======================================
# ⚠️ WICHTIGE HINWEISE:
# - USER SETTINGS (Abschnitt 1-7): Häufig geänderte Parameter → Siehe CONFIG_README.md für Details
# - SYSTEM DEFAULTS (Abschnitt 8+): Selten geänderte technische Einstellungen
# - Aliase für Rückwärtskompatibilität am Ende (nicht ändern!)

import os
import uuid
import shutil
from datetime import datetime, timezone

# =============================================================================
# ABSCHNITT 0: RUN IDENTITY & DIRECTORIES (automatisch, nicht ändern)
# =============================================================================

_now_utc = datetime.now(timezone.utc)
run_timestamp_utc = _now_utc.strftime('%Y-%m-%d_%H-%M-%S')
run_timestamp = _now_utc.strftime('%Y%m%d_%H%M%S')
run_timestamp_readable = run_timestamp_utc
run_id = str(uuid.uuid4())[:8]

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
SESSION_DIR_NAME = f"session_{run_timestamp}"
SESSION_DIR = os.path.join(BASE_DIR, "sessions", SESSION_DIR_NAME)

LOG_DIR = os.path.join(SESSION_DIR, "logs")
STATE_DIR = os.path.join(SESSION_DIR, "state")
REPORTS_DIR = os.path.join(SESSION_DIR, "reports")
SNAPSHOTS_DIR = os.path.join(SESSION_DIR, "snapshots")

CONFIG_VERSION = 1
MIGRATIONS = {}


# #############################################################################
# ###  ██╗   ██╗███████╗███████╗██████╗     ███████╗███████╗████████╗████████╗██╗███╗   ██╗ ██████╗ ███████╗
# ###  ██║   ██║██╔════╝██╔════╝██╔══██╗    ██╔════╝██╔════╝╚══██╔══╝╚══██╔══╝██║████╗  ██║██╔════╝ ██╔════╝
# ###  ██║   ██║███████╗█████╗  ██████╔╝    ███████╗█████╗     ██║      ██║   ██║██╔██╗ ██║██║  ███╗███████╗
# ###  ██║   ██║╚════██║██╔══╝  ██╔══██╗    ╚════██║██╔══╝     ██║      ██║   ██║██║╚██╗██║██║   ██║╚════██║
# ###  ╚██████╔╝███████║███████╗██║  ██║    ███████║███████╗   ██║      ██║   ██║██║ ╚████║╚██████╔╝███████║
# ###   ╚═════╝ ╚══════╝╚══════╝╚═╝  ╚═╝    ╚══════╝╚══════╝   ╚═╝      ╚═╝   ╚═╝╚═╝  ╚═══╝ ╚═════╝ ╚══════╝
# ###  HÄUFIG GEÄNDERTE PARAMETER - Details in CONFIG_README.md
# #############################################################################

# =============================================================================
# 1. MASTER SWITCH
# =============================================================================

GLOBAL_TRADING = True  # True = Live Trading, False = Nur Beobachten

# Portfolio Reset beim Start (nur für Testing!)
# ⚠️ ACHTUNG: Verkauft ALLE Assets beim Start - nur für Tests aktivieren!
RESET_PORTFOLIO_ON_START = True  # True = Verkaufe alles beim Start, False = Normal (empfohlen)

# =============================================================================
# 2. EXIT STRATEGIE
# =============================================================================

TAKE_PROFIT_THRESHOLD = 1.005  # +0.5% Gewinn
STOP_LOSS_THRESHOLD = 0.990  # -1.0% Verlust
SWITCH_TO_SL_THRESHOLD = 0.995  # Bei -0.5% auf SL umschalten
SWITCH_TO_TP_THRESHOLD = 1.002  # Bei +0.2% zurück zu TP
SWITCH_COOLDOWN_S = 20  # Mindestens 20s zwischen Umschaltungen

# ATR-basierte dynamische Exits (Fortgeschritten)
USE_ATR_BASED_EXITS = False  # False = Feste %, True = Volatilitäts-basiert
ATR_PERIOD = 14
ATR_SL_MULTIPLIER = 0.6
ATR_TP_MULTIPLIER = 1.6
ATR_MIN_SAMPLES = 15

# =============================================================================
# 3. ENTRY STRATEGIE
# =============================================================================

DROP_TRIGGER_VALUE = 0.985  # -1.5% Drop → Kaufsignal
LOOKBACK_S = 120  # 2min Lookback-Fenster
MODE = 4  # Mode 4: Drop-Trigger ohne Impuls
CONFIRM_TICKS = 0  # Sofort scharf
HYSTERESIS_BPS = 0  # Kein Hysteresis-Puffer
DEBOUNCE_S = 3  # Minimale Entprellung
USE_IOC_FOR_MODE2 = True
USE_ROBUST_MARKET_FETCH = True

DROP_TRIGGER_MODE = 4  # Reset nach jedem Trade (empfohlen)
DROP_TRIGGER_LOOKBACK_MIN = 2  # Zeitfenster für Mode 2/3

# Drop Anchor System
USE_DROP_ANCHOR = True  # Hochpunkte dauerhaft speichern
ANCHOR_UPDATES_WHEN_FLAT = True  # Anker folgt Hochs auch ohne Position
ANCHOR_STALE_MINUTES = 60  # Anker älter als 60 Min neu initialisieren
ANCHOR_CLAMP_MAX_ABOVE_PEAK_PCT = 5.0  # Max 5% über Peak erlaubt
ANCHOR_MAX_START_DROP_PCT = 6.0  # Zu Beginn max. 6% Drop erlaubt

# =============================================================================
# 4. POSITION MANAGEMENT
# =============================================================================

MAX_TRADES = 10  # Maximal 10 Positionen gleichzeitig
POSITION_SIZE_USDT = 25.0  # 25 USDT pro Kauf
ALLOW_AUTO_SIZE_UP = True  # Menge leicht erhöhen bei MinNotional
MAX_AUTO_SIZE_UP_BPS = 500  # Max. +500 bps Notional-Erhöhung
MAX_AUTO_SIZE_UP_ABS_USDT = 5.0  # Absolut gedeckelt bei +5.0 USDT
MAX_PER_SYMBOL_USD = 60.0  # Maximal 60 USDT pro Coin
TRADE_TTL_MIN = 120  # Position nach 120 Min zwangsschließen
COOLDOWN_MIN = 15  # 15 Min Pause nach Verkauf
ALLOW_DUPLICATE_COINS = False  # Nur eine Position pro Symbol

# =============================================================================
# 5. GUARDS - Qualitätsfilter
# =============================================================================

USE_SMA_GUARD = False  # SMA Trendfilter (deaktiviert)
SMA_GUARD_MIN_RATIO = 0.992
SMA_GUARD_WINDOW = 50

USE_VOLUME_GUARD = False  # Volumen-Filter (deaktiviert)
VOLUME_GUARD_WINDOW = 15
VOLUME_GUARD_FACTOR = 1.020
MIN_24HUSD_VOLUME = 150000

USE_SPREAD_GUARD = False  # Spread-Filter (deaktiviert)
GUARD_MAX_SPREAD_BPS = 35

USE_VOL_SIGMA_GUARD = False  # Volatilitäts-Guard (deaktiviert)
VOL_SIGMA_WINDOW = 30
REQUIRE_VOL_SIGMA_BPS_MIN = 10

# Makro-Filter
USE_BTC_FILTER = False  # Bitcoin-Filter (deaktiviert)
BTC_CHANGE_THRESHOLD = None
USE_FALLING_COINS_FILTER = False  # Falling-Coins-Filter (deaktiviert)
FALLING_COINS_THRESHOLD = 0.55
USE_BTC_TREND_GUARD = False

# Machine Learning
USE_ML_GATEKEEPER = False  # ML-Gatekeeper (deaktiviert)
ML_BUY_THRESHOLD = 0.65
MODEL_DIR = "models"

# =============================================================================
# 6. ORDER EXECUTION
# =============================================================================

# Buy Execution
BUY_MODE = "PREDICTIVE"  # "PREDICTIVE", "ESCALATION" oder "CLASSIC"
PREDICTIVE_BUY_ZONE_BPS = 3  # 0.03% über Marktpreis
USE_PREDICTIVE_BUYS = True
BUY_LIMIT_PREMIUM_BPS = 8
BUY_GTC_WAIT_SECS = 4.0
BUY_GTC_MIN_PARTIAL = 0.25
BUY_GTC_WAIT_DYNAMIC = True
USE_BUY_ESCALATION = True
BUY_ESCALATION_STEPS = [
    {"tif": "IOC", "premium_bps": 10, "max_attempts": 1},
    {"tif": "IOC", "premium_bps": 30, "max_attempts": 1},
    {"tif": "IOC", "premium_bps": 60, "max_attempts": 1},
]
IOC_ORDER_TTL_MS = 600
ENTRY_LIMIT_OFFSET_BPS = 0  # 0 = deaktiviert
ENTRY_ORDER_TIF = "IOC"

# Sell Execution
NEVER_MARKET_SELLS = False  # False = Erlaube Market-Orders
EXIT_LADDER_BPS = [10, 30, 70, 120]  # Preisstufen für Verkaufsversuche
EXIT_ESCALATION_BPS = [0, 10]
ALLOW_MARKET_FALLBACK_TTL = False

# Depth-Sweep (Synthetischer Market mit Limit-IOC)
USE_DEPTH_SWEEP = True
SWEEP_ORDERBOOK_LEVELS = 20
MAX_SLIPPAGE_BPS_ENTRY = 15
MAX_SLIPPAGE_BPS_EXIT = 12
SWEEP_REPRICE_ATTEMPTS = 4
SWEEP_REPRICE_SLEEP_MS = 150

# =============================================================================
# 7. TRAILING & ADVANCED EXITS
# =============================================================================

# Trailing Stop Loss
USE_TRAILING_STOP = True
TRAILING_STOP_ACTIVATION_PCT = 1.001  # Bei +0.1% aktivieren
TRAILING_STOP_DISTANCE_PCT = 0.999  # 0.1% Abstand vom Hoch

# Trailing Take Profit
USE_TRAILING_TP = False
TRAILING_TP_ACTIVATION_PCT = 1.0040
TRAILING_TP_STEP_BP = 5
TRAILING_TP_UNDER_HIGH_BP = 10

# Relative Trailing (automatisch aus TP/SL ableiten)
USE_RELATIVE_TRAILING = True
TSL_ACTIVATE_FRAC_OF_TP = 0.60
TSL_DISTANCE_FRAC_OF_SL_GAP = 0.30
TTP_ACTIVATE_FRAC_OF_TP = 0.60
TTP_UNDER_HIGH_FRAC_OF_TP = 0.20
TTP_STEPS_PER_TP = 6

def _clamp(x, lo, hi):
    return max(lo, min(hi, x))

def _derive_trailing_from_relatives():
    global TRAILING_STOP_ACTIVATION_PCT, TRAILING_STOP_DISTANCE_PCT
    global TRAILING_TP_ACTIVATION_PCT, TRAILING_TP_UNDER_HIGH_BP, TRAILING_TP_STEP_BP
    tp_gap = max(0.0, TAKE_PROFIT_THRESHOLD - 1.0)
    sl_gap = max(0.0, 1.0 - STOP_LOSS_THRESHOLD)
    act_frac = _clamp(TSL_ACTIVATE_FRAC_OF_TP, 0.0, 1.0)
    TRAILING_STOP_ACTIVATION_PCT = 1.0 + tp_gap * act_frac
    dist_frac = _clamp(TSL_DISTANCE_FRAC_OF_SL_GAP, 0.05, 1.0)
    TRAILING_STOP_DISTANCE_PCT = 1.0 - sl_gap * dist_frac
    TRAILING_STOP_DISTANCE_PCT = max(TRAILING_STOP_DISTANCE_PCT, STOP_LOSS_THRESHOLD)
    ttp_act_frac = _clamp(TTP_ACTIVATE_FRAC_OF_TP, 0.0, 1.0)
    TRAILING_TP_ACTIVATION_PCT = 1.0 + tp_gap * ttp_act_frac
    TRAILING_TP_ACTIVATION_PCT = min(TRAILING_TP_ACTIVATION_PCT, TAKE_PROFIT_THRESHOLD)
    total_tp_bps = int(round(tp_gap * 10_000))
    under_high_frac = _clamp(TTP_UNDER_HIGH_FRAC_OF_TP, 0.05, 0.90)
    TRAILING_TP_UNDER_HIGH_BP = max(1, int(round(total_tp_bps * under_high_frac)))
    steps = max(1, int(TTP_STEPS_PER_TP))
    TRAILING_TP_STEP_BP = max(1, total_tp_bps // steps)

if USE_RELATIVE_TRAILING:
    _derive_trailing_from_relatives()

# Breakeven
BE_ACTIVATION_PCT = None


# #############################################################################
# ###  ███████╗██╗   ██╗███████╗████████╗███████╗███╗   ███╗    ██████╗ ███████╗███████╗ █████╗ ██╗   ██╗██╗  ████████╗███████╗
# ###  ██╔════╝╚██╗ ██╔╝██╔════╝╚══██╔══╝██╔════╝████╗ ████║    ██╔══██╗██╔════╝██╔════╝██╔══██╗██║   ██║██║  ╚══██╔══╝██╔════╝
# ###  ███████╗ ╚████╔╝ ███████╗   ██║   █████╗  ██╔████╔██║    ██║  ██║█████╗  █████╗  ███████║██║   ██║██║     ██║   ███████╗
# ###  ╚════██║  ╚██╔╝  ╚════██║   ██║   ██╔══╝  ██║╚██╔╝██║    ██║  ██║██╔══╝  ██╔══╝  ██╔══██║██║   ██║██║     ██║   ╚════██║
# ###  ███████║   ██║   ███████║   ██║   ███████╗██║ ╚═╝ ██║    ██████╔╝███████╗██║     ██║  ██║╚██████╔╝███████╗██║   ███████║
# ###  ╚══════╝   ╚═╝   ╚══════╝   ╚═╝   ╚══════╝╚═╝     ╚═╝    ╚═════╝ ╚══════╝╚═╝     ╚═╝  ╚═╝ ╚═════╝ ╚══════╝╚═╝   ╚══════╝
# ###  SELTEN GEÄNDERTE PARAMETER - Technische Einstellungen
# #############################################################################

# =============================================================================
# 8. RISIKO & BUDGET MANAGEMENT
# =============================================================================

SAFE_MIN_BUDGET = 10.0
CASH_RESERVE_USDT = 0.0
ON_INSUFFICIENT_BUDGET = "wait"
MAX_LOSSES_IN_ROW = 5
CB_WINDOW_MIN = 60
MAX_DAILY_DRAWDOWN_PCT = 0.08
MAX_TRADES_PER_DAY = 120
FEE_RATE = 0.001
SELL_SLIPPAGE_PCT = 0.001

# =============================================================================
# 9. EXCHANGE & TECHNISCHE LIMITS
# =============================================================================

UNIVERSE_TOP_N_BY_VOL = 72
MIN_NOTIONAL_USDT = 5.0
EXCLUDE_SYMBOL_PREFIXES = ["BULL/", "BEAR/", "3L/", "3S/", "UP/", "DOWN/"]
MIN_ORDER_VALUE = 5.1
MIN_ORDER_BUFFER = 0.002
DUST_FACTOR = 0.9995
MAX_HISTORY_LEN = 1440
DUST_SWEEP_ENABLED = False
DUST_SWEEP_INTERVAL_MIN = 30
DUST_MIN_COST_USD = 6.0
DUST_FORCE_MARKET_IOC = True
DUST_TARGET_QUOTE = "USDT"
SETTLEMENT_TIMEOUT = 240
SETTLEMENT_TOLERANCE = 0.995
SETTLEMENT_CHECK_INTERVAL = 30
SETTLEMENT_MAX_ATTEMPTS = 8
SETTLEMENT_POLL_INTERVAL_S = 20
SAFETY_BUFFER_PCT = 0.01
SAFETY_BUFFER_MIN = 0.5
MIN_SLOT_USDT = 5.0
SKIP_TRADE_IF_EXIT_UNDER_MIN = True
API_BLOCK_TTL_MINUTES = 120
PERMANENT_API_BLOCKLIST = {"FLOW/USDT"}
RETRY_REDUCTION_PCT = 0.97
BACKFILL_MINUTES = 90
BACKFILL_TIMEFRAME = '1m'
BUY_ORDER_TIMEOUT_MINUTES = 3
BUY_ORDER_CANCEL_THRESHOLD_PCT = 1.03
STALE_ORDER_CLEANUP_INTERVAL = 60
STALE_ORDER_MAX_AGE = 60

# IOC Settings
IOC_SELL_BUFFER_PCT = 0.10
IOC_TIME_IN_FORCE = "IOC"
IOC_PRICE_BUFFERS_BPS = [5, 12, 20, 35, 60, 90]
IOC_RETRY_SLEEP_S = 0.4
POST_ONLY_REST_TTL_S = 8
EXIT_IOC_TTL_MS = 500
POST_ONLY_undershoot = None
POST_ONLY_UNDERSHOOT_BPS = 3
EXIT_LADDER_SLEEP_MS = 0

# Symbol-spezifische Limits
MAX_SPREAD_BP_BY_SYMBOL = {
    "BTC/USDT": 10, "ETH/USDT": 12, "SOL/USDT": 15, "BNB/USDT": 12,
    "XRP/USDT": 8, "ADA/USDT": 15, "DOGE/USDT": 12, "DOT/USDT": 18,
    "AVAX/USDT": 18, "LINK/USDT": 15,
}
SLIPPAGE_BP_ALLOWED_BY_SYMBOL = {
    "BTC/USDT": 15, "ETH/USDT": 18, "SOL/USDT": 25, "BNB/USDT": 18,
    "XRP/USDT": 12, "ADA/USDT": 25, "DOGE/USDT": 20, "DOT/USDT": 30,
    "AVAX/USDT": 30, "LINK/USDT": 25,
}
defaults = {"max_spread_bp": 30, "slippage_bp_allowed": 15}
max_spread_bp_by_symbol = MAX_SPREAD_BP_BY_SYMBOL
slippage_bp_allowed_by_symbol = SLIPPAGE_BP_ALLOWED_BY_SYMBOL
DEFAULT_MAX_SPREAD_BPS = 30
DEFAULT_SLIPPAGE_BPS = 15

# =============================================================================
# 10. LOGGING & MONITORING
# =============================================================================

LOG_LEVEL = "DEBUG"
LOG_SCHEMA_VERSION = 4
ENABLE_PRETTY_TRADE_LOGS = True
CONSOLE_LEVEL = "DEBUG"
SHOW_EVENT_TYPE_IN_CONSOLE = True
SHOW_THREAD_NAME_IN_CONSOLE = True
ENABLE_PNL_MONITOR = True
VERBOSE_GUARD_LOGS = True
LOG_MAX_BYTES = 50_000_000
LOG_BACKUP_COUNT = 5
WRITE_SNAPSHOTS = True

LOG_FILE = os.path.join(LOG_DIR, f"bot_log_{run_timestamp_readable}.jsonl")
EVENTS_LOG = os.path.join(LOG_DIR, f"events_{run_timestamp}.jsonl")
METRICS_LOG = os.path.join(LOG_DIR, f"metrics_{run_timestamp}.jsonl")
MEXC_ORDERS_LOG = os.path.join(LOG_DIR, f"mexc_orders_{run_timestamp}.jsonl")
AUDIT_EVENTS_LOG = os.path.join(LOG_DIR, f"audit_events_{run_timestamp}.jsonl")
ENABLE_DROP_TRIGGER_MINUTELY = True
DROP_AUDIT_INTERVAL_S = 60
DROP_AUDIT_LOG = os.path.join(LOG_DIR, f"drop_trigger_{run_timestamp}.jsonl")

# Adaptive Debug
DEBUG_MODE = "TRADING"
DEBUG_AUTO_ESCALATE = True
DEBUG_STARTUP_FULL_MINUTES = 30
DEBUG_POST_TRADE_FULL_MINUTES = 5
DEBUG_MARKET_DATA_SAMPLING = 10
DEBUG_PERFORMANCE_AGGREGATION_SECONDS = 60
DEBUG_ENABLE_LOG_COMPRESSION = True
DEBUG_LOG_RETENTION_DAYS = 30
SNAPSHOTS_PARQUET = os.path.join(SNAPSHOTS_DIR, f"snapshots_{run_timestamp}.parquet")
RUN_SUMMARY_JSON = os.path.join(REPORTS_DIR, f"run_summary_{run_timestamp}.json")
RECONCILE_REPORT = os.path.join(REPORTS_DIR, f"reconcile_{run_timestamp}.json")

# Monitoring
HEARTBEAT_INTERVAL_S = 60
METRIC_DECISION_LATENCY = True
METRIC_OHLCV_FETCH_MS = True
METRIC_API_RETRY_COUNT = True
EMIT_DECISION_EVAL = True
EMIT_GUARD_BLOCK_REASON = True
EMIT_EXIT_REASON = True
USE_CLIENT_ORDER_ID = True
SNAPSHOT_CHUNK_ROWS = 1500
SNAPSHOT_WRITE_INTERVAL_S = 60
ORDER_UPDATE_MIN_INTERVAL_S = 3
ORDER_UPDATE_MIN_DELTA_FILLED = 1e-8
ACTIVE_ORDER_SYNC_INTERVAL_S = 60
ACTIVE_ORDER_SYNC_JITTER_S = 5

# =============================================================================
# 11. FSM (FINITE STATE MACHINE) CONFIGURATION
# =============================================================================

# Master Switch & Mode Selection
FSM_ENABLED = False  # False = Legacy engine (default), True = Use FSM_MODE
FSM_MODE = "legacy"  # Options: "legacy", "fsm", or "both" (parallel validation)

# Phase Event Logging (JSONL audit trail for all phase transitions)
PHASE_LOG_FILE = os.path.join(LOG_DIR, f"phase_events_{run_timestamp}.jsonl")
PHASE_LOG_BUFFER_SIZE = 8192  # Write buffer size

# Rich Terminal Status Table (live FSM visualization)
ENABLE_RICH_TABLE = False  # Enable live-updating FSM status table
RICH_TABLE_REFRESH_HZ = 2.0  # Refresh rate (updates per second)
RICH_TABLE_SHOW_IDLE = False  # Show IDLE/WARMUP symbols in table

# Prometheus Metrics Server
ENABLE_PROMETHEUS = False  # Enable Prometheus HTTP server for metrics
PROMETHEUS_PORT = 8000  # Metrics endpoint: http://localhost:8000/metrics

# FSM Phase Timeouts (seconds)
BUY_ORDER_TIMEOUT_SECONDS = 30  # Max time in WAIT_FILL phase
SELL_ORDER_TIMEOUT_SECONDS = 20  # Max time in WAIT_SELL_FILL phase
MAX_POSITION_HOLD_MINUTES = 60  # Auto-exit timeout (overrides TRADE_TTL_MIN if lower)

# FSM Error Recovery
FSM_MAX_RETRIES = 5  # Max retry attempts before ERROR phase
FSM_BACKOFF_BASE_SECONDS = 10  # Exponential backoff base (max: 300s)

# Hybrid Mode Validation (when FSM_MODE="both")
HYBRID_VALIDATION_INTERVAL_S = 60  # Comparison check interval
HYBRID_LOG_DIVERGENCES = True  # Log when legacy/FSM states diverge

# =============================================================================
# 12. STATE FILES & PERSISTENCE
# =============================================================================

STATE_FILE_HELD = os.path.join(BASE_DIR, "held_assets.json")
STATE_FILE_OPEN_BUYS = os.path.join(BASE_DIR, "open_buy_orders.json")
HISTORY_FILE = os.path.join(BASE_DIR, "trade_history.csv")
DROP_ANCHORS_FILE = os.path.join(BASE_DIR, "drop_anchors.json")
CONFIG_BACKUP_PATH = os.path.join(SESSION_DIR, "config_backup.py")

# =============================================================================
# 13. SIMULATION / TRACING
# =============================================================================

EXCHANGE_TRACE_ENABLED = True
EXCHANGE_TRACE_PATH = None
EXCHANGE_TRACE_ORDERBOOK_LEVELS = 10
EXCHANGE_TRACE_SCRUB_IDS = True
EXCHANGE_TRACE_MAX_ARGLEN = 2000

# =============================================================================
# 14. ERWEITERTE EINSTELLUNGEN
# =============================================================================

TICKER_THREADPOOL_SIZE = 6
SYMBOL_MIN_COST_OVERRIDE = {"OKB/USDT": 10.0}
MAX_POSITION_SIZE_USD = 1000
MAX_PORTFOLIO_RISK_PCT = 0.05
SESSION_GRANULARITY = "minute"
BUY_ESCALATION_EXTRA_BPS = 20
ALLOW_MARKET_FALLBACK = True
MAX_TRADES_CONCURRENT = MAX_TRADES
MAX_CONCURRENT_POSITIONS = MAX_TRADES
FEE_RT = FEE_RATE
RUN_ID = run_id
RUN_TIMESTAMP_UTC = run_timestamp_utc
RUN_TIMESTAMP_LOCAL = run_timestamp
PREDICTIVE_BUY_ZONE_PCT = 1.0 + (PREDICTIVE_BUY_ZONE_BPS / 10_000.0)

# =============================================================================
# 15. WATCHLIST
# =============================================================================

topcoins_keys = [
    'BTCUSDT', 'XRPUSDT', 'SOLUSDT', 'BNBUSDT', 'DOGEUSDT', 'ADAUSDT',
    'TRXUSDT', 'LINKUSDT', 'AVAXUSDT', 'XLMUSDT', 'TONUSDT', 'HBARUSDT',
    'SUIUSDT', 'SHIBUSDT', 'DOTUSDT', 'LTCUSDT', 'BCHUSDT', 'UNIUSDT',
    'NEARUSDT', 'ONDOUSDT', 'AAVEUSDT', 'APTUSDT', 'ICPUSDT', 'XMRUSDT',
    'TAOUSDT', 'MNTUSDT', 'VETUSDT', 'CROUSDT', 'POLUSDT', 'KASUSDT',
    'ALGOUSDT', 'RENDERUSDT', 'FILUSDT', 'ARBUSDT', 'FETUSDT', 'ATOMUSDT',
    'TKXUSDT', 'ENAUSDT', 'TIAUSDT', 'RAYUSDT', 'JUPUSDT', 'IMXUSDT',
    'STXUSDT', 'OPUSDT', 'BERAUSDT', 'DEXEUSDT', 'XDCUSDT', 'MOVEUSDT',
    'INJUSDT', 'LDOUSDT', 'WLDUSDT', 'FTNUSDT', 'KCSUSDT', 'GRTUSDT',
    'NEXOUSDT', 'QNTUSDT', 'FLRUSDT', 'SEIUSDT', 'SANDUSDT', 'GALAUSDT',
    'XTZUSDT', 'IOTAUSDT', 'MKRUSDT', 'FLOWUSDT', 'CRVUSDT', 'AXSUSDT',
    'MANAUSDT', 'RUNEUSDT', 'PEPEUSDT', 'BONKUSDT', 'WIFUSDT', 'FLOKIUSDT',
    'POPCATUSDT', 'MOGUSDT', 'PNUTUSDT', 'TURBOUSDT', 'TRUMPUSDT',
    'DAIUSDT', 'EOSUSDT', 'NEOUSDT', 'COREUSDT', 'STRKUSDT', 'JASMYUSDT',
    'MINAUSDT', 'PENDLEUSDT', 'ORDIUSDT', 'RONUSDT', 'SNXUSDT', 'COMPUSDT',
    'ZECUSDT', 'JTOUSDT', 'GRASSUSDT', 'DYDXUSDT', 'ARUSDT'
]

# =============================================================================
# 16. MARKET DATA & RETENTION
# =============================================================================

MARKET_DATA_FLUSH_INTERVAL_S = 5
RETENTION = {
    "ticks_days": 60, "quotes_days": 60, "orderbook_days": 60,
    "ohlc1s_days": 180, "ohlc1m_days": 365, "logs_days": 365
}

# =============================================================================
# 17. TERMINAL UI
# =============================================================================

TERMINAL_PRESET = "verbose"
USE_STATUS_LINE = True
USE_ANSI_COLORS = True
USE_EMOJI_ICONS = False
SHOW_SESSION_TRADES_DIGEST = True
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
for _k, _v in TERMINAL_PRESETS.get(TERMINAL_PRESET, {}).items():
    globals()[_k] = _v
STATUS_LINE_TICK_S = 30
ENABLE_TOP_DROPS_TICKER = True
TOP_DROPS_INTERVAL_S = 60
TOP_DROPS_LIMIT = 10
TOP_DROPS_WITHIN_BPS_OF_TRIGGER = 200
ENABLE_PRETTY_TRADE_LOGS = True
USE_ANSI_COLORS = True
SHOW_EVENT_TYPE_IN_CONSOLE = True
ENABLE_PNL_MONITOR = True
PNL_MONITOR_INTERVAL_S = 120
SHOW_INDIVIDUAL_POSITIONS = True
SHOW_TOTAL_SUMMARY = True

# =============================================================================
# 18. HELPER FUNCTIONS
# =============================================================================

def backup_config():
    """Erstellt eine Kopie der config.py im Session-Ordner"""
    import logging
    logger = logging.getLogger(__name__)

    try:
        config_source = os.path.join(BASE_DIR, "config.py")
        shutil.copy2(config_source, CONFIG_BACKUP_PATH)
        logger.info(f"Config backed up to: {CONFIG_BACKUP_PATH}",
                   extra={'event_type': 'CONFIG_BACKUP_SUCCESS', 'path': CONFIG_BACKUP_PATH})
        return True
    except Exception as e:
        logger.warning(f"Could not backup config: {e}",
                      extra={'event_type': 'CONFIG_BACKUP_FAILED', 'error': str(e)})
        return False

def validate_config():
    """Leichte Konsistenzprüfungen, kann in main() aufgerufen werden."""
    problems = []
    min_notional_need = MIN_ORDER_VALUE * MIN_ORDER_BUFFER
    if POSITION_SIZE_USDT < min_notional_need:
        problems.append(f"POSITION_SIZE_USDT ({POSITION_SIZE_USDT}) < MIN_ORDER_VALUE*BUFFER ({min_notional_need:.2f})")
    if MAX_PER_SYMBOL_USD < POSITION_SIZE_USDT:
        problems.append("MAX_PER_SYMBOL_USD < POSITION_SIZE_USDT")
    if NEVER_MARKET_SELLS and ALLOW_MARKET_FALLBACK_TTL:
        problems.append("Policy-Konflikt: NEVER_MARKET_SELLS=True aber ALLOW_MARKET_FALLBACK_TTL=True")
    if any(b <= 0 for b in EXIT_LADDER_BPS) or EXIT_LADDER_BPS != sorted(EXIT_LADDER_BPS):
        problems.append("EXIT_LADDER_BPS müssen >0 und aufsteigend sein")
    if USE_TRAILING_STOP:
        if not (1.0 <= TRAILING_STOP_ACTIVATION_PCT <= TAKE_PROFIT_THRESHOLD):
            problems.append("TRAILING_STOP_ACTIVATION_PCT muss zwischen 1.0 und TP liegen")
        if not (STOP_LOSS_THRESHOLD < TRAILING_STOP_DISTANCE_PCT < 1.0):
            problems.append("TRAILING_STOP_DISTANCE_PCT muss zwischen SL und 1.0 liegen")
    if USE_TRAILING_TP:
        if not (1.0 <= TRAILING_TP_ACTIVATION_PCT <= TAKE_PROFIT_THRESHOLD):
            problems.append("TRAILING_TP_ACTIVATION_PCT muss zwischen 1.0 und TP liegen")
        if not (1 <= TRAILING_TP_STEP_BP <= 500):
            problems.append("TRAILING_TP_STEP_BP unrealistisch")
        max_tp_bps = max(1, int((TAKE_PROFIT_THRESHOLD - 1.0) * 10_000))
        if not (1 <= TRAILING_TP_UNDER_HIGH_BP < max_tp_bps):
            problems.append("TRAILING_TP_UNDER_HIGH_BP muss kleiner als die TP-Breite sein")
    if MARKET_DATA_FLUSH_INTERVAL_S <= 0:
        problems.append("MARKET_DATA_FLUSH_INTERVAL_S muss > 0 sein")
    if any(v <= 0 for v in RETENTION.values()):
        problems.append("RETENTION Werte mussen > 0 sein")
    if USE_SMA_GUARD and SMA_GUARD_MIN_RATIO >= 1.0:
        problems.append("SMA_GUARD_MIN_RATIO >= 1.0 blockiert alle Käufe")
    if not isinstance(EXCLUDE_SYMBOL_PREFIXES, (list, tuple)):
        problems.append("EXCLUDE_SYMBOL_PREFIXES muss Liste/Tuple sein")
    expected_pct = 1.0 + (PREDICTIVE_BUY_ZONE_BPS / 10_000.0)
    if abs(PREDICTIVE_BUY_ZONE_PCT - expected_pct) > 1e-9:
        problems.append("PREDICTIVE_BUY_ZONE_PCT weicht von BPS-Ableitung ab")
    if problems:
        raise ValueError("Config validation failed: " + "; ".join(problems))
    return True

def validate_config_schema():
    """Validiert kritische Config-Parameter für fail-fast Verhalten."""
    errors = []
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
    def check_enum(name, value, valid_values):
        if value not in valid_values:
            errors.append(f"{name} = {value} ist ungültig. Erlaubt: {valid_values}")
            return False
        return True
    check_range("GLOBAL_TRADING", GLOBAL_TRADING, required_type=bool)
    check_range("TAKE_PROFIT_THRESHOLD", TAKE_PROFIT_THRESHOLD, 1.001, 2.0, float)
    check_range("STOP_LOSS_THRESHOLD", STOP_LOSS_THRESHOLD, 0.5, 0.999, float)
    check_range("SWITCH_TO_SL_THRESHOLD", SWITCH_TO_SL_THRESHOLD, 0.5, 1.0, float)
    check_range("SWITCH_TO_TP_THRESHOLD", SWITCH_TO_TP_THRESHOLD, 1.0, 1.1, float)
    check_range("SWITCH_COOLDOWN_S", SWITCH_COOLDOWN_S, 5, 300, (int, float))
    if STOP_LOSS_THRESHOLD >= TAKE_PROFIT_THRESHOLD:
        errors.append("STOP_LOSS_THRESHOLD muss kleiner als TAKE_PROFIT_THRESHOLD sein")
    if SWITCH_TO_SL_THRESHOLD >= SWITCH_TO_TP_THRESHOLD:
        errors.append("SWITCH_TO_SL_THRESHOLD muss kleiner als SWITCH_TO_TP_THRESHOLD sein")
    check_range("USE_ATR_BASED_EXITS", USE_ATR_BASED_EXITS, required_type=bool)
    if USE_ATR_BASED_EXITS:
        check_range("ATR_PERIOD", ATR_PERIOD, 5, 200, int)
        check_range("ATR_SL_MULTIPLIER", ATR_SL_MULTIPLIER, 0.1, 5.0, (int, float))
        check_range("ATR_TP_MULTIPLIER", ATR_TP_MULTIPLIER, 0.5, 10.0, (int, float))
        check_range("ATR_MIN_SAMPLES", ATR_MIN_SAMPLES, 5, 500, int)
    check_range("DROP_TRIGGER_VALUE", DROP_TRIGGER_VALUE, 0.5, 0.999, float)
    check_enum("DROP_TRIGGER_MODE", DROP_TRIGGER_MODE, [1, 2, 3, 4])
    check_range("DROP_TRIGGER_LOOKBACK_MIN", DROP_TRIGGER_LOOKBACK_MIN, 1, 1440, int)
    check_range("MAX_TRADES", MAX_TRADES, 1, 50, int)
    check_range("POSITION_SIZE_USDT", POSITION_SIZE_USDT, 1.0, 10000.0, (int, float))
    check_range("MAX_PER_SYMBOL_USD", MAX_PER_SYMBOL_USD, 1.0, 100000.0, (int, float))
    check_range("TRADE_TTL_MIN", TRADE_TTL_MIN, 5, 10080, (int, float))
    check_range("COOLDOWN_MIN", COOLDOWN_MIN, 0, 1440, (int, float))
    if POSITION_SIZE_USDT > MAX_PER_SYMBOL_USD:
        errors.append("POSITION_SIZE_USDT darf nicht größer als MAX_PER_SYMBOL_USD sein")
    check_range("USE_SMA_GUARD", USE_SMA_GUARD, required_type=bool)
    if USE_SMA_GUARD:
        check_range("SMA_GUARD_WINDOW", SMA_GUARD_WINDOW, 3, 500, int)
        check_range("SMA_GUARD_MIN_RATIO", SMA_GUARD_MIN_RATIO, 0.5, 1.5, float)
    check_range("USE_VOLUME_GUARD", USE_VOLUME_GUARD, required_type=bool)
    if USE_VOLUME_GUARD:
        check_range("VOLUME_GUARD_WINDOW", VOLUME_GUARD_WINDOW, 1, 120, int)
        check_range("VOLUME_GUARD_FACTOR", VOLUME_GUARD_FACTOR, 0.1, 10.0, (int, float))
    if hasattr(globals(), 'MIN_NOTIONAL_USDT'):
        check_range("MIN_NOTIONAL_USDT", MIN_NOTIONAL_USDT, 0.1, 1000.0, (int, float))
    if USE_DROP_ANCHOR:
        check_range("ANCHOR_STALE_MINUTES", ANCHOR_STALE_MINUTES, 1, 10080, (int, float))
        check_range("ANCHOR_CLAMP_MAX_ABOVE_PEAK_PCT", ANCHOR_CLAMP_MAX_ABOVE_PEAK_PCT, 0.1, 50.0, (int, float))
        check_range("ANCHOR_MAX_START_DROP_PCT", ANCHOR_MAX_START_DROP_PCT, 0.1, 50.0, (int, float))
    if hasattr(globals(), 'ENTRY_LIMIT_OFFSET_BPS'):
        check_range("ENTRY_LIMIT_OFFSET_BPS", ENTRY_LIMIT_OFFSET_BPS, 0, 1000, (int, float))
    if hasattr(globals(), 'ENTRY_ORDER_TIF'):
        check_enum("ENTRY_ORDER_TIF", ENTRY_ORDER_TIF, ["GTC", "IOC", "FOK"])
    if errors:
        error_msg = "[ERROR] CONFIG VALIDATION FAILED!\n" + "\n".join(f"  - {err}" for err in errors)
        error_msg += f"\n\nCheck config.py lines 46-200 for main parameters"
        raise ValueError(error_msg)
    return True

LOG_LEVEL = os.getenv("BOT_LOG_LEVEL", LOG_LEVEL)


# #############################################################################
# ###  ██████╗  █████╗  ██████╗██╗  ██╗██╗    ██╗ █████╗ ██████╗ ██████╗      ██████╗ ██████╗ ███╗   ███╗██████╗  █████╗ ████████╗
# ###  ██╔══██╗██╔══██╗██╔════╝██║ ██╔╝██║    ██║██╔══██╗██╔══██╗██╔══██╗    ██╔════╝██╔═══██╗████╗ ████║██╔══██╗██╔══██╗╚══██╔══╝
# ###  ██████╔╝███████║██║     █████╔╝ ██║ █╗ ██║███████║██████╔╝██║  ██║    ██║     ██║   ██║██╔████╔██║██████╔╝███████║   ██║
# ###  ██╔══██╗██╔══██║██║     ██╔═██╗ ██║███╗██║██╔══██║██╔══██╗██║  ██║    ██║     ██║   ██║██║╚██╔╝██║██╔═══╝ ██╔══██║   ██║
# ###  ██████╔╝██║  ██║╚██████╗██║  ██╗╚███╔███╔╝██║  ██║██║  ██║██████╔╝    ╚██████╗╚██████╔╝██║ ╚═╝ ██║██║     ██║  ██║   ██║
# ###  ╚═════╝ ╚═╝  ╚═╝ ╚═════╝╚═╝  ╚═╝ ╚══╝╚══╝ ╚═╝  ╚═╝╚═╝  ╚═╝╚═════╝      ╚═════╝ ╚═════╝ ╚═╝     ╚═╝╚═╝     ╚═╝  ╚═╝   ╚═╝
# ###  ALIASE FÜR RÜCKWÄRTSKOMPATIBILITÄT - Nicht ändern!
# #############################################################################

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
drop_trigger_mode = DROP_TRIGGER_MODE
market_data_flush_interval_s = MARKET_DATA_FLUSH_INTERVAL_S
retention = RETENTION
drop_trigger_lookback_min = DROP_TRIGGER_LOOKBACK_MIN
use_spread_guard = USE_SPREAD_GUARD
guard_max_spread_bps = GUARD_MAX_SPREAD_BPS
use_vol_sigma_guard = USE_VOL_SIGMA_GUARD
vol_sigma_window = VOL_SIGMA_WINDOW
require_vol_sigma_bps_min = REQUIRE_VOL_SIGMA_BPS_MIN
exit_ioc_ttl_ms = EXIT_IOC_TTL_MS
symbol_min_cost_override = SYMBOL_MIN_COST_OVERRIDE
enable_pretty_trade_logs = ENABLE_PRETTY_TRADE_LOGS
show_event_type_in_console = SHOW_EVENT_TYPE_IN_CONSOLE
enable_pnl_monitor = ENABLE_PNL_MONITOR
verbose_guard_logs = VERBOSE_GUARD_LOGS
enable_top_drops_ticker = ENABLE_TOP_DROPS_TICKER
top_drops_interval_s = TOP_DROPS_INTERVAL_S
top_drops_limit = TOP_DROPS_LIMIT
top_drops_within_bps_of_trigger = TOP_DROPS_WITHIN_BPS_OF_TRIGGER
use_depth_sweep = USE_DEPTH_SWEEP
sweep_orderbook_levels = SWEEP_ORDERBOOK_LEVELS
max_slippage_bps_entry = MAX_SLIPPAGE_BPS_ENTRY
max_slippage_bps_exit = MAX_SLIPPAGE_BPS_EXIT
sweep_reprice_attempts = SWEEP_REPRICE_ATTEMPTS
sweep_reprice_sleep_ms = SWEEP_REPRICE_SLEEP_MS

if "Settings" not in globals():
    if "C" in globals():
        Settings = C  # type: ignore[name-defined]
    else:
        class Settings:
            def __init__(self):
                for k, v in list(globals().items()):
                    if isinstance(k, str) and k.isupper() and not k.startswith('_'):
                        setattr(self, k, v)
                        setattr(self, k.lower(), v)
            def to_dict(self):
                return {k: v for k, v in self.__dict__.items() if not k.startswith('_')}

try:
    max_trades
except NameError:
    max_trades = MAX_TRADES

if __name__ != "__main__":
    import logging
    logger = logging.getLogger(__name__)

    try:
        validate_config_schema()
        logger.info("Config Schema Validation: PASSED",
                   extra={'event_type': 'CONFIG_VALIDATION_SUCCESS'})
    except ValueError as e:
        logger.error(f"Config Schema Validation: FAILED\n{e}",
                    extra={'event_type': 'CONFIG_VALIDATION_FAILED', 'error': str(e)})
        raise
