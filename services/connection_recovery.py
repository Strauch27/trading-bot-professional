"""
Exchange Connection Recovery Service
Smart reconnection with exponential backoff and health monitoring
"""

import logging
import random
import threading
import time
from collections import deque
from dataclasses import dataclass
from enum import Enum
from typing import Any, Callable, Dict, List, Optional

logger = logging.getLogger(__name__)


class ConnectionState(Enum):
    """Connection state enumeration"""
    CONNECTED = "connected"
    DISCONNECTED = "disconnected"
    CONNECTING = "connecting"
    FAILED = "failed"
    RECOVERING = "recovering"


@dataclass
class ConnectionAttempt:
    """Record of a connection attempt"""
    timestamp: float
    success: bool
    error: Optional[str] = None
    attempt_number: int = 0
    duration_ms: int = 0


@dataclass
class RecoveryConfig:
    """Configuration for connection recovery"""
    # Backoff configuration
    initial_delay: float = 1.0
    max_delay: float = 300.0  # 5 minutes
    backoff_multiplier: float = 2.0
    jitter_factor: float = 0.15

    # Health check configuration
    health_check_interval: float = 30.0
    health_check_timeout: float = 10.0
    consecutive_failures_threshold: int = 3

    # Recovery limits
    max_recovery_attempts: int = 10
    recovery_window: float = 3600.0  # 1 hour
    connection_timeout: float = 30.0

    # Circuit breaker
    circuit_breaker_failure_threshold: int = 5
    circuit_breaker_timeout: float = 60.0


