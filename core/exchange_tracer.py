#!/usr/bin/env python3
"""
Exchange Tracer - Comprehensive CCXT Request/Response Logging

Wraps CCXT exchange instance to log all API calls with:
- Request parameters
- Response payloads (sanitized)
- Latency (milliseconds)
- HTTP status codes
- Rate limit information
- Retry attempts and backoffs

This provides a complete audit trail of all exchange interactions,
essential for debugging failed orders and API issues.

Usage:
    import ccxt
    from core.exchange_tracer import TracedExchange

    # Create CCXT exchange
    ccxt_exchange = ccxt.mexc({...})

    # Wrap with tracer
    exchange = TracedExchange(ccxt_exchange)

    # Use normally - all calls are logged
    ticker = exchange.fetch_ticker("BTC/USDT")
    order = exchange.create_order("BTC/USDT", "limit", "buy", 0.001, 50000)
"""

import time
import logging
from typing import Any, Dict, List, Optional, Callable
from functools import wraps

from core.logger_factory import TRACER_LOG, log_event, safe_exchange_raw


logger = logging.getLogger(__name__)


class TracedExchange:
    """
    Wrapper for CCXT exchange with comprehensive request/response logging.

    Logs every API call to TRACER_LOG with:
    - API category (public/private)
    - Method name
    - Parameters
    - Latency
    - HTTP status
    - Rate limit remaining
    - Response payload (sanitized)
    """

    def __init__(self, ccxt_client: Any):
        """
        Initialize traced exchange.

        Args:
            ccxt_client: CCXT exchange instance
        """
        self._exchange = ccxt_client
        self._request_count = 0
        self._total_latency_ms = 0

    def _trace_call(
        self,
        api: str,
        method: str,
        fn: Callable,
        *args,
        **kwargs
    ) -> Any:
        """
        Trace a single API call.

        Args:
            api: API category ("public", "private", "other")
            method: Method name (e.g., "fetch_ticker", "create_order")
            fn: Function to call
            *args: Positional arguments
            **kwargs: Keyword arguments

        Returns:
            Function result

        Raises:
            Exception: Re-raises any exception after logging
        """
        # Generate request ID
        self._request_count += 1
        request_id = f"req_{self._request_count:06d}"

        # Start timing
        t_start = time.perf_counter()

        # Prepare parameters for logging (sanitize)
        params_log = {
            "args": args,
            "kwargs": {k: v for k, v in kwargs.items() if k not in {"apiKey", "secret"}}
        }

        # Execute call
        success = True
        http_status = None
        rate_limit_remaining = None
        result = None
        error_info = None

        try:
            result = fn(*args, **kwargs)

            # Extract HTTP status from response headers (if available)
            if hasattr(self._exchange, 'last_response_headers'):
                headers = self._exchange.last_response_headers or {}
                http_status = headers.get('Status') or headers.get('status')

                # Extract rate limit info
                rate_limit_remaining = (
                    headers.get('X-RateLimit-Remaining') or
                    headers.get('x-ratelimit-remaining') or
                    headers.get('X-MBX-USED-WEIGHT-1M')  # Binance
                )

            return result

        except Exception as e:
            success = False
            error_info = {
                "error_class": type(e).__name__,
                "error_message": str(e),
                "error_code": getattr(e, 'code', None),
            }

            # Try to extract HTTP status from exception
            if hasattr(e, 'response') and hasattr(e.response, 'status_code'):
                http_status = e.response.status_code

            # Phase 3: Log rate_limit_hit event if this is a rate limit error
            error_class_name = type(e).__name__
            if 'RateLimit' in error_class_name or http_status == 429:
                try:
                    from core.logger_factory import HEALTH_LOG
                    log_event(
                        HEALTH_LOG(),
                        "rate_limit_hit",
                        api=api,
                        method=method,
                        http_status=http_status,
                        error_message=str(e),
                        rate_limit_remaining=rate_limit_remaining,
                        level=logging.WARNING
                    )
                except Exception as health_log_error:
                    # Don't fail the call if health logging fails
                    logger.debug(f"Failed to log rate_limit_hit event: {health_log_error}")

            raise

        finally:
            # Calculate latency
            latency_ms = int((time.perf_counter() - t_start) * 1000)
            self._total_latency_ms += latency_ms

            # Sanitize response
            exchange_raw = None
            if result is not None:
                exchange_raw = safe_exchange_raw(result)
            elif error_info:
                exchange_raw = error_info

            # Log event
            log_event(
                TRACER_LOG(),
                "exchange_call",
                message=f"{api}.{method}",
                request_id=request_id,
                api=api,
                method=method,
                params=params_log,
                latency_ms=latency_ms,
                http_status=http_status,
                rate_limit_remaining=rate_limit_remaining,
                success=success,
                exchange_raw=exchange_raw,
                **(error_info or {})
            )

    # =========================================================================
    # PUBLIC API METHODS
    # =========================================================================

    def load_markets(self, reload: bool = False, params: Dict = None) -> Dict:
        """Load market information."""
        return self._trace_call(
            "public", "load_markets",
            self._exchange.load_markets,
            reload, params or {}
        )

    def fetch_ticker(self, symbol: str, params: Dict = None) -> Dict:
        """Fetch ticker for symbol."""
        return self._trace_call(
            "public", "fetch_ticker",
            self._exchange.fetch_ticker,
            symbol, params or {}
        )

    def fetch_tickers(self, symbols: List[str] = None, params: Dict = None) -> Dict:
        """Fetch tickers for multiple symbols."""
        return self._trace_call(
            "public", "fetch_tickers",
            self._exchange.fetch_tickers,
            symbols, params or {}
        )

    def fetch_order_book(self, symbol: str, limit: int = None, params: Dict = None) -> Dict:
        """Fetch order book."""
        return self._trace_call(
            "public", "fetch_order_book",
            self._exchange.fetch_order_book,
            symbol, limit, params or {}
        )

    def fetch_ohlcv(
        self,
        symbol: str,
        timeframe: str = '1m',
        since: int = None,
        limit: int = None,
        params: Dict = None
    ) -> List:
        """Fetch OHLCV candles."""
        return self._trace_call(
            "public", "fetch_ohlcv",
            self._exchange.fetch_ohlcv,
            symbol, timeframe, since, limit, params or {}
        )

    def fetch_trades(self, symbol: str, since: int = None, limit: int = None, params: Dict = None) -> List:
        """Fetch recent trades."""
        return self._trace_call(
            "public", "fetch_trades",
            self._exchange.fetch_trades,
            symbol, since, limit, params or {}
        )

    # =========================================================================
    # PRIVATE API METHODS
    # =========================================================================

    def fetch_balance(self, params: Dict = None) -> Dict:
        """Fetch account balance."""
        return self._trace_call(
            "private", "fetch_balance",
            self._exchange.fetch_balance,
            params or {}
        )

    def create_order(
        self,
        symbol: str,
        type: str,
        side: str,
        amount: float,
        price: float = None,
        params: Dict = None
    ) -> Dict:
        """Create order."""
        return self._trace_call(
            "private", "create_order",
            self._exchange.create_order,
            symbol, type, side, amount, price, params or {}
        )

    def create_limit_buy_order(
        self,
        symbol: str,
        amount: float,
        price: float,
        params: Dict = None
    ) -> Dict:
        """Create limit buy order."""
        return self._trace_call(
            "private", "create_limit_buy_order",
            self._exchange.create_limit_buy_order,
            symbol, amount, price, params or {}
        )

    def create_limit_sell_order(
        self,
        symbol: str,
        amount: float,
        price: float,
        params: Dict = None
    ) -> Dict:
        """Create limit sell order."""
        return self._trace_call(
            "private", "create_limit_sell_order",
            self._exchange.create_limit_sell_order,
            symbol, amount, price, params or {}
        )

    def create_market_buy_order(
        self,
        symbol: str,
        amount: float,
        params: Dict = None
    ) -> Dict:
        """Create market buy order."""
        return self._trace_call(
            "private", "create_market_buy_order",
            self._exchange.create_market_buy_order,
            symbol, amount, params or {}
        )

    def create_market_sell_order(
        self,
        symbol: str,
        amount: float,
        params: Dict = None
    ) -> Dict:
        """Create market sell order."""
        return self._trace_call(
            "private", "create_market_sell_order",
            self._exchange.create_market_sell_order,
            symbol, amount, params or {}
        )

    def cancel_order(self, order_id: str, symbol: str = None, params: Dict = None) -> Dict:
        """Cancel order."""
        return self._trace_call(
            "private", "cancel_order",
            self._exchange.cancel_order,
            order_id, symbol, params or {}
        )

    def cancel_all_orders(self, symbol: str = None, params: Dict = None) -> List:
        """Cancel all orders."""
        return self._trace_call(
            "private", "cancel_all_orders",
            self._exchange.cancel_all_orders,
            symbol, params or {}
        )

    def fetch_order(self, order_id: str, symbol: str = None, params: Dict = None) -> Dict:
        """Fetch order by ID."""
        return self._trace_call(
            "private", "fetch_order",
            self._exchange.fetch_order,
            order_id, symbol, params or {}
        )

    def fetch_orders(
        self,
        symbol: str = None,
        since: int = None,
        limit: int = None,
        params: Dict = None
    ) -> List:
        """Fetch orders."""
        return self._trace_call(
            "private", "fetch_orders",
            self._exchange.fetch_orders,
            symbol, since, limit, params or {}
        )

    def fetch_open_orders(
        self,
        symbol: str = None,
        since: int = None,
        limit: int = None,
        params: Dict = None
    ) -> List:
        """Fetch open orders."""
        return self._trace_call(
            "private", "fetch_open_orders",
            self._exchange.fetch_open_orders,
            symbol, since, limit, params or {}
        )

    def fetch_closed_orders(
        self,
        symbol: str = None,
        since: int = None,
        limit: int = None,
        params: Dict = None
    ) -> List:
        """Fetch closed orders."""
        return self._trace_call(
            "private", "fetch_closed_orders",
            self._exchange.fetch_closed_orders,
            symbol, since, limit, params or {}
        )

    def fetch_my_trades(
        self,
        symbol: str = None,
        since: int = None,
        limit: int = None,
        params: Dict = None
    ) -> List:
        """Fetch my trades."""
        return self._trace_call(
            "private", "fetch_my_trades",
            self._exchange.fetch_my_trades,
            symbol, since, limit, params or {}
        )

    # =========================================================================
    # PASSTHROUGH PROPERTIES
    # =========================================================================

    @property
    def markets(self) -> Dict:
        """Access markets property."""
        return self._exchange.markets

    @property
    def symbols(self) -> List[str]:
        """Access symbols property."""
        return self._exchange.symbols

    @property
    def currencies(self) -> Dict:
        """Access currencies property."""
        return self._exchange.currencies

    @property
    def id(self) -> str:
        """Exchange ID."""
        return self._exchange.id

    @property
    def name(self) -> str:
        """Exchange name."""
        return self._exchange.name

    @property
    def last_response_headers(self) -> Dict:
        """Last response headers."""
        return getattr(self._exchange, 'last_response_headers', {})

    def __getattr__(self, name: str) -> Any:
        """
        Fallback for any methods not explicitly wrapped.

        This ensures compatibility even if some methods are missing.
        """
        attr = getattr(self._exchange, name)

        # If it's a callable, wrap it with tracing
        if callable(attr):
            def traced_method(*args, **kwargs):
                # Determine API type from method name
                api = "private" if any(
                    x in name.lower()
                    for x in ["balance", "order", "trade", "withdraw", "deposit"]
                ) else "public"

                return self._trace_call(api, name, attr, *args, **kwargs)

            return traced_method

        return attr

    # =========================================================================
    # STATISTICS
    # =========================================================================

    def get_stats(self) -> Dict[str, Any]:
        """Get tracer statistics."""
        return {
            "total_requests": self._request_count,
            "total_latency_ms": self._total_latency_ms,
            "avg_latency_ms": (
                self._total_latency_ms / self._request_count
                if self._request_count > 0
                else 0
            ),
        }
