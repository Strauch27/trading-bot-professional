# Trading Bot - Detaillierter Implementierungsplan
**Plan Version:** 1.0
**Erstellt:** 2025-10-26
**Basis:** CODE_REVIEW_CLAUDE.md v1.1
**Scope:** Alle 12 verifizierten Probleme + 2 Feature-Integrationen

---

## Executive Summary

Dieser Plan adressiert **12 verifizierte Probleme** aus der Code Review in **4 Phasen** über **3-4 Wochen**. Die Implementierung ist so strukturiert, dass kritische Fixes zuerst durchgeführt werden und jede Phase einzeln getestet und deployed werden kann.

### Gesamt-Statistik
- **Tasks insgesamt:** 14
- **Kritische Fixes:** 4 (Phase 1)
- **Hohe Priorität:** 5 (Phase 2)
- **Mittlere Priorität:** 3 (Phase 3)
- **Feature Integration:** 2 (Phase 4)
- **Geschätzter Aufwand:** 16-20 Stunden
- **Geschätzte Dauer:** 3-4 Wochen

### Success Metrics
- ✅ Keine Race Conditions bei Signal Handling
- ✅ Alle Threads gracefully gestoppt
- ✅ State persistence bei Crash
- ✅ Logging Queue sauber beendet
- ✅ Regression Tests: 100% Pass
- ✅ Keine neuen Bugs eingeführt

---

## Phase 1: Kritische Fixes (Woche 1)
**Priorität:** CRITICAL
**Aufwand:** 4-5 Stunden
**Deploy:** Vor nächstem Production Run

### Task 1.1: Fix Duplicate Signal Handlers (Problem 2)
**Priorität:** CRITICAL | **Aufwand:** 30 Minuten | **Risk:** LOW

**Problem:**
Legacy signal handlers überschreiben ShutdownCoordinator's thread-safe handlers (main.py:346-348), was zu Race Conditions führen kann.

**Betroffene Dateien:**
- `main.py` (Lines 346-348)

**Code-Änderung:**

**VORHER (main.py:346-348):**
```python
# Setup thread-safe signal handlers (replaces old approach)
shutdown_coordinator.setup_signal_handlers()

# Legacy fallback (should not be needed with coordinator)
signal.signal(signal.SIGINT, legacy_signal_handler)
signal.signal(signal.SIGTERM, legacy_signal_handler)
```

**NACHHER:**
```python
# Setup thread-safe signal handlers
shutdown_coordinator.setup_signal_handlers()

# Note: Legacy signal handlers REMOVED to prevent override
# ShutdownCoordinator now handles all signal processing
logger.debug(
    "Signal handlers registered via ShutdownCoordinator",
    extra={'event_type': 'SIGNAL_HANDLERS_SETUP'}
)
```

**Testing:**
```bash
# Test 1: Normal Shutdown (Ctrl+C)
python main.py &
PID=$!
sleep 5
kill -SIGINT $PID
# Expected: Clean shutdown via coordinator

# Test 2: SIGTERM
python main.py &
PID=$!
sleep 5
kill -SIGTERM $PID
# Expected: Clean shutdown via coordinator

# Test 3: Verify no race condition
grep -n "Shutdown signal received" latest_session/logs/events_*.jsonl
# Expected: Exactly one shutdown event, no duplicates
```

**Rollback-Plan:**
```python
# If issues occur, temporarily restore:
signal.signal(signal.SIGINT, legacy_signal_handler)
signal.signal(signal.SIGTERM, legacy_signal_handler)
# And disable coordinator handlers for investigation
```

**Success Criteria:**
- ✅ Shutdown via Ctrl+C funktioniert
- ✅ Shutdown via SIGTERM funktioniert
- ✅ Keine duplicate shutdown messages in logs
- ✅ Telegram shutdown notification wird gesendet

---

### Task 1.2: Fix Cleanup Callbacks Registration (Problem 9)
**Priorität:** CRITICAL | **Aufwand:** 45 Minuten | **Risk:** MEDIUM

**Problem:**
Cleanup callbacks werden im `finally` block registriert (main.py:1213-1216), was zu spät ist wenn `execute_graceful_shutdown()` bereits gelaufen ist.

**Betroffene Dateien:**
- `main.py` (Lines 1024-1244)

**Code-Änderung:**

**VORHER (main.py:1024-1244):**
```python
try:
    # Wait for shutdown signal
    shutdown_coordinator.wait_for_shutdown(check_interval=1.0)

except KeyboardInterrupt:
    logger.info("Shutdown signal received (KeyboardInterrupt)")
    shutdown_coordinator.request_shutdown()

except Exception as e:
    logger.error(f"Critical error in main loop: {e}", exc_info=True)
    shutdown_coordinator.request_shutdown()

finally:
    logger.info("Starting graceful shutdown...")

    # Cleanup Callbacks Registration (TOO LATE!)
    shutdown_coordinator.add_cleanup_callback(
        "telegram_shutdown",
        _telegram_shutdown_cleanup
    )
    shutdown_coordinator.add_cleanup_callback(
        "telegram_summary",
        _telegram_shutdown_summary
    )
    shutdown_coordinator.add_cleanup_callback(
        "error_summary",
        _error_summary_cleanup
    )

    # Execute Graceful Shutdown
    success = shutdown_coordinator.execute_graceful_shutdown()
    shutdown_coordinator.trigger_process_exit(0)
```

**NACHHER:**
```python
# Register cleanup callbacks BEFORE entering main loop
# This ensures they execute even if shutdown is triggered internally
logger.info("Registering shutdown cleanup callbacks...")

shutdown_coordinator.add_cleanup_callback(
    "telegram_shutdown",
    _telegram_shutdown_cleanup
)
shutdown_coordinator.add_cleanup_callback(
    "telegram_summary",
    lambda: _telegram_shutdown_summary(engine, portfolio)
)
shutdown_coordinator.add_cleanup_callback(
    "error_summary",
    _error_summary_cleanup
)
shutdown_coordinator.add_cleanup_callback(
    "metrics_export",
    lambda: _export_metrics_on_shutdown(engine) if hasattr(engine, 'metrics') else None
)

logger.info(
    f"Registered {len(shutdown_coordinator._cleanup_callbacks)} cleanup callbacks",
    extra={'event_type': 'CLEANUP_CALLBACKS_REGISTERED'}
)

try:
    # Wait for shutdown signal
    shutdown_coordinator.wait_for_shutdown(check_interval=1.0)

except KeyboardInterrupt:
    logger.info("Shutdown signal received (KeyboardInterrupt)")
    shutdown_coordinator.request_shutdown()

except Exception as e:
    logger.error(f"Critical error in main loop: {e}", exc_info=True)
    shutdown_coordinator.request_shutdown()

finally:
    logger.info("Starting graceful shutdown...")

    # Execute Graceful Shutdown (callbacks already registered)
    success = shutdown_coordinator.execute_graceful_shutdown()

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

    # Trigger clean process exit
    shutdown_coordinator.trigger_process_exit(0)
```

**Neue Helper-Funktionen hinzufügen (main.py):**
```python
def _export_metrics_on_shutdown(engine):
    """Export metrics summary on shutdown"""
    try:
        if hasattr(engine, 'metrics') and engine.metrics:
            engine.metrics.export_json()
            engine.metrics.log_summary()
            logger.info("Metrics exported on shutdown")
    except Exception as e:
        logger.error(f"Metrics export failed: {e}")
```

