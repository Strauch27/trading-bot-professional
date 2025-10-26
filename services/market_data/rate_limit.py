#!/usr/bin/env python3
"""
Rate Limit Budget for Exchange API Calls

Implements token bucket algorithm to prevent exceeding exchange rate limits.
Supports Bybit's rate limits (120 req/s by default) with configurable capacity.

Features:
- Token bucket algorithm with continuous refill
- Thread-safe operations
- Blocking and non-blocking acquire modes
- Burst capacity for traffic spikes
- Statistics tracking (throttled requests, wait times)
- Context manager support for automatic release

Example:
    limiter = get_rate_limiter()

    # Blocking acquire (waits until token available)
    with limiter.acquire():
        result = exchange.fetch_ticker("BTC/USDT")

    # Non-blocking acquire (returns immediately if no tokens)
    if limiter.try_acquire():
        result = exchange.fetch_ticker("BTC/USDT")
    else:
        print("Rate limit exceeded")

Configuration:
    Bybit spot trading API limits:
    - 120 requests/second for public endpoints
    - 50 requests/second for private endpoints

    Conservative settings (used by default):
    - Capacity: 100 tokens (burst buffer)
    - Refill rate: 80 tokens/second (66% of limit)
    - Max wait: 5 seconds
"""

from __future__ import annotations

import logging
import threading
import time
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)


@dataclass
class RateLimitStats:
    """Rate limit statistics."""
    requests: int = 0            # Total acquire attempts
    acquired: int = 0            # Successful acquires
    throttled: int = 0           # Requests that had to wait
    rejected: int = 0            # Rejected (non-blocking mode)
    total_wait_time_s: float = 0.0  # Total time spent waiting
    max_wait_time_s: float = 0.0    # Maximum wait time for single request

    @property
    def avg_wait_time_s(self) -> float:
        """Average wait time per throttled request."""
        if self.throttled > 0:
            return self.total_wait_time_s / self.throttled
        return 0.0

    @property
    def throttle_rate(self) -> float:
        """Percentage of requests that were throttled."""
        if self.requests > 0:
            return self.throttled / self.requests
        return 0.0


