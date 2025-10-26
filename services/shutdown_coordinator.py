"""
Thread-Safe Shutdown Coordinator
Manages clean shutdown across multiple threads and components
"""

import time
import time as _t
import threading
from threading import Lock
import logging
import sys
import platform
import signal as _signal
import collections
from typing import Dict, List, Callable, Optional, Any
from dataclasses import dataclass
from enum import Enum


logger = logging.getLogger(__name__)


class ShutdownReason(Enum):
    """Possible shutdown reasons"""
    USER_INTERRUPT = "user_interrupt"  # Ctrl+C
    SYSTEM_SIGNAL = "system_signal"    # SIGTERM
    ENGINE_ERROR = "engine_error"      # Critical engine failure
    MANUAL_REQUEST = "manual_request"  # Programmatic shutdown
    HEALTH_CHECK_FAIL = "health_check_fail"  # Health monitoring failure
    HEARTBEAT_TIMEOUT = "heartbeat_timeout"  # Heartbeat monitoring timeout


@dataclass
class ShutdownRequest:
    """Represents a shutdown request"""
    reason: ShutdownReason
    initiator: str
    message: Optional[str] = None
    timestamp: float = 0.0
    emergency: bool = False  # Emergency shutdown (skip graceful cleanup)

    def __post_init__(self):
        if self.timestamp == 0.0:
            self.timestamp = time.time()


