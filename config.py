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
# 3. ENTRY STRATEGIE (V9_3-Compatible Anchor-based Drop Trigger)
# =============================================================================

# V9_3 Drop Trigger Settings
DROP_TRIGGER_VALUE = 0.985  # Trigger at -1.5% drop from anchor (0.985 = ~-1.5%)
DROP_TRIGGER_MODE = 4  # 1=Session-High, 2=Rolling-High, 3=Hybrid, 4=Persistent (recommended)
DROP_TRIGGER_LOOKBACK_MIN = 5  # Rolling-High window (minutes) for Mode 2/3

# Legacy settings (kept for compatibility)
LOOKBACK_S = 120  # 2min Lookback-Fenster
MODE = 4  # Mode 4: Drop-Trigger ohne Impuls
CONFIRM_TICKS = 0  # Sofort scharf
HYSTERESIS_BPS = 5  # Hysteresis-Puffer (5 BPS)
DEBOUNCE_S = 3  # Minimale Entprellung
USE_IOC_FOR_MODE2 = True
USE_ROBUST_MARKET_FETCH = True

# V9_3 Anchor System (Mode 4 Clamps & Guards)
USE_DROP_ANCHOR = True  # Enable anchor persistence (required for Mode 4)
ANCHOR_UPDATES_WHEN_FLAT = True  # Update anchor even when no position held
ANCHOR_STALE_MINUTES = 60  # Stale-Reset: Reset anchor if older than 60 min (during runtime)
ANCHOR_MAX_AGE_HOURS = 24  # TTL: Discard anchors older than 24h on bot start (persistence filter)
ANCHOR_CLAMP_MAX_ABOVE_PEAK_PCT = 0.5  # Over-Peak-Clamp: max 0.5% above session peak
ANCHOR_MAX_START_DROP_PCT = 8.0  # Start-Drop-Clamp: anchor not >8% below start price

# Drop Tracking (Long-term solution: RollingWindowManager)
DROP_LOOKBACK_SECONDS = 300  # 5 min Lookback für Drop-Peak-Tracking
DROP_SNAPSHOT_INTERVAL_MS = 500  # Snapshot-Events alle 500ms
DROP_PERSIST_WINDOWS = True  # Persistiere RollingWindows zwischen Restarts
DROP_STORAGE_PATH = "state/drop_windows"  # Persistence-Verzeichnis

# Pipeline Architecture (NEW - Unified Market Data Pipeline)
POLL_MS = 300  # Market data poll interval in milliseconds
MD_POLL_MS = 1500  # Market data polling interval (1.5 seconds - balanced for 91 symbols)
WINDOW_LOOKBACK_S = 300  # Price cache and rolling window lookback in seconds
WINDOW_STRICT_WARMUP = False  # Allow drop% calculation immediately (no warmup period)
PERSIST_WINDOWS = True  # Persist rolling windows to disk
WINDOW_STORE = "state/drop_windows"  # Window persistence directory
USE_NEW_PIPELINE = True  # Use new snapshot-based pipeline architecture
ORDER_FLOW_ENABLED = True  # Kill switch for order flow (disable for dry-run testing)

# Batch Polling & Rate Limiting (Market Data Pipeline)
MD_USE_WEBSOCKET = False  # True = WebSocket primary, False = HTTP Polling
MD_BATCH_POLLING = True  # Enable batch-based polling for HTTP mode
MD_BATCH_SIZE = 13  # 91 symbols → 7 batches (13 symbols per batch)
MD_BATCH_INTERVAL_MS = 150  # 150ms between batches (was 1000ms - too slow!)
MD_CACHE_TTL_MS = 2500  # Soft-TTL for ticker cache (ms)
MD_JITTER_MS = 50  # Random jitter to spread request spikes (ms)

# Per-Coin Market Data Debugging
MD_DEBUG_PER_COIN = True  # Enable detailed per-coin fetch logging
MD_DEBUG_LOG_FILE = "market_data_debug.log"  # Dedicated log file for market data debugging