**Testing:**
```bash
# Test 1: Normal shutdown with Telegram notifications
python main.py &
PID=$!
sleep 10
kill -SIGINT $PID
# Expected: Telegram shutdown notification + summary sent

# Test 2: Crash shutdown
python main.py &
PID=$!
sleep 5
kill -SIGKILL $PID  # Force kill to simulate crash
# Expected: Callbacks NOT executed (expected behavior for SIGKILL)

# Test 3: Verify callback execution order
grep "cleanup_callback" latest_session/logs/system_*.jsonl
# Expected: All 4 callbacks executed in reverse registration order
```

**Rollback-Plan:**
```python
# If callbacks cause issues, can be temporarily disabled:
# shutdown_coordinator.add_cleanup_callback(...)  # Comment out
# And call manually in finally block as before
```

**Success Criteria:**
- ✅ Telegram shutdown notification gesendet
- ✅ Telegram summary gesendet
- ✅ Error summary geloggt
- ✅ Callbacks in korrekter Reihenfolge ausgeführt
- ✅ Keine exceptions während cleanup

---

### Task 1.3: Register Queue Listener with Coordinator (Problem 10)
**Priorität:** CRITICAL | **Aufwand:** 30 Minuten | **Risk:** LOW

**Problem:**
Queue Listener für async logging wird nicht beim ShutdownCoordinator registriert, was zu verlorenen Log-Einträgen führen kann.

**Betroffene Dateien:**
- `main.py` (Lines 396-411)
- `core/logging/logger_setup.py` (neue Funktion)

**Code-Änderung 1: logger_setup.py - Export Listener:**

**HINZUFÜGEN (core/logging/logger_setup.py):**
```python
def get_queue_listener():
    """
    Get the global queue listener instance.

    Returns:
        QueueListener or None if not initialized
    """
    global _queue_listener
    return _queue_listener


def shutdown_queue_logging():
    """Shutdown the queue listener gracefully (idempotent)"""
    global _queue_listener
    if _queue_listener:
        try:
            logger.info("Shutting down queue-based logging...")
            _queue_listener.stop()
            _queue_listener = None
            logger.info("Queue-based logging stopped")
        except Exception as e:
            print(f"Error stopping queue listener: {e}")
```

**Code-Änderung 2: main.py - Register Listener:**

**VORHER (main.py:396-411):**
```python
# Activate split logging (separate JSONL files)
if getattr(config, 'ENABLE_SPLIT_LOGGING', True):
    from core.logging.logger_setup import setup_split_logging
    setup_split_logging(logging.getLogger())
    logger.info("Split logging enabled", extra={'event_type': 'LOGGING_SPLIT_ENABLED'})
```

**NACHHER:**
```python
# Activate split logging (separate JSONL files)
if getattr(config, 'ENABLE_SPLIT_LOGGING', True):
    from core.logging.logger_setup import setup_split_logging, get_queue_listener, shutdown_queue_logging
    setup_split_logging(logging.getLogger())

    # Register queue listener with shutdown coordinator
    queue_listener = get_queue_listener()
    if queue_listener:
        shutdown_coordinator.add_cleanup_callback(
            "logging_queue",
            shutdown_queue_logging
        )
        logger.info(
            "Queue-based logging enabled and registered with coordinator",
            extra={
                'event_type': 'LOGGING_QUEUE_ENABLED',
                'queue_active': queue_listener is not None
            }
        )
    else:
        logger.warning(
            "Queue listener not found - logging may be synchronous",
            extra={'event_type': 'LOGGING_QUEUE_NOT_FOUND'}
        )
```

**Testing:**
```bash
# Test 1: Verify queue listener active
python -c "
from core.logging.logger_setup import get_queue_listener
listener = get_queue_listener()
print(f'Queue listener: {listener}')
print(f'Thread alive: {listener._thread.is_alive() if listener else False}')
"
# Expected: Listener exists and thread is alive

# Test 2: Shutdown with queue flush
python main.py &
PID=$!
# Generate many log messages
sleep 5
kill -SIGINT $PID
# Expected: All log messages flushed before exit

# Test 3: Check for lost logs
BEFORE_COUNT=$(wc -l < /tmp/before.log)
python main.py &
PID=$!
sleep 10
DURING_COUNT=$(grep -c "event_type" latest_session/logs/events_*.jsonl)
kill -SIGINT $PID
sleep 2
AFTER_COUNT=$(grep -c "event_type" latest_session/logs/events_*.jsonl)
echo "During: $DURING_COUNT, After: $AFTER_COUNT"
# Expected: AFTER_COUNT >= DURING_COUNT (no logs lost)
```

**Rollback-Plan:**
```python
# If queue logging causes issues:
# In core/logging/logger_setup.py:
def setup_logger(use_queue=False):  # Set default to False
    ...
```

**Success Criteria:**
- ✅ Queue listener wird bei Startup registriert
- ✅ Queue listener wird bei Shutdown sauber gestoppt
- ✅ Keine Log-Einträge gehen verloren
- ✅ Shutdown-Log enthält "Queue-based logging stopped"

---

### Task 1.4: Implement Periodic State Persistence (Problem 11)
**Priorität:** CRITICAL | **Aufwand:** 2 Stunden | **Risk:** MEDIUM

**Problem:**
Intent state wird nur bei clean shutdown persistiert (engine.py:702-716), bei Crash gehen Daten verloren.

**Betroffene Dateien:**
- `engine/engine.py` (Lines 186-250, 730-928)
- `config.py` (neue Einstellung)

**Code-Änderung 1: config.py - Neuer Parameter:**

**HINZUFÜGEN (config.py):**
```python
# =============================================================================
# 12. STATE PERSISTENCE (System Defaults)
# =============================================================================

# Periodic state persistence for crash recovery
STATE_PERSIST_INTERVAL_S = 30  # Persist state every 30 seconds
STATE_PERSIST_ON_SHUTDOWN = True  # Also persist on clean shutdown
STATE_PERSIST_ON_CRASH = True  # Enable periodic persistence for crash recovery
```

**Code-Änderung 2: engine.py - Periodic Persistence:**

**HINZUFÜGEN (engine/engine.py nach __init__):**
```python
class TradingEngine:
    def __init__(self, ...):
        # ... existing code ...

        # Periodic state persistence configuration
        self._state_persist_interval = getattr(
            config, 'STATE_PERSIST_INTERVAL_S', 30
        )
        self._last_state_persist = time.time()
        self._state_persist_on_crash = getattr(
            config, 'STATE_PERSIST_ON_CRASH', True
        )

        logger.info(
            f"State persistence configured: "
            f"interval={self._state_persist_interval}s, "
            f"crash_recovery={'enabled' if self._state_persist_on_crash else 'disabled'}",
            extra={'event_type': 'STATE_PERSISTENCE_CONFIG'}
        )
```

**ÄNDERN (engine/engine.py:730-928 im _main_loop):**
```python
def _main_loop(self):
    """Main trading loop with periodic state persistence"""

    last_md_update = 0
    last_position_update = 0
    last_heartbeat_emit = 0
    last_stale_check = 0
    last_health_check = 0

    logger.info("Engine main loop started", extra={'event_type': 'ENGINE_LOOP_START'})

    while self.running:
        cycle_start = time.time()

        try:
            # Heartbeat tracking
            if shutdown_coordinator:
                shutdown_coordinator.beat("engine_cycle_start")

            # Health monitoring (every 60s)
            if time.time() - last_health_check > 60:
                self._check_market_data_health()
                last_health_check = time.time()

            # ... existing code for MD updates, exits, positions, buy opportunities ...

            # ⭐ NEW: Periodic state persistence (crash recovery)
            if self._state_persist_on_crash:
                if time.time() - self._last_state_persist > self._state_persist_interval:
                    try:
                        self._persist_intent_state()
                        self._last_state_persist = time.time()
                        logger.debug(
                            f"Periodic state persisted: "
                            f"{len(self.pending_buy_intents)} pending intents",
                            extra={
                                'event_type': 'STATE_PERSISTED_PERIODIC',
                                'pending_intents': len(self.pending_buy_intents)
                            }
                        )
                    except Exception as e:
                        logger.error(
                            f"Periodic state persistence failed: {e}",
                            extra={'event_type': 'STATE_PERSIST_ERROR'},
                            exc_info=True
                        )

            # Rate limiting
            time.sleep(0.5)

        except Exception as loop_error:
            logger.error(
                f"Error in main loop iteration: {loop_error}",
                extra={'event_type': 'ENGINE_LOOP_ERROR'},
                exc_info=True
            )
            time.sleep(1.0)  # Back off on errors

    logger.info("Engine main loop stopped", extra={'event_type': 'ENGINE_LOOP_STOP'})
```

