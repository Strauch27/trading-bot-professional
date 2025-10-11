# logger_setup.py - Logging-Konfiguration und Error-Tracking
import logging
from logging.handlers import RotatingFileHandler
import time
import re
import os
from pythonjsonlogger import jsonlogger
from collections import deque
import traceback
from datetime import datetime, timezone
from config import LOG_FILE, run_id, run_timestamp, USE_STATUS_LINE

# Import adaptive logging functions
try:
    from adaptive_logger import should_log_event, should_log_market_data, should_log_performance_metric
    ADAPTIVE_LOGGING_AVAILABLE = True
except ImportError:
    ADAPTIVE_LOGGING_AVAILABLE = False

# =================================================================================
# Benutzerdefinierte Formatter-Klasse für UTC-Zeitstempel
# =================================================================================
class UTCJsonFormatter(jsonlogger.JsonFormatter):
    def formatTime(self, record, datefmt=None):
        ct = time.gmtime(record.created)
        if datefmt:
            if '%f' in datefmt:
                base_fmt = datefmt.replace('.%f', '').rstrip('Z')
                s = time.strftime(base_fmt, ct)
                s = f"{s}.{int(record.msecs):03d}"
                if datefmt.endswith('Z') and not s.endswith('Z'):
                    s += 'Z'
            else:
                s = time.strftime(datefmt, ct)
        else:
            t = time.strftime("%Y-%m-%d %H:%M:%S", ct)
            s = f"{t},{int(record.msecs):03d}"
        return s

# =================================================================================
# Filter-Klassen
# =================================================================================
class EnsureEventTypeFilter(logging.Filter):
    def filter(self, record):
        if not hasattr(record, 'event_type'):
            record.event_type = 'GENERAL'
        return True

class AddRunIdFilter(logging.Filter):
    def filter(self, record):
        record.run_id = run_id
        return True

class AddSessionIdFilter(logging.Filter):
    """Add session_id to all log records"""
    def filter(self, record):
        # Try to get session_id from environment variable
        session_id = os.environ.get("BOT_SESSION_ID", "")
        if session_id:
            record.session_id = session_id
        return True

class ConsoleImportantInfoFilter(logging.Filter):
    """Filter für Console Handler - lässt nur wichtige INFOs durch"""
    def filter(self, record):
        # Immer durchlassen: WARNING und höher
        if record.levelno >= logging.WARNING:
            return True
        
        # Statuszeile aktiv? Dann SESSION_END/SESSION_START nicht doppelt in der Konsole zeigen
        evt = getattr(record, "event_type", "")
        if USE_STATUS_LINE and evt in ("SESSION_END", "SESSION_START", "TERMINAL_MARKET_MONITOR"):
            return False
        
        # Bei INFO Level: Nur wichtige Events durchlassen
        if record.levelno == logging.INFO:
            # Event types die IMMER angezeigt werden sollen
            important_events = {
                'BOT_READY', 'BOT_MODE', 'ENGINE_STARTING', 'ENGINE_STOPPED',
                'SESSION_START', 'SESSION_END',
                'PORTFOLIO_RESET_START', 'PORTFOLIO_RESET_COMPLETE',
                'BUY_PLACED', 'BUY_FILLED', 'SELL_PLACED', 'SELL_FILLED',
                'EXIT_EXECUTED', 'TP_FILLED', 'SL_FILLED',
                'TTL_ENFORCED', 'CIRCUIT_BREAKER_ACTIVATED', 'CIRCUIT_BREAKER_RESET',
                'INITIAL_PRICES_LOADED', 'SETTINGS_INIT',
                'INSUFFICIENT_STARTUP_BUDGET', 'BOT_EXIT_INSUFFICIENT_BUDGET',
                # Trading Events für Terminal-Sichtbarkeit
                'TRADE_OPENED', 'TRADE_CLOSED',
                'ORDER_FILLED', 'ORDER_PLACED',
                'BUY_ORDER_PLACED', 'SELL_ORDER_PLACED',
                'EXIT_PLACED', 'EXIT_FILLED',
                # Guard Events für Live-Feedback
                'GUARD_BLOCK_SUMMARY', 'GUARD_PASS_SUMMARY', 'GUARD_ROLLING_SUMMARY'
            }
            
            event_type = getattr(record, 'event_type', '')
            if event_type in important_events:
                return True
            
            # Auch wichtige Text-Patterns durchlassen
            msg = record.getMessage() if hasattr(record, 'getMessage') else ''
            important_patterns = [
                '--- Session', 'Bot erfolgreich', 'BOT LÄUFT',
                'Portfolio-Reset', 'Budget:', '======',
                'Trading-Engine', 'PORTFOLIO RESET',
                'Main trading loop started'
            ]
            
            for pattern in important_patterns:
                if pattern in msg:
                    return True
        
        # Alles andere blockieren
        return False