# Market Data Health Monitoring
MD_HEALTH_CHECK_INTERVAL_S = 60  # Check thread liveness every 60s
MD_HEARTBEAT_INTERVAL_CYCLES = 100  # Log heartbeat every 100 cycles
MD_SNAPSHOT_TIMEOUT_S = 30  # Alert if no snapshots for 30s
MD_AUTO_RESTART_ON_CRASH = False  # Auto-restart thread if it dies (experimental)
MD_SUCCESS_RATE_WARNING_THRESHOLD = 0.80  # Warn if success rate < 80%

# Rate Limiting (Token Bucket)
RATE_LIMIT_ENABLED = True  # Enable API rate limiting
RATE_LIMIT_RPM_CAP = 800  # Hard cap: 800 requests per minute
RATE_LIMIT_BURST = 25  # Token bucket burst capacity

# Optional: Hot/Cold Adaptive Polling
ADAPTIVE_HOT_PERCENT = 20  # Top 20% volatility symbols polled more frequently
HOT_INTERVAL_MS = 1000  # Hot symbols: 1 second
COLD_INTERVAL_MS = 5000  # Cold symbols: 5 seconds

# WebSocket Fallback (when MD_USE_WEBSOCKET=True)
MD_WS_FALLBACK_INTERVAL_MS = 10000  # HTTP fallback interval when WebSocket fails

# Debug Drops - Detailed Logging for Drop% Debugging
DEBUG_DROPS = True  # Enable detailed drop% debug logging with counters and watchdog

# UI Fallback Feed - Direct ticker polling for Dashboard when snapshot bus fails
UI_FALLBACK_FEED = True  # TEMPORARY WORKAROUND: Enable direct polling fallback until market_data.start() issue is fixed
UI_FALLBACK_SYMBOLS = ["BTC/USDT", "ETH/USDT", "SOL/USDT"]  # Symbols for fallback feed

# V9_3 Persistence - 4-Stream JSONL (Ticks, Snapshots, Windows, Anchors)
SNAPSHOT_MIN_PERIOD_MS = 500  # Minimum time between snapshots (ms)
MAX_FILE_MB = 50  # JSONL rotation threshold (MB)
PERSIST_TICKS = True  # Enable per-symbol tick persistence
PERSIST_SNAPSHOTS = True  # Enable snapshot stream persistence

# V9_3 Feature Flags (for rollback capability)
FEATURE_ANCHOR_ENABLED = True  # Enable anchor-based drop trigger system
FEATURE_PERSIST_STREAMS = True  # Enable 4-stream JSONL persistence
FEATURE_WARMSTART_TICKS = True  # Enable warm-start from persisted ticks
FEATURE_RETRY_BACKOFF = True  # Enable exponential backoff for failed tickers

# Guards - Market Quality (NEW - Simplified)
MAX_SPREAD_BPS = 12  # Maximum spread in basis points (disabled if guards off)
MIN_DEPTH_USD = 200  # Minimum order book depth in USD (disabled if guards off)

# Order Router - FSM Execution (NEW - Decision/Execution Separation)
ROUTER_MAX_RETRIES = 3  # Maximum retry attempts for failed orders
ROUTER_BACKOFF_MS = 400  # Initial backoff in milliseconds (exponential)
ROUTER_TIF = "IOC"  # Time in force: "IOC" (Immediate or Cancel) or "GTC" (Good Till Cancel)
ROUTER_SLIPPAGE_BPS = 20  # Maximum allowed slippage in basis points
ROUTER_MIN_NOTIONAL = 5.0  # Minimum order notional in quote currency
ROUTER_FETCH_ORDER_ON_FILL = False  # P2: Fetch full order details on fill (slower, more complete data)

# Reconciliation & Position Lifecycle (NEW - Exchange-Truth-Based Position Management)
USE_RECONCILER = True  # Enable reconciler for position lifecycle management
TAKER_FEE_RATE = 0.001  # Default taker fee (0.1%) if not provided by exchange per-trade