**Testing:**
```bash
# Test 1: Verify periodic persistence
python main.py &
PID=$!
sleep 60  # Wait for at least 2 persist cycles
kill -SIGINT $PID
grep "STATE_PERSISTED_PERIODIC" latest_session/logs/system_*.jsonl
# Expected: At least 2 periodic persist events

# Test 2: Crash recovery test
python main.py &
PID=$!
sleep 35  # Wait for first persist
STATEFILE="latest_session/state/engine_transient.json"
cp $STATEFILE /tmp/state_before_crash.json
kill -SIGKILL $PID  # Simulate crash
# Verify state file exists and contains data
jq . $STATEFILE
# Expected: State file intact with pending_buy_intents

# Test 3: State restoration after crash
# Start bot again
python main.py &
PID=$!
sleep 5
kill -SIGINT $PID
grep "pending_buy_intents" latest_session/logs/engine_*.jsonl
# Expected: Previously persisted intents loaded
```

**Rollback-Plan:**
```python
# If periodic persistence causes performance issues:
# In config.py:
STATE_PERSIST_ON_CRASH = False  # Disable periodic persistence
STATE_PERSIST_INTERVAL_S = 300  # Reduce frequency to 5 minutes
```

**Success Criteria:**
- ✅ State wird alle 30s persistiert
- ✅ Bei Crash ist letzter State verfügbar
- ✅ State-Datei ist valides JSON
- ✅ Keine Performance-Degradation (<1ms per persist)
- ✅ Error logs bei Persist-Fehlern

---

## Phase 2: Hohe Priorität (Woche 2)
**Priorität:** HIGH
**Aufwand:** 4-5 Stunden
**Deploy:** Nach erfolgreicher Phase 1

### Task 2.1: Validate Queue Listener Initialization (Problem 3)
**Priorität:** HIGH | **Aufwand:** 30 Minuten | **Risk:** LOW

**Problem:**
`setup_split_logging()` wird aufgerufen aber Queue Listener Initialization nicht validiert.

**Betroffene Dateien:**
- `core/logging/logger_setup.py` (Line 343)

**Code-Änderung:**

**HINZUFÜGEN (core/logging/logger_setup.py nach setup_logger()):**
```python
def setup_logger(use_queue=True):
    """Setup logger with optional queue-based logging"""
    # ... existing code ...

    # ⭐ NEW: Validate queue listener initialization
    if use_queue:
        if _queue_listener and _queue_listener._thread.is_alive():
            logger.info(
                "Async queue-based logging initialized successfully",
                extra={
                    'event_type': 'LOGGING_QUEUE_VALIDATED',
                    'queue_size': log_queue.qsize() if 'log_queue' in locals() else 0,
                    'thread_name': _queue_listener._thread.name
                }
            )
        else:
            logger.error(
                "Queue-based logging initialization FAILED",
                extra={
                    'event_type': 'LOGGING_QUEUE_INIT_FAILED',
                    'listener_exists': _queue_listener is not None,
                    'thread_alive': _queue_listener._thread.is_alive() if _queue_listener else False
                }
            )
            raise RuntimeError("Failed to initialize queue-based logging")

    return logger
```

**Testing:**
```bash
# Test: Verify validation
python -c "
from core.logging.logger_setup import setup_logger
logger = setup_logger(use_queue=True)
print('Logger initialized successfully')
"
# Expected: Success message + validation log

# Test: Force failure
python -c "
import logging
from core.logging import logger_setup
logger_setup._queue_listener = None  # Simulate failure
try:
    logger = logger_setup.setup_logger(use_queue=True)
except RuntimeError as e:
    print(f'Caught expected error: {e}')
"
# Expected: RuntimeError caught
```

**Success Criteria:**
- ✅ Validation log bei erfolgreichem Setup
- ✅ RuntimeError bei Fehler
- ✅ Queue size geloggt

---

### Task 2.2: Register Dust Sweeper with Coordinator (Problem 4)
**Priorität:** HIGH | **Aufwand:** 45 Minuten | **Risk:** LOW

**Problem:**
Dust Sweeper Thread wird ohne Registration oder Stop-Hook gestartet (main.py:470-533).

**Betroffene Dateien:**
- `main.py` (Lines 470-533)
- `core/utils.py` (DustSweeper class)

**Code-Änderung 1: DustSweeper - Add Stop Method:**

**VORHER (core/utils.py - DustSweeper):**
```python
class DustSweeper:
    def __init__(self, ...):
        self.running = True

    def run(self):
        while self.running:
            # ... dust sweep logic ...
            time.sleep(self.interval)
```

**NACHHER:**
```python
class DustSweeper:
    def __init__(self, ...):
        self.running = True
        self._thread = None

    def start(self):
        """Start dust sweeper in background thread"""
        if self._thread and self._thread.is_alive():
            logger.warning("Dust sweeper already running")
            return

        self._thread = threading.Thread(
            target=self.run,
            daemon=True,
            name="DustSweeper"
        )
        self._thread.start()
        logger.info("Dust sweeper thread started")

    def stop(self):
        """Stop dust sweeper gracefully"""
        if not self.running:
            logger.debug("Dust sweeper already stopped")
            return

        logger.info("Stopping dust sweeper...")
        self.running = False

        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=5.0)
            if self._thread.is_alive():
                logger.warning("Dust sweeper thread did not stop in time")
            else:
                logger.info("Dust sweeper stopped cleanly")

    def run(self):
        """Main dust sweep loop"""
        logger.info("Dust sweeper loop started")
        while self.running:
            try:
                # ... existing dust sweep logic ...
                time.sleep(self.interval)
            except Exception as e:
                logger.error(f"Dust sweep error: {e}", exc_info=True)
        logger.info("Dust sweeper loop ended")
```

**Code-Änderung 2: main.py - Register with Coordinator:**

**VORHER (main.py:470-533):**
```python
# Start dust sweep thread (if enabled)
if config.ENABLE_DUST_SWEEP:
    dust_sweeper = DustSweeper(exchange, ...)
    dust_thread = threading.Thread(
        target=dust_sweeper.run,
        daemon=True,
        name="DustSweeper"
    )
    dust_thread.start()
    logger.info("Dust sweep thread started")
```

**NACHHER:**
```python
# Start dust sweep thread (if enabled)
dust_sweeper = None
if config.ENABLE_DUST_SWEEP:
    dust_sweeper = DustSweeper(exchange, ...)
    dust_sweeper.start()

    # Register with shutdown coordinator
    shutdown_coordinator.register_component("dust_sweeper", dust_sweeper)
    logger.info(
        "Dust sweeper registered with shutdown coordinator",
        extra={'event_type': 'DUST_SWEEPER_REGISTERED'}
    )
```