class ConnectionRecoveryService:
    """
    Manages exchange connection health and automatic recovery
    """

    def __init__(self, exchange_adapter, config: Optional[RecoveryConfig] = None):
        self.exchange_adapter = exchange_adapter
        self.config = config or RecoveryConfig()

        # State management
        self._state = ConnectionState.DISCONNECTED
        self._state_lock = threading.RLock()
        self._recovery_thread: Optional[threading.Thread] = None
        self._health_monitor_thread: Optional[threading.Thread] = None
        self._shutdown_event = threading.Event()

        # Connection tracking
        self._last_successful_connection: Optional[float] = None
        self._consecutive_failures = 0
        self._attempt_history: deque = deque(maxlen=100)
        self._recovery_attempts_window: deque = deque(maxlen=self.config.max_recovery_attempts)

        # Circuit breaker
        self._circuit_breaker_open_until: float = 0.0
        self._circuit_breaker_failures = 0

        # Health monitoring
        self._health_check_callbacks: List[Callable[[], bool]] = []
        self._last_health_check: Optional[float] = None

        # Statistics
        self._stats = {
            'total_attempts': 0,
            'successful_connections': 0,
            'failed_connections': 0,
            'recovery_sessions': 0,
            'circuit_breaker_trips': 0,
            'health_checks_performed': 0,
            'health_check_failures': 0
        }

        # Default health checks
        self._setup_default_health_checks()

    def _setup_default_health_checks(self):
        """Setup default health check methods"""

        def basic_markets_check() -> bool:
            """Basic health check - can we load markets?"""
            try:
                markets = self.exchange_adapter.load_markets(reload=False)
                return bool(markets and len(markets) > 0)
            except Exception:
                return False

        def balance_check() -> bool:
            """Health check - can we fetch balance?"""
            try:
                balance = self.exchange_adapter.fetch_balance()
                return bool(balance)
            except Exception:
                return False

        self.add_health_check(basic_markets_check)
        self.add_health_check(balance_check)

    def add_health_check(self, callback: Callable[[], bool]) -> None:
        """Add a custom health check callback"""
        self._health_check_callbacks.append(callback)

    def get_state(self) -> ConnectionState:
        """Get current connection state"""
        with self._state_lock:
            return self._state

    def is_connected(self) -> bool:
        """Check if connection is healthy"""
        return self.get_state() == ConnectionState.CONNECTED

    def is_circuit_breaker_open(self) -> bool:
        """Check if circuit breaker is open"""
        return time.time() < self._circuit_breaker_open_until

    def start_monitoring(self) -> None:
        """Start connection monitoring and recovery"""
        logger.info("Starting connection recovery service")

        # Start health monitoring thread
        if not self._health_monitor_thread or not self._health_monitor_thread.is_alive():
            self._health_monitor_thread = threading.Thread(
                target=self._health_monitor_loop,
                name="ConnectionHealthMonitor",
                daemon=True
            )
            self._health_monitor_thread.start()

        # Initial connection test
        self._test_connection()

    def stop_monitoring(self) -> None:
        """Stop connection monitoring and recovery"""
        logger.info("Stopping connection recovery service")
        self._shutdown_event.set()

        # Wait for threads to finish
        if self._recovery_thread and self._recovery_thread.is_alive():
            self._recovery_thread.join(timeout=5.0)

        if self._health_monitor_thread and self._health_monitor_thread.is_alive():
            self._health_monitor_thread.join(timeout=5.0)

    def _set_state(self, new_state: ConnectionState) -> None:
        """Set connection state with logging"""
        with self._state_lock:
            if new_state != self._state:
                old_state = self._state
                self._state = new_state
                logger.info(f"Connection state changed: {old_state.value} -> {new_state.value}")

    def _test_connection(self) -> bool:
        """Test if connection is working"""
        start_time = time.time()
        attempt = ConnectionAttempt(timestamp=start_time, success=False)

        try:
            self._stats['total_attempts'] += 1

            # Circuit breaker check
            if self.is_circuit_breaker_open():
                logger.debug("Circuit breaker is open, skipping connection test")
                return False

            # Run health checks
            all_passed = True
            for health_check in self._health_check_callbacks:
                try:
                    if not health_check():
                        all_passed = False
                        break
                except Exception as e:
                    logger.debug(f"Health check failed: {e}")
                    all_passed = False
                    break

            # Record attempt result
            duration_ms = int((time.time() - start_time) * 1000)
            attempt.success = all_passed
            attempt.duration_ms = duration_ms

            if all_passed:
                self._on_connection_success()
                return True
            else:
                self._on_connection_failure("Health checks failed")
                return False

        except Exception as e:
            duration_ms = int((time.time() - start_time) * 1000)
            attempt.error = str(e)
            attempt.duration_ms = duration_ms
            self._on_connection_failure(str(e))
            return False

        finally:
            self._attempt_history.append(attempt)

    def _on_connection_success(self) -> None:
        """Handle successful connection"""
        with self._state_lock:
            self._stats['successful_connections'] += 1
            self._last_successful_connection = time.time()
            self._consecutive_failures = 0
            self._circuit_breaker_failures = 0
            self._set_state(ConnectionState.CONNECTED)

        logger.info("Connection test successful")

    def _on_connection_failure(self, error: str) -> None:
        """Handle connection failure"""
        with self._state_lock:
            self._stats['failed_connections'] += 1
            self._consecutive_failures += 1
            self._circuit_breaker_failures += 1

            logger.warning(f"Connection test failed: {error} "
                          f"(consecutive failures: {self._consecutive_failures})")

            # Check if should trip circuit breaker
            if self._circuit_breaker_failures >= self.config.circuit_breaker_failure_threshold:
                self._trip_circuit_breaker()

            # Determine new state
            if self._consecutive_failures >= self.config.consecutive_failures_threshold:
                self._set_state(ConnectionState.FAILED)
                self._start_recovery()
            else:
                self._set_state(ConnectionState.DISCONNECTED)

    def _trip_circuit_breaker(self) -> None:
        """Trip the circuit breaker"""
        timeout = self.config.circuit_breaker_timeout
        self._circuit_breaker_open_until = time.time() + timeout
        self._stats['circuit_breaker_trips'] += 1

        logger.warning(f"Connection circuit breaker tripped for {timeout}s "
                      f"(failures: {self._circuit_breaker_failures})")

    def _start_recovery(self) -> None:
        """Start connection recovery process"""
        if self._recovery_thread and self._recovery_thread.is_alive():
            logger.debug("Recovery already in progress")
            return

        logger.info("Starting connection recovery")
        self._stats['recovery_sessions'] += 1

        self._recovery_thread = threading.Thread(
            target=self._recovery_loop,
            name="ConnectionRecovery",
            daemon=True
        )
        self._recovery_thread.start()

    def _recovery_loop(self) -> None:
        """Main recovery loop with exponential backoff"""
        attempt_number = 0
        start_time = time.time()

        while not self._shutdown_event.is_set():
            attempt_number += 1

            # Check recovery limits
            if attempt_number > self.config.max_recovery_attempts:
                logger.error(f"Max recovery attempts ({self.config.max_recovery_attempts}) reached")
                self._set_state(ConnectionState.FAILED)
                break

            # Check recovery window
            elapsed = time.time() - start_time
            if elapsed > self.config.recovery_window:
                logger.error(f"Recovery window ({self.config.recovery_window}s) exceeded")
                self._set_state(ConnectionState.FAILED)
                break

            self._set_state(ConnectionState.RECOVERING)

            # Calculate delay with exponential backoff and jitter
            delay = self._calculate_backoff_delay(attempt_number)

            logger.info(f"Recovery attempt {attempt_number}/{self.config.max_recovery_attempts} "
                       f"in {delay:.1f}s")

            # Wait with ability to cancel
            if self._shutdown_event.wait(delay):
                break

            # Attempt recovery
            if self._attempt_recovery():
                logger.info("Connection recovery successful")
                break

        logger.info("Recovery loop ended")

    def _calculate_backoff_delay(self, attempt_number: int) -> float:
        """Calculate delay with exponential backoff and jitter"""
        delay = self.config.initial_delay * (self.config.backoff_multiplier ** (attempt_number - 1))
        delay = min(delay, self.config.max_delay)

        # Add jitter to prevent thundering herd
        jitter = delay * self.config.jitter_factor * random.uniform(-1, 1)
        delay = max(0, delay + jitter)

        return delay

    def _attempt_recovery(self) -> bool:
        """Attempt to recover connection"""
        logger.debug("Attempting connection recovery")

        try:
            # Force reload markets to test connection
            markets = self.exchange_adapter.load_markets(reload=True)
            if not markets:
                return False

            # Test with a simple balance call
            balance = self.exchange_adapter.fetch_balance()
            if not balance:
                return False

            # If we get here, connection is working
            self._on_connection_success()
            return True

        except Exception as e:
            logger.debug(f"Recovery attempt failed: {e}")
            return False

    def _health_monitor_loop(self) -> None:
        """Continuous health monitoring loop"""
        logger.info("Starting connection health monitoring")

        while not self._shutdown_event.is_set():
            try:
                # Wait for next health check
                if self._shutdown_event.wait(self.config.health_check_interval):
                    break

                # Skip health check if in recovery or circuit breaker is open
                current_state = self.get_state()
                if current_state in (ConnectionState.RECOVERING, ConnectionState.CONNECTING):
                    continue

                if self.is_circuit_breaker_open():
                    continue

                # Perform health check
                self._stats['health_checks_performed'] += 1
                self._last_health_check = time.time()

                health_ok = self._test_connection()

                if not health_ok and current_state == ConnectionState.CONNECTED:
                    logger.warning("Health check detected connection degradation")

            except Exception as e:
                self._stats['health_check_failures'] += 1
                logger.error(f"Health monitor error: {e}")

        logger.info("Connection health monitoring stopped")

    def force_reconnect(self) -> bool:
        """Force immediate reconnection attempt"""
        logger.info("Forcing reconnection attempt")

        with self._state_lock:
            self._consecutive_failures = 0
            self._circuit_breaker_failures = 0
            self._circuit_breaker_open_until = 0.0

        return self._attempt_recovery()

    def get_connection_stats(self) -> Dict[str, Any]:
        """Get connection statistics and status"""
        with self._state_lock:
            recent_attempts = list(self._attempt_history)[-10:]  # Last 10 attempts

            stats = {
                'state': self._state.value,
                'connected': self.is_connected(),
                'circuit_breaker_open': self.is_circuit_breaker_open(),
                'consecutive_failures': self._consecutive_failures,
                'last_successful_connection': self._last_successful_connection,
                'last_health_check': self._last_health_check,
                'recent_attempts': [
                    {
                        'timestamp': attempt.timestamp,
                        'success': attempt.success,
                        'duration_ms': attempt.duration_ms,
                        'error': attempt.error
                    }
                    for attempt in recent_attempts
                ],
                'statistics': self._stats.copy()
            }

            # Add timing info
            if self._last_successful_connection:
                stats['seconds_since_last_success'] = time.time() - self._last_successful_connection

            if self._circuit_breaker_open_until > time.time():
                stats['circuit_breaker_remaining_seconds'] = self._circuit_breaker_open_until - time.time()

            return stats

    def get_health_summary(self) -> Dict[str, Any]:
        """Get health summary for monitoring"""
        stats = self.get_connection_stats()

        # Calculate health score (0-100)
        health_score = 100

        if not stats['connected']:
            health_score -= 50

        if stats['circuit_breaker_open']:
            health_score -= 30

        if stats['consecutive_failures'] > 0:
            health_score -= min(20, stats['consecutive_failures'] * 5)

        # Success rate from recent attempts
        recent_attempts = stats['recent_attempts']
        if recent_attempts:
            success_rate = sum(1 for a in recent_attempts if a['success']) / len(recent_attempts)
            health_score = int(health_score * success_rate)

        return {
            'health_score': max(0, health_score),
            'status': 'healthy' if health_score >= 80 else 'degraded' if health_score >= 50 else 'unhealthy',
            'connected': stats['connected'],
            'last_success_age_seconds': stats.get('seconds_since_last_success', float('inf')),
            'consecutive_failures': stats['consecutive_failures']
        }