class TokenBucket:
    """
    Thread-safe token bucket rate limiter.

    The token bucket algorithm:
    1. Bucket has a capacity (max tokens)
    2. Tokens are added at a constant rate (refill_rate)
    3. Each request consumes 1 token
    4. If no tokens available, request must wait or be rejected

    Attributes:
        capacity: Maximum number of tokens in bucket
        refill_rate: Tokens added per second
        tokens: Current number of available tokens
    """

    def __init__(
        self,
        capacity: int = 100,
        refill_rate: float = 80.0,
        initial_tokens: Optional[int] = None
    ):
        """
        Initialize token bucket.

        Args:
            capacity: Maximum tokens in bucket (burst capacity)
            refill_rate: Tokens added per second
            initial_tokens: Starting tokens (defaults to capacity)
        """
        self.capacity = capacity
        self.refill_rate = refill_rate
        self.tokens = initial_tokens if initial_tokens is not None else capacity
        self._last_refill = time.time()
        self._lock = threading.Lock()
        self._stats = RateLimitStats()

    def _refill(self) -> None:
        """
        Refill tokens based on time elapsed.

        Called before each acquire to add tokens based on elapsed time.
        Thread Safety: Must be called with _lock held.
        """
        now = time.time()
        elapsed = now - self._last_refill

        # Calculate tokens to add
        tokens_to_add = elapsed * self.refill_rate

        # Add tokens but cap at capacity
        self.tokens = min(self.capacity, self.tokens + tokens_to_add)

        # Update last refill time
        self._last_refill = now

    def acquire(
        self,
        tokens: int = 1,
        blocking: bool = True,
        timeout: Optional[float] = None
    ) -> bool:
        """
        Acquire tokens from bucket.

        Args:
            tokens: Number of tokens to acquire (default 1)
            blocking: If True, wait until tokens available
            timeout: Maximum wait time in seconds (None = infinite)

        Returns:
            True if tokens acquired, False if rejected (non-blocking mode)

        Raises:
            TimeoutError: If timeout exceeded in blocking mode
        """
        self._stats.requests += 1
        start_time = time.time()
        wait_time = 0.0

        while True:
            with self._lock:
                # Refill tokens based on elapsed time
                self._refill()

                # Check if enough tokens available
                if self.tokens >= tokens:
                    self.tokens -= tokens
                    self._stats.acquired += 1

                    # Track wait time if we had to wait
                    if wait_time > 0:
                        self._stats.throttled += 1
                        self._stats.total_wait_time_s += wait_time
                        self._stats.max_wait_time_s = max(self._stats.max_wait_time_s, wait_time)

                    return True

                # Not enough tokens
                if not blocking:
                    # Non-blocking mode - reject immediately
                    self._stats.rejected += 1
                    return False

                # Check timeout
                if timeout is not None:
                    elapsed = time.time() - start_time
                    if elapsed >= timeout:
                        self._stats.rejected += 1
                        raise TimeoutError(f"Rate limit acquire timeout after {elapsed:.2f}s")

            # Wait before retrying (outside lock to allow other threads)
            # Calculate how long to wait based on refill rate
            wait_duration = tokens / self.refill_rate
            time.sleep(min(0.1, wait_duration))  # Cap at 100ms to remain responsive
            wait_time = time.time() - start_time

    def try_acquire(self, tokens: int = 1) -> bool:
        """
        Try to acquire tokens without blocking.

        Args:
            tokens: Number of tokens to acquire

        Returns:
            True if tokens acquired, False otherwise
        """
        return self.acquire(tokens=tokens, blocking=False)

    @contextmanager
    def acquire_context(self, tokens: int = 1, blocking: bool = True, timeout: Optional[float] = None):
        """
        Context manager for acquiring tokens.

        Usage:
            with limiter.acquire_context():
                # Do rate-limited operation
                result = api_call()

        Args:
            tokens: Number of tokens to acquire
            blocking: If True, wait until tokens available
            timeout: Maximum wait time in seconds
        """
        acquired = self.acquire(tokens=tokens, blocking=blocking, timeout=timeout)
        try:
            yield acquired
        finally:
            # Nothing to release in token bucket
            # (tokens are consumed, not borrowed)
            pass

    def get_available_tokens(self) -> float:
        """
        Get current number of available tokens.

        Returns:
            Current token count
        """
        with self._lock:
            self._refill()
            return self.tokens

    def get_statistics(self) -> Dict[str, Any]:
        """
        Get rate limiter statistics.

        Returns:
            Dict with statistics:
                - requests: Total acquire attempts
                - acquired: Successful acquires
                - throttled: Requests that had to wait
                - rejected: Rejected requests
                - throttle_rate: Percentage throttled
                - avg_wait_time_s: Average wait time
                - max_wait_time_s: Maximum wait time
                - current_tokens: Current tokens available
        """
        with self._lock:
            self._refill()
            return {
                "requests": self._stats.requests,
                "acquired": self._stats.acquired,
                "throttled": self._stats.throttled,
                "rejected": self._stats.rejected,
                "throttle_rate": self._stats.throttle_rate,
                "avg_wait_time_s": self._stats.avg_wait_time_s,
                "max_wait_time_s": self._stats.max_wait_time_s,
                "current_tokens": self.tokens,
                "capacity": self.capacity,
                "refill_rate": self.refill_rate,
            }

    def reset_statistics(self) -> None:
        """Reset all statistics counters."""
        with self._lock:
            self._stats = RateLimitStats()