# Exit Engine - Prioritized Exit Rules (NEW - Signal-Based Exit Flow)
EXIT_HARD_SL_PCT = 2.0  # Max loss before forced exit (%)
EXIT_HARD_TP_PCT = 3.0  # Target profit (%)
EXIT_TRAILING_ENABLE = True  # Enable trailing stop loss
EXIT_TRAILING_PCT = 1.0  # Drawdown from peak/trough (%)
EXIT_MAX_HOLD_S = 3600 * 24  # 24h max hold time in seconds
EXIT_SL_MARKET = True  # Stop loss as market order
EXIT_TP_MARKET = True  # Take profit as market order

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

# Buy Execution (V9_3-Compatible)
BUY_MODE = "PREDICTIVE"  # "RAW" = direct trigger, "PREDICTIVE" = buy zone (stricter)
PREDICTIVE_BUY_ZONE_PCT = 0.995  # 99.5% of trigger price (buy below trigger, V9_3)
PREDICTIVE_BUY_ZONE_BPS = 3  # Legacy: 0.03% über Marktpreis
PREDICTIVE_BUY_ZONE_CAP_BPS = 15  # Phase 7: Max Cap für Repricing
USE_PREDICTIVE_BUYS = True
BUY_LIMIT_PREMIUM_BPS = 8

# Phase 7: Spread & Depth Guards
MAX_SPREAD_BPS_ENTRY = 10  # Max Spread für Buy-Repricing (10 bps)
DEPTH_MIN_NOTIONAL_USD = 200  # Min kumulierte Depth (Notional)

# =============================================================================
# ORDER FLOW HARDENING (Phases 2-12) - Feature Flags
# =============================================================================

# Phase 2: Idempotent COIDs + Persistence
ENABLE_COID_MANAGER = True  # Idempotent Client Order IDs with persistent KV-store
ENABLE_STARTUP_RECONCILE = True  # Reconcile pending COIDs on startup

# Phase 4: Entry Slippage Guard
ENABLE_ENTRY_SLIPPAGE_GUARD = False  # Check entry slippage vs expected price
# MAX_SLIPPAGE_BPS_ENTRY already defined above (15 bps)

# Phase 5: Exit TTL Timing
USE_FIRST_FILL_TS_FOR_TTL = True  # Use first_fill_ts instead of order timestamp for TTL

# Phase 6: Symbol-Scoped Locks
ENABLE_SYMBOL_LOCKS = True  # Thread-safe per-symbol locking for portfolio ops

# Phase 7: Spread/Depth Guards (already defined above)
ENABLE_SPREAD_GUARD_ENTRY = False  # Block buys when spread > MAX_SPREAD_BPS_ENTRY
ENABLE_DEPTH_GUARD_ENTRY = False  # Block buys when depth < DEPTH_MIN_NOTIONAL_USD
# MAX_SPREAD_BPS_ENTRY and DEPTH_MIN_NOTIONAL_USD defined above

# Phase 8: Risk Guards Consolidation
ENABLE_CONSOLIDATED_ENTRY_GUARDS = True  # Use evaluate_all_entry_guards()

# Phase 9: Fill Telemetry
ENABLE_FILL_TELEMETRY = True  # Track fill rates, latency, slippage metrics
FILL_TELEMETRY_MAX_HISTORY = 10000  # Max orders to keep in memory
FILL_TELEMETRY_EXPORT_ENABLED = False  # Export telemetry to JSONL
FILL_TELEMETRY_EXPORT_DIR = None  # Directory for telemetry exports (None = disabled)

# Phase 10: Consolidated Exit Evaluation
ENABLE_CONSOLIDATED_EXITS = True  # Use unified exit evaluation (ATR, trailing, profit targets)

# Phase 11 & 12: Testing & Validation
ENABLE_ORDER_FLOW_HARDENING = True  # Master switch for all order flow hardening features

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

LOG_LEVEL = "DEBUG"  # File logs: Vollständiges Debug-Logging
LOG_SCHEMA_VERSION = 4
ENABLE_PRETTY_TRADE_LOGS = True
ENABLE_RICH_LOGGING = True  # Use Rich Console for colored structured logging
CONSOLE_LEVEL = "INFO"  # Terminal: Nur wichtige Meldungen (INFO, WARNING, ERROR)
SHOW_EVENT_TYPE_IN_CONSOLE = False  # Weniger Clutter im Terminal
SHOW_THREAD_NAME_IN_CONSOLE = False  # Weniger Clutter im Terminal
ENABLE_PNL_MONITOR = True
VERBOSE_GUARD_LOGS = False  # Guards nur in File-Logs, nicht im Terminal
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
FSM_ENABLED = True  # False = Legacy engine (default), True = Use FSM_MODE
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

