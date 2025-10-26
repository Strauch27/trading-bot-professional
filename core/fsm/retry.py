#!/usr/bin/env python3
"""
Retry decorators with exponential backoff for exchange calls
"""

import functools
import logging
import time
from typing import Callable, Optional, Tuple, Type

logger = logging.getLogger(__name__)


def with_retry(
    max_attempts: int = 3,
    base_delay: float = 1.0,
    max_delay: float = 10.0,
    backoff_factor: float = 2.0,
    retryable_exceptions: Optional[Tuple[Type[Exception], ...]] = None
):
    """
    Retry decorator with exponential backoff.

    Args:
        max_attempts: Maximum retry attempts
        base_delay: Initial delay in seconds
        max_delay: Maximum delay cap
        backoff_factor: Exponential backoff multiplier
        retryable_exceptions: Tuple of exception types to retry

    Example:
        @with_retry(max_attempts=3, base_delay=1.0)
        def place_order(exchange, symbol, side, amount, price):
            return exchange.create_limit_order(symbol, side, amount, price)
    """
    # Default retryable exceptions
    if retryable_exceptions is None:
        try:
            from ccxt.base.errors import ExchangeNotAvailable, NetworkError, RequestTimeout
            retryable_exceptions = (NetworkError, ExchangeNotAvailable, RequestTimeout, ConnectionError, TimeoutError)
        except ImportError:
            logger.warning("ccxt not found, using generic exceptions for retry")
            retryable_exceptions = (ConnectionError, TimeoutError)

    def decorator(func: Callable):
        @functools.wraps(func)
        def wrapper(*args, **kwargs):
            last_exception = None

            for attempt in range(1, max_attempts + 1):
                try:
                    return func(*args, **kwargs)

                except retryable_exceptions as e:
                    last_exception = e

                    if attempt == max_attempts:
                        logger.error(
                            f"{func.__name__} failed after {max_attempts} attempts: {e}"
                        )
                        raise

                    # Calculate delay with exponential backoff
                    delay = min(base_delay * (backoff_factor ** (attempt - 1)), max_delay)

                    logger.warning(
                        f"{func.__name__} attempt {attempt}/{max_attempts} failed: {e}. "
                        f"Retrying in {delay:.1f}s..."
                    )

                    time.sleep(delay)

                except Exception as e:
                    # Non-retryable exception, fail immediately
                    logger.error(f"{func.__name__} failed with non-retryable error: {e}")
                    raise

            # Should never reach here, but for safety
            raise last_exception

        return wrapper
    return decorator


# Pre-configured decorators for common use cases

def with_order_retry(func: Callable):
    """Retry decorator for order placement (3 attempts, 1s base delay)"""
    return with_retry(max_attempts=3, base_delay=1.0)(func)


def with_fetch_retry(func: Callable):
    """Retry decorator for data fetches (5 attempts, 0.5s base delay)"""
    return with_retry(max_attempts=5, base_delay=0.5, max_delay=5.0)(func)


def with_cancel_retry(func: Callable):
    """Retry decorator for order cancellations (2 attempts, 0.5s base delay)"""
    return with_retry(max_attempts=2, base_delay=0.5)(func)


def with_trade_retry(func: Callable):
    """Retry decorator for trade execution (3 attempts, 2s base delay)"""
    return with_retry(max_attempts=3, base_delay=2.0, max_delay=10.0)(func)


# Usage examples:
"""
from core.fsm.retry import with_order_retry, with_fetch_retry

@with_order_retry
def place_buy_order(exchange, symbol, amount, price):
    return exchange.create_limit_order(symbol, 'buy', amount, price)

@with_fetch_retry
def fetch_orderbook(exchange, symbol):
    return exchange.fetch_order_book(symbol)

@with_cancel_retry
def cancel_order(exchange, order_id, symbol):
    return exchange.cancel_order(order_id, symbol)
"""
