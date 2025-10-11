# adapters/retry.py
import time
import random
import functools
import logging

log = logging.getLogger(__name__)

def with_backoff(max_attempts=5, base_delay=0.2, max_delay=2.0, total_time_cap_s=6.0, retry_on=(Exception,)):
    """
    Exponentieller Backoff mit Jitter + hartem Gesamtzeitbudget.
    Bricht deterministisch ab und spammt Threads nicht voll.
    Shutdown-aware: Bricht bei shutdown request sofort ab.
    """
    def deco(fn):
        @functools.wraps(fn)
        def wrapper(*args, **kwargs):
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
                    if attempt >= max_attempts or elapsed + delay > total_time_cap_s:
                        log.warning("ORDER_UPDATE", extra={
                            "status": "RETRY_FAILED", "attempt": attempt, "elapsed_s": round(elapsed,3),
                            "error": str(e)[:300]
                        })
                        raise

                    # Check if shutdown requested before retrying
                    if shutdown_coordinator and shutdown_coordinator.is_shutdown_requested():
                        log.info("ORDER_UPDATE", extra={
                            "status": "RETRY_ABORTED_SHUTDOWN", "attempt": attempt
                        })
                        raise

                    sleep_for = min(max_delay, delay) * (1.0 + 0.25*random.random())
                    log.info("ORDER_UPDATE", extra={
                        "status": "RETRYING", "attempt": attempt, "sleep_s": round(sleep_for,3)
                    })

                    # Shutdown-aware sleep
                    if shutdown_coordinator:
                        if shutdown_coordinator.wait_for_shutdown(timeout=sleep_for):
                            log.info("ORDER_UPDATE", extra={"status": "RETRY_INTERRUPTED_SHUTDOWN"})
                            raise
                    else:
                        time.sleep(sleep_for)

                    delay *= 2.0
        return wrapper
    return deco