# FSM Timeout Parameters (used by TimeoutManager)
BUY_FILL_TIMEOUT_SECS = 30  # Buy order timeout in seconds
SELL_FILL_TIMEOUT_SECS = 30  # Sell order timeout in seconds
COOLDOWN_SECS = 60  # Cooldown duration in seconds (overrides COOLDOWN_MIN if set)

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

# Intent System & Order Router State Management (P1)
# Debounced persistence to reduce I/O load
ENGINE_TRANSIENT_STATE_FILE = os.path.join(STATE_DIR, "engine_transient.json")
ORDER_ROUTER_META_FILE = os.path.join(STATE_DIR, "order_router_meta.json")
STATE_PERSIST_INTERVAL_S = 10.0  # Write state every 10s (debounced)
STATE_PERSIST_ON_SHUTDOWN = True  # Always persist on clean shutdown
INTENT_STALE_THRESHOLD_S = 60  # Intent considered stale after 60s
ORDER_META_MAX_AGE_S = 86400  # Clean up metadata older than 24h

# P4: Stale Intent Monitoring & Alerts
STALE_INTENT_CHECK_ENABLED = True  # Enable periodic stale intent cleanup
STALE_INTENT_TELEGRAM_ALERTS = False  # Send Telegram alerts for stale intents (disable in dev/test)

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
# Note: PREDICTIVE_BUY_ZONE_PCT is set manually in Section 6 (V9_3 mode)

# =============================================================================
# 15. WATCHLIST
# =============================================================================

topcoins_keys = [
    '0GUSDT', '4USDT', 'AAVEUSDT', 'ADAUSDT', 'AEAUSDT', 'AIXUSDT', 'ALGOUSDT', 'API3USDT',
    'APTUSDT', 'ARBUSDT', 'ARUSDT', 'ASTERUSDT', 'ATLAUSDT', 'ATOMUSDT', 'AVAXUSDT', 'AXSUSDT',
    'BASUSDT', 'BATUSDT', 'BCHUSDT', 'BELUSDT', 'BGBUSDT', 'BLESSUSDT', 'BNBUSDT', 'BTCUSDT',
    'CAKEUSDT', 'COAIUSDT', 'CROUSDT', 'CRVUSDT', 'CUSDT', 'DASHUSDT', 'DOGEUSDT', 'DOTUSDT',
    'EDUUSDT', 'EIGENUSDT', 'ENAUSDT', 'ETCUSDT', 'ETHUSDT', 'FETUSDT', 'FFUSDT', 'FILUSDT',
    'FLOWUSDT', 'FLRUSDT', 'FTNUSDT', 'GALAUSDT', 'GIGGLEUSDT', 'GRTUSDT', 'HANAUSDT', 'HBARUSDT',
    'HUSDT', 'HYPERUSDT', 'ICPUSDT', 'IDOLUSDT', 'IMXUSDT', 'INITUSDT', 'INJUSDT', 'IOTAUSDT',
    'JUPUSDT', 'KASUSDT', 'KCSUSDT', 'KERNELUSDT', 'KGENUSDT', 'LABUSDT', 'LDOUSDT', 'LGCTUSDT',
    'LINKUSDT', 'LTCUSDT', 'MANAUSDT', 'MBGUSDT', 'METYAUSDT', 'MLNUSDT', 'MNTUSDT', 'MOVEUSDT',
    'MXUSDT', 'NEARUSDT', 'NEXOUSDT', 'NFPUSDT', 'OKBUSDT', 'OMUSDT', 'ONDOUSDT', 'OPNODEUSDT',
    'OPUSDT', 'PAXGUSDT', 'PENGUUSDT', 'PEPEUSDT', 'PHBUSDT', 'PKAMUSDT', 'POLUSDT', 'POPCATUSDT',
    'PROVEUSDT', 'PUMPUSDT', 'QNTUSDT', 'RAYUSDT', 'RDACUSDT', 'READYUSDT', 'RECALLUSDT', 'RENDERUSDT',
    'RIVERUSDT', 'RUNEUSDT', 'RVVUSDT', 'SANDUSDT', 'SEIUSDT', 'SHIBUSDT', 'SNXUSDT', 'SOLUSDT',
    'SOONUSDT', 'STBLUSDT', 'STXUSDT', 'SUIUSDT', 'TAOUSDT', 'TIAUSDT', 'TLMUSDT', 'TONUSDT',
    'TOWNSUSDT', 'TRUMPUSDT', 'TRXUSDT', 'UCNUSDT', 'ULTIUSDT', 'UMAUSDT', 'UNIUSDT', 'USELESSUSDT',
    'VETUSDT', 'VIRTUALUSDT', 'WALUSDT', 'WBTCUSDT', 'WIFUSDT', 'WLDUSDT', 'WLFIUSDT', 'XDCUSDT',
    'XLMUSDT', 'XMRUSDT', 'XPINUSDT', 'XPLUSUSDT', 'XRPUSDT', 'XTZUSDT', 'ZBTUSDT', 'ZECUSDT',
    'ZENUSDT', 'ZORAUSDT'
]