# =================================================================================
# Error Tracker Klasse für besseres Fehler-Management
# =================================================================================
class ErrorTracker:
    """Trackt Fehler für Statistiken und Muster-Erkennung"""
    def __init__(self, max_history=1000):
        self.error_history = deque(maxlen=max_history)
        self.error_counts = {}
        self.error_by_symbol = {}
        self.last_summary_time = time.time()
        
    def track_error(self, error_type, symbol, error_msg):
        """Fügt einen Fehler zur Historie hinzu"""
        error_entry = {
            'timestamp': time.time(),
            'type': error_type,
            'symbol': symbol,
            'message': error_msg[:200]  # Begrenzt auf 200 Zeichen
        }
        self.error_history.append(error_entry)
        
        # Update Zähler
        self.error_counts[error_type] = self.error_counts.get(error_type, 0) + 1
        if symbol:
            if symbol not in self.error_by_symbol:
                self.error_by_symbol[symbol] = {}
            self.error_by_symbol[symbol][error_type] = self.error_by_symbol[symbol].get(error_type, 0) + 1
    
    def get_recent_errors(self, seconds=300):
        """Gibt Fehler der letzten X Sekunden zurück"""
        cutoff = time.time() - seconds
        return [e for e in self.error_history if e['timestamp'] > cutoff]
    
    def get_error_summary(self):
        """Erstellt eine Zusammenfassung aller Fehler"""
        recent_5min = self.get_recent_errors(300)
        recent_1hour = self.get_recent_errors(3600)
        
        # Top-Fehlertypen
        top_errors = sorted(self.error_counts.items(), key=lambda x: x[1], reverse=True)[:5]
        
        # Problematische Symbole
        problem_symbols = sorted(
            [(s, sum(counts.values())) for s, counts in self.error_by_symbol.items()],
            key=lambda x: x[1], reverse=True
        )[:5]
        
        return {
            'total_errors': len(self.error_history),
            'errors_last_5min': len(recent_5min),
            'errors_last_hour': len(recent_1hour),
            'top_error_types': top_errors,
            'problem_symbols': problem_symbols,
            'unique_error_types': len(self.error_counts)
        }
    
    def should_alert(self, threshold_5min=10):
        """Prüft ob eine Fehler-Warnung ausgegeben werden sollte"""
        recent = self.get_recent_errors(300)
        return len(recent) >= threshold_5min