**Testing:**
```bash
# Test 1: Verify registration
python main.py &
PID=$!
sleep 10
grep "DUST_SWEEPER_REGISTERED" latest_session/logs/system_*.jsonl
# Expected: Registration event found

# Test 2: Graceful shutdown
python main.py &
PID=$!
sleep 30
kill -SIGINT $PID
grep "Dust sweeper stopped" latest_session/logs/system_*.jsonl
# Expected: Clean stop message

# Test 3: Verify no zombie threads
python main.py &
PID=$!
sleep 10
THREADS_BEFORE=$(ps -T -p $PID | wc -l)
kill -SIGINT $PID
sleep 2
# Expected: All threads terminated
```

**Success Criteria:**
- ✅ Dust sweeper registriert
- ✅ Clean stop bei shutdown
- ✅ Keine zombie threads
- ✅ Stop-Timeout funktioniert

---

### Task 2.3: Register Dashboard with Coordinator (Problem 5)
**Priorität:** HIGH | **Aufwand:** 30 Minuten | **Risk:** LOW

**Problem:**
Dashboard Thread fehlt Coordinator-Registration (main.py:916-937).

**Betroffene Dateien:**
- `main.py` (Lines 916-937)
- `ui/dashboard.py` (ggf. Stop-Method)

**Code-Änderung:**

**NACHHER (main.py:916-937):**
```python
# Live Dashboard Thread
dashboard_thread = None
if config.ENABLE_LIVE_DASHBOARD:
    dashboard_thread = threading.Thread(
        target=run_dashboard,
        args=(engine, portfolio, config_module),
        daemon=True,
        name="LiveDashboard"
    )
    dashboard_thread.start()

    # Register with shutdown coordinator for monitoring
    # Note: Dashboard is daemon thread, will auto-terminate
    # But we register it for health monitoring
    shutdown_coordinator.register_component("dashboard", {
        'thread': dashboard_thread,
        'type': 'daemon',
        'shutdown_method': None  # Auto-terminates as daemon
    })

    logger.info(
        "Live dashboard started and registered",
        extra={
            'event_type': 'DASHBOARD_STARTED',
            'thread_name': dashboard_thread.name,
            'daemon': dashboard_thread.daemon
        }
    )
```

**Testing:**
```bash
# Test: Verify registration
python main.py &
PID=$!
sleep 5
grep "DASHBOARD_STARTED" latest_session/logs/system_*.jsonl
# Expected: Dashboard registered event

# Test: Shutdown monitoring
python main.py &
PID=$!
sleep 10
kill -SIGINT $PID
# Expected: Dashboard terminates with main process
```

**Success Criteria:**
- ✅ Dashboard registriert
- ✅ Health monitoring möglich
- ✅ Auto-termination funktioniert

---

### Task 2.4: Make Main Loop Intervals Configurable (Problem 7)
**Priorität:** HIGH | **Aufwand:** 1 Stunde | **Risk:** LOW

**Problem:**
Alle Main Loop Intervalle sind hart kodiert in engine.py:730-928.

**Betroffene Dateien:**
- `config.py` (neue Parameter)
- `engine/engine.py` (Lines 730-928)

**Code-Änderung 1: config.py - Neue Parameter:**

**HINZUFÜGEN (config.py):**
```python
# =============================================================================
# 13. ENGINE TIMING CONFIGURATION (System Defaults)
# =============================================================================

# Main loop intervals (in seconds)
ENGINE_HEALTH_CHECK_INTERVAL_S = 60      # Health monitoring interval
ENGINE_MD_UPDATE_INTERVAL_S = 5          # Market data update interval
ENGINE_EXIT_CHECK_INTERVAL_S = 1         # Exit signal processing interval
ENGINE_POSITION_UPDATE_INTERVAL_S = 2    # Position management interval
ENGINE_HEARTBEAT_INTERVAL_S = 30         # Heartbeat emission interval
ENGINE_STALE_CHECK_INTERVAL_S = 30       # Stale intent cleanup interval
ENGINE_LOOP_SLEEP_S = 0.5                # Rate limiting sleep between cycles

# Performance monitoring
ENGINE_LOG_CYCLE_TIME = False            # Log cycle duration for performance tuning
```

**Code-Änderung 2: engine.py - Use Config:**

**NACHHER (engine/engine.py:730-928):**
```python
def _main_loop(self):
    """Main trading loop with configurable intervals"""

    # Load intervals from config
    health_check_interval = getattr(config, 'ENGINE_HEALTH_CHECK_INTERVAL_S', 60)
    md_update_interval = getattr(config, 'ENGINE_MD_UPDATE_INTERVAL_S', 5)
    exit_check_interval = getattr(config, 'ENGINE_EXIT_CHECK_INTERVAL_S', 1)
    position_update_interval = getattr(config, 'ENGINE_POSITION_UPDATE_INTERVAL_S', 2)
    heartbeat_interval = getattr(config, 'ENGINE_HEARTBEAT_INTERVAL_S', 30)
    stale_check_interval = getattr(config, 'ENGINE_STALE_CHECK_INTERVAL_S', 30)
    loop_sleep = getattr(config, 'ENGINE_LOOP_SLEEP_S', 0.5)
    log_cycle_time = getattr(config, 'ENGINE_LOG_CYCLE_TIME', False)

    logger.info(
        f"Main loop intervals: health={health_check_interval}s, "
        f"md={md_update_interval}s, exit={exit_check_interval}s, "
        f"position={position_update_interval}s, heartbeat={heartbeat_interval}s, "
        f"stale={stale_check_interval}s, sleep={loop_sleep}s",
        extra={'event_type': 'ENGINE_LOOP_CONFIG'}
    )

    last_md_update = 0
    last_position_update = 0
    last_heartbeat_emit = 0
    last_stale_check = 0
    last_health_check = 0

    while self.running:
        cycle_start = time.time()

        try:
            # ... existing code but with configurable intervals ...

            # Health monitoring
            if time.time() - last_health_check > health_check_interval:
                self._check_market_data_health()
                last_health_check = time.time()

            # Market data updates
            if time.time() - last_md_update > md_update_interval:
                self._update_market_data()
                last_md_update = time.time()

            # ... etc for all intervals ...

            # Rate limiting
            time.sleep(loop_sleep)

            # Optional cycle time logging
            if log_cycle_time:
                cycle_duration = time.time() - cycle_start
                if cycle_duration > 1.0:  # Log slow cycles
                    logger.warning(
                        f"Slow engine cycle: {cycle_duration:.2f}s",
                        extra={'event_type': 'ENGINE_SLOW_CYCLE', 'duration': cycle_duration}
                    )

        except Exception as loop_error:
            logger.error(f"Error in main loop: {loop_error}", exc_info=True)
            time.sleep(1.0)
```

**Testing:**
```bash
# Test 1: Default intervals
python main.py &
PID=$!
sleep 5
kill -SIGINT $PID
grep "ENGINE_LOOP_CONFIG" latest_session/logs/system_*.jsonl
# Expected: Default intervals logged

# Test 2: Custom intervals
# In config.py: ENGINE_MD_UPDATE_INTERVAL_S = 10
python main.py &
PID=$!
sleep 25
kill -SIGINT $PID
MD_UPDATES=$(grep "Market data updated" latest_session/logs/*.jsonl | wc -l)
echo "MD Updates: $MD_UPDATES"
# Expected: ~2-3 updates (25s / 10s interval)

# Test 3: Performance monitoring
# In config.py: ENGINE_LOG_CYCLE_TIME = True
python main.py &
PID=$!
sleep 60
kill -SIGINT $PID
grep "ENGINE_SLOW_CYCLE" latest_session/logs/system_*.jsonl
# Expected: Slow cycles logged (if any)
```