# Convert to CCXT format (with slash) for market data loop
TOPCOINS_SYMBOLS = [key.replace('USDT', '/USDT') for key in topcoins_keys]

# =============================================================================
# 16. MARKET DATA & RETENTION
# =============================================================================

# Intervall für Marktdaten-Updates in der Engine-Hauptschleife
# Empfehlung: 15–30s für stabile Last, 5s für maximale Reaktionsfähigkeit
MD_UPDATE_INTERVAL_S = 30.0

# Ticker-Cache TTL (Sekunden) – wie lange ein frisch geholter Ticker
# als gültig im Cache gilt (für ad‑hoc Abrufe außerhalb des Batch-Updates)
# Typisch: 5–10s. Kleinere Werte = aktueller, größere = weniger API-Last.
TICKER_CACHE_TTL = 5.0

# Batch-Fetch Einstellungen für Markt-Daten (Rate-Limit freundlich)
MARKET_DATA_USE_FETCH_TICKERS = True      # fetch_tickers() mit Symbol-Chunking verwenden
MARKET_DATA_BATCH_SIZE = 50              # Anzahl Symbole pro Batch-Request
MARKET_DATA_BATCH_DELAY_S = 0.05         # Pause zwischen Batch-Requests in Sekunden

# Retry/Degrade Settings für Market-Data
MARKET_DATA_MAX_RETRIES = 2              # Anzahl zusätzlicher Einzel-Retries nach Batch-Fehlschlag
MARKET_DATA_RETRY_DELAY_S = 0.3          # Delay zwischen Retries
MARKET_DATA_FAILURE_DEGRADE_THRESHOLD = 5  # Ab wie vielen Fehlschlägen Symbol verlangsamen
MARKET_DATA_DEGRADE_INTERVAL_S = 30.0      # Dauer der Verlangsamung in Sekunden
MARKET_DATA_FAILURE_LOG_TOP_N = 5         # Wie viele Fehler-Symbole im Health-Log anzeigen
MARKET_DATA_HEALTH_LOG_INTERVAL_S = 60.0   # Intervall für Health-Log (Sekunden)

# Snapshot Stale Handling
SNAPSHOT_STALE_TTL_S = 30.0              # Gültigkeitsdauer eines Snapshots in Sekunden

# Optional: Budget Refresh nach Market-Data Update
MD_REFRESH_PORTFOLIO_BUDGET = False

# Optional: Export Health-Stats to JSONL (None = disabled, or path like "data/md_health")
MARKET_DATA_HEALTH_EXPORT_DIR = None

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

# Live Monitors (Rich Console Live Displays)
ENABLE_LIVE_MONITORS = True  # Enable Rich Live Monitors (Heartbeat, Drop, Portfolio)
ENABLE_LIVE_HEARTBEAT = True  # Show live system + API + fill metrics panel
ENABLE_LIVE_DASHBOARD = True  # Combined dashboard with all monitors
LIVE_MONITOR_REFRESH_S = 2.0  # Update interval in seconds (2.0 = 0.5 Hz)

