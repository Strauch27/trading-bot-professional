# main_new.py - Schlanker Entry Point für den Trading Bot
import faulthandler, os
faulthandler.enable()
os.environ.setdefault("PYTHONFAULTHANDLER", "1")

# Global file handle for stackdump (prevents GC cleanup causing access violations)
STACKDUMP_FP = None

try:
    from core.utils import numpy_fix  # NumPy Kompatibilitäts-Fix (optional)
except Exception:
    pass

import ccxt
import json
import os
import shutil
import signal
import sys
import time as tmod
import pathlib
import faulthandler
import threading
from collections import deque
from datetime import datetime as dt_datetime, timezone
from pathlib import Path
try:
    from dotenv import load_dotenv  # type: ignore[assignment]
    load_dotenv()  # Load .env early for Telegram
except ImportError:
    # Fallback wenn dotenv nicht installiert - definiere Dummy-Funktion
    def load_dotenv(*args, **kwargs):  # type: ignore[no-redef,misc]
        """Fallback when dotenv is not installed"""
        pass
    load_dotenv()  # Call dummy to satisfy same flow

# Einheitlicher Config-Import + Alias
import config as config_module  # config.py liegt im Projektwurzelordner

# Importiere alle Config-Werte für lokale Nutzung
from config import *
from scripts import config_lint
from core.logging.logger_setup import logger, error_tracker, log_detailed_error, setup_split_logging

# --- direkt nach dem Import von config.py ---
logger.debug("CFG_SNAPSHOT: GLOBAL_TRADING=%s, ON_INSUFFICIENT_BUDGET=%s",
            config_module.GLOBAL_TRADING, getattr(config_module, "ON_INSUFFICIENT_BUDGET", None))
from core.logging.loggingx import log_event, get_run_summary, setup_rotating_logger
from core.utils import (
    SettlementManager, DustSweeper, log_initial_config
)
from core.portfolio import PortfolioManager
from engine import TradingEngine
# HybridEngine wird nur bei Bedarf importiert (wenn FSM_ENABLED=True)
from integrations.telegram import init_telegram_from_config, tg
from integrations.telegram import start_telegram_command_server

# UI Module für Rich Terminal Output
from ui.console_ui import banner, start_summary, line, ts
from ui.live_monitors import LiveHeartbeat, PortfolioMonitorView
from telemetry import mem


def _ensure_runtime_dirs():
    """
    Create runtime directories safely at startup.

    This function ensures all required session directories exist
    without causing side-effects during import (which affected tests,
    tools, linter, and IDE indexing).
    """
    # Config-Werte sind bereits über 'from config import *' verfügbar

    for directory in (SESSION_DIR, LOG_DIR, STATE_DIR, REPORTS_DIR, SNAPSHOTS_DIR):
        pathlib.Path(directory).mkdir(parents=True, exist_ok=True)


def legacy_signal_handler(signum, frame):
    """Legacy handler - now delegates to shutdown coordinator"""
    from services.shutdown_coordinator import get_shutdown_coordinator, ShutdownRequest, ShutdownReason

    coordinator = get_shutdown_coordinator()
    request = ShutdownRequest(
        reason=ShutdownReason.USER_INTERRUPT,
        initiator="legacy_signal_handler",
        message="Legacy signal handler triggered - migrating to coordinator"
    )
    coordinator.request_shutdown(request)


def setup_exchange():
    """Initialisiert Exchange-Verbindung"""
    load_dotenv()

    # Validate environment variables (fail-fast on missing/invalid config)
    try:
        from core.utils.env_validator import validate_environment
        env_vars = validate_environment(strict=True)  # Exit if validation fails
        logger.info("Environment validation passed", extra={'event_type': 'ENV_VALIDATION_SUCCESS'})
    except SystemExit:
        # Validation failed and already logged - re-raise to exit
        raise
    except Exception as e:
        logger.error(f"Environment validation error: {e}", extra={'event_type': 'ENV_VALIDATION_ERROR'})
        raise

    # Robust API key loading (already validated by env_validator)
    api_key = os.environ.get('API_KEY')
    api_secret = os.environ.get('API_SECRET')

    if not api_key or not api_secret:
        log_event("API_KEYS_MISSING", message="API_KEY oder API_SECRET nicht in Umgebungsvariablen gefunden. Bot startet im Observe-Modus.", level="ERROR")
        return None, False
    
    # Konservative requests Session für TLS-Stabilität (verhindert Windows crashes)
    import requests
    from requests.adapters import HTTPAdapter
    from urllib3.util.retry import Retry

    session = requests.Session()
    retry_strategy = Retry(
        total=3,
        connect=3,
        read=3,
        backoff_factor=0.5,
        status_forcelist=(429, 500, 502, 503, 504),
        allowed_methods=frozenset(['GET', 'POST'])
    )

    # Konservatives Connection-Pooling (verhindert TLS-Race-Conditions)
    adapter = HTTPAdapter(
        max_retries=retry_strategy,
        pool_connections=1,  # Nur 1 Connection-Pool
        pool_maxsize=1       # Max 1 Connection pro Pool
    )

    session.mount('https://', adapter)
    session.mount('http://', adapter)

    exchange = ccxt.mexc({
        'apiKey': api_key,
        'secret': api_secret,
        'enableRateLimit': True,
        'timeout': 30000,  # 30 Sekunden Timeout
        'session': session,  # type: ignore[arg-type]
        'options': {
            'adjustForTimeDifference': True,
            'recvWindow': 30000,  # 30s Toleranz (erhöht von 10s)
            'defaultType': 'spot',      # Exchange strikt auf Spot pinnen
            'defaultMarket': 'spot',    # Zusaetzliche Sicherheit fuer MEXC
        },
    })
    try:
        exchange.set_sandbox_mode(False)
    except Exception:
        pass

    # Robuster Zeit-Sync und Market-Loading
    try:
        # robustere Defaults
        if hasattr(exchange, 'options') and isinstance(exchange.options, dict):
            exchange.options['recvWindow'] = 30000  # type: ignore[index]
        # expliziter Zeitabgleich gegen MEXC
        _ = exchange.load_time_difference()          # ms-Diff berechnen & intern kompensieren
    except Exception as e:
        logger.warning(f"Zeitabgleich fehlgeschlagen: {e}", extra={'event_type': 'TIME_SYNC_WARN'})

    # Force reload, damit symbol->id Mapping fuer Spot fix ist
    try:
        exchange.load_markets(True)
        logger.info("Markets erfolgreich geladen", extra={'event_type': 'LOAD_MARKETS_SUCCESS'})

        # Optional: load_markets Dump für Exchange-Tracing (einmal pro Session)
        try:
            markets_dump_path = os.path.join(SESSION_DIR, "logs", "load_markets.json")
            os.makedirs(os.path.dirname(markets_dump_path), exist_ok=True)
            with open(markets_dump_path, "w", encoding="utf-8") as fh:
                json.dump(exchange.markets, fh, ensure_ascii=False, indent=2)
            logger.debug(f"Saved load_markets → {markets_dump_path}")
        except Exception as e:
            logger.warning(f"Could not dump markets: {e}")

    except Exception as e:
        logger.warning(f"load_markets(True) failed: {e}", extra={'event_type': 'LOAD_MARKETS_WARN'})
        # Bei Timestamp-Fehlern: Warnung und Hinweis
        if 'timestamp' in str(e).lower() or 'recvWindow' in str(e):
            logger.warning("Timestamp-Fehler: Systemzeit möglicherweise nicht synchron mit MEXC-Servern",
                          extra={'event_type': 'TIMESTAMP_ERROR_WARNING'})
    
    return exchange, True


