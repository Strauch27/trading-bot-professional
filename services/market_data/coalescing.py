#!/usr/bin/env python3
"""
Request Coalescing for Market Data

Prevents duplicate API calls when multiple threads request the same data simultaneously.
Uses lock-based request deduplication with shared result distribution.

Features:
- Thread-safe request coordination
- Automatic cleanup of completed requests
- Error propagation to all waiting threads
- Timeout support for stale requests

Example:
    cache = get_coalescing_cache()

    # Multiple threads calling this simultaneously will share one API call
    result = cache.get_or_fetch(
        key="BTC/USDT:ticker",
        fetch_fn=lambda: exchange.fetch_ticker("BTC/USDT"),
        timeout_ms=5000
    )
"""

from __future__ import annotations
import threading
import time
from typing import Dict, Callable, TypeVar, Optional, Any
from dataclasses import dataclass, field
from enum import Enum
import logging

logger = logging.getLogger(__name__)

T = TypeVar('T')


class RequestState(Enum):
    """State of an in-flight request."""
    PENDING = "pending"      # Request in progress
    COMPLETED = "completed"  # Request finished successfully
    FAILED = "failed"        # Request failed with error


@dataclass
class InFlightRequest:
    """
    Tracks an in-flight request that multiple threads may be waiting for.

    Attributes:
        state: Current state of the request
        result: The result data (if COMPLETED)
        error: The error exception (if FAILED)
        event: Threading event to signal completion
        start_time: Unix timestamp when request started
        waiters: Number of threads waiting for this request
    """
    state: RequestState = RequestState.PENDING
    result: Any = None
    error: Optional[Exception] = None
    event: threading.Event = field(default_factory=threading.Event)
    start_time: float = field(default_factory=time.time)
    waiters: int = 0

    def wait(self, timeout_s: Optional[float] = None) -> bool:
        """
        Wait for request completion.

        Args:
            timeout_s: Maximum time to wait in seconds

        Returns:
            True if request completed, False if timed out
        """
        return self.event.wait(timeout=timeout_s)

    def complete(self, result: Any) -> None:
        """Mark request as successfully completed."""
        self.state = RequestState.COMPLETED
        self.result = result
        self.event.set()

    def fail(self, error: Exception) -> None:
        """Mark request as failed with error."""
        self.state = RequestState.FAILED
        self.error = error
        self.event.set()