# Live Drop Monitor (Terminal UI)
ENABLE_LIVE_DROP_MONITOR = True  # Live-updating Terminal-Table mit Top Drops
LIVE_DROP_MONITOR_REFRESH_S = 5  # Update-Intervall in Sekunden
LIVE_DROP_MONITOR_TOP_N = 10  # Anzahl der Top Drops (max 20)

# Drop Ticker (Legacy)
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
    if MD_UPDATE_INTERVAL_S <= 0:
        problems.append("MD_UPDATE_INTERVAL_S muss > 0 sein")
    if TICKER_CACHE_TTL <= 0:
        problems.append("TICKER_CACHE_TTL muss > 0 sein")
    if MARKET_DATA_BATCH_SIZE <= 0:
        problems.append("MARKET_DATA_BATCH_SIZE muss > 0 sein")
    if MARKET_DATA_BATCH_DELAY_S < 0:
        problems.append("MARKET_DATA_BATCH_DELAY_S darf nicht negativ sein")
    if MARKET_DATA_MAX_RETRIES < 0:
        problems.append("MARKET_DATA_MAX_RETRIES darf nicht negativ sein")
    if MARKET_DATA_RETRY_DELAY_S < 0:
        problems.append("MARKET_DATA_RETRY_DELAY_S darf nicht negativ sein")
    if MARKET_DATA_FAILURE_DEGRADE_THRESHOLD < 0:
        problems.append("MARKET_DATA_FAILURE_DEGRADE_THRESHOLD darf nicht negativ sein")
    if MARKET_DATA_DEGRADE_INTERVAL_S <= 0:
        problems.append("MARKET_DATA_DEGRADE_INTERVAL_S muss > 0 sein")
    if MARKET_DATA_FAILURE_LOG_TOP_N <= 0:
        problems.append("MARKET_DATA_FAILURE_LOG_TOP_N muss > 0 sein")
    if MARKET_DATA_HEALTH_LOG_INTERVAL_S <= 0:
        problems.append("MARKET_DATA_HEALTH_LOG_INTERVAL_S muss > 0 sein")
    if SNAPSHOT_STALE_TTL_S <= 0:
        problems.append("SNAPSHOT_STALE_TTL_S muss > 0 sein")
    if not isinstance(MD_REFRESH_PORTFOLIO_BUDGET, bool):
        problems.append("MD_REFRESH_PORTFOLIO_BUDGET muss bool sein")
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
market_update_interval = MD_UPDATE_INTERVAL_S
md_update_interval_s = MD_UPDATE_INTERVAL_S
ticker_cache_ttl = TICKER_CACHE_TTL
market_data_use_fetch_tickers = MARKET_DATA_USE_FETCH_TICKERS
market_data_batch_size = MARKET_DATA_BATCH_SIZE
market_data_batch_delay_s = MARKET_DATA_BATCH_DELAY_S
market_data_max_retries = MARKET_DATA_MAX_RETRIES
market_data_retry_delay_s = MARKET_DATA_RETRY_DELAY_S
market_data_failure_degrade_threshold = MARKET_DATA_FAILURE_DEGRADE_THRESHOLD
market_data_degrade_interval_s = MARKET_DATA_DEGRADE_INTERVAL_S
market_data_failure_log_top_n = MARKET_DATA_FAILURE_LOG_TOP_N
market_data_health_log_interval_s = MARKET_DATA_HEALTH_LOG_INTERVAL_S
snapshot_stale_ttl_s = SNAPSHOT_STALE_TTL_S
md_refresh_portfolio_budget = MD_REFRESH_PORTFOLIO_BUDGET
market_data_health_export_dir = MARKET_DATA_HEALTH_EXPORT_DIR
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
enable_live_drop_monitor = ENABLE_LIVE_DROP_MONITOR
live_drop_monitor_refresh_s = LIVE_DROP_MONITOR_REFRESH_S
live_drop_monitor_top_n = LIVE_DROP_MONITOR_TOP_N
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