class ShutdownCoordinator:
    """
    Thread-safe coordinator for managing application shutdown

    Prevents race conditions between signal handlers, engine threads,
    and the main application loop.
    """

    def __init__(
        self,
        join_timeout_s: float = 3.0,
        auto_shutdown_on_missed_heartbeat: bool = False,
        *,
        # Legacy/alternative parameter names for backwards compatibility:
        shutdown_timeout: Optional[float] = None,
        join_grace_s: Optional[float] = None,
        **kwargs  # Ignore unknown keywords to prevent future TypeErrors
    ):
        # Map legacy parameter names to new internal fields
        if shutdown_timeout is not None:
            # Allow main.py to pass shutdown_timeout
            join_timeout_s = float(shutdown_timeout)
            logger.info("SHUTDOWN_CFG", extra={
                "join_timeout_s": join_timeout_s,
                "source": "shutdown_timeout_parameter"
            })
        else:
            logger.info("SHUTDOWN_CFG", extra={
                "join_timeout_s": join_timeout_s,
                "source": "join_timeout_s_parameter"
            })

        if join_grace_s is None:
            join_grace_s = 0.0

        # Log any unused kwargs for future compatibility
        if kwargs:
            logger.warning("SHUTDOWN_CFG_UNUSED_KWARGS", extra={"keys": list(kwargs.keys())})

        # Initialize internal state
        self.shutdown_timeout = join_timeout_s  # Keep for backwards compatibility
        self._join_timeout_s = join_timeout_s
        self._join_grace_s = float(join_grace_s)
        self.auto_shutdown_on_missed_heartbeat = auto_shutdown_on_missed_heartbeat
        self._shutdown_event = threading.Event()
        self._shutdown_lock = threading.RLock()
        self._shutdown_request: Optional[ShutdownRequest] = None
        self._cleanup_callbacks: List[Callable[[], None]] = []
        self._components: Dict[str, Any] = {}
        self._threads: List[threading.Thread] = []
        self._original_handlers = {}
        self._shutdown_in_progress = False
        self._shutdown_complete = False

        # Centralized heartbeat tracking
        self._hb_lock = Lock()
        self._last_heartbeat = time.time()
        self._beat_history = collections.deque(maxlen=200)  # (ts, label, thread)

        # Statistics
        self._stats = {
            'shutdown_requests': 0,
            'emergency_shutdowns': 0,
            'cleanup_callbacks_executed': 0,
            'cleanup_failures': 0
        }

        # Start heartbeat logger thread
        # CRITICAL FIX (C-SERV-05): Non-daemon thread with explicit join during shutdown
        self._heartbeat_interval = 30.0  # Log every 30 seconds
        self._heartbeat_thread = threading.Thread(
            target=self._heartbeat_logger,
            daemon=False,
            name="ShutdownHeartbeatLogger"
        )
        self._heartbeat_thread.start()

    def _heartbeat_logger(self) -> None:
        """Background thread that logs shutdown coordinator heartbeat status"""
        while not self._shutdown_event.is_set():
            try:
                # Wait for interval or shutdown signal
                if self._shutdown_event.wait(timeout=self._heartbeat_interval):
                    break  # Shutdown requested

                # Collect component and thread status
                with self._shutdown_lock:
                    registered_components = list(self._components.keys())
                    registered_threads = [
                        {
                            'name': t.name,
                            'alive': t.is_alive(),
                            'daemon': t.daemon
                        }
                        for t in self._threads
                    ]

                # Collect recent heartbeat activity
                with self._hb_lock:
                    recent_beats = list(self._beat_history)[-10:]  # Last 10 beats
                    last_beat_age = time.time() - self._last_heartbeat if self._last_heartbeat else None

                # Log SHUTDOWN_HEARTBEAT event
                logger.info(
                    "SHUTDOWN_HEARTBEAT",
                    extra={
                        'event_type': 'SHUTDOWN_HEARTBEAT',
                        'registered_components': registered_components,
                        'registered_threads': registered_threads,
                        'recent_heartbeats': recent_beats,
                        'last_heartbeat_age_s': last_beat_age,
                        'shutdown_requested': self._shutdown_event.is_set(),
                        'stats': dict(self._stats)
                    }
                )

            except Exception as e:
                logger.debug(f"Heartbeat logger error: {e}")

    def register_component(self, name: str, component: Any) -> None:
        """Register a component for shutdown management"""
        with self._shutdown_lock:
            self._components[name] = component
            logger.debug(f"Registered component for shutdown: {name}")

    def register_thread(self, t: threading.Thread) -> None:
        """Register thread for shutdown management"""
        with self._shutdown_lock:
            self._threads.append(t)
            logger.debug(f"Registered thread for shutdown: {t.name}")

    def add_cleanup_callback(self, callback: Callable[[], None]) -> None:
        """Add a cleanup callback to execute during shutdown"""
        with self._shutdown_lock:
            self._cleanup_callbacks.append(callback)
            logger.debug(f"Added cleanup callback: {callback.__name__}")

    def setup_signal_handlers(self) -> None:
        """Setup thread-safe signal handlers"""
        def signal_handler(signum, frame):
            signal_names = {
                _signal.SIGINT: "SIGINT",
                _signal.SIGTERM: "SIGTERM"
            }
            signal_name = signal_names.get(signum, f"Signal-{signum}")

            reason = ShutdownReason.USER_INTERRUPT if signum == _signal.SIGINT else ShutdownReason.SYSTEM_SIGNAL

            request = ShutdownRequest(
                reason=reason,
                initiator=f"signal_handler_{signal_name}",
                message=f"Received {signal_name} signal"
            )

            self.request_shutdown(request)

        # Nur im Main-Thread; auf Windows keine OS-Signale setzen
        if threading.current_thread() is threading.main_thread():
            try:
                if platform.system() != "Windows":
                    self._original_handlers[_signal.SIGINT] = _signal.signal(_signal.SIGINT, signal_handler)
                    self._original_handlers[_signal.SIGTERM] = _signal.signal(_signal.SIGTERM, signal_handler)
                    logger.info("Signal handlers installed (POSIX)")
                else:
                    logger.info("Windows detected: skip OS signal handlers; using Event-only shutdown")
            except Exception as e:
                logger.warning(f"Signal handler install skipped: {e}")
        else:
            logger.warning("Signal handlers can only be installed from main thread")

    def beat(self, label: str = "engine"):
        """Record a heartbeat with label and thread info"""
        ts = time.time()
        self._last_heartbeat = ts
        try:
            self._beat_history.append((ts, label, threading.current_thread().name))
        except Exception:
            pass

    def record_heartbeat(self, source: str = "engine"):
        """Record a heartbeat from the specified source (legacy method)"""
        self.beat(source)

    def request_shutdown(self, request: ShutdownRequest) -> bool:
        """
        Request application shutdown

        Returns:
            True if shutdown was initiated, False if already in progress
        """
        with self._shutdown_lock:
            self._stats['shutdown_requests'] += 1

            if self._shutdown_in_progress:
                logger.warning(f"Shutdown already in progress, ignoring request from {request.initiator}")
                return False

            if request.emergency:
                self._stats['emergency_shutdowns'] += 1

            self._shutdown_request = request
            self._shutdown_in_progress = True

            logger.info(f"Shutdown requested: {request.reason.value} by {request.initiator}"
                       f"{' (EMERGENCY)' if request.emergency else ''}")

            if request.message:
                logger.info(f"Shutdown message: {request.message}")

            # Signal shutdown event
            self._shutdown_event.set()

            return True

    def is_shutdown_requested(self) -> bool:
        """Check if shutdown has been requested"""
        return self._shutdown_event.is_set()

    def wait_for_shutdown(self, timeout: Optional[float] = None) -> bool:
        """
        Wait for shutdown signal with optional timeout.

        Args:
            timeout: Optional timeout in seconds. If None, waits indefinitely with 1s heartbeat checks.
                    If provided, waits for that duration and returns True if shutdown was signaled.

        Returns:
            True if shutdown was signaled, False if timeout occurred (only when timeout is specified)
        """
        if timeout is not None:
            # Single wait with specific timeout (used by main loop)
            return self._shutdown_event.wait(timeout=timeout)

        # Wait indefinitely with heartbeat monitoring (used at final shutdown)
        while not self._shutdown_event.wait(timeout=1.0):
            pass

        # Small grace period for cleanup
        if self._join_grace_s > 0:
            logger.info(f"Shutdown grace period: {self._join_grace_s}s")
            time.sleep(self._join_grace_s)

        # CRITICAL FIX (C-SERV-04): Create snapshot to prevent race condition
        # Modifying _threads list during iteration causes incomplete cleanup
        with self._shutdown_lock:
            threads_copy = list(self._threads)

        # Join all registered threads with timeout
        for t in threads_copy:
            if t.is_alive():
                t.join(timeout=self._join_timeout_s)
                if t.is_alive():
                    logger.warning("SHUTDOWN_FORCE", extra={
                        "thread": t.name,
                        "timeout_s": self._join_timeout_s
                    })

        logger.info("SHUTDOWN_COMPLETE")
        return True

    def execute_graceful_shutdown(self) -> bool:
        """
        Execute graceful shutdown sequence

        Returns:
            True if shutdown completed successfully, False if timeout/error
        """
        if not self._shutdown_request:
            logger.warning("No shutdown request found")
            return False

        start_time = time.time()
        request = self._shutdown_request

        logger.info(f"Starting graceful shutdown: {request.reason.value}")

        try:
            # Emergency shutdown skips graceful cleanup
            if request.emergency:
                logger.warning("Emergency shutdown - skipping graceful cleanup")
                self._force_shutdown()
                return True

            # Execute cleanup callbacks
            self._execute_cleanup_callbacks()

            # Shutdown registered components
            self._shutdown_components()

            # Final system cleanup
            self._final_cleanup()

            shutdown_duration = time.time() - start_time
            logger.info(f"Graceful shutdown completed in {shutdown_duration:.2f}s")

            with self._shutdown_lock:
                self._shutdown_complete = True

            return True

        except Exception as e:
            logger.error(f"Error during graceful shutdown: {e}")
            logger.exception("Graceful shutdown failed")

            # Fall back to force shutdown
            self._force_shutdown()
            return False

    def _execute_cleanup_callbacks(self) -> None:
        """Execute all registered cleanup callbacks"""
        logger.info(f"Executing {len(self._cleanup_callbacks)} cleanup callbacks")

        for i, callback in enumerate(self._cleanup_callbacks):
            try:
                callback_name = getattr(callback, '__name__', f'callback_{i}')
                logger.debug(f"Executing cleanup callback: {callback_name}")

                callback()
                self._stats['cleanup_callbacks_executed'] += 1

            except Exception as e:
                self._stats['cleanup_failures'] += 1
                logger.error(f"Cleanup callback {i} failed: {e}")

    def _shutdown_components(self) -> None:
        """Shutdown all registered components"""
        logger.info(f"Shutting down {len(self._components)} components")

        for name, component in self._components.items():
            try:
                logger.debug(f"Shutting down component: {name}")

                # Try different shutdown methods
                if hasattr(component, 'stop'):
                    component.stop()
                elif hasattr(component, 'shutdown'):
                    component.shutdown()
                elif hasattr(component, 'close'):
                    component.close()
                else:
                    logger.warning(f"Component {name} has no known shutdown method")

            except Exception as e:
                logger.error(f"Failed to shutdown component {name}: {e}")

    def stop_and_join(self, timeout: Optional[float] = None):
        """
        Threads koordiniert einsammeln. Nutzt optionales timeout, sonst join_timeout_s.
        """
        to = float(timeout if timeout is not None else self._join_timeout_s)
        if self._join_grace_s > 0:
            time.sleep(self._join_grace_s)
        for t in self._threads:
            t.join(timeout=to)
            if t.is_alive():
                logger.warning("SHUTDOWN_FORCE", extra={"thread": t.name, "timeout_s": to})
        logger.info("SHUTDOWN_COMPLETE")

    def _final_cleanup(self) -> None:
        """Perform final system cleanup"""
        try:
            # CRITICAL FIX (C-SERV-05): Join heartbeat thread before final cleanup
            # Heartbeat thread should exit gracefully when shutdown_event is set
            if self._heartbeat_thread and self._heartbeat_thread.is_alive():
                logger.debug("Waiting for heartbeat thread to finish...")
                self._heartbeat_thread.join(timeout=2.0)
                if self._heartbeat_thread.is_alive():
                    logger.warning("Heartbeat thread did not stop within timeout")
                else:
                    logger.debug("Heartbeat thread joined successfully")

            # Flush log handlers
            for handler in logging.getLogger().handlers:
                try:
                    handler.flush()
                except Exception:
                    pass

            # Flush stdout/stderr
            sys.stdout.flush()
            sys.stderr.flush()

            # Snapshot writer cleanup
            try:
                from loggingx import _snapshot_writer
                if _snapshot_writer:
                    _snapshot_writer.final_flush()
            except Exception as e:
                logger.debug(f"Snapshot writer cleanup failed: {e}")

        except Exception as e:
            logger.error(f"Final cleanup failed: {e}")

    def _force_shutdown(self) -> None:
        """Force immediate shutdown"""
        logger.warning("Executing force shutdown")

        try:
            # Minimal cleanup
            sys.stdout.flush()
            sys.stderr.flush()
        except Exception:
            pass

        with self._shutdown_lock:
            self._shutdown_complete = True

    def trigger_process_exit(self, code: int = 0):
        """Nur vom Main-Thread aufrufen, nachdem alle Worker sauber gestoppt sind."""
        try:
            sys.exit(code)
        except SystemExit:
            raise

    def get_shutdown_status(self) -> Dict[str, Any]:
        """Get current shutdown status"""
        with self._shutdown_lock:
            status = {
                'shutdown_requested': self._shutdown_event.is_set(),
                'shutdown_in_progress': self._shutdown_in_progress,
                'shutdown_complete': self._shutdown_complete,
                'components_registered': len(self._components),
                'cleanup_callbacks_registered': len(self._cleanup_callbacks),
                'statistics': self._stats.copy()
            }

            # Add heartbeat details
            status['last_heartbeat_ts'] = self._last_heartbeat
            status['last_heartbeat_age'] = time.time() - self._last_heartbeat
            if self._beat_history:
                ts, label, thread = self._beat_history[-1]
                status['last_beat'] = {'ts': ts, 'label': label, 'thread': thread}
                # optional: die letzten 5 Beats
                status['recent_beats'] = list(self._beat_history)[-5:]

            if self._shutdown_request:
                status['shutdown_request'] = {
                    'reason': self._shutdown_request.reason.value,
                    'initiator': self._shutdown_request.initiator,
                    'message': self._shutdown_request.message,
                    'timestamp': self._shutdown_request.timestamp,
                    'emergency': self._shutdown_request.emergency
                }

            return status

    def create_heartbeat_monitor(self, check_interval: float = 30.0,
                                timeout_threshold: float = 300.0) -> threading.Thread:
        """
        Create a heartbeat monitoring thread

        Args:
            check_interval: How often to check heartbeat
            timeout_threshold: Max time without heartbeat before shutdown

        Returns:
            Thread object (not started)
        """
        # Keep strong reference to shutdown event to prevent GC issues
        # This prevents Windows access violations when coordinator is GC'd
        shutdown_event = self._shutdown_event

        def _monitor():
            logger.info(f"Heartbeat monitor started (interval: {check_interval}s, timeout: {timeout_threshold}s, auto_shutdown: {self.auto_shutdown_on_missed_heartbeat})",
                       extra={"event_type": "HEARTBEAT_MONITOR_START"})

            # Keep local reference to prevent GC-related access violations
            while not self._shutdown_in_progress and not self.is_shutdown_requested():
                # Use local event reference instead of self._shutdown_event
                # This prevents access violations if ShutdownCoordinator is GC'd
                try:
                    if shutdown_event.wait(timeout=check_interval):
                        # Shutdown was signaled during wait
                        logger.info("Heartbeat monitor stopping due to shutdown signal",
                                   extra={"event_type": "HEARTBEAT_MONITOR_SHUTDOWN"})
                        break
                except Exception as event_error:
                    logger.error(f"Heartbeat monitor event wait error: {event_error}")
                    # If event access fails (GC issue), exit gracefully
                    break

                last = self._last_heartbeat
                elapsed = time.time() - last

                # Debug logging vor Timeout-Check
                logger.debug(
                    f"HB-Monitor: now={time.time():.3f} last_beat={last:.3f} elapsed={elapsed:.3f} threshold={timeout_threshold:.1f}"
                )

                # Immer loggen, wenn > 0.8 * threshold
                if elapsed > 0.8 * timeout_threshold:
                    last_label = None
                    last_thread = None
                    recent_beats_info = "no beats"
                    if self._beat_history:
                        _, last_label, last_thread = self._beat_history[-1]
                        # Show last 3 beats for context
                        recent_beats = list(self._beat_history)[-3:]
                        recent_beats_info = " | ".join([f"{label}@{ts:.1f}" for ts, label, _ in recent_beats])

                    logger.info(
                        f"Heartbeat late: {elapsed:.1f}s since last beat | last_label={last_label} | last_thread={last_thread} | recent: {recent_beats_info}",
                        extra={"event_type": "HEARTBEAT_LATE"}
                    )

                if elapsed > timeout_threshold:
                    if self.auto_shutdown_on_missed_heartbeat:
                        logger.error(f"Heartbeat timeout: {elapsed:.1f}s > {timeout_threshold}s -> requesting shutdown",
                                   extra={"event_type": "HEARTBEAT_TIMEOUT_SHUTDOWN"})
                        shutdown_request = ShutdownRequest(
                            reason=ShutdownReason.HEARTBEAT_TIMEOUT,
                            initiator="heartbeat_monitor",
                            message=f"Heartbeat timeout after {elapsed:.1f}s",
                            emergency=True
                        )
                        self.request_shutdown(shutdown_request)
                    else:
                        # Nur warnen, NICHT beenden - erweiterte Diagnose
                        beat_analysis = "no beat history available"
                        if self._beat_history:
                            recent_beats = list(self._beat_history)[-10:]
                            beat_analysis = f"Last 10 beats: {[(label, f'{ts:.1f}') for ts, label, _ in recent_beats]}"

                        logger.error(f"Heartbeat timeout: {elapsed:.1f}s > {timeout_threshold}s (warn-only mode) | {beat_analysis}",
                                   extra={"event_type": "HEARTBEAT_TIMEOUT"})

                        # optional: Telegram-Hinweis
                        try:
                            from services.notifier import notify_telegram
                            notify_telegram(f"⚠️ Heartbeat timeout ({elapsed:.1f}s) – Bot läuft weiter (warn-only). Last beat: {last_label or 'unknown'}")
                        except Exception:
                            pass

            # Heartbeat monitor exit logging
            logger.info("Heartbeat monitor stopped gracefully",
                       extra={"event_type": "HEARTBEAT_MONITOR_STOPPED"})

        thread = threading.Thread(target=_monitor, name="HeartbeatMonitor", daemon=False)
        return thread


# Global instance for easy access
_global_coordinator: Optional[ShutdownCoordinator] = None


def get_shutdown_coordinator() -> ShutdownCoordinator:
    """Get or create global shutdown coordinator"""
    global _global_coordinator
    if _global_coordinator is None:
        _global_coordinator = ShutdownCoordinator()
    return _global_coordinator


def request_emergency_shutdown(reason: str, initiator: str = "unknown") -> None:
    """Request emergency shutdown via global coordinator"""
    coordinator = get_shutdown_coordinator()
    request = ShutdownRequest(
        reason=ShutdownReason.MANUAL_REQUEST,
        initiator=initiator,
        message=reason,
        emergency=True
    )
    coordinator.request_shutdown(request)


def is_shutdown_requested() -> bool:
    """Check if shutdown has been requested via global coordinator"""
    return get_shutdown_coordinator().is_shutdown_requested()