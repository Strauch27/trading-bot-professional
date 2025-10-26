#!/usr/bin/env python3
"""
Retry Module with Exponential Backoff

Provides decorators for retry logic with:
- Exponential backoff with jitter
- Hard timeout limits
- Shutdown awareness
- Phase 2: Structured retry event logging

Usage:
    @with_backoff(max_attempts=3, base_delay=0.5)
    def risky_operation():
        # code that might fail
        pass
"""

import functools
import logging
import random
import time

log = logging.getLogger(__name__)


def with_backoff(max_attempts=5, base_delay=0.2, max_delay=2.0, total_time_cap_s=6.0, retry_on=(Exception,)):
    """
    Exponentieller Backoff mit Jitter + hartem Gesamtzeitbudget.

    Bricht deterministisch ab und spammt Threads nicht voll.
    Shutdown-aware: Bricht bei shutdown request sofort ab.
    Phase 2: Logs structured retry_attempt events for forensic analysis.

    Args:
        max_attempts: Maximum number of retry attempts
        base_delay: Initial delay in seconds (doubles each retry)
        max_delay: Maximum delay cap in seconds
        total_time_cap_s: Total time budget across all retries
        retry_on: Tuple of exception types to retry on

    Returns:
        Decorated function with retry logic
    """
    def deco(fn):
        @functools.wraps(fn)
        def wrapper(*args, **kwargs):
            # Extract symbol if available (for better event context)
            symbol = kwargs.get('symbol') or (args[0] if args and isinstance(args[0], str) else 'unknown')
            operation = fn.__name__

            # Try to get shutdown coordinator (optional)
            try:
                from services.shutdown_coordinator import get_shutdown_coordinator
                shutdown_coordinator = get_shutdown_coordinator()
            except ImportError:
                shutdown_coordinator = None

            start = time.monotonic()
            delay = base_delay
            attempt = 0

            while True:
                attempt += 1
                try:
                    return fn(*args, **kwargs)

                except retry_on as e:
                    elapsed = time.monotonic() - start
                    error_class = e.__class__.__name__
                    error_message = str(e)[:300]

                    # Check if we should retry or fail
                    will_retry = (attempt < max_attempts) and (elapsed + delay <= total_time_cap_s)

                    # Calculate backoff time
                    backoff_ms = int(min(max_delay, delay) * (1.0 + 0.25 * random.random()) * 1000)

                    # Phase 2: Log structured retry_attempt event
                    try:
                        from core.event_schemas import RetryAttempt
                        from core.logger_factory import AUDIT_LOG, log_event
                        from core.trace_context import Trace

                        retry_event = RetryAttempt(
                            symbol=symbol,
                            operation=operation,
                            attempt=attempt,
                            max_retries=max_attempts,
                            error_class=error_class,
                            error_message=error_message,
                            backoff_ms=backoff_ms,
                            will_retry=will_retry
                        )

                        with Trace():
                            log_event(
                                AUDIT_LOG(),
                                "retry_attempt",
                                **retry_event.model_dump(),
                                level=logging.WARNING if not will_retry else logging.INFO
                            )
                    except Exception as log_error:
                        # Never fail on logging
                        log.debug(f"Failed to log retry_attempt event: {log_error}")

                    # Fail if max attempts reached or time budget exceeded
                    if not will_retry:
                        log.warning(
                            f"Retry failed for {operation}: attempt {attempt}/{max_attempts}, "
                            f"elapsed {elapsed:.2f}s, error: {error_message}"
                        )
                        raise

                    # Check if shutdown requested before retrying
                    if shutdown_coordinator and shutdown_coordinator.is_shutdown_requested():
                        log.info(f"Retry aborted due to shutdown: {operation} (attempt {attempt})")
                        raise

                    # Sleep with backoff
                    sleep_for = backoff_ms / 1000.0
                    log.info(
                        f"Retrying {operation}: attempt {attempt}/{max_attempts}, "
                        f"backoff {sleep_for:.3f}s"
                    )

                    # Shutdown-aware sleep
                    if shutdown_coordinator:
                        if shutdown_coordinator.wait_for_shutdown(timeout=sleep_for):
                            log.info(f"Retry interrupted by shutdown: {operation}")
                            raise
                    else:
                        time.sleep(sleep_for)

                    # Increase delay for next retry
                    delay *= 2.0

        return wrapper
    return deco