def setup_topcoins(exchange):
    """Initialisiert handelbare Coins"""
    if not exchange:
        return {}
    
    # Versuche Markets zu laden mit robustem Error-Handling
    try:
        if not hasattr(exchange, 'symbols') or not exchange.symbols:
            exchange.load_markets()  # sicherstellen, dass symbols da sind
    except Exception as e:
        logger.error(f"Fehler beim Laden der Märkte: {e}", extra={'event_type': 'LOAD_MARKETS_ERROR'})
        # Fallback: verwende bereits geladene symbols falls vorhanden
        if not hasattr(exchange, 'symbols') or not exchange.symbols:
            logger.error("Keine Märkte verfügbar - Bot kann nicht starten", extra={'event_type': 'NO_MARKETS_AVAILABLE'})
            return {}
    
    # Initialize memory manager for better resource management
    from services.memory_manager import get_memory_manager, create_managed_deque
    memory_manager = get_memory_manager()

    supported = set(exchange.symbols)
    normalized = {symbol.replace("/", ""): symbol for symbol in supported}

    # Create managed deques instead of unlimited deques
    topcoins = {}
    for k in topcoins_keys:
        symbol = normalized.get(k)
        if symbol in supported:
            # Create managed deque with reasonable limits and auto-cleanup
            managed_deque = create_managed_deque(
                name=f"price_history_{symbol}",
                max_size=min(MAX_HISTORY_LEN, 1000),  # Cap at 1000 to prevent memory issues
                max_age_seconds=7200  # 2 hours max age for price data
            )
            topcoins[symbol] = managed_deque
    
    logger.info(f"{len(topcoins)} handelbare Coins gefunden")
    return topcoins


def backup_state_files():
    """Erstellt Backup der State-Dateien im Session-State-Ordner"""
    # Backup-Ordner ist jetzt STATE_DIR im Session-Ordner
    backup_dir = os.path.join(STATE_DIR, "initial_backups")
    os.makedirs(backup_dir, exist_ok=True)
    
    if os.path.exists(STATE_FILE_HELD):
        backup_path = os.path.join(backup_dir, f"held_assets_backup_{run_timestamp}.json")
        shutil.copy2(STATE_FILE_HELD, backup_path)
        logger.debug(f"State-Backup: held_assets -> {backup_path}")
    if os.path.exists(STATE_FILE_OPEN_BUYS):
        backup_path = os.path.join(backup_dir, f"open_buy_orders_backup_{run_timestamp}.json")
        shutil.copy2(STATE_FILE_OPEN_BUYS, backup_path)
        logger.debug(f"State-Backup: open_buys -> {backup_path}")

    logger.debug("State-Backups im Session-Ordner erstellt", extra={'event_type': 'STATE_BACKUP_CREATED', 'backup_dir': backup_dir})


def wait_for_sufficient_budget(portfolio: PortfolioManager, exchange):
    """Wartet auf ausreichendes Budget wenn konfiguriert"""
    from services.shutdown_coordinator import get_shutdown_coordinator

    if on_insufficient_budget == "wait" and exchange:
        shutdown_coordinator = get_shutdown_coordinator()
        while portfolio.my_budget < safe_min_budget:
            logger.info(f"Warte auf Budget... Aktuell: {portfolio.my_budget:.2f} USDT, "
                       f"Benötigt: {safe_min_budget:.2f} USDT",
                       extra={'event_type': 'WAITING_FOR_BUDGET',
                             'current': portfolio.my_budget,
                             'required': safe_min_budget})

            # Use shutdown-aware wait instead of blocking sleep
            if shutdown_coordinator.wait_for_shutdown(timeout=60.0):
                logger.info("Shutdown requested during budget wait")
                return False

            portfolio.refresh_budget()

            # Check for Ctrl+C während des Wartens
            if 'engine' in globals() and hasattr(engine, 'running') and not engine.running:  # type: ignore[attr-defined]
                return False
    

    elif on_insufficient_budget == "observe":
        if portfolio.my_budget < safe_min_budget:
            logger.info(f"Budget unter Minimum. Bot laeuft im Observe-Modus.",
                       extra={'event_type': 'OBSERVE_MODE_LOW_BUDGET',
                             'budget': portfolio.my_budget,
                             'required': safe_min_budget})
            global global_trading
            global_trading = False
            config_module.global_trading = False
            config_module.GLOBAL_TRADING = False
            if hasattr(config_module, 'GLOBAL_TRADING'):
                config_module.GLOBAL_TRADING = False
    return True