class RateLimiter:
    """
    Rate limiter with multiple buckets for different API endpoints.

    Bybit API limits:
    - Public endpoints: 120 req/s
    - Private endpoints: 50 req/s

    This limiter manages separate buckets for different rate limit groups.
    """

    def __init__(
        self,
        public_capacity: int = 100,
        public_rate: float = 80.0,
        private_capacity: int = 40,
        private_rate: float = 30.0
    ):
        """
        Initialize rate limiter with separate buckets.

        Args:
            public_capacity: Public endpoint burst capacity
            public_rate: Public endpoint refill rate (req/s)
            private_capacity: Private endpoint burst capacity
            private_rate: Private endpoint refill rate (req/s)
        """
        self.public_bucket = TokenBucket(capacity=public_capacity, refill_rate=public_rate)
        self.private_bucket = TokenBucket(capacity=private_capacity, refill_rate=private_rate)
        self._enabled = True

    def acquire(
        self,
        endpoint_type: str = "public",
        tokens: int = 1,
        blocking: bool = True,
        timeout: Optional[float] = 5.0
    ) -> bool:
        """
        Acquire tokens for API call.

        Args:
            endpoint_type: "public" or "private"
            tokens: Number of tokens to acquire
            blocking: If True, wait until tokens available
            timeout: Maximum wait time

        Returns:
            True if acquired, False if rejected
        """
        if not self._enabled:
            return True

        bucket = self.public_bucket if endpoint_type == "public" else self.private_bucket
        return bucket.acquire(tokens=tokens, blocking=blocking, timeout=timeout)

    @contextmanager
    def acquire_context(
        self,
        endpoint_type: str = "public",
        tokens: int = 1,
        blocking: bool = True,
        timeout: Optional[float] = 5.0
    ):
        """
        Context manager for acquiring rate limit tokens.

        Usage:
            limiter = RateLimiter()
            with limiter.acquire_context(endpoint_type="public"):
                result = exchange.fetch_ticker("BTC/USDT")

        Args:
            endpoint_type: "public" or "private"
            tokens: Number of tokens to acquire
            blocking: If True, wait until tokens available
            timeout: Maximum wait time
        """
        acquired = self.acquire(endpoint_type=endpoint_type, tokens=tokens, blocking=blocking, timeout=timeout)
        try:
            yield acquired
        finally:
            pass

    def get_statistics(self) -> Dict[str, Any]:
        """
        Get statistics for all buckets.

        Returns:
            Dict with statistics for public and private buckets
        """
        return {
            "public": self.public_bucket.get_statistics(),
            "private": self.private_bucket.get_statistics(),
            "enabled": self._enabled
        }

    def enable(self) -> None:
        """Enable rate limiting."""
        self._enabled = True

    def disable(self) -> None:
        """Disable rate limiting (for testing)."""
        self._enabled = False


# ============================================================================
# Global Singleton
# ============================================================================

_global_rate_limiter: Optional[RateLimiter] = None
_limiter_lock = threading.Lock()


def get_rate_limiter(
    public_capacity: int = 100,
    public_rate: float = 80.0,
    private_capacity: int = 40,
    private_rate: float = 30.0
) -> RateLimiter:
    """
    Get the global rate limiter singleton.

    Args:
        public_capacity: Public endpoint burst capacity
        public_rate: Public endpoint refill rate (req/s)
        private_capacity: Private endpoint burst capacity
        private_rate: Private endpoint refill rate (req/s)

    Returns:
        Global RateLimiter instance

    Thread Safety:
        Safe to call from multiple threads
    """
    global _global_rate_limiter

    if _global_rate_limiter is None:
        with _limiter_lock:
            if _global_rate_limiter is None:
                _global_rate_limiter = RateLimiter(
                    public_capacity=public_capacity,
                    public_rate=public_rate,
                    private_capacity=private_capacity,
                    private_rate=private_rate
                )

    return _global_rate_limiter


def reset_rate_limiter() -> None:
    """
    Reset the global rate limiter (for testing).

    Warning:
        This should only be used in tests.
    """
    global _global_rate_limiter
    with _limiter_lock:
        _global_rate_limiter = None