class CoalescingCache:
    """
    Thread-safe request coalescing cache.

    When multiple threads request the same resource, only one fetch is performed
    and all threads receive the same result.

    Example:
        cache = CoalescingCache()

        # Thread 1, 2, 3 all call this at same time for "BTC/USDT"
        # Only one actual API call is made, result shared with all
        result = cache.get_or_fetch(
            key="ticker:BTC/USDT",
            fetch_fn=lambda: exchange.fetch_ticker("BTC/USDT")
        )
    """

    def __init__(self):
        self._in_flight: Dict[str, InFlightRequest] = {}
        self._lock = threading.Lock()
        self._stats = {
            "requests": 0,
            "coalesced": 0,
            "fetches": 0,
            "errors": 0,
        }

    def get_or_fetch(
        self,
        key: str,
        fetch_fn: Callable[[], T],
        timeout_ms: int = 5000
    ) -> T:
        """
        Get data from cache or fetch if not in-flight.

        If another thread is already fetching this key, waits for result.
        If no fetch is in progress, initiates fetch and shares result with other threads.

        Args:
            key: Unique identifier for the request (e.g., "ticker:BTC/USDT")
            fetch_fn: Function to call to fetch data (called only once per key)
            timeout_ms: Maximum time to wait for in-flight request (milliseconds)

        Returns:
            The fetched data

        Raises:
            Exception: If fetch_fn raises an exception
            TimeoutError: If waiting for in-flight request times out

        Thread Safety:
            Safe to call from multiple threads simultaneously
        """
        self._stats["requests"] += 1
        timeout_s = timeout_ms / 1000.0

        # Fast path: Check if request is already in-flight
        with self._lock:
            if key in self._in_flight:
                request = self._in_flight[key]
                request.waiters += 1
                self._stats["coalesced"] += 1
                is_waiting = True
            else:
                # We're the first - create request entry
                request = InFlightRequest()
                self._in_flight[key] = request
                request.waiters = 1
                is_waiting = False

        # If we're waiting for another thread's fetch
        if is_waiting:
            try:
                # Wait for the fetch to complete
                completed = request.wait(timeout_s=timeout_s)

                if not completed:
                    # Timeout waiting for other thread
                    logger.warning(f"Coalescing timeout waiting for {key}")
                    raise TimeoutError(f"Timeout waiting for coalesced request: {key}")

                # Check result
                if request.state == RequestState.COMPLETED:
                    return request.result
                elif request.state == RequestState.FAILED:
                    raise request.error
                else:
                    raise RuntimeError(f"Unexpected request state: {request.state}")
            finally:
                with self._lock:
                    request.waiters -= 1
                    # Cleanup if we're the last waiter
                    if request.waiters == 0 and request.state != RequestState.PENDING:
                        self._in_flight.pop(key, None)

        # We're the thread that fetches
        else:
            try:
                # Perform the actual fetch
                self._stats["fetches"] += 1
                result = fetch_fn()

                # Mark as completed and wake waiters
                request.complete(result)

                return result

            except Exception as e:
                # Mark as failed and propagate error to waiters
                self._stats["errors"] += 1
                request.fail(e)
                raise

            finally:
                # Cleanup: Remove from in-flight after brief delay
                # (gives waiting threads time to read result)
                def cleanup():
                    time.sleep(0.1)
                    with self._lock:
                        # Only remove if no waiters and not pending
                        if key in self._in_flight:
                            req = self._in_flight[key]
                            if req.waiters == 0 and req.state != RequestState.PENDING:
                                self._in_flight.pop(key, None)

                cleanup_thread = threading.Thread(target=cleanup, daemon=True)
                cleanup_thread.start()

    def clear_stale(self, max_age_s: float = 10.0) -> int:
        """
        Remove stale in-flight requests that have been pending too long.

        Args:
            max_age_s: Maximum age in seconds before request is considered stale

        Returns:
            Number of stale requests removed
        """
        now = time.time()
        removed = 0

        with self._lock:
            stale_keys = [
                key for key, req in self._in_flight.items()
                if req.state == RequestState.PENDING and (now - req.start_time) > max_age_s
            ]

            for key in stale_keys:
                logger.warning(f"Removing stale coalescing request: {key}")
                req = self._in_flight.pop(key)
                req.fail(TimeoutError(f"Request became stale: {key}"))
                removed += 1

        return removed

    def get_statistics(self) -> Dict[str, Any]:
        """
        Get coalescing cache statistics.

        Returns:
            Dict with keys:
                - requests: Total number of get_or_fetch calls
                - coalesced: Number of requests that waited for another thread
                - fetches: Number of actual fetch_fn calls
                - errors: Number of failed fetches
                - in_flight: Current number of in-flight requests
                - coalesce_rate: Percentage of requests that were coalesced
        """
        with self._lock:
            stats = self._stats.copy()
            stats["in_flight"] = len(self._in_flight)

        if stats["requests"] > 0:
            stats["coalesce_rate"] = stats["coalesced"] / stats["requests"]
        else:
            stats["coalesce_rate"] = 0.0

        return stats

    def reset_statistics(self) -> None:
        """Reset all statistics counters."""
        self._stats = {
            "requests": 0,
            "coalesced": 0,
            "fetches": 0,
            "errors": 0,
        }


# ============================================================================
# Global Singleton
# ============================================================================

_global_coalescing_cache: Optional[CoalescingCache] = None
_cache_lock = threading.Lock()


def get_coalescing_cache() -> CoalescingCache:
    """
    Get the global coalescing cache singleton.

    Returns:
        Global CoalescingCache instance

    Thread Safety:
        Safe to call from multiple threads
    """
    global _global_coalescing_cache

    if _global_coalescing_cache is None:
        with _cache_lock:
            if _global_coalescing_cache is None:
                _global_coalescing_cache = CoalescingCache()

    return _global_coalescing_cache


def reset_coalescing_cache() -> None:
    """
    Reset the global coalescing cache (for testing).

    Warning:
        This should only be used in tests. Calling this while
        other threads are using the cache will cause errors.
    """
    global _global_coalescing_cache
    with _cache_lock:
        _global_coalescing_cache = None
