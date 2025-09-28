# main_new.py - Schlanker Entry Point für den Trading Bot
import faulthandler, os
faulthandler.enable()
os.environ.setdefault("PYTHONFAULTHANDLER", "1")

# Global file handle for stackdump (prevents GC cleanup causing access violations)
STACKDUMP_FP = None

try:
    import numpy_fix  # NumPy Kompatibilitäts-Fix (optional)
except Exception:
    pass

import ccxt
import json
import os
import shutil
import signal
import sys
import time
import pathlib
import faulthandler
import threading
from collections import deque
from datetime import datetime, timezone
from pathlib import Path
try:
    from dotenv import load_dotenv
    load_dotenv()  # Load .env early for Telegram
except ImportError:
    # Fallback wenn dotenv nicht installiert
    def load_dotenv():
        pass

from config import *
import config as config_module
import config_lint
from logger_setup import logger, error_tracker, log_detailed_error, setup_split_logging

# --- direkt nach dem Import von config.py ---
logger.info("CFG_SNAPSHOT: GLOBAL_TRADING=%s, ON_INSUFFICIENT_BUDGET=%s",
            config_module.GLOBAL_TRADING, getattr(config_module, "ON_INSUFFICIENT_BUDGET", None))
from loggingx import log_event, get_run_summary, setup_rotating_logger
from utils import (
    SettlementManager, DustSweeper, log_initial_config
)
from portfolio import PortfolioManager
from engine import TradingEngine
from telegram_notify import init_telegram_from_config, tg
from telegram_commands import start_telegram_command_server