def main():
    """Hauptfunktion - Entry Point"""
    global engine

    # Enable fault handling for debugging hangs/crashes
    faulthandler.enable()

    # Create stackdump file in session directory for debugging
    try:
        stackdump_file = pathlib.Path(SESSION_DIR) / "stackdump.txt"
        stackdump_file.parent.mkdir(parents=True, exist_ok=True)
        # Handle offen halten!
        global STACKDUMP_FP
        STACKDUMP_FP = open(stackdump_file, "w", encoding="utf-8", buffering=1)
        faulthandler.dump_traceback_later(30, repeat=True, file=STACKDUMP_FP)
    except Exception as e:
        print(f"Warning: Could not setup stackdump: {e}")

    # Phase 1: Install global exception hook (structured logging)
    from core.logger_factory import install_global_excepthook
    install_global_excepthook()

    # Legacy exception handler as fallback
    def legacy_exception_handler(exc_type, exc_value, exc_traceback):
        try:
            logger.exception("UNHANDLED_EXCEPTION", exc_info=(exc_type, exc_value, exc_traceback))
        except Exception:
            # Ultra-defensive: If logging itself crashes, fall back to original hook
            pass
        sys.__excepthook__(exc_type, exc_value, exc_traceback)

    # Keep legacy handler as secondary backup (Phase 1 hook is primary)
    # sys.excepthook = legacy_exception_handler  # Commented out - Phase 1 hook takes precedence

    # ---- Runtime Directories (no side-effects during import) ----
    _ensure_runtime_dirs()

    # ---- Config Healthcheck (fail fast) ----
    try:
        config_lint.validate_config()
    except config_lint.ConfigError as e:
        print(str(e))
        raise

    # Initialize thread-safe shutdown coordinator with warn-only heartbeat monitoring
    from services.shutdown_coordinator import get_shutdown_coordinator, ShutdownCoordinator
    shutdown_coordinator = ShutdownCoordinator(
        shutdown_timeout=30.0,
        auto_shutdown_on_missed_heartbeat=False  # Warn-only mode, no auto-shutdown
    )

    # Setup thread-safe signal handlers (replaces old approach)
    shutdown_coordinator.setup_signal_handlers()

    logger.info("🚀 Trading Bot wird gestartet...", extra={'event_type': 'BOT_INITIALIZING'})
    logger.debug(f"Session-Ordner: {SESSION_DIR}", extra={'event_type': 'SESSION_DIR', 'path': SESSION_DIR})

    # Extract session ID from session directory
    SESSION_ID = Path(SESSION_DIR).name  # e.g. 'session_20250907_190217'
    os.environ["BOT_SESSION_ID"] = SESSION_ID  # For logger/notifier as fallback

    # Display Rich Banner with Session Info
    START_ISO = dt_datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    MODE_FOR_BANNER = "LIVE" if GLOBAL_TRADING else "OBSERVE"
    banner(
        app_name="Trading Bot",
        mode=MODE_FOR_BANNER,
        session_dir=SESSION_DIR,
        session_id=SESSION_ID,
        start_iso=START_ISO
    )

    # Phase 3: Set session ID and log config snapshot
    from core.trace_context import set_session_id
    from core.logger_factory import AUDIT_LOG, log_event, log_config_snapshot, log_config_diff

    set_session_id(SESSION_ID)

    # Log complete config snapshot with categorization (Phase 3)
    config_hash = log_config_snapshot(config_module, session_id=SESSION_ID)

    # Log config diff from previous run
    log_config_diff(config_module, config_hash, session_id=SESSION_ID)

    # Log CONFIG_OVERRIDES (environment-driven adjustments)
    config_overrides = {}
    # Check for common environment-driven overrides
    if os.environ.get('ENABLE_LIVE_DASHBOARD') == 'False':
        config_overrides['ENABLE_LIVE_DASHBOARD'] = 'False (from env)'
    if os.environ.get('GLOBAL_TRADING') is not None:
        config_overrides['GLOBAL_TRADING'] = f"{os.environ.get('GLOBAL_TRADING')} (from env)"
    if os.environ.get('RESET_PORTFOLIO_ON_START') is not None:
        config_overrides['RESET_PORTFOLIO_ON_START'] = f"{os.environ.get('RESET_PORTFOLIO_ON_START')} (from env)"

    if config_overrides:
        log_event(
            AUDIT_LOG(),
            "config_overrides",
            message=f"Configuration overrides detected: {', '.join(config_overrides.keys())}",
            level=logging.INFO,
            overrides=config_overrides,
            session_id=SESSION_ID
        )
        logger.info(f"CONFIG_OVERRIDES: {config_overrides}")

    # Log session start with config hash reference
    log_event(
        AUDIT_LOG(),
        "session_start",
        message=f"Trading bot session started: {SESSION_ID}",
        session_id=SESSION_ID,
        config_hash=config_hash,
        bot_version=getattr(config_module, 'BOT_VERSION', 'unknown'),
        exchange="MEXC",
        python_version=f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}"
    )
    
    # Config bereits oben validiert
    
    # Config-Backup erstellen
    backup_config = config_module.backup_config
    backup_config()
    
    # Split-Logs gleich zu Beginn aktivieren (an Root-Logger)
    from core.logging.logger_setup import setup_split_logging
    import logging
    setup_split_logging(logging.getLogger())  # Root-Logger bekommt die Split-Handler

    # Config-Snapshot loggen
    from core.logging.loggingx import config_snapshot
    C = config_module
    config_snapshot({k: getattr(C, k) for k in dir(C) if k.isupper()})
    
    # Rotating Logger initialisieren (jeder Logger -> eigene Datei)
    # Config-Werte sind bereits über 'from config import *' verfügbar
    setup_rotating_logger("events", log_file=EVENTS_LOG, max_bytes=LOG_MAX_BYTES, backups=LOG_BACKUP_COUNT)
    setup_rotating_logger("mexc", log_file=MEXC_ORDERS_LOG, max_bytes=LOG_MAX_BYTES, backups=LOG_BACKUP_COUNT)
    setup_rotating_logger("audit", log_file=AUDIT_EVENTS_LOG, max_bytes=LOG_MAX_BYTES, backups=LOG_BACKUP_COUNT)
    setup_rotating_logger("drop", log_file=DROP_AUDIT_LOG, max_bytes=LOG_MAX_BYTES, backups=LOG_BACKUP_COUNT)
    # KEIN eigener Rotator für "engine" – Engine-Logs laufen über logger_setup.LOG_FILE
    
    # Log Konfiguration
    log_initial_config({
        'max_trades': max_trades,
        'drop_trigger_value': drop_trigger_value,
        'take_profit_threshold': take_profit_threshold,
        'stop_loss_threshold': stop_loss_threshold,
        'use_trailing_stop': use_trailing_stop,
        'symbol_cooldown_minutes': symbol_cooldown_minutes,
        'min_order_buffer': min_order_buffer,
        'reset_portfolio_on_start': reset_portfolio_on_start,
        'use_ml_gatekeeper': use_ml_gatekeeper
    })
    
    # Exchange Setup
    exchange, has_api_keys = setup_exchange()

    # Phase 1: Exchange Tracing Wrapper (structured logging)
    if getattr(config_module, "EXCHANGE_TRACE_ENABLED", False) and exchange:
        from core.exchange_tracer import TracedExchange
        exchange = TracedExchange(exchange)
        logger.debug("Exchange tracing wrapper activated (Phase 1 structured logging)")

    if not has_api_keys:
        global global_trading
        global_trading = False
        config_module.global_trading = False
        config_module.GLOBAL_TRADING = False
        if hasattr(config_module, 'GLOBAL_TRADING'):
            config_module.GLOBAL_TRADING = False
    
    # Topcoins Setup
    try:
        topcoins = setup_topcoins(exchange)
        if not topcoins:
            logger.error("Keine handelbare Coins gefunden - Bot kann nicht starten", 
                        extra={'event_type': 'NO_TRADEABLE_COINS'})
            return
    except Exception as e:
        logger.error(f"Kritischer Fehler beim Topcoins-Setup: {e}", 
                    extra={'event_type': 'TOPCOINS_SETUP_ERROR'})
        return
    
    # State Backup
    backup_state_files()
    
    # Manager initialisieren
    DUST_MIN_COST_USD = config_module.DUST_MIN_COST_USD
    dust_sweeper = DustSweeper(exchange, min_sweep_value=DUST_MIN_COST_USD) if exchange else None
    settlement_manager = SettlementManager(dust_sweeper)
    
    # Portfolio Manager
    portfolio = PortfolioManager(exchange, settlement_manager, dust_sweeper)

    # Periodischer Dust-Sweep (Background-Thread)
    DUST_SWEEP_ENABLED = config_module.DUST_SWEEP_ENABLED
    DUST_SWEEP_INTERVAL_MIN = config_module.DUST_SWEEP_INTERVAL_MIN
    from services.shutdown_coordinator import get_shutdown_coordinator
    dust_sweep_thread = None
    if DUST_SWEEP_ENABLED and dust_sweeper and exchange:
        import threading

        def _dust_loop():
            """Background-Thread für periodische Dust-Sweeps"""
            logger.debug("Dust-Sweeper Thread gestartet",
                       extra={'event_type': 'DUST_SWEEP_THREAD_START',
                              'interval_min': DUST_SWEEP_INTERVAL_MIN})

            while not get_shutdown_coordinator().is_shutdown_requested():
                try:
                    # Warte das konfigurierte Intervall
                    sleep_seconds = max(60, int(DUST_SWEEP_INTERVAL_MIN) * 60)

                    # Check for shutdown während sleep (responsive)
                    for _ in range(sleep_seconds):
                        if get_shutdown_coordinator().wait_for_shutdown(timeout=1.0):
                            logger.info("Shutdown requested during dust sweep wait")
                            return

                    # Gedrosselte Preis-Abfrage: Batch von max 50 Symbolen, mit Cache und Sleep
                    try:
                        from services.market_data import fetch_ticker_cached
                        # Verwende topcoins_keys statt nicht-existierendem SYMBOLS
                        symbols_list = getattr(config_module, 'topcoins_keys', [])

                        # Nur aktive Symbole, max 50 pro Sweep-Zyklus, konvertiere zu "BTC/USDT" Format
                        symbols = [s.replace("USDT", "/USDT") for s in symbols_list[:50]] if symbols_list else []
                        prices = {}

                        for symbol in symbols:
                            try:
                                # fetch_ticker_cached() statt frischem HTTP
                                ticker = fetch_ticker_cached(exchange, symbol)
                                if ticker and ticker.get('last'):
                                    prices[symbol] = float(ticker.get('last'))  # type: ignore[arg-type]
                                # Drosseln: 200ms zwischen Calls (shutdown-aware)
                                if get_shutdown_coordinator().wait_for_shutdown(timeout=0.2):
                                    logger.debug("Shutdown requested during dust sweep price fetch")
                                    return
                            except Exception as e:
                                logger.debug(f"Dust-Sweep: Ticker für {symbol} fehlgeschlagen: {e}")
                                continue

                        logger.debug(f"Dust-Sweep: {len(prices)} Preise geladen (gedrosselt)",
                                   extra={'event_type': 'DUST_SWEEP_PRICES_LOADED', 'count': len(prices)})

                        # Dust-Sweep ausführen
                        if prices:
                            dust_sweeper.sweep(prices)

                    except Exception as price_error:
                        logger.warning(f"Dust-Sweep: Konnte Preise nicht laden: {price_error}",
                                     extra={'event_type': 'DUST_SWEEP_PRICE_ERROR', 'error': str(price_error)})

                except Exception as e:
                    logger.warning(f"Dust sweep failed: {e}",
                                 extra={'event_type': 'DUST_SWEEP_ERROR', 'error': str(e)})

        # Starte Dust-Sweep Thread
        dust_sweep_thread = threading.Thread(target=_dust_loop, daemon=True, name="DustSweeper")
        dust_sweep_thread.start()
        shutdown_coordinator.register_thread(dust_sweep_thread)
        logger.debug("Periodischer Dust-Sweeper aktiviert",
                   extra={'event_type': 'DUST_SWEEP_ACTIVATED',
                          'interval_min': DUST_SWEEP_INTERVAL_MIN})
    
    # Sanity Check und Reconciliation mit robuster Market Data
    preise: dict[str, float] = {}
    if exchange:
        # Initialize robust market data provider
        from adapters.exchange import ExchangeAdapter
        from services.market_data import MarketDataProvider
        from services.robust_market_data import RobustMarketDataProvider

        # V9_3-Style: Robuste Orderbuch-Nutzung aktivieren
        USE_ROBUST_MARKET_FETCH = config_module.USE_ROBUST_MARKET_FETCH
        if not USE_ROBUST_MARKET_FETCH:
            logger.debug("Using basic market data fetch (robust system bypassed)")
            preise = {}
        else:
            logger.debug("Using robust orderbook fetch")
            # Hier würde der robuste OB-Streamer / Snapshot+Depth Pfad starten
            preise = {}
        try:
            try:
                tickers = exchange.fetch_tickers(list(topcoins.keys()))
            except Exception as filter_error:
                logger.warning(f"Gefilterte fetch_tickers fehlgeschlagen: {filter_error}")
                tickers = exchange.fetch_tickers()

            for symbol, ticker in tickers.items():
                if symbol in topcoins and ticker and ticker.get('last'):
                    last_price = float(ticker.get('last', 0))
                    if last_price > 0:
                        preise[symbol] = last_price
                        for _ in range(5):
                            topcoins[symbol].append(last_price)

            logger.debug(f"Basic market data fetch: {len(preise)} prices loaded")
        except Exception as basic_error:
            logger.error(f"Basic market data fetch failed: {basic_error}")
            preise = {}
        
        portfolio.sanity_check_and_reconcile(preise)
    
    # Drop-Anchors initialisieren wenn BACKFILL_MINUTES=0 und Mode 4
    if BACKFILL_MINUTES == 0 and DROP_TRIGGER_MODE == 4 and USE_DROP_ANCHOR:
        # Setze initiale Anchors für alle Coins ohne persistente Anchors
        current_ts = dt_datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
        initialized_anchors = 0
        
        for symbol, price in preise.items():
            if symbol in topcoins and price > 0:
                # Prüfe ob schon ein Anchor existiert
                existing_anchor = portfolio.get_drop_anchor(symbol)
                if not existing_anchor:
                    # Setze initialen Anchor auf aktuellen Preis
                    portfolio.set_drop_anchor(symbol, price, current_ts)
                    initialized_anchors += 1
        
        if initialized_anchors > 0:
            logger.debug(f"Initiale Drop-Anchors für {initialized_anchors} Coins gesetzt (BACKFILL_MINUTES=0)",
                       extra={'event_type': 'DROP_ANCHORS_INITIALIZED',
                              'count': initialized_anchors,
                              'mode': DROP_TRIGGER_MODE})
    
    # Portfolio Reset wenn konfiguriert
    if reset_portfolio_on_start and exchange:
        portfolio.perform_startup_reset(preise)
    
    # Budget pruefen
    if not portfolio.check_startup_budget():
        if not wait_for_sufficient_budget(portfolio, exchange):
            logger.info("Bot beendet - Unzureichendes Budget",
                       extra={'event_type': 'BOT_EXIT_INSUFFICIENT_BUDGET'})
            return

    # --- direkt danach ---
    state = {
        'has_api_keys': has_api_keys,
        'budget_gate': portfolio.my_budget >= safe_min_budget if portfolio else False
    }
    logger.info("EFFECTIVE_MODE: global_trading=%s (post-gates), has_api_keys=%s, budget_gate=%s",
                config_module.GLOBAL_TRADING, state.get('has_api_keys'), state.get('budget_gate'))
    
    # Trading Engine initialisieren und starten
    # Settings fuer Dependency Injection (robust)
    try:
        Settings = config_module.Settings  # type: ignore[attr-defined]
    except (ImportError, AttributeError):
        _cfg = config_module
        Settings = getattr(_cfg, "C", None)  # type: ignore[assignment]
        if Settings is None:
            # Fallback: Klasse Settings aus Modul-Konstanten bauen
            class Settings:  # type: ignore[no-redef]
                pass
            for _k, _v in _cfg.__dict__.items():
                if isinstance(_k, str) and _k.isupper():
                    setattr(Settings, _k, _v)
            del _k, _v, _cfg

    # Settings instanziieren (ist jetzt immer eine Klasse)
    settings = Settings()  # type: ignore[misc]

    # Zugriff auf Settings-Attribute mit Fallback
    tp_threshold = getattr(settings, 'take_profit_threshold', getattr(settings, 'TAKE_PROFIT_THRESHOLD', 1.005))
    sl_threshold = getattr(settings, 'stop_loss_threshold', getattr(settings, 'STOP_LOSS_THRESHOLD', 0.990))
    dt_value = getattr(settings, 'drop_trigger_value', getattr(settings, 'DROP_TRIGGER_VALUE', 0.980))
    dt_mode = getattr(settings, 'drop_trigger_mode', getattr(settings, 'DROP_TRIGGER_MODE', 4))
    dt_lookback = getattr(settings, 'drop_trigger_lookback_min', getattr(settings, 'DROP_TRIGGER_LOOKBACK_MIN', 2))

    logger.info(
        f"Settings initialisiert: "
        f"TP={tp_threshold:.3f}, "
        f"SL={sl_threshold:.3f}, "
        f"DT={dt_value:.3f} ({-(1.0 - dt_value)*100:.1f}%), "
        f"Mode={dt_mode}, LB={dt_lookback}m",
        extra={'event_type': 'SETTINGS_INIT', 'settings': settings.to_dict() if hasattr(settings, 'to_dict') else {}}  # type: ignore[attr-defined]
    )
    
    # Create simple orderbookprovider (mock implementation)
    class SimpleOrderbookProvider:
        def _decision_bump(self, side: str, gate: str, reason: str):
            pass

    orderbookprovider = SimpleOrderbookProvider()

    # FSM Engine Selection
    FSM_ENABLED = getattr(config_module, 'FSM_ENABLED', False)
    FSM_MODE = getattr(config_module, 'FSM_MODE', 'legacy')

    logger.info(f"Creating TradingEngine (FSM_ENABLED={FSM_ENABLED}, FSM_MODE={FSM_MODE})...",
               extra={'event_type': 'BEFORE_ENGINE_INIT', 'fsm_enabled': FSM_ENABLED, 'fsm_mode': FSM_MODE})
    try:
        # Prepare watchlist for engine (keys only, values not needed)
        engine_watchlist = {symbol: {} for symbol in topcoins.keys()} if topcoins else {}
        logger.debug(f"Watchlist prepared for engine: {len(engine_watchlist)} symbols",
                   extra={'event_type': 'ENGINE_WATCHLIST_PREPARED',
                          'symbol_count': len(engine_watchlist),
                          'symbols_preview': list(engine_watchlist.keys())[:10]})

        # Engine initialization: FSM-aware or legacy
        if FSM_ENABLED:
            # Import HybridEngine only when FSM is enabled
            from engine.hybrid_engine import HybridEngine

            logger.info(f"Initializing HybridEngine in mode: {FSM_MODE}",
                       extra={'event_type': 'HYBRID_ENGINE_INIT', 'mode': FSM_MODE})

            # Hybrid engine - legacy engine will use default EngineConfig
            # (passing dict causes TypeError: 'dict' object has no attribute)
            engine = HybridEngine(
                exchange=exchange,
                portfolio=portfolio,
                orderbookprovider=orderbookprovider,
                telegram=None,  # Telegram wird später gesetzt
                watchlist=engine_watchlist,
                mode=FSM_MODE,
                engine_config=None  # Let legacy engine use defaults
            )
            logger.info(f"HybridEngine created: {engine}",
                       extra={'event_type': 'HYBRID_ENGINE_CREATED', 'mode': FSM_MODE})
        else:
            logger.info("Initializing legacy TradingEngine (FSM disabled)",
                       extra={'event_type': 'LEGACY_ENGINE_INIT'})

            engine = TradingEngine(
                exchange=exchange,
                portfolio=portfolio,
                orderbookprovider=orderbookprovider,
                telegram=None,  # Telegram wird später gesetzt
                mock_mode=False
            )

            # >>> WICHTIG: Engine mit Watchlist füttern (nur Keys werden genutzt)
            engine.topcoins = {symbol: {} for symbol in topcoins.keys()}
            # BTC/USDT sicherstellen – Guards/BTC-Filter brauchen BTC-Daten
            engine.topcoins.setdefault("BTC/USDT", {})

            logger.debug(f"Engine topcoins configured: {len(engine.topcoins)} symbols",
                       extra={'event_type': 'ENGINE_TOPCOINS_CONFIGURED', 'count': len(engine.topcoins)})

        logger.info("TradingEngine created successfully", extra={'event_type': 'ENGINE_INIT_COMPLETE'})

        # Register engine with shutdown coordinator
        shutdown_coordinator.register_component("trading_engine", engine)

        # Initialize and register trade cache for API optimization
        from services.trade_cache import get_trade_cache, shutdown_trade_cache
        trade_cache = get_trade_cache()

        # Register shutdown callback for trade cache
        shutdown_coordinator.add_cleanup_callback(shutdown_trade_cache)

        # Phase 2: Initialize idempotency store for duplicate order prevention
        try:
            from core.idempotency import initialize_idempotency_store
            idempotency_store = initialize_idempotency_store(db_path="state/idempotency.db")
            logger.info("Idempotency store initialized for duplicate order prevention",
                       extra={'event_type': 'IDEMPOTENCY_STORE_INIT'})

            # Register cleanup callback for periodic maintenance
            def idempotency_cleanup():
                try:
                    deleted = idempotency_store.cleanup_old_orders(max_age_days=7)
                    if deleted > 0:
                        logger.debug(f"Idempotency cleanup: removed {deleted} old records")
                except Exception as e:
                    logger.warning(f"Idempotency cleanup failed: {e}")

            shutdown_coordinator.add_cleanup_callback(idempotency_cleanup)
        except Exception as idempotency_error:
            logger.warning(f"Idempotency store initialization failed: {idempotency_error}",
                          extra={'event_type': 'IDEMPOTENCY_STORE_INIT_ERROR', 'error': str(idempotency_error)})

        # Register memory manager for cleanup and monitoring (optional)
        if getattr(config_module, 'ENABLE_MEMORY_MONITORING', True):
            from services.memory_manager import get_memory_manager, shutdown_memory_manager
            memory_manager = get_memory_manager()
            shutdown_coordinator.register_component("memory_manager", memory_manager)
            shutdown_coordinator.add_cleanup_callback(shutdown_memory_manager)

            # Add memory cleanup callback to trade cache
            def memory_cleanup_callback() -> int:
                """Cleanup callback for memory pressure"""
                try:
                    # Force cache cleanup and return items cleaned
                    return trade_cache.cleanup_expired()
                except Exception:
                    return 0

            memory_manager.add_cleanup_callback(memory_cleanup_callback)
            logger.debug("Memory monitoring enabled", extra={'event_type': 'MEMORY_MANAGER_ENABLED'})
        else:
            logger.debug("Memory monitoring disabled (ENABLE_MEMORY_MONITORING=False)",
                       extra={'event_type': 'MEMORY_MANAGER_DISABLED'})

    except Exception as engine_init_error:
        logger.error(f"CRITICAL: TradingEngine initialization failed: {engine_init_error}",
                     extra={'event_type': 'ENGINE_INIT_FAILED', 'error': str(engine_init_error)})
        logger.exception("Full engine init traceback:")
        raise
    
    # --- Telegram: init + Startup ---
    try:
        init_telegram_from_config()  # liest .env
        # Set session ID on the global tg instance
        tg.set_session_id(SESSION_ID)
        _mode_for_tg = "LIVE" if (global_trading and exchange) else "OBSERVE"
        tg.notify_startup(_mode_for_tg, portfolio.my_budget if portfolio else 0.0)
        logger.info("Telegram notifications activated", extra={'event_type': 'TELEGRAM_INIT_SUCCESS'})
    except Exception as e:
        logger.debug(f"Telegram init skipped: {e}", extra={'event_type':'TELEGRAM_INIT_WARN','error':str(e)})

    # --- Telegram-Kommandos (optional) ---
    # requires: telegram_commands.py im Projektordner
    if getattr(config_module, 'ENABLE_TELEGRAM_COMMANDS', True):
        try:
            logger.debug("Starting Telegram command server...", extra={'event_type': 'TELEGRAM_COMMANDS_STARTING'})
            start_telegram_command_server(engine)
            logger.debug("Telegram command server gestartet.", extra={'event_type': 'TELEGRAM_COMMANDS_STARTED'})
        except Exception as e:
            logger.warning(f"Telegram command server konnte nicht starten: {e}",
                           extra={'event_type': 'TELEGRAM_COMMANDS_ERROR', 'error': str(e)})
    else:
        logger.debug("Telegram command server disabled (ENABLE_TELEGRAM_COMMANDS=False)",
                   extra={'event_type': 'TELEGRAM_COMMANDS_DISABLED'})

    # --- FSM Optional Features ---
    if FSM_ENABLED:
        # Prometheus Metrics Server
        ENABLE_PROMETHEUS = getattr(config_module, 'ENABLE_PROMETHEUS', False)
        PROMETHEUS_PORT = getattr(config_module, 'PROMETHEUS_PORT', 8000)

        if ENABLE_PROMETHEUS:
            try:
                from telemetry.phase_metrics import start_metrics_server
                start_metrics_server(port=PROMETHEUS_PORT)
                logger.info(f"Prometheus metrics server started on port {PROMETHEUS_PORT}",
                           extra={'event_type': 'PROMETHEUS_STARTED', 'port': PROMETHEUS_PORT})
            except Exception as e:
                logger.warning(f"Failed to start Prometheus server: {e}",
                              extra={'event_type': 'PROMETHEUS_START_FAILED', 'error': str(e)})

        # Rich Terminal Status Table (optional - can coexist with Live Dashboard)
        ENABLE_RICH_TABLE = getattr(config_module, 'ENABLE_RICH_TABLE', False)

        if ENABLE_RICH_TABLE:
            try:
                from interfaces.status_table import run_live_table
                import threading

                RICH_TABLE_REFRESH_HZ = getattr(config_module, 'RICH_TABLE_REFRESH_HZ', 2.0)
                RICH_TABLE_SHOW_IDLE = getattr(config_module, 'RICH_TABLE_SHOW_IDLE', False)

                def status_table_thread():
                    """Background thread for Rich status table"""
                    try:
                        logger.info("Starting Rich status table...",
                                   extra={'event_type': 'RICH_TABLE_STARTING'})
                        # Check if engine has get_states method
                        if hasattr(engine, 'get_states'):
                            run_live_table(
                                get_states_func=lambda: engine.get_states(),  # type: ignore[attr-defined]
                                refresh_hz=RICH_TABLE_REFRESH_HZ,
                                show_idle=RICH_TABLE_SHOW_IDLE
                            )
                        else:
                            logger.warning("Engine does not have get_states method, skipping Rich status table")
                    except Exception as e:
                        logger.warning(f"Rich status table error: {e}",
                                      extra={'event_type': 'RICH_TABLE_ERROR', 'error': str(e)})

                rich_table_thread = threading.Thread(
                    target=status_table_thread,
                    daemon=True,
                    name="RichStatusTable"
                )
                rich_table_thread.start()
                shutdown_coordinator.register_thread(rich_table_thread)
                logger.info("Rich status table thread started",
                           extra={'event_type': 'RICH_TABLE_STARTED',
                                  'refresh_hz': RICH_TABLE_REFRESH_HZ,
                                  'show_idle': RICH_TABLE_SHOW_IDLE})
            except Exception as e:
                logger.warning(f"Failed to start Rich status table: {e}",
                              extra={'event_type': 'RICH_TABLE_START_FAILED', 'error': str(e)})

    # --- Live Drop Monitor (Terminal UI) (DISABLED - replaced by Live Dashboard) ---
    ENABLE_LIVE_DROP_MONITOR = False  # Deactivated - use ENABLE_LIVE_DASHBOARD instead

    if ENABLE_LIVE_DROP_MONITOR:
        try:
            from interfaces.drop_monitor import run_live_drop_monitor
            import threading

            LIVE_DROP_MONITOR_REFRESH_S = getattr(config_module, 'LIVE_DROP_MONITOR_REFRESH_S', 5.0)
            LIVE_DROP_MONITOR_TOP_N = getattr(config_module, 'LIVE_DROP_MONITOR_TOP_N', 10)

            def drop_monitor_thread():
                """Background thread for Live Drop Monitor"""
                try:
                    logger.info("Starting Live Drop Monitor...",
                               extra={'event_type': 'DROP_MONITOR_STARTING'})
                    run_live_drop_monitor(
                        engine=engine,
                        config=config_module,
                        top_n=LIVE_DROP_MONITOR_TOP_N,
                        refresh_seconds=LIVE_DROP_MONITOR_REFRESH_S
                    )
                except Exception as e:
                    logger.warning(f"Live drop monitor error: {e}",
                                  extra={'event_type': 'DROP_MONITOR_ERROR', 'error': str(e)})

            drop_monitor_thread_obj = threading.Thread(
                target=drop_monitor_thread,
                daemon=True,
                name="LiveDropMonitor"
            )
            drop_monitor_thread_obj.start()
            shutdown_coordinator.register_thread(drop_monitor_thread_obj)
            logger.info("Live drop monitor thread started",
                       extra={'event_type': 'DROP_MONITOR_STARTED',
                              'refresh_s': LIVE_DROP_MONITOR_REFRESH_S,
                              'top_n': LIVE_DROP_MONITOR_TOP_N})
        except Exception as e:
            logger.warning(f"Failed to start live drop monitor: {e}",
                          extra={'event_type': 'DROP_MONITOR_START_FAILED', 'error': str(e)})

    # Display Rich Start Summary
    start_budget = portfolio.my_budget if portfolio else 0.0
    mode_str = "LIVE" if global_trading and exchange else "OBSERVE"

    start_summary(
        coins=len(topcoins),
        budget_usdt=start_budget,
        cfg={
            'TP': tp_threshold,
            'SL': sl_threshold,
            'DT': dt_value,
            'FSM_MODE': FSM_MODE,
            'FSM_ENABLED': FSM_ENABLED
        }
    )

    logger.info("Bot erfolgreich initialisiert", extra={'event_type': 'BOT_READY'})

    # --- Live Dashboard (NEW - replaces old monitors) ---
    ENABLE_LIVE_DASHBOARD = getattr(config_module, 'ENABLE_LIVE_DASHBOARD', True)

    if ENABLE_LIVE_DASHBOARD:
        try:
            from ui.dashboard import run_dashboard
            import threading

            dashboard_thread = threading.Thread(
                target=run_dashboard,
                args=(engine, portfolio, config_module),
                daemon=True,
                name="LiveDashboard"
            )
            dashboard_thread.start()
            shutdown_coordinator.register_thread(dashboard_thread)
            logger.info("Live Dashboard thread started", extra={'event_type': 'DASHBOARD_STARTED'})

            # Give dashboard time to initialize before engine starts
            tmod.sleep(1.0)
        except Exception as e:
            logger.warning(f"Live Dashboard could not start: {e}",
                          extra={'event_type': 'DASHBOARD_START_FAILED', 'error': str(e)})

    # Ensure system stability (critical for avoiding race conditions)
    try:
        for handler in logging.getLogger().handlers:
            handler.flush()
        sys.stdout.flush()
        sys.stderr.flush()
    except (OSError, AttributeError, ValueError) as e:
        # Ignore flush errors (file closed, invalid state, etc.)
        pass

    # Brief stabilization pause (resolves timing-sensitive engine startup issues)
    tmod.sleep(0.2)

    # Hauptloop starten
    try:
        # DEBUG: Log engine type and check start method
        logger.debug(f"About to call engine.start() - engine type: {type(engine)}")
        logger.debug(f"engine.start method: {engine.start}")
        logger.debug(f"Has HybridEngine? {hasattr(engine, 'legacy_engine')}")

        # Start engine with timeout monitoring
        start_time = tmod.time()
        engine.start()
        start_duration = tmod.time() - start_time

        if start_duration > 10:
            logger.warning(f"Engine start took {start_duration:.1f}s (>10s)",
                          extra={'event_type': 'ENGINE_START_SLOW', 'duration': start_duration})

        logger.info("Trading-Engine gestartet", extra={'event_type': 'ENGINE_START'})

        # Guards-Status für TEST-Patch anzeigen (DEBUG)
        logger.debug(
            "GUARDS_OFF_TEST: sma=%s spread=%s volume=%s sigma=%s falling=%s btc_flag=%s btc_thr=%s",
            config_module.USE_SMA_GUARD,
            config_module.USE_SPREAD_GUARD,
            config_module.USE_VOLUME_GUARD,
            config_module.USE_VOL_SIGMA_GUARD,
            config_module.USE_FALLING_COINS_FILTER,
            getattr(config_module, 'USE_BTC_TREND_GUARD', None),
            getattr(config_module, 'BTC_CHANGE_THRESHOLD', None)
        )

        # Explizite Mode-Unterscheidung für Klarheit (DEBUG)
        logger.debug(
            "ENTRY_MODES: impulse_MODE=%s, DROP_TRIGGER_MODE=%s, LOOKBACK_S=%s, DROP_TRIGGER_LOOKBACK_MIN=%s",
            config_module.MODE,
            config_module.DROP_TRIGGER_MODE,
            config_module.LOOKBACK_S,
            config_module.DROP_TRIGGER_LOOKBACK_MIN
        )

        # Heartbeat Telemetry (optional)
        if getattr(config_module, 'ENABLE_HEARTBEAT_TELEMETRY', False):
            try:
                from core.utils.heartbeat_telemetry import start_auto_heartbeat
                start_auto_heartbeat(getattr(config_module, 'HEARTBEAT_INTERVAL_S', 60.0))
                logger.debug("Heartbeat telemetry enabled", extra={'event_type': 'HEARTBEAT_TELEMETRY_ENABLED'})
            except Exception as e:
                logger.warning(f"Failed to start heartbeat telemetry: {e}",
                              extra={'event_type': 'HEARTBEAT_TELEMETRY_ERROR', 'error': str(e)})
        else:
            logger.debug("Heartbeat telemetry disabled (ENABLE_HEARTBEAT_TELEMETRY=False)",
                       extra={'event_type': 'HEARTBEAT_TELEMETRY_DISABLED'})

        # Register additional cleanup callbacks
        shutdown_coordinator.add_cleanup_callback(lambda: logger.info("Pre-shutdown log flush"))

        # Start heartbeat monitoring (optional - detects hung engine)
        heartbeat_monitor = shutdown_coordinator.create_heartbeat_monitor(
            check_interval=30.0,
            timeout_threshold=300.0  # 5 minutes
        )
        heartbeat_monitor.start()

        # Thread-safe main loop using shutdown coordinator
        logger.debug("Thread-safe hauptschleife gestartet", extra={'event_type': 'THREAD_SAFE_HEARTBEAT_STARTED'})

        next_heartbeat = tmod.time() + 60  # Minütliche Updates statt alle 30s
        next_config_check = tmod.time() + 3600  # Config drift check every hour
        heartbeat_failures = 0
        max_heartbeat_failures = 3

        # Initialize LiveHeartbeat display (DISABLED - causes double rendering)
        # live_heartbeat = LiveHeartbeat()

        while not shutdown_coordinator.is_shutdown_requested():
            # Wait for shutdown with timeout (responsive to signals)
            shutdown_signaled = shutdown_coordinator.wait_for_shutdown(timeout=1.0)

            if shutdown_signaled:
                logger.info("Shutdown signal detected in main loop")
                break

            # Send detailed heartbeat log every 60 seconds
            if tmod.time() >= next_heartbeat:
                try:
                    budget = portfolio.my_budget if portfolio else 0.0
                    mode = "LIVE" if global_trading and exchange else "OBSERVE"
                    engine_running = engine.is_running() if engine else False

                    # Gather extended heartbeat information
                    positions_count = 0
                    if engine:
                        if hasattr(engine, 'positions'):
                            positions_count = len(engine.positions)  # type: ignore[attr-defined]
                        elif hasattr(engine, 'portfolio') and hasattr(engine.portfolio, 'held_assets'):
                            positions_count = len(engine.portfolio.held_assets)

                    # Get memory metrics
                    rss_mb = mem.rss_mb()
                    mem_pct = mem.percent()

                    # Calculate uptime
                    uptime_s = tmod.time() - start_time

                    # Update LiveHeartbeat display (DISABLED - logs show heartbeat info)
                    # heartbeat_stats = {
                    #     "session_id": SESSION_ID,
                    #     "time": ts(),
                    #     "engine": engine_running,
                    #     "mode": mode,
                    #     "budget": f"{budget:.2f} USDT",
                    #     "positions": positions_count,
                    #     "coins": len(topcoins),
                    #     "rss_mb": rss_mb,
                    #     "mem_pct": mem_pct,
                    #     "uptime_s": uptime_s
                    # }
                    # live_heartbeat.update(heartbeat_stats)

                    # Enhanced heartbeat log
                    logger.info(f"📊 HEARTBEAT - engine={engine_running}, mode={mode}, budget={budget:.2f} USDT, "
                               f"positions={positions_count}, RSS={rss_mb:.1f}MB, Mem={mem_pct:.1f}%, Uptime={uptime_s:.0f}s",
                               extra={'event_type': 'HEARTBEAT',
                                      'engine_running': engine_running,
                                      'mode': mode,
                                      'budget': budget,
                                      'positions_count': positions_count,
                                      'rss_mb': rss_mb,
                                      'mem_pct': mem_pct,
                                      'uptime_s': uptime_s})

                    # Phase 3: Log structured heartbeat event
                    try:
                        from core.logger_factory import HEALTH_LOG, log_event
                        log_event(
                            HEALTH_LOG(),
                            "heartbeat",
                            engine_running=engine_running,
                            mode=mode,
                            budget=budget,
                            positions_count=positions_count,
                            rss_mb=rss_mb,
                            mem_pct=mem_pct,
                            uptime_s=uptime_s
                        )
                    except Exception as health_log_error:
                        # Don't fail heartbeat if logging fails
                        logger.debug(f"Failed to log heartbeat event: {health_log_error}")

                    # If engine stopped unexpectedly, request shutdown
                    if not engine_running:
                        logger.warning("Engine stopped unexpectedly - requesting shutdown",
                                     extra={'event_type': 'ENGINE_STOPPED_UNEXPECTEDLY'})

                        from services.shutdown_coordinator import ShutdownRequest, ShutdownReason
                        request = ShutdownRequest(
                            reason=ShutdownReason.ENGINE_ERROR,
                            initiator="main_heartbeat_loop",
                            message="Engine stopped unexpectedly"
                        )
                        shutdown_coordinator.request_shutdown(request)
                        break

                    heartbeat_failures = 0  # Reset on successful heartbeat

                except Exception as hb_error:
                    heartbeat_failures += 1
                    logger.warning(f"Heartbeat error ({heartbeat_failures}/{max_heartbeat_failures}): {hb_error}",
                                 extra={'event_type': 'HEARTBEAT_ERROR', 'failure_count': heartbeat_failures})

                    # Too many heartbeat failures - something is seriously wrong
                    if heartbeat_failures >= max_heartbeat_failures:
                        logger.error("Max heartbeat failures reached - requesting emergency shutdown")

                        from services.shutdown_coordinator import ShutdownRequest, ShutdownReason
                        request = ShutdownRequest(
                            reason=ShutdownReason.HEALTH_CHECK_FAIL,
                            initiator="main_heartbeat_loop",
                            message=f"Heartbeat failed {heartbeat_failures} times",
                            emergency=True
                        )
                        shutdown_coordinator.request_shutdown(request)
                        break

                next_heartbeat = tmod.time() + 60  # Nächster Heartbeat in 60s

            # Config drift check every hour
            if tmod.time() >= next_config_check:
                try:
                    from core.logger_factory import check_config_drift

                    drift_result = check_config_drift(
                        config_module,
                        expected_hash=config_hash,
                        session_id=SESSION_ID
                    )

                    if drift_result["drift_detected"]:
                        logger.warning(
                            f"Config drift detected! Expected {config_hash}, got {drift_result['current_hash']}",
                            extra={
                                'event_type': 'CONFIG_DRIFT_WARNING',
                                'expected_hash': config_hash,
                                'current_hash': drift_result['current_hash']
                            }
                        )
                    else:
                        logger.debug(
                            f"Config drift check passed (hash: {drift_result['current_hash']})",
                            extra={
                                'event_type': 'CONFIG_DRIFT_CHECK_PASSED',
                                'config_hash': drift_result['current_hash']
                            }
                        )

                except Exception as drift_error:
                    logger.warning(
                        f"Config drift check failed: {drift_error}",
                        extra={'event_type': 'CONFIG_DRIFT_CHECK_ERROR', 'error': str(drift_error)}
                    )

                next_config_check = tmod.time() + 3600  # Next check in 1 hour

        logger.info("Thread-safe main loop ended", extra={'event_type': 'THREAD_SAFE_HEARTBEAT_ENDED'})

    except KeyboardInterrupt:
        logger.info("Shutdown requested by user (Ctrl+C)", extra={'event_type': 'USER_SHUTDOWN'})
        from services.shutdown_coordinator import ShutdownRequest, ShutdownReason
        request = ShutdownRequest(
            reason=ShutdownReason.USER_INTERRUPT,
            initiator="keyboard_interrupt_handler",
            message="KeyboardInterrupt caught in main"
        )
        shutdown_coordinator.request_shutdown(request)

    except Exception as e:
        log_detailed_error(logger, error_tracker, 'BOT_CRITICAL_ERROR', None, e,
                          {'event_type': 'BOT_CRITICAL_ERROR'})
        from services.shutdown_coordinator import ShutdownRequest, ShutdownReason
        request = ShutdownRequest(
            reason=ShutdownReason.ENGINE_ERROR,
            initiator="exception_handler",
            message=f"Critical error: {str(e)}",
            emergency=True
        )
        shutdown_coordinator.request_shutdown(request)
        raise

    finally:
        # Join heartbeat monitor thread if it exists
        if 'heartbeat_monitor' in locals() and heartbeat_monitor.is_alive():
            logger.info("Joining heartbeat monitor thread...")
            try:
                heartbeat_monitor.join(timeout=5.0)
                if heartbeat_monitor.is_alive():
                    logger.warning("Heartbeat monitor did not stop gracefully")
            except Exception as e:
                logger.warning(f"Error joining heartbeat monitor: {e}")

        # Execute coordinated shutdown
        logger.info("🔚 Executing coordinated shutdown...", extra={'event_type': 'COORDINATED_SHUTDOWN_START'})

        # Register final cleanup callbacks
        shutdown_coordinator.add_cleanup_callback(lambda: _telegram_shutdown_cleanup())
        shutdown_coordinator.add_cleanup_callback(lambda: _telegram_shutdown_summary())
        shutdown_coordinator.add_cleanup_callback(lambda: _error_summary_cleanup())

        # Execute graceful shutdown
        shutdown_success = shutdown_coordinator.execute_graceful_shutdown()

        if shutdown_success:
            logger.info("🛑 Coordinated shutdown completed successfully",
                       extra={'event_type': 'COORDINATED_SHUTDOWN_SUCCESS'})
        else:
            logger.warning("⚠️ Coordinated shutdown completed with errors",
                          extra={'event_type': 'COORDINATED_SHUTDOWN_WITH_ERRORS'})

        # Get final shutdown status
        shutdown_status = shutdown_coordinator.get_shutdown_status()
        logger.info(f"Final shutdown status: {shutdown_status}")

        # Clean up faulthandler resources
        try:
            faulthandler.cancel_dump_traceback_later()
        except Exception:
            pass
        try:
            if STACKDUMP_FP:
                STACKDUMP_FP.close()
        except Exception:
            pass

        # Trigger clean process exit (nur vom Main-Thread)
        shutdown_coordinator.trigger_process_exit(0)


def _telegram_shutdown_cleanup():
    """Cleanup callback for stopping Telegram services"""
    try:
        from integrations.telegram import stop_telegram_command_server
        stop_telegram_command_server()
        logger.info("Telegram command server stopped")
    except Exception as e:
        logger.warning(f"Error stopping Telegram command server: {e}")

def _telegram_shutdown_summary():
    """Cleanup callback for Telegram shutdown summary"""
    try:
        from core.logging.loggingx import get_run_summary
        tg.notify_shutdown(get_run_summary())
    except Exception as e:
        logger.debug(f"Telegram shutdown summary failed: {e}")


def _error_summary_cleanup():
    """Cleanup callback for error summary"""
    try:
        error_summary = error_tracker.get_error_summary()
        if error_summary['total_errors'] > 0:
            logger.info(f"Finale Error-Zusammenfassung: {error_summary}",
                       extra={'event_type': 'FINAL_ERROR_SUMMARY', **error_summary})
    except Exception as e:
        logger.warning(f"Error getting error summary: {e}")


if __name__ == "__main__":
    main()