**Success Criteria:**
- ✅ Alle Intervalle konfigurierbar
- ✅ Default-Werte funktionieren
- ✅ Config-Änderungen werden übernommen
- ✅ Performance-Logging optional verfügbar

---

### Task 2.5: Add Dashboard Staleness Warnings (Problem 8)
**Priorität:** HIGH | **Aufwand:** 1.5 Stunden | **Risk:** LOW

**Problem:**
Dashboard zeigt stale snapshots ohne Warning (ui/dashboard.py:201-236).

**Betroffene Dateien:**
- `ui/dashboard.py` (Lines 201-236)
- `config.py` (neue Thresholds)

**Code-Änderung 1: config.py - Thresholds:**

**HINZUFÜGEN (config.py):**
```python
# Dashboard data freshness
DASHBOARD_SNAPSHOT_STALE_THRESHOLD_S = 300  # 5 minutes
DASHBOARD_WARN_STALE_COUNT = 5              # Warn if >5 stale snapshots
DASHBOARD_SHOW_STALE_INDICATOR = True       # Show staleness in UI
```

**Code-Änderung 2: dashboard.py - Staleness Detection:**

**NACHHER (ui/dashboard.py:201-236):**
```python
def get_drop_data(engine, portfolio, config_module):
    """Get drop data with staleness detection and warnings"""
    drops = []
    stale_count = 0
    stale_symbols = []

    stale_threshold = getattr(
        config_module, 'DASHBOARD_SNAPSHOT_STALE_THRESHOLD_S', 300
    )
    warn_threshold = getattr(
        config_module, 'DASHBOARD_WARN_STALE_COUNT', 5
    )
    show_indicator = getattr(
        config_module, 'DASHBOARD_SHOW_STALE_INDICATOR', True
    )

    now = time.time()

    for symbol, snapshot in engine.drop_snapshot_store.items():
        drop_pct = snapshot.get('drop_pct', 0.0)
        anchor_price = snapshot.get('anchor_price', 0.0)
        current_price = snapshot.get('price', 0.0)
        spread_pct = snapshot.get('spread_pct', 0.0)
        snapshot_ts = snapshot.get('timestamp', 0)
        age_s = now - snapshot_ts

        # Track stale snapshots
        if age_s > stale_threshold:
            stale_count += 1
            stale_symbols.append(symbol)

            # Skip display of very stale data
            if age_s > stale_threshold * 2:  # 10+ minutes
                continue

        drops.append({
            'symbol': symbol,
            'drop': drop_pct,
            'anchor': anchor_price,
            'current': current_price,
            'spread': spread_pct,
            'age': age_s,
            'stale': age_s > stale_threshold  # Mark as stale
        })

    # Log warning if many stale snapshots
    if stale_count >= warn_threshold:
        logger.warning(
            f"Dashboard: {stale_count} stale market snapshots detected",
            extra={
                'event_type': 'DASHBOARD_STALE_DATA',
                'stale_count': stale_count,
                'stale_symbols': stale_symbols[:10],  # First 10
                'threshold_s': stale_threshold
            }
        )

    # Sort: BTC first, then by biggest drop
    drops.sort(key=lambda x: (
        0 if x['symbol'] == 'BTC/USDT' else 1,
        x['drop']
    ))

    # Add staleness metadata for UI display
    if show_indicator and stale_count > 0:
        drops.insert(0, {
            'type': 'warning',
            'message': f'⚠ {stale_count} stale snapshots (>{stale_threshold}s old)',
            'stale_count': stale_count
        })

    return drops
```

**Code-Änderung 3: dashboard.py - UI Indicator:**

**NACHHER (ui/dashboard.py - make_drop_panel):**
```python
def make_drop_panel(drop_data, config_data, engine):
    """Create drop panel with staleness indicators"""

    # ... existing code ...

    for item in drop_data:
        # Check for warning type
        if item.get('type') == 'warning':
            table.add_row(
                Text(item['message'], style="bold yellow"),
                "", "", "", ""
            )
            continue

        symbol = item['symbol']
        drop = item['drop']
        stale = item.get('stale', False)

        # Style based on staleness
        symbol_style = "dim" if stale else "bold cyan"
        drop_style = "dim red" if stale else "red"

        # Add stale indicator
        symbol_display = f"{symbol} ⏱" if stale else symbol

        table.add_row(
            Text(symbol_display, style=symbol_style),
            Text(f"{drop:+.2f}%", style=drop_style),
            # ... etc
        )

    return Panel(table, title="Market Drops", border_style="cyan")
```

**Testing:**
```bash
# Test 1: Fresh data (no warnings)
python main.py &
PID=$!
sleep 10
# Dashboard should show normal display
kill -SIGINT $PID

# Test 2: Simulate stale data
# Stop market data updates, wait 6 minutes
python main.py &
PID=$!
# Manually stop MD thread to simulate staleness
sleep 400
# Dashboard should show warning banner
grep "DASHBOARD_STALE_DATA" latest_session/logs/system_*.jsonl
# Expected: Warning logged

# Test 3: Staleness indicator in UI
# Verify ⏱ symbol appears next to stale entries
```

**Success Criteria:**
- ✅ Stale snapshots detektiert
- ✅ Warning geloggt ab 5+ stale
- ✅ UI zeigt Staleness-Indicator
- ✅ Sehr alte Daten (10min+) gefiltert

---

## Phase 3: Mittlere Priorität (Woche 3)
**Priorität:** MEDIUM
**Aufwand:** 4-5 Stunden
**Deploy:** Nach erfolgreicher Phase 2

### Task 3.1: Integrate Execution Controls Module (Problem 12)
**Priorität:** MEDIUM | **Aufwand:** 2 Stunden | **Risk:** MEDIUM

**Problem:**
`trading/execution_controls.py` existiert aber wird nicht verwendet.

**Betroffene Dateien:**
- `engine/buy_decision.py` (Integration)
- `services/order_router.py` (Dynamic constraints)
- `config.py` (Feature flag)

**Code-Änderung 1: config.py - Feature Flag:**

**HINZUFÜGEN (config.py):**
```python
# =============================================================================
# 14. DYNAMIC EXECUTION CONTROLS
# =============================================================================

ENABLE_DYNAMIC_EXECUTION_CONTROLS = True  # Use adaptive slippage/spread
EXECUTION_CONTROLS_LOG_DECISIONS = True   # Log constraint decisions
```

**Code-Änderung 2: buy_decision.py - Integration:**

**HINZUFÜGEN (engine/buy_decision.py vor intent creation):**
```python
def _create_buy_intent(self, symbol, signal, qty, limit_px):
    """Create buy intent with dynamic execution constraints"""

    # ... existing code ...

    # ⭐ NEW: Dynamic execution constraints
    if getattr(config, 'ENABLE_DYNAMIC_EXECUTION_CONTROLS', False):
        from trading.execution_controls import determine_entry_constraints

        # Gather market data
        orderbook = self._get_orderbook_snapshot(symbol)
        volatility = self._get_volatility_metrics(symbol)
        recent_stats = self._get_recent_fill_stats(symbol)

        market_data = {
            "orderbook": orderbook,
            "volatility": volatility
        }

        # Get adaptive constraints
        constraints = determine_entry_constraints(
            symbol=symbol,
            side="buy",
            market_data=market_data,
            recent_stats=recent_stats
        )

        # Add to intent
        intent["max_slippage_bps"] = constraints["max_slippage_bps"]
        intent["max_spread_bps"] = constraints["max_spread_bps"]
        intent["constraint_reason"] = constraints["reason"]

        if getattr(config, 'EXECUTION_CONTROLS_LOG_DECISIONS', False):
            logger.info(
                f"Dynamic constraints for {symbol}: "
                f"slippage={constraints['max_slippage_bps']}bps, "
                f"spread={constraints['max_spread_bps']}bps, "
                f"reason={constraints['reason']}",
                extra={
                    'event_type': 'DYNAMIC_CONSTRAINTS',
                    'symbol': symbol,
                    **constraints
                }
            )
    else:
        # Use static config values
        intent["max_slippage_bps"] = config.MAX_SLIPPAGE_BPS_ENTRY
        intent["max_spread_bps"] = config.MAX_SPREAD_BPS_ENTRY

    return intent
```