# =================================================================================
# Logger-Setup-Funktion
# =================================================================================
def setup_logger():
    logger = logging.getLogger(__name__)
    logger.setLevel(logging.DEBUG)
    
    # Verhindere Handler-Duplikate bei mehrfachem Import
    if logger.handlers:
        return logger
    
    # Stelle sicher, dass Log-Verzeichnis existiert
    log_dir = os.path.dirname(LOG_FILE)
    if log_dir:
        os.makedirs(log_dir, exist_ok=True)

    json_formatter = UTCJsonFormatter(
        '%(asctime)s %(levelname)s %(run_id)s %(session_id)s %(message)s %(event_type)s',
        rename_fields={'levelname': 'level', 'asctime': 'timestamp'},
        datefmt='%Y-%m-%dT%H:%M:%S.%fZ'
    )

    # Use RotatingFileHandler as the only file handler (delay=True for Windows compatibility)
    rotating_handler = RotatingFileHandler(LOG_FILE, maxBytes=10_000_000, backupCount=5, 
                                           encoding='utf-8', delay=True)
    rotating_handler.setLevel(logging.DEBUG)
    rotating_handler.setFormatter(json_formatter)
    logger.addHandler(rotating_handler)

    # Import console configuration
    try:
        from config import SHOW_EVENT_TYPE_IN_CONSOLE, CONSOLE_LEVEL, SHOW_THREAD_NAME_IN_CONSOLE
    except ImportError:
        SHOW_EVENT_TYPE_IN_CONSOLE = True
        CONSOLE_LEVEL = "INFO"
        SHOW_THREAD_NAME_IN_CONSOLE = False

    # Convert CONSOLE_LEVEL string to logging level
    console_level_mapping = {
        "DEBUG": logging.DEBUG,
        "INFO": logging.INFO,
        "WARNING": logging.WARNING,
        "ERROR": logging.ERROR
    }
    console_log_level = console_level_mapping.get(CONSOLE_LEVEL.upper(), logging.INFO)
    
    # Build console format string with optional thread name
    console_fmt = '%(asctime)s - %(levelname)s - '
    if SHOW_THREAD_NAME_IN_CONSOLE:
        console_fmt += '[%(threadName)s] - '
    if SHOW_EVENT_TYPE_IN_CONSOLE:
        console_fmt += '%(event_type)s - '
    console_fmt += '%(message)s'
    
    console_formatter = logging.Formatter(console_fmt, datefmt='%Y-%m-%d %H:%M:%S')
    console_handler = logging.StreamHandler()
    console_handler.setLevel(console_log_level)  # Use configured console level
    console_handler.setFormatter(console_formatter)

    # Fix Unicode encoding issues for Windows console
    import sys
    if hasattr(console_handler.stream, 'reconfigure'):
        try:
            console_handler.stream.reconfigure(encoding='utf-8')
        except (AttributeError, OSError):
            # Console reconfigure not supported on this platform
            pass
    # Entferne den restriktiven Filter für mehr Console-Output
    # console_handler.addFilter(ConsoleImportantInfoFilter())  # Mehr Events anzeigen
    logger.addHandler(console_handler)

    logger.addFilter(EnsureEventTypeFilter())
    logger.addFilter(AddRunIdFilter())
    logger.addFilter(AddSessionIdFilter())
    
    return logger

# =================================================================================
# Detailliertes Error-Logging
# =================================================================================
def log_detailed_error(logger, error_tracker, error_type, symbol, error, context=None, my_budget=0, held_assets={}, open_buy_orders={}, settlement_manager=None):
    """Detailliertes Error-Logging mit vollständigem Kontext"""
    
    # Track im Error-Tracker
    error_tracker.track_error(error_type, symbol, str(error))
    
    # Basis-Error-Info
    error_data = {
        'event_type': error_type,
        'symbol': symbol,
        'error_class': error.__class__.__name__,
        'error_message': str(error),
        'timestamp': datetime.now(timezone.utc).isoformat()
    }
    
    # Exchange-spezifische Fehler parsen
    if hasattr(error, 'code'):
        error_data['exchange_error_code'] = error.code
    
    # CCXT-spezifische Details
    if 'ccxt' in str(type(error)):
        try:
            import json
            if hasattr(error, 'response') and error.response:
                error_data['exchange_response'] = error.response
        except (AttributeError, TypeError, ValueError):
            # Failed to parse exchange response
            pass
    
    # Füge Kontext hinzu wenn vorhanden
    if context:
        error_data.update(context)
    
    # Stack-Trace für kritische Fehler
    if error_type in ['BUY_ORDER_PLACE_ERROR', 'CRITICAL_ERROR', 'STATE_CORRUPTION']:
        error_data['stack_trace'] = traceback.format_exc()
    
    # System-Status beim Fehler
    try:
        error_data['system_status'] = {
            'local_budget': my_budget,
            'held_assets_count': len(held_assets),
            'open_buy_orders_count': len(open_buy_orders),
            'pending_settlements': settlement_manager.get_total_pending() if settlement_manager else 0,
            'pending_settlements_count': len(settlement_manager.pending_settlements) if settlement_manager else 0
        }
    except (AttributeError, TypeError, KeyError):
        # System status collection failed, skip
        pass
    
    # Prüfe auf kritische Fehler-Häufung
    if error_tracker.should_alert():
        error_data['ALERT'] = 'High error rate detected!'
        error_data['error_summary'] = error_tracker.get_error_summary()
    
    # Log mit allen Details
    logger.error(f"DETAILED ERROR: {error_type} for {symbol}: {str(error)}", 
                extra=error_data)