def _ensure_runtime_dirs():
    """
    Create runtime directories safely at startup.

    This function ensures all required session directories exist
    without causing side-effects during import (which affected tests,
    tools, linter, and IDE indexing).
    """
    from config import SESSION_DIR, LOG_DIR, STATE_DIR, REPORTS_DIR, SNAPSHOTS_DIR

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
    
    # Robust API key loading
    api_key = os.environ.get('API_KEY')
    api_secret = os.environ.get('API_SECRET')
    
    if not api_key or not api_secret:
        log_event("API_KEYS_MISSING", message="API_KEY oder API_SECRET nicht in Umgebungsvariablen gefunden. Bot startet im Observe-Modus.", level="ERROR")
        return None, False
    
    exchange = ccxt.mexc({
        'apiKey': api_key,
        'secret': api_secret,
        'enableRateLimit': True,
        'timeout': 30000,  # 30 Sekunden Timeout
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
        exchange.options['recvWindow'] = 30000       # 30s Toleranz
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
            logger.info(f"Saved load_markets → {markets_dump_path}")
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
        logger.info(f"State-Backup: held_assets -> {backup_path}")
    if os.path.exists(STATE_FILE_OPEN_BUYS):
        backup_path = os.path.join(backup_dir, f"open_buy_orders_backup_{run_timestamp}.json")
        shutil.copy2(STATE_FILE_OPEN_BUYS, backup_path)
        logger.info(f"State-Backup: open_buys -> {backup_path}")
    
    logger.info("State-Backups im Session-Ordner erstellt", extra={'event_type': 'STATE_BACKUP_CREATED', 'backup_dir': backup_dir})


def wait_for_sufficient_budget(portfolio: PortfolioManager, exchange):
    """Wartet auf ausreichendes Budget wenn konfiguriert"""
    if on_insufficient_budget == "wait" and exchange:
        while portfolio.my_budget < safe_min_budget:
            logger.info(f"Warte auf Budget... Aktuell: {portfolio.my_budget:.2f} USDT, "
                       f"Benötigt: {safe_min_budget:.2f} USDT",
                       extra={'event_type': 'WAITING_FOR_BUDGET',
                             'current': portfolio.my_budget,
                             'required': safe_min_budget})
            
            time.sleep(60)
            portfolio.refresh_budget()
            
            # Check for Ctrl+C während des Wartens
            if 'engine' in globals() and not engine.running:
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
        STACKDUMP_FP = open(stackdump_file, "w", buffering=1)
        faulthandler.dump_traceback_later(30, repeat=True, file=STACKDUMP_FP)
    except Exception as e:
        print(f"Warning: Could not setup stackdump: {e}")

    # Global exception handler
    def global_exception_handler(exc_type, exc_value, exc_traceback):
        try:
            logger.exception("UNHANDLED_EXCEPTION", exc_info=(exc_type, exc_value, exc_traceback))
        except:
            pass
        sys.__excepthook__(exc_type, exc_value, exc_traceback)

    sys.excepthook = global_exception_handler

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

    # Legacy fallback (should not be needed with coordinator)
    signal.signal(signal.SIGINT, legacy_signal_handler)
    signal.signal(signal.SIGTERM, legacy_signal_handler)

    logger.info("=" * 80, extra={'event_type': 'BOT_STARTUP'})
    logger.info("🚀 Trading Bot wird gestartet...", extra={'event_type': 'BOT_INITIALIZING'})
    logger.info(f"Session-Ordner: {SESSION_DIR}", extra={'event_type': 'SESSION_DIR', 'path': SESSION_DIR})
    
    # Extract session ID from session directory
    SESSION_ID = Path(SESSION_DIR).name  # e.g. 'session_20250907_190217'
    os.environ["BOT_SESSION_ID"] = SESSION_ID  # For logger/notifier as fallback
    
    # Config bereits oben validiert
    
    # Config-Backup erstellen
    from config import backup_config
    backup_config()
    
    # Split-Logs gleich zu Beginn aktivieren (an Root-Logger)
    from logger_setup import setup_split_logging
    import logging
    setup_split_logging(logging.getLogger())  # Root-Logger bekommt die Split-Handler
    
    # Config-Snapshot loggen
    from loggingx import config_snapshot
    import config as C
    config_snapshot({k: getattr(C, k) for k in dir(C) if k.isupper()})
    
    # Rotating Logger initialisieren (jeder Logger -> eigene Datei)
    from config import LOG_MAX_BYTES, LOG_BACKUP_COUNT, EVENTS_LOG, MEXC_ORDERS_LOG, AUDIT_EVENTS_LOG, DROP_AUDIT_LOG
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

    # Exchange Tracing Wrapper
    if getattr(config_module, "EXCHANGE_TRACE_ENABLED", False) and exchange:
        from adapters.exchange_tracer import TracedExchange
        exchange = TracedExchange(exchange, config_module, logger)

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
    settlement_manager = SettlementManager()
    dust_sweeper = DustSweeper(exchange, min_sweep_value=5.2) if exchange else None  # 5.2 > MEXC min (5.1)
    
    # Portfolio Manager
    portfolio = PortfolioManager(exchange, settlement_manager, dust_sweeper)
    
    # Sanity Check und Reconciliation mit robuster Market Data
    if exchange:
        # Initialize robust market data provider
        from adapters.exchange import ExchangeAdapter
        from services.market_data import MarketDataProvider
        from services.robust_market_data import RobustMarketDataProvider

        # V9_3-Style: Robuste Orderbuch-Nutzung aktivieren
        from config import USE_ROBUST_MARKET_FETCH
        if not USE_ROBUST_MARKET_FETCH:
            logger.info("GENERAL - Using basic market data fetch (robust system bypassed)")
            preise = {}
        else:
            logger.info("GENERAL - Using robust orderbook fetch")
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

            logger.info(f"Basic market data fetch: {len(preise)} prices loaded")
        except Exception as basic_error:
            logger.error(f"Basic market data fetch failed: {basic_error}")
            preise = {}
        
        portfolio.sanity_check_and_reconcile(preise)
    
    # Drop-Anchors initialisieren wenn BACKFILL_MINUTES=0 und Mode 4
    if BACKFILL_MINUTES == 0 and DROP_TRIGGER_MODE == 4 and USE_DROP_ANCHOR:
        # Setze initiale Anchors für alle Coins ohne persistente Anchors
        from datetime import datetime, timezone
        current_ts = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
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
            logger.info(f"Initiale Drop-Anchors für {initialized_anchors} Coins gesetzt (BACKFILL_MINUTES=0)",
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
        from config import Settings
    except ImportError:
        import config as _cfg
        Settings = getattr(_cfg, "C", None)
        if Settings is None:
            # Fallback: Klasse Settings aus Modul-Konstanten bauen
            class Settings:
                pass
            for _k, _v in _cfg.__dict__.items():
                if isinstance(_k, str) and _k.isupper():
                    setattr(Settings, _k, _v)
            del _k, _v, _cfg
    
    # Settings instanziieren (ist jetzt immer eine Klasse)
    settings = Settings()
    
    logger.info(
        f"Settings initialisiert: "
        f"TP={settings.take_profit_threshold:.3f}, "
        f"SL={settings.stop_loss_threshold:.3f}, "
        f"DT={settings.drop_trigger_value:.3f} ({-(1.0 - settings.drop_trigger_value)*100:.1f}%), "
        f"Mode={settings.drop_trigger_mode}, LB={settings.drop_trigger_lookback_min}m",
        extra={'event_type': 'SETTINGS_INIT', 'settings': settings.to_dict()}
    )
    
    # Create simple orderbookprovider (mock implementation)
    class SimpleOrderbookProvider:
        def _decision_bump(self, side: str, gate: str, reason: str):
            pass

    orderbookprovider = SimpleOrderbookProvider()

    logger.info("Creating TradingEngine...", extra={'event_type': 'BEFORE_ENGINE_INIT'})
    try:
        # Prepare watchlist for engine (keys only, values not needed)
        engine_watchlist = {symbol: {} for symbol in topcoins.keys()} if topcoins else {}
        logger.info(f"Watchlist prepared for engine: {len(engine_watchlist)} symbols",
                   extra={'event_type': 'ENGINE_WATCHLIST_PREPARED',
                          'symbol_count': len(engine_watchlist),
                          'symbols_preview': list(engine_watchlist.keys())[:10]})

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

        logger.info(f"Engine topcoins configured: {len(engine.topcoins)} symbols",
                   extra={'event_type': 'ENGINE_TOPCOINS_CONFIGURED', 'count': len(engine.topcoins)})
        logger.info("TradingEngine created successfully", extra={'event_type': 'ENGINE_INIT_COMPLETE'})

        # Register engine with shutdown coordinator
        shutdown_coordinator.register_component("trading_engine", engine)

        # Initialize and register trade cache for API optimization
        from services.trade_cache import get_trade_cache, shutdown_trade_cache
        trade_cache = get_trade_cache()

        # Register shutdown callback for trade cache
        shutdown_coordinator.add_cleanup_callback(shutdown_trade_cache)

        # Register memory manager for cleanup and monitoring
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
    try:
        logger.info("Starting Telegram command server...", extra={'event_type': 'TELEGRAM_COMMANDS_STARTING'})
        start_telegram_command_server(engine)
        logger.info("Telegram command server gestartet.", extra={'event_type': 'TELEGRAM_COMMANDS_STARTED'})
    except Exception as e:
        logger.warning(f"Telegram command server konnte nicht starten: {e}",
                       extra={'event_type': 'TELEGRAM_COMMANDS_ERROR', 'error': str(e)})
    
    # Modus-Anzeige
    mode_str = "LIVE-MODUS" if global_trading and exchange else "OBSERVE-MODUS"
    mode_reason = ""
    if not exchange:
        mode_reason = " (keine API-Keys)"
    elif not global_trading:
        mode_reason = " (Trading deaktiviert)"
    
    # Start-Info mit Budget
    start_budget = portfolio.my_budget if portfolio else 0.0
    logger.info("Bot erfolgreich initialisiert. Starte Trading-Engine...",
               extra={'event_type': 'BOT_READY'})
    logger.info(f"BOT LAEUFT IM: {mode_str}{mode_reason} | Budget: {start_budget:.2f} USDT",
               extra={'event_type': 'BOT_MODE', 'mode': 'LIVE' if global_trading and exchange else 'OBSERVE', 
                      'budget': start_budget})
    logger.info("=" * 80, extra={'event_type': 'ENGINE_STARTING'})

    # Ensure system stability (critical for avoiding race conditions)
    try:
        for handler in logging.getLogger().handlers:
            handler.flush()
        sys.stdout.flush()
        sys.stderr.flush()
    except:
        pass

    # Brief stabilization pause (resolves timing-sensitive engine startup issues)
    # Note: These print statements are critical for preventing race conditions
    print("Starting Trading Engine...", flush=True)
    time.sleep(0.2)

    # Hauptloop starten
    try:
        # Start engine with timeout monitoring
        start_time = time.time()
        engine.start()
        start_duration = time.time() - start_time
        print("Trading Engine initialized successfully", flush=True)

        if start_duration > 10:
            logger.warning(f"Engine start took {start_duration:.1f}s (>10s)",
                          extra={'event_type': 'ENGINE_START_SLOW', 'duration': start_duration})

        logger.info("Trading-Engine gestartet", extra={'event_type': 'ENGINE_START'})

        # Register additional cleanup callbacks
        shutdown_coordinator.add_cleanup_callback(lambda: logger.info("Pre-shutdown log flush"))

        # Start heartbeat monitoring (optional - detects hung engine)
        heartbeat_monitor = shutdown_coordinator.create_heartbeat_monitor(
            check_interval=30.0,
            timeout_threshold=300.0  # 5 minutes
        )
        heartbeat_monitor.start()

        # Thread-safe main loop using shutdown coordinator
        logger.info("Thread-safe hauptschleife gestartet", extra={'event_type': 'THREAD_SAFE_HEARTBEAT_STARTED'})

        next_heartbeat = time.time() + 60  # Minütliche Updates statt alle 30s
        heartbeat_failures = 0
        max_heartbeat_failures = 3

        while not shutdown_coordinator.is_shutdown_requested():
            # Wait for shutdown with timeout (responsive to signals)
            shutdown_signaled = shutdown_coordinator.wait_for_shutdown(timeout=1.0)

            if shutdown_signaled:
                logger.info("Shutdown signal detected in main loop")
                break

            # Send detailed heartbeat log every 60 seconds
            if time.time() >= next_heartbeat:
                try:
                    budget = portfolio.my_budget if portfolio else 0.0
                    mode = "LIVE" if global_trading and exchange else "OBSERVE"
                    engine_running = engine.is_running() if engine else False

                    # Gather extended heartbeat information
                    positions_count = len(engine.positions) if engine and hasattr(engine, 'positions') else 0

                    # Get robust market data stats if available
                    market_health = "unknown"
                    if 'robust_market_data_provider' in globals():
                        try:
                            health_info = globals()['robust_market_data_provider'].get_health_status()
                            market_health = health_info.get('status', 'unknown')
                        except Exception:
                            pass

                    # Get memory status if available
                    memory_status = "unknown"
                    try:
                        from services.memory_manager import get_memory_manager
                        memory_mgr = get_memory_manager()
                        memory_info = memory_mgr.get_memory_status()
                        memory_status = f"{memory_info['current_memory']['percent']:.1f}%"
                    except Exception:
                        pass

                    # Enhanced heartbeat log
                    logger.info(f"📊 HEARTBEAT - engine={engine_running}, mode={mode}, budget={budget:.2f} USDT, "
                               f"positions={positions_count}, market_health={market_health}, memory={memory_status}",
                               extra={'event_type': 'HEARTBEAT',
                                      'engine_running': engine_running,
                                      'mode': mode,
                                      'budget': budget,
                                      'positions_count': positions_count,
                                      'market_health': market_health,
                                      'memory_status': memory_status})

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

                next_heartbeat = time.time() + 60  # Nächster Heartbeat in 60s

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
        from telegram_commands import stop_telegram_command_server
        stop_telegram_command_server()
        logger.info("Telegram command server stopped")
    except Exception as e:
        logger.warning(f"Error stopping Telegram command server: {e}")

def _telegram_shutdown_summary():
    """Cleanup callback for Telegram shutdown summary"""
    try:
        from loggingx import get_run_summary
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