**Helper Methods (buy_decision.py):**
```python
def _get_orderbook_snapshot(self, symbol):
    """Get current orderbook metrics"""
    try:
        ob = self.engine.market_data.get_orderbook(symbol)
        if not ob:
            return {}

        return {
            "bid_depth_usdt": sum(b['size'] * b['price'] for b in ob['bids'][:10]),
            "ask_depth_usdt": sum(a['size'] * a['price'] for a in ob['asks'][:10]),
            "spread_bps": (
                (ob['asks'][0]['price'] - ob['bids'][0]['price']) /
                ob['bids'][0]['price'] * 10000
            ) if ob['bids'] and ob['asks'] else 0
        }
    except Exception as e:
        logger.debug(f"Orderbook snapshot error: {e}")
        return {}

def _get_volatility_metrics(self, symbol):
    """Get volatility metrics"""
    try:
        # Use price history to calculate ATR or variance
        history = self.engine.topcoins.get(symbol, {}).get('prices', [])
        if len(history) < 10:
            return {}

        prices = list(history)
        mean_price = sum(prices) / len(prices)
        variance = sum((p - mean_price) ** 2 for p in prices) / len(prices)
        std_dev = variance ** 0.5
        atr_pct = (std_dev / mean_price) * 100

        return {
            "atr_pct": atr_pct,
            "variance_1h": variance
        }
    except Exception as e:
        logger.debug(f"Volatility metrics error: {e}")
        return {}

def _get_recent_fill_stats(self, symbol):
    """Get recent fill rate for symbol"""
    try:
        # Query order audit log for recent orders
        # This is a simplified version - implement based on your logging
        return {
            "fill_rate_24h": 0.7  # Placeholder
        }
    except Exception as e:
        logger.debug(f"Fill stats error: {e}")
        return {}
```

**Code-Änderung 3: order_router.py - Use Dynamic Constraints:**

**ÄNDERN (services/order_router.py):**
```python
def handle_intent(self, intent: Dict[str, Any]) -> None:
    """Handle intent with dynamic constraints"""

    # ... existing code ...

    # Check for dynamic constraints in intent
    max_slippage_bps = intent.get(
        'max_slippage_bps',
        getattr(config, 'MAX_SLIPPAGE_BPS_ENTRY', 30)
    )
    max_spread_bps = intent.get(
        'max_spread_bps',
        getattr(config, 'MAX_SPREAD_BPS_ENTRY', 20)
    )

    # Apply constraints to limit price calculation
    # ... existing slippage guard logic but use dynamic values ...
```

**Testing:**
```bash
# Test 1: Feature enabled
# In config.py: ENABLE_DYNAMIC_EXECUTION_CONTROLS = True
python main.py &
PID=$!
sleep 30
kill -SIGINT $PID
grep "DYNAMIC_CONSTRAINTS" latest_session/logs/events_*.jsonl
# Expected: Constraint decisions logged

# Test 2: Low depth adjustment
# Simulate low depth orderbook
# Expected: Higher slippage tolerance applied

# Test 3: High volatility adjustment
# Simulate high ATR
# Expected: Higher spread tolerance applied
```

**Rollback-Plan:**
```python
# In config.py:
ENABLE_DYNAMIC_EXECUTION_CONTROLS = False  # Disable feature
```

**Success Criteria:**
- ✅ Feature aktivierbar via config
- ✅ Constraints berechnet und geloggt
- ✅ OrderRouter verwendet dynamic values
- ✅ Keine Regression bei disabled

---

### Task 3.2: Integrate Metrics Collector (Problem 13)
**Priorität:** MEDIUM | **Aufwand:** 1.5 Stunden | **Risk:** LOW

**Problem:**
`core/monitoring/metrics.py` existiert aber wird nicht verwendet.

**Betroffene Dateien:**
- `main.py` (Initialization)
- `engine/engine.py` (Metric recording)
- `config.py` (Feature flags)

**Code-Änderung 1: config.py - Metrics Configuration:**

**HINZUFÜGEN (config.py):**
```python
# =============================================================================
# 15. METRICS & MONITORING
# =============================================================================

ENABLE_METRICS_COLLECTION = True         # Enable metrics collection
ENABLE_PROMETHEUS_METRICS = False        # Export to Prometheus (requires prometheus_client)
ENABLE_STATSD_METRICS = False            # Export to StatsD (requires statsd)
STATSD_HOST = "localhost"                # StatsD server host
STATSD_PORT = 8125                       # StatsD server port
METRICS_EXPORT_INTERVAL_S = 60           # Export metrics to JSON every 60s
```

**Code-Änderung 2: main.py - Initialize Metrics:**

**HINZUFÜGEN (main.py nach engine creation):**
```python
# Initialize metrics collection
metrics = None
if getattr(config, 'ENABLE_METRICS_COLLECTION', False):
    from core.monitoring import init_metrics

    try:
        metrics = init_metrics(
            session_dir=SESSION_DIR,
            enable_prometheus=config.ENABLE_PROMETHEUS_METRICS,
            enable_statsd=config.ENABLE_STATSD_METRICS,
            statsd_host=config.STATSD_HOST,
            statsd_port=config.STATSD_PORT
        )

        # Pass to engine
        engine.metrics = metrics

        logger.info(
            "Metrics collection initialized",
            extra={
                'event_type': 'METRICS_INITIALIZED',
                'prometheus': config.ENABLE_PROMETHEUS_METRICS,
                'statsd': config.ENABLE_STATSD_METRICS
            }
        )

        # Register metrics export in cleanup
        shutdown_coordinator.add_cleanup_callback(
            "metrics_final_export",
            lambda: metrics.export_json() if metrics else None
        )

    except Exception as e:
        logger.error(f"Metrics initialization failed: {e}", exc_info=True)
```

**Code-Änderung 3: engine.py - Record Metrics:**

**ÄNDERN (engine/engine.py event handlers):**
```python
def _on_order_intent(self, intent):
    """Handle order.intent event with metrics"""
    # ... existing code ...

    # Record metric
    if hasattr(self, 'metrics') and self.metrics:
        self.metrics.record_order_sent(
            intent['symbol'],
            intent['side']
        )

def _on_order_filled(self, event_data):
    """Handle order.filled event with metrics"""
    # ... existing code ...

    # Record metric
    if hasattr(self, 'metrics') and self.metrics:
        self.metrics.record_order_filled(
            event_data['symbol'],
            event_data['side'],
            latency_ms=event_data.get('latency_ms', 0)
        )

    # Update pending intents gauge
    if hasattr(self, 'metrics') and self.metrics:
        self.metrics.record_intent_pending(
            len(self.pending_buy_intents)
        )

def _on_order_failed(self, event_data):
    """Handle order.failed event with metrics"""
    # ... existing code ...

    # Record metric
    if hasattr(self, 'metrics') and self.metrics:
        self.metrics.record_order_failed(
            event_data['symbol'],
            event_data['side'],
            error_code=event_data.get('exchange_error')
        )

def _check_stale_intents(self):
    """Check stale intents with metrics"""
    # ... existing code ...

    for stale_intent in stale_intents:
        # Record metric
        if hasattr(self, 'metrics') and self.metrics:
            self.metrics.record_stale_intent(
                stale_intent['age_s']
            )
```