# =================================================================================
# Split-Logging Funktionalität - Separate JSONL Files pro Kategorie
# =================================================================================

class RegexEventTypeFilter(logging.Filter):
    """Filter der Records basierend auf event_type Regex Pattern durchlässt"""
    def __init__(self, pattern: str):
        super().__init__()
        self.pattern = re.compile(pattern, re.IGNORECASE)

    def filter(self, record: logging.LogRecord) -> bool:
        event_type = getattr(record, 'event_type', '') or ''

        # Check adaptive logging if available
        if ADAPTIVE_LOGGING_AVAILABLE:
            # For market events, check adaptive logger
            if 'MARKET_' in event_type or 'OHLCV' in event_type or 'TICKER' in event_type:
                if not should_log_market_data(event_type):
                    return False

            # For general events, check adaptive logger
            level = record.levelname
            if not should_log_event(event_type, level):
                return False

        return bool(self.pattern.search(str(event_type)))

class MessageOrEventTypeFilter(logging.Filter):
    """Filter der Records durchlässt wenn event_type ODER message auf Pattern matcht"""
    def __init__(self, pattern: str):
        super().__init__()
        self.pattern = re.compile(pattern, re.IGNORECASE)

    def filter(self, record: logging.LogRecord) -> bool:
        event_type = getattr(record, 'event_type', '') or ''
        message = record.getMessage() if hasattr(record, 'getMessage') else ''

        # Check adaptive logging if available
        if ADAPTIVE_LOGGING_AVAILABLE:
            level = record.levelname
            if not should_log_event(event_type, level):
                return False

        return bool(self.pattern.search(str(event_type))) or bool(self.pattern.search(str(message)))

class LevelOrEventTypeFilter(logging.Filter):
    """Filter der Records durchlässt wenn Level >= threshold ODER event_type matcht"""
    def __init__(self, level: int, pattern: str):
        super().__init__()
        self.level = level
        self.pattern = re.compile(pattern, re.IGNORECASE)

    def filter(self, record: logging.LogRecord) -> bool:
        event_type = getattr(record, 'event_type', '') or ''

        # Always allow high-level events (warnings, errors)
        if record.levelno >= self.level:
            return True

        # Check adaptive logging for other events
        if ADAPTIVE_LOGGING_AVAILABLE:
            level_name = record.levelname
            if not should_log_event(event_type, level_name):
                return False

        return bool(self.pattern.search(str(event_type)))