**Code-Änderung 4: Periodic Export in Main Loop:**

**HINZUFÜGEN (engine/engine.py _main_loop):**
```python
def _main_loop(self):
    """Main loop with periodic metrics export"""

    last_metrics_export = 0
    metrics_export_interval = getattr(config, 'METRICS_EXPORT_INTERVAL_S', 60)

    while self.running:
        # ... existing code ...

        # Periodic metrics export
        if hasattr(self, 'metrics') and self.metrics:
            if time.time() - last_metrics_export > metrics_export_interval:
                try:
                    self.metrics.export_json()
                    self.metrics.log_summary()
                    last_metrics_export = time.time()
                except Exception as e:
                    logger.error(f"Metrics export error: {e}")
```

**Testing:**
```bash
# Test 1: Metrics file created
python main.py &
PID=$!
sleep 65  # Wait for first export
kill -SIGINT $PID
ls -l latest_session/metrics/summary.json
# Expected: Metrics file exists

# Test 2: Metrics content
cat latest_session/metrics/summary.json | jq .
# Expected: Valid JSON with counters, gauges, histograms

# Test 3: Prometheus (optional)
# If enabled:
curl http://localhost:8000/metrics
# Expected: Prometheus metrics exposed

# Test 4: Verify recording
grep "orders_sent" latest_session/metrics/summary.json
# Expected: Counter > 0 if orders sent
```

**Rollback-Plan:**
```python
# In config.py:
ENABLE_METRICS_COLLECTION = False  # Disable metrics
```

**Success Criteria:**
- ✅ Metrics aktivierbar via config
- ✅ JSON export funktioniert
- ✅ Order metrics aufgezeichnet
- ✅ Intent metrics aufgezeichnet
- ✅ Prometheus/StatsD optional

---

### Task 3.3: Fix GTC Order Timeout Handling (Problem 14)
**Priorität:** MEDIUM | **Aufwand:** 45 Minuten | **Risk:** LOW

**Problem:**
GTC orders verwenden IOC timeout (services/order_router.py:458), was zu früh ist.

**Betroffene Dateien:**
- `services/order_router.py` (Lines 458-460)

**Code-Änderung:**

**VORHER (services/order_router.py:458):**
```python
# Wait for fill
status = self.ex.wait_for_fill(symbol, order_id, timeout_ms=2500)
```

**NACHHER:**
```python
# Wait for fill with timeout based on TIF type
if self.cfg.tif == "GTC":
    # GTC orders remain open until filled or manually canceled
    # Use longer timeout for initial check
    wait_timeout_ms = 30000  # 30 seconds for GTC
    logger.debug(f"GTC order placed: {order_id}, using {wait_timeout_ms}ms timeout")
elif self.cfg.tif == "IOC":
    # IOC must fill immediately or cancel
    wait_timeout_ms = self.cfg.ioc_order_ttl_ms or 2000
    logger.debug(f"IOC order placed: {order_id}, using {wait_timeout_ms}ms timeout")
else:
    # Default timeout for other TIF types
    wait_timeout_ms = 5000
    logger.debug(f"{self.cfg.tif} order placed: {order_id}, using {wait_timeout_ms}ms timeout")

status = self.ex.wait_for_fill(symbol, order_id, timeout_ms=wait_timeout_ms)
```

**Testing:**
```bash
# Test 1: IOC order (existing behavior)
# config.py: BUY_ESCALATION_STEPS with IOC
python main.py &
PID=$!
sleep 30
kill -SIGINT $PID
grep "IOC order placed" latest_session/logs/system_*.jsonl
# Expected: IOC timeout = 2000ms

# Test 2: GTC order (new behavior)
# config.py: BUY_ESCALATION_STEPS with GTC fallback
# Trigger buy signal that reaches GTC step
python main.py &
PID=$!
sleep 60
kill -SIGINT $PID
grep "GTC order placed" latest_session/logs/system_*.jsonl
# Expected: GTC timeout = 30000ms

# Test 3: Fill rate improvement
# Compare fill rates before/after
# Expected: GTC fill rate higher than before
```

**Rollback-Plan:**
```python
# Revert to fixed timeout:
wait_timeout_ms = 2500
status = self.ex.wait_for_fill(symbol, order_id, timeout_ms=wait_timeout_ms)
```

**Success Criteria:**
- ✅ GTC orders verwenden 30s timeout
- ✅ IOC orders verwenden 2s timeout
- ✅ Timeout wird geloggt
- ✅ Fill rate für GTC verbessert

---

## Phase 4: Testing & Validation (Woche 4)
**Priorität:** CRITICAL
**Aufwand:** 4-6 Stunden
**Deploy:** Final validation before production

### Task 4.1: Comprehensive Integration Testing
**Aufwand:** 2 Stunden

**Test Suite:**

```bash
#!/bin/bash
# integration_test.sh

echo "=== Trading Bot Integration Test Suite ==="
echo ""

# Test 1: Startup & Shutdown
echo "[1/10] Testing startup and shutdown..."
python main.py &
PID=$!
sleep 10
kill -SIGINT $PID
wait $PID
if [ $? -eq 0 ]; then
    echo "✓ Startup/shutdown successful"
else
    echo "✗ Startup/shutdown failed"
    exit 1
fi

# Test 2: Signal Handling
echo "[2/10] Testing signal handling..."
python main.py &
PID=$!
sleep 5
kill -SIGTERM $PID
wait $PID
grep -q "Shutdown signal received" latest_session/logs/events_*.jsonl
if [ $? -eq 0 ]; then
    echo "✓ Signal handling working"
else
    echo "✗ Signal handling failed"
    exit 1
fi

# Test 3: State Persistence
echo "[3/10] Testing state persistence..."
python main.py &
PID=$!
sleep 35  # Wait for periodic persist
kill -SIGKILL $PID  # Simulate crash
if [ -f latest_session/state/engine_transient.json ]; then
    echo "✓ State persisted"
else
    echo "✗ State persistence failed"
    exit 1
fi

# Test 4: Cleanup Callbacks
echo "[4/10] Testing cleanup callbacks..."
python main.py &
PID=$!
sleep 10
kill -SIGINT $PID
wait $PID
grep -q "cleanup_callback.*telegram" latest_session/logs/system_*.jsonl
if [ $? -eq 0 ]; then
    echo "✓ Cleanup callbacks executed"
else
    echo "✗ Cleanup callbacks failed"
    exit 1
fi

# Test 5: Logging Queue
echo "[5/10] Testing logging queue..."
python main.py &
PID=$!
sleep 5
kill -SIGINT $PID
wait $PID
grep -q "Queue-based logging stopped" latest_session/logs/system_*.jsonl
if [ $? -eq 0 ]; then
    echo "✓ Logging queue cleanup working"
else
    echo "✗ Logging queue cleanup failed"
    exit 1
fi

# Test 6: Dust Sweeper
echo "[6/10] Testing dust sweeper..."
python main.py &
PID=$!
sleep 10
kill -SIGINT $PID
wait $PID
grep -q "Dust sweeper stopped" latest_session/logs/system_*.jsonl
if [ $? -eq 0 ]; then
    echo "✓ Dust sweeper stopped cleanly"
else
    echo "✗ Dust sweeper cleanup failed"
    exit 1
fi

# Test 7: Dashboard
echo "[7/10] Testing dashboard..."
python main.py &
PID=$!
sleep 5
ps -T -p $PID | grep -q "LiveDashboard"
DASHBOARD_RUNNING=$?
kill -SIGINT $PID
wait $PID
if [ $DASHBOARD_RUNNING -eq 0 ]; then
    echo "✓ Dashboard thread running"
else
    echo "✗ Dashboard thread not found"
    exit 1
fi

# Test 8: Metrics Collection
echo "[8/10] Testing metrics collection..."
python main.py &
PID=$!
sleep 65  # Wait for metrics export
kill -SIGINT $PID
wait $PID
if [ -f latest_session/metrics/summary.json ]; then
    echo "✓ Metrics exported"
else
    echo "✗ Metrics export failed"
    exit 1
fi

# Test 9: Dynamic Constraints
echo "[9/10] Testing dynamic execution controls..."
python main.py &
PID=$!
sleep 30
kill -SIGINT $PID
wait $PID
grep -q "DYNAMIC_CONSTRAINTS" latest_session/logs/events_*.jsonl
if [ $? -eq 0 ]; then
    echo "✓ Dynamic constraints applied"
else
    echo "⚠ Dynamic constraints not triggered (may be normal)"
fi

# Test 10: Regression Test
echo "[10/10] Running regression test..."
./scripts/run_paper_session.sh 300
if [ $? -eq 0 ]; then
    echo "✓ Regression test passed"
else
    echo "✗ Regression test failed"
    exit 1
fi

echo ""
echo "=== All Integration Tests Passed ==="
```

**Success Criteria:**
- ✅ Alle 10 Tests bestanden
- ✅ Keine Errors in Logs
- ✅ Keine Memory Leaks
- ✅ Keine Zombie Threads

---

### Task 4.2: Performance Validation
**Aufwand:** 1 Stunde

**Benchmarks:**

```python
# benchmark.py
import time
import psutil
import os

def benchmark_main_loop():
    """Benchmark main loop cycle time"""
    print("Benchmarking main loop performance...")

    # Start bot
    import subprocess
    proc = subprocess.Popen(['python', 'main.py'])
    pid = proc.pid

    time.sleep(60)  # Run for 1 minute

    # Measure performance
    process = psutil.Process(pid)
    cpu_percent = process.cpu_percent(interval=5)
    mem_info = process.memory_info()

    print(f"CPU Usage: {cpu_percent}%")
    print(f"Memory: {mem_info.rss / 1024 / 1024:.1f} MB")

    # Check cycle times from logs
    proc.terminate()
    proc.wait()

    # Analyze logs
    with open('latest_session/logs/system_*.jsonl', 'r') as f:
        slow_cycles = [l for l in f if 'ENGINE_SLOW_CYCLE' in l]

    print(f"Slow cycles (>1s): {len(slow_cycles)}")

    # Thresholds
    assert cpu_percent < 50, "CPU usage too high"
    assert mem_info.rss / 1024 / 1024 < 500, "Memory usage too high"
    assert len(slow_cycles) < 10, "Too many slow cycles"

    print("✓ Performance benchmarks passed")

if __name__ == '__main__':
    benchmark_main_loop()
```

**Success Criteria:**
- ✅ CPU usage < 50%
- ✅ Memory usage < 500MB
- ✅ Slow cycles < 10 in 1 minute
- ✅ No performance regression vs baseline

---

### Task 4.3: Production Deployment Checklist
**Aufwand:** 1 Stunde

**Pre-Deployment:**
- [ ] All Phase 1 fixes deployed and tested
- [ ] All Phase 2 fixes deployed and tested
- [ ] All Phase 3 fixes deployed and tested
- [ ] Integration tests passing (10/10)
- [ ] Performance benchmarks passing
- [ ] Regression test passing
- [ ] Code reviewed and approved
- [ ] Documentation updated
- [ ] Rollback plan prepared

**Deployment Steps:**
1. Backup current production code
2. Deploy new version to staging
3. Run 24-hour soak test in staging
4. Review metrics and logs
5. Deploy to production with monitoring
6. Monitor first 6 hours closely
7. Verify success metrics

**Post-Deployment Monitoring (First 24h):**
- [ ] Order fill rate > 30%
- [ ] Pending intents < 10
- [ ] ERROR logs for failures present
- [ ] No stackdumps
- [ ] Budget refresh timeouts < 5%
- [ ] All threads stop cleanly on shutdown
- [ ] Telegram notifications working
- [ ] Dashboard displaying correctly

**Success Criteria:**
- ✅ Zero critical bugs in first 24h
- ✅ All success metrics met
- ✅ No rollback required
- ✅ Team confidence high

---

## Summary & Timeline

### Implementation Timeline

| Phase | Tasks | Duration | Dependencies |
|-------|-------|----------|--------------|
| Phase 1 | 4 critical fixes | Week 1 (5h) | None |
| Phase 2 | 5 high priority | Week 2 (5h) | Phase 1 complete |
| Phase 3 | 3 medium priority | Week 3 (4h) | Phase 2 complete |
| Phase 4 | Testing & deployment | Week 4 (6h) | Phase 3 complete |
| **Total** | **14 tasks** | **3-4 weeks (20h)** | Sequential |

### Risk Matrix

| Risk | Probability | Impact | Mitigation |
|------|------------|--------|------------|
| Phase 1 breaks shutdown | LOW | HIGH | Extensive testing + rollback plan |
| Performance degradation | LOW | MEDIUM | Benchmarks + monitoring |
| State corruption | LOW | HIGH | Validation + periodic backups |
| Metrics overhead | LOW | LOW | Optional feature, can disable |
| Integration issues | MEDIUM | MEDIUM | Incremental deployment + staging |

### Success Metrics Targets

| Metric | Baseline | Target | Measurement |
|--------|----------|--------|-------------|
| Order Fill Rate | 0% | >30% | orders.jsonl |
| Pending Intents | 53 | <10 | engine_transient.json |
| ERROR Logs | 0 (silent) | Present | events.jsonl |
| Stackdumps | 1 | 0 | stackdump.txt |
| Budget Timeouts | Unknown | <5% | Metrics |
| Thread Cleanup | Partial | 100% | Logs |
| Telegram Notifications | Partial | 100% | User reports |

### Rollback Procedures

**If Critical Issue Detected:**
```bash
# 1. Stop production bot
kill -SIGTERM $(pgrep -f "python main.py")

# 2. Restore backup
git checkout v4.4-stable  # Previous stable version
# or
cp -r backup/trading-bot-v4.4/* .

# 3. Restart bot
python main.py &

# 4. Verify rollback successful
grep "Bot erfolgreich gestartet" latest_session/logs/events_*.jsonl

# 5. Document issue
# Create incident report with logs and metrics
```

**Partial Rollback (Feature-Specific):**
```python
# In config.py, disable problematic features:
ENABLE_METRICS_COLLECTION = False
ENABLE_DYNAMIC_EXECUTION_CONTROLS = False
STATE_PERSIST_ON_CRASH = False
# etc.
```

---

## Maintenance & Support

### Post-Implementation Review (After 1 Week)
- [ ] Review metrics dashboard
- [ ] Analyze success criteria achievement
- [ ] Document lessons learned
- [ ] Identify optimization opportunities
- [ ] Update CODE_REVIEW_CLAUDE.md with results

### Ongoing Monitoring
- Daily: Check error rates and fill rates
- Weekly: Review metrics trends
- Monthly: Performance optimization review
- Quarterly: Security and dependency updates

### Documentation Updates
- [x] CODE_REVIEW_CLAUDE.md
- [x] IMPLEMENTATION_PLAN.md
- [ ] CHANGELOG.md (update after each phase)
- [ ] README.md (if user-facing changes)
- [ ] TESTING_AND_MONITORING.md (update with new tests)

---

**Plan Version:** 1.0
**Author:** Claude (Anthropic)
**Review Status:** Ready for execution
**Estimated Success Rate:** 95%+ (with proper testing and incremental deployment)