def setup_split_logging(root_logger: logging.Logger = None):
    """
    Fügt zusätzliche File-Handler für kategorisierte Logs hinzu:
    - trades_*.jsonl: Buy/Sell/Orders/Fills
    - market_*.jsonl: Market Data & Snapshots
    - system_*.jsonl: System/Engine/State/Warnings/Errors
    - guards_*.jsonl: Guard Events
    Wir hängen diese Handler an den ROOT-LOGGER, damit ALLE Logger propagieren.
    """
    from config import LOG_DIR, run_timestamp
    logger = root_logger or logging.getLogger()  # root
    os.makedirs(LOG_DIR, exist_ok=True)

    # Dateinamen (im Session-Log-Ordner)
    fn_trades = os.path.join(LOG_DIR, f"trades_{run_timestamp}.jsonl")
    fn_market = os.path.join(LOG_DIR, f"market_{run_timestamp}.jsonl")
    fn_system = os.path.join(LOG_DIR, f"system_{run_timestamp}.jsonl")
    fn_guards = os.path.join(LOG_DIR, f"guards_{run_timestamp}.jsonl")

    # JSON-Formatter (UTC)
    json_formatter = UTCJsonFormatter(
        '%(asctime)s %(levelname)s %(run_id)s %(session_id)s %(message)s %(event_type)s',
        rename_fields={'levelname': 'level', 'asctime': 'timestamp'},
        datefmt='%Y-%m-%dT%H:%M:%S.%fZ'
    )

    def _ensure_file_handler(path: str, level: int, formatter, filters: list):
        abspath = os.path.abspath(path)
        for h in logger.handlers:
            if isinstance(h, logging.FileHandler):
                try:
                    if os.path.abspath(getattr(h, "baseFilename", "")) == abspath:
                        return h  # schon vorhanden
                except Exception:
                    pass
        h = logging.FileHandler(path, encoding='utf-8')
        h.setLevel(level)
        h.setFormatter(formatter)
        for f in filters:
            h.addFilter(f)
        logger.addHandler(h)
        return h

    # Trades (DEBUG level for comprehensive trade tracking)
    trades_pattern = r'^(BUY_|SELL_|TP_|SL_|ORDER_FILLED|EXIT_|SIGNAL|TRADE_)|(.*_FILLED|.*_ORDER)'
    _ensure_file_handler(
        fn_trades, logging.DEBUG, json_formatter,
        [MessageOrEventTypeFilter(trades_pattern), AddRunIdFilter()]
    )

    # Market (DEBUG level for comprehensive market data tracking)
    market_pattern = r'(MARKET_|SNAPSHOT|FEATURE|TICK|OHLCV|BACKFILL|PRICE_)'
    _ensure_file_handler(
        fn_market, logging.DEBUG, json_formatter,
        [RegexEventTypeFilter(market_pattern), AddRunIdFilter()]
    )

    # System (DEBUG level for comprehensive system tracking)
    system_pattern = r'^(ENGINE_|STATE_|BUDGET_|SETTLEMENT_|SYNC_|CLEANUP_|PORTFOLIO_|ERROR|WARNING)'
    _ensure_file_handler(
        fn_system, logging.DEBUG, json_formatter,
        [LevelOrEventTypeFilter(logging.DEBUG, system_pattern), AddRunIdFilter()]
    )

    # Guards (DEBUG level for comprehensive guard tracking)
    guards_pattern = r'(GUARD|SMA_GUARD|VOLUME_GUARD|.*_GUARD_.*)'
    _ensure_file_handler(
        fn_guards, logging.DEBUG, json_formatter,
        [RegexEventTypeFilter(guards_pattern), AddRunIdFilter()]
    )

    # Full Debug Log (captures EVERYTHING for overnight testing and error reconstruction)
    fn_debug_full = os.path.join(LOG_DIR, f"debug_full_{run_timestamp}.jsonl")
    debug_handler = logging.FileHandler(fn_debug_full, encoding='utf-8')
    debug_handler.setLevel(logging.DEBUG)
    debug_handler.setFormatter(json_formatter)
    debug_handler.addFilter(AddRunIdFilter())
    logger.addHandler(debug_handler)

    logger.info("Split logging enabled", extra={
        'event_type': 'ENGINE_LOGGING_SETUP',
        'files': {
            'trades': fn_trades,
            'market': fn_market,
            'system': fn_system,
            'guards': fn_guards
        }
    })
    return logger

# =================================================================================
# Initialisierung
# =================================================================================
logger = setup_logger()
error_tracker = ErrorTracker()