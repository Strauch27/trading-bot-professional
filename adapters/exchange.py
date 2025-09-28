"""
Exchange Adapter - Dünne, mockbare Schicht über CCXT
Vereinheitlicht Exchange-Zugriff und macht Tests ohne echte Exchange möglich.
"""

import time
import random
import threading
from typing import Dict, List, Optional, Any, Union
from decimal import Decimal
from abc import ABC, abstractmethod
import logging
import ccxt
import socket
import requests
from adapters.retry import with_backoff

logger = logging.getLogger(__name__)

# CCXT timeout and network error handling
CCXT_TIMEOUT_MS = 7000  # hartes Request-Timeout
NETWORK_ERRORS = (ccxt.NetworkError, ccxt.DDoSProtection, requests.RequestException, socket.timeout)

# Import for heartbeat coordination
from services.shutdown_coordinator import get_shutdown_coordinator

# Import robust sizing logic
from helpers_filters import (
    ExchangeFilters,
    size_buy_from_quote,
    size_sell_from_base,
    post_only_guard_buy,
    post_only_guard_sell,
    apply_exchange_filters,
    create_filters_from_market
)


def _emit_order_sent(log_instance, order_dict):
    """
    Emit ORDER_SENT event with mandatory field validation.
    Ensures all required fields are present for proper audit trail.
    """
    required_fields = {"symbol", "side", "price", "qty", "notional", "filters", "audit"}
    missing = required_fields - set(order_dict.keys())

    if missing:
        # Auto-fill missing fields with safe defaults
        order_dict.setdefault("audit", {})
        order_dict.setdefault("filters", {})
        log_instance.warning("ORDER_SENT_MISSING_FIELDS", extra={"missing": sorted(missing)})

    # Create log entry with event_type
    log_data = {"event_type": "ORDER_SENT"}
    log_data.update(order_dict)

    log_instance.info("ORDER_SENT", extra=log_data)


class ExchangeInterface(ABC):
    """
    Abstract interface für Exchange-Operationen.
    Ermöglicht Mocking und verschiedene Exchange-Implementierungen.
    """

    @abstractmethod
    def fetch_ticker(self, symbol: str) -> Dict[str, Any]:
        """Fetches ticker data for symbol"""
        pass

    @abstractmethod
    def fetch_ohlcv(self, symbol: str, timeframe: str, limit: int = 100, since: Optional[int] = None) -> List[List]:
        """Fetches OHLCV candle data"""
        pass

    @abstractmethod
    def fetch_order_book(self, symbol: str, limit: int = 100) -> Dict[str, Any]:
        """Fetches order book for symbol"""
        pass

    @abstractmethod
    def create_limit_order(
        self,
        symbol: str,
        side: str,
        amount: float,
        price: float,
        time_in_force: str = "GTC",
        client_order_id: Optional[str] = None,
        post_only: bool = False
    ) -> Dict[str, Any]:
        """Creates limit order"""
        pass

    @abstractmethod
    def create_market_order(
        self,
        symbol: str,
        side: str,
        amount: float,
        time_in_force: str = "IOC",
        client_order_id: Optional[str] = None
    ) -> Dict[str, Any]:
        """Creates market order"""
        pass

    @abstractmethod
    def cancel_order(self, order_id: str, symbol: str) -> Dict[str, Any]:
        """Cancels order"""
        pass

    @abstractmethod
    def fetch_order(self, order_id: str, symbol: str) -> Dict[str, Any]:
        """Fetches order status"""
        pass

    @abstractmethod
    def fetch_my_trades(self, symbol: str, since: Optional[int] = None, limit: int = 100, params: Dict = None) -> List[Dict]:
        """Fetches user trades"""
        pass

    @abstractmethod
    def fetch_open_orders(self, symbol: str = None) -> List[Dict]:
        """Fetches open orders"""
        pass

    @abstractmethod
    def fetch_orders(self, symbol: str = None, since: Optional[int] = None, limit: int = 100) -> List[Dict]:
        """Fetches orders"""
        pass

    @abstractmethod
    def fetch_closed_orders(self, symbol: str = None, since: Optional[int] = None, limit: int = 100) -> List[Dict]:
        """Fetches closed orders"""
        pass

    @abstractmethod
    def fetch_balance(self) -> Dict[str, Any]:
        """Fetches account balance"""
        pass

    @abstractmethod
    def load_markets(self, reload: bool = False) -> Dict[str, Any]:
        """Loads market information"""
        pass

    @abstractmethod
    def amount_to_precision(self, symbol: str, amount: float) -> Union[str, float]:
        """Converts amount to exchange precision"""
        pass

    @abstractmethod
    def price_to_precision(self, symbol: str, price: float) -> Union[str, float]:
        """Converts price to exchange precision"""
        pass


class ExchangeAdapterRobust(ExchangeInterface):
    """
    Robuster Exchange Adapter mit hartem Timeout-Budget und Backoff-Mechanismus.
    Verhindert Thread-Blockaden durch deterministische Zeitbegrenzung.
    """

    def __init__(self, api_key, secret, **kwargs):
        """
        Args:
            api_key: Exchange API Key
            secret: Exchange API Secret
            **kwargs: Weitere CCXT-Parameter
        """
        self.x = ccxt.mexc({
            "apiKey": api_key,
            "secret": secret,
            "enableRateLimit": True,
            "timeout": CCXT_TIMEOUT_MS
        })
        self.logger = logger

    @with_backoff(max_attempts=4, base_delay=0.25, max_delay=1.0, total_time_cap_s=5.0, retry_on=NETWORK_ERRORS)
    def _safe_ccxt_call(self, fn, *args, **kwargs):
        """Harmloser Wrapper mit eindeutiger Fehlerklassifizierung"""
        return fn(*args, **kwargs)

    def fetch_ticker(self, symbol):
        return self._safe_ccxt_call(self.x.fetch_ticker, symbol)

    def fetch_ohlcv(self, symbol, timeframe="1m", limit=250):
        return self._safe_ccxt_call(self.x.fetch_ohlcv, symbol, timeframe=timeframe, limit=limit)

    def fetch_order_book(self, symbol: str, limit: int = 100) -> Dict[str, Any]:
        return self._safe_ccxt_call(self.x.fetch_order_book, symbol, limit)

    def create_limit_order(self, symbol: str, side: str, amount: float, price: float,
                          time_in_force: str = "GTC", client_order_id: Optional[str] = None,
                          post_only: bool = False) -> Dict[str, Any]:
        params = {"timeInForce": time_in_force}
        if client_order_id:
            params["clientOrderId"] = client_order_id
        if post_only:
            params["postOnly"] = True
        return self._safe_ccxt_call(self.x.create_order, symbol, "limit", side, amount, price, params)

    def create_market_order(self, symbol: str, side: str, amount: float,
                           time_in_force: str = "IOC", client_order_id: Optional[str] = None) -> Dict[str, Any]:
        params = {"timeInForce": time_in_force}
        if client_order_id:
            params["clientOrderId"] = client_order_id
        return self._safe_ccxt_call(self.x.create_order, symbol, "market", side, amount, None, params)

    def cancel_order(self, order_id: str, symbol: str) -> Dict[str, Any]:
        return self._safe_ccxt_call(self.x.cancel_order, order_id, symbol)

    def fetch_order(self, order_id: str, symbol: str) -> Dict[str, Any]:
        return self._safe_ccxt_call(self.x.fetch_order, order_id, symbol)

    def fetch_my_trades(self, symbol: str, since: Optional[int] = None, limit: int = 100, params: Dict = None) -> List[Dict]:
        return self._safe_ccxt_call(self.x.fetch_my_trades, symbol, since, limit, params or {})

    def fetch_open_orders(self, symbol: str = None) -> List[Dict]:
        return self._safe_ccxt_call(self.x.fetch_open_orders, symbol)

    def fetch_orders(self, symbol: str = None, since: Optional[int] = None, limit: int = 100) -> List[Dict]:
        return self._safe_ccxt_call(self.x.fetch_orders, symbol, since, limit)

    def fetch_closed_orders(self, symbol: str = None, since: Optional[int] = None, limit: int = 100) -> List[Dict]:
        return self._safe_ccxt_call(self.x.fetch_closed_orders, symbol, since, limit)

    def fetch_balance(self) -> Dict[str, Any]:
        return self._safe_ccxt_call(self.x.fetch_balance)

    def load_markets(self, reload: bool = False) -> Dict[str, Any]:
        return self._safe_ccxt_call(self.x.load_markets, reload)

    def amount_to_precision(self, symbol: str, amount: float) -> Union[str, float]:
        return self.x.amount_to_precision(symbol, amount)

    def price_to_precision(self, symbol: str, price: float) -> Union[str, float]:
        return self.x.price_to_precision(symbol, price)


class ExchangeAdapter(ExchangeInterface):
    def __init__(self, ccxt_exchange, max_retries=3, base_delay=1.0, enable_connection_recovery=True):
        """
        Args:
            ccxt_exchange: CCXT Exchange Instanz
            max_retries: Maximale Retry-Versuche
            base_delay: Basis-Delay für exponential backoff
            enable_connection_recovery: Enable automatic connection recovery
        """
        self.exchange = ccxt_exchange
        self.max_retries = max_retries
        self.base_delay = base_delay
        self._last_request_time = 0
        self._min_request_interval = 0.1  # Minimum 100ms zwischen Requests

        # KRITISCH: HTTP-Lock für Thread-Safety - ccxt ist NICHT threadsicher!
        # Verhindert Access Violations bei parallelen HTTP-Calls
        self._http_lock = threading.RLock()
        self._timeout_s = 10
        self._retry_backoff = (0.25, 0.5, 1.0, 2.0)

        # Rekursionsschutz für Connection Recovery
        self._in_recovery = threading.local()

        # Sichere ccxt-Konfiguration für Windows
        self.exchange.enableRateLimit = True
        self.exchange.timeout = 15000  # 15s timeout

        # Verhindere Keep-Alive-Probleme unter Windows
        if hasattr(self.exchange, 'session'):
            self.exchange.session.headers.update({'Connection': 'close'})

        # Connection recovery
        self._connection_recovery = None
        if enable_connection_recovery:
            self._setup_connection_recovery()

    def _setup_connection_recovery(self):
        """Setup connection recovery service"""
        try:
            from services.connection_recovery import ConnectionRecoveryService, RecoveryConfig

            recovery_config = RecoveryConfig(
                initial_delay=1.0,
                max_delay=60.0,
                consecutive_failures_threshold=3,
                health_check_interval=60.0  # Check every minute
            )

            self._connection_recovery = ConnectionRecoveryService(self, recovery_config)
            self._connection_recovery.start_monitoring()

            logger.info("Connection recovery service initialized")
        except Exception as e:
            logger.warning(f"Could not initialize connection recovery: {e}")

    def get_connection_health(self) -> dict:
        """Get connection health information"""
        if self._connection_recovery:
            return self._connection_recovery.get_health_summary()
        return {'status': 'unknown', 'health_score': 0}

    def is_connection_healthy(self) -> bool:
        """Check if connection is healthy"""
        if self._connection_recovery:
            return self._connection_recovery.is_connected()
        return True  # Assume healthy if no monitoring

    def force_reconnect(self) -> bool:
        """Force reconnection attempt"""
        if self._connection_recovery:
            return self._connection_recovery.force_reconnect()
        return False

    def stop_connection_monitoring(self):
        """Stop connection monitoring (cleanup)"""
        if self._connection_recovery:
            self._connection_recovery.stop_monitoring()

    def _rate_limit(self):
        """Einfaches Rate-Limiting"""
        now = time.time()
        elapsed = now - self._last_request_time
        if elapsed < self._min_request_interval:
            time.sleep(self._min_request_interval - elapsed)
        self._last_request_time = time.time()

    def _safe_ccxt_call(self, func, label: str, *args, **kwargs):
        """
        Safe wrapper for ccxt calls with heartbeat tracking and timeout management

        Args:
            func: ccxt function to call
            label: descriptive label for heartbeat tracking
            *args, **kwargs: arguments for the function

        Returns:
            Result of the function call
        """
        co = get_shutdown_coordinator()
        co.beat(f"ccxt_call_enter:{label}")

        # Log für kritische oder lange dauernde Calls
        if "fetch_ohlcv" in label or "fetch_ticker" in label:
            logger.info(f"HEARTBEAT - Starting ccxt call: {label}",
                       extra={"event_type": "HEARTBEAT"})

        try:
            # Set shorter timeout for this call
            old_timeout = getattr(self.exchange, "timeout", None)
            try:
                self.exchange.timeout = self._timeout_s * 1000  # ccxt expects milliseconds
            except Exception:
                pass

            result = func(*args, **kwargs)
            co.beat(f"ccxt_call_exit:{label}")

            # Log erfolgreichen Abschluss
            if "fetch_ohlcv" in label or "fetch_ticker" in label:
                logger.info(f"HEARTBEAT - Completed ccxt call: {label}",
                           extra={"event_type": "HEARTBEAT"})
            return result

        except Exception as e:
            co.beat(f"ccxt_call_error:{label}")
            logger.info(f"HEARTBEAT - Failed ccxt call (handled): {label} - {str(e)[:50]}",
                       extra={"event_type": "HEARTBEAT"})
            raise
        finally:
            # Restore original timeout
            try:
                if old_timeout is not None:
                    self.exchange.timeout = old_timeout
            except Exception:
                pass

    def _retry_request(self, func, *args, **kwargs):
        """
        Führt Request mit Retry-Logic und Connection Recovery aus.

        Args:
            func: Funktion die ausgeführt werden soll
            *args, **kwargs: Argumente für die Funktion

        Returns:
            Ergebnis der Funktion

        Raises:
            Exception: Nach allen Retry-Versuchen
        """
        last_error = None
        co = get_shutdown_coordinator()

        for attempt, t in enumerate(self._retry_backoff, 1):
            co.beat(f"retry_enter:attempt_{attempt}")
            try:
                # Check connection health before request (mit Rekursionsschutz)
                if (self._connection_recovery and
                    not self._connection_recovery.is_connected() and
                    not getattr(self._in_recovery, 'active', False)):
                    # Try to force reconnect on first attempt
                    logger.info("Connection unhealthy, attempting recovery before request")
                    self._in_recovery.active = True
                    try:
                        reconnect_success = self._connection_recovery.force_reconnect()
                        if not reconnect_success:
                            logger.warning("Reconnection failed, proceeding with request anyway")
                    finally:
                        self._in_recovery.active = False

                self._rate_limit()

                # KRITISCH: Alle ccxt-Calls müssen serialisiert werden!
                # Verhindert Access Violations durch parallele OpenSSL/HTTP-Zugriffe
                with self._http_lock:
                    # ccxt akzeptiert "params={'timeout': sec}" je nach Methode;
                    # fallback: client hat globales Timeout; hier defensiv erzwingen
                    if "params" in kwargs and isinstance(kwargs["params"], dict):
                        kwargs["params"] = {**kwargs["params"], "timeout": self._timeout_s}
                    result = func(*args, **kwargs)

                # Mark successful request in connection recovery
                if self._connection_recovery:
                    # Successful request indicates healthy connection
                    pass

                co.beat(f"retry_success:attempt_{attempt}")
                logger.info(f"HEARTBEAT - Retry successful on attempt {attempt}",
                           extra={"event_type": "HEARTBEAT"})
                return result

            except Exception as e:
                last_error = e
                msg = str(e).lower()
                co.beat(f"retry_error:attempt_{attempt}")
                if attempt == len(self._retry_backoff):  # Letzter Versuch
                    logger.info(f"HEARTBEAT - All retry attempts failed, giving up",
                               extra={"event_type": "HEARTBEAT"})

                # >>> NEU: Timestamp-/recvWindow-Fehler abfangen und Zeit resyncen
                if ('recvwindow' in msg) or ('timestamp' in msg and 'outside' in msg):
                    try:
                        with self._http_lock:  # ccxt nicht threadsicher
                            # großzügigeres Fenster und aktiver Zeitabgleich
                            if hasattr(self.exchange, 'options'):
                                self.exchange.options['recvWindow'] = max(30000, self.exchange.options.get('recvWindow', 0) or 0)
                            if hasattr(self.exchange, 'load_time_difference'):
                                self.exchange.load_time_difference()
                        # nach dem Resync sofort 1x direkt erneut versuchen
                        self._rate_limit()
                        with self._http_lock:
                            return func(*args, **kwargs)
                    except Exception as e2:
                        last_error = e2  # weiter unten normal weiter-retryen

                if attempt == len(self._retry_backoff):
                    co.beat("retry_failed_final")
                    break

                logger.warning(f"HTTP retry in {t}s after {type(e).__name__}: {e}")
                time.sleep(t)
            finally:
                co.beat(f"retry_exit:attempt_{attempt}")

        # Nach allen Versuchen: Exception werfen
        logger.error(
            f"Request failed after {len(self._retry_backoff)} attempts: {last_error}",
            extra={
                'event_type': 'EXCHANGE_REQUEST_FAILED',
                'max_retries': len(self._retry_backoff),
                'final_error': str(last_error)
            }
        )
        raise last_error

    def _is_connection_error(self, error: Exception) -> bool:
        """Check if error is connection-related"""
        error_str = str(error).lower()
        connection_indicators = [
            'connection', 'network', 'timeout', 'unreachable',
            'refused', 'reset', 'broken', 'socket', 'ssl',
            'certificate', 'handshake', 'dns'
        ]
        return any(indicator in error_str for indicator in connection_indicators)

    def fetch_ticker(self, symbol: str) -> Dict[str, Any]:
        """Fetches ticker with retry logic"""
        return self._safe_ccxt_call(
            lambda: self._retry_request(self.exchange.fetch_ticker, symbol),
            f"fetch_ticker:{symbol}"
        )

    def fetch_ohlcv(self, symbol: str, timeframe: str, limit: int = 100, since: Optional[int] = None) -> List[List]:
        """Fetches OHLCV with retry logic"""
        return self._safe_ccxt_call(
            lambda: self._retry_request(self.exchange.fetch_ohlcv, symbol, timeframe, limit, since),
            f"fetch_ohlcv:{symbol}:{timeframe}"
        )

    def fetch_order_book(self, symbol: str, limit: int = 100) -> Dict[str, Any]:
        """Fetches order book with retry logic"""
        return self._retry_request(self.exchange.fetch_order_book, symbol, limit)

    def create_limit_order(
        self,
        symbol: str,
        side: str,
        amount: float,
        price: float,
        time_in_force: str = "GTC",
        client_order_id: Optional[str] = None,
        post_only: bool = False
    ) -> Dict[str, Any]:
        """Creates limit order with retry logic"""
        params = {"timeInForce": time_in_force}

        if client_order_id:
            params["clientOrderId"] = client_order_id

        if post_only:
            params["postOnly"] = True

        return self._retry_request(
            self.exchange.create_order,
            symbol, "limit", side, amount, price, params
        )

    def create_market_order(
        self,
        symbol: str,
        side: str,
        amount: float,
        time_in_force: str = "IOC",
        client_order_id: Optional[str] = None
    ) -> Dict[str, Any]:
        """Creates market order with retry logic"""
        params = {"timeInForce": time_in_force}

        if client_order_id:
            params["clientOrderId"] = client_order_id

        return self._retry_request(
            self.exchange.create_order,
            symbol, "market", side, amount, None, params
        )

    def cancel_order(self, order_id: str, symbol: str) -> Dict[str, Any]:
        """Cancels order with retry logic"""
        return self._retry_request(self.exchange.cancel_order, order_id, symbol)

    def fetch_order(self, order_id: str, symbol: str) -> Dict[str, Any]:
        """Fetches order with retry logic"""
        return self._retry_request(self.exchange.fetch_order, order_id, symbol)

    def fetch_my_trades(self, symbol: str, since: Optional[int] = None, limit: int = 100, params: Dict = None) -> List[Dict]:
        """Fetches trades with retry logic"""
        return self._retry_request(self.exchange.fetch_my_trades, symbol, since, limit, params or {})

    def fetch_open_orders(self, symbol: str = None) -> List[Dict]:
        """Fetches open orders with retry logic"""
        return self._retry_request(self.exchange.fetch_open_orders, symbol)

    def fetch_orders(self, symbol: str = None, since: Optional[int] = None, limit: int = 100) -> List[Dict]:
        """Fetches orders with retry logic"""
        return self._retry_request(self.exchange.fetch_orders, symbol, since, limit)

    def fetch_closed_orders(self, symbol: str = None, since: Optional[int] = None, limit: int = 100) -> List[Dict]:
        """Fetches closed orders with retry logic"""
        return self._retry_request(self.exchange.fetch_closed_orders, symbol, since, limit)

    def fetch_balance(self) -> Dict[str, Any]:
        """Fetches balance with retry logic"""
        return self._retry_request(self.exchange.fetch_balance)

    def load_markets(self, reload: bool = False) -> Dict[str, Any]:
        """Loads markets with retry logic"""
        return self._retry_request(self.exchange.load_markets, reload)

    def amount_to_precision(self, symbol: str, amount: float) -> Union[str, float]:
        """Converts amount to exchange precision"""
        return self.exchange.amount_to_precision(symbol, amount)

    def price_to_precision(self, symbol: str, price: float) -> Union[str, float]:
        """Converts price to exchange precision"""
        return self.exchange.price_to_precision(symbol, price)

    def get_market_info(self, symbol: str) -> Dict[str, Any]:
        """Gets market information for symbol"""
        if not hasattr(self.exchange, 'markets') or not self.exchange.markets:
            self.load_markets()

        return self.exchange.markets.get(symbol, {})

    def get_min_notional(self, symbol: str) -> float:
        """Gets minimum notional value for symbol"""
        market = self.get_market_info(symbol)
        limits = market.get('limits', {})
        cost_limits = limits.get('cost', {})
        return float(cost_limits.get('min', 0.0))

    def meets_minimums(self, symbol: str, price: float, quote_amt: float) -> tuple[bool, str]:
        """Prüft minNotional/minQty/stepSize robust inkl. Preis-Fallback."""
        try:
            market = self.get_market_info(symbol)
            if not market:
                return False, "market_info_unavailable"

            f = create_filters_from_market(market)

            # Preis validieren; bei <=0 auf Orderbuch zurückfallen
            p = float(price or 0.0)
            if p <= 0:
                try:
                    book = self.fetch_order_book(symbol, limit=5) or {}
                    best_bid = float((book.get("bids") or [[0,0]])[0][0])
                    best_ask = float((book.get("asks") or [[0,0]])[0][0])
                    # konservativ: Kaufgrößen am BID bemessen
                    p = best_bid if best_bid > 0 else best_ask
                except Exception:
                    p = 0.0

            if p <= 0:
                return False, "price_zero_or_negative"

            # robuste Größenberechnung
            _, qty, notional, audit = size_buy_from_quote(quote_amt, p, f.tickSize, f.stepSize, f.minNotional)

            if qty <= 0:
                logger.warning(f"Robust sizing resulted in zero qty for {symbol}", extra={'audit': audit})
                return False, "qty_rounded_to_zero_after_robust_sizing"

            if notional > quote_amt * 1.01:
                return False, f"insufficient_budget_after_sizing:{notional:.2f}>{quote_amt:.2f}"

            return True, "ok"

        except Exception as e:
            logger.error(f"meets_minimums failed for {symbol}: {e}")
            return False, f"meets_minimums_error:{str(e)}"

    def get_rate_limit(self) -> float:
        """Gets exchange rate limit in ms"""
        return getattr(self.exchange, 'rateLimit', 200) / 1000.0

    @property
    def id(self) -> str:
        """Exchange ID"""
        return getattr(self.exchange, 'id', 'unknown')

    # New robust order placement methods
    @with_backoff(max_attempts=4, base_delay=0.25, max_delay=1.0, total_time_cap_s=5.0)
    def place_limit_buy_robust(
        self,
        symbol: str,
        quote_budget: float,
        order_cfg: Dict[str, Any],
        market_data: Dict[str, Any],
        meta: Dict[str, Any],
    ) -> Dict[str, Any]:
        """
        Robust BUY order placement mit fail-safe sizing und Post-Only-Absicherung.

        Args:
            symbol: Trading symbol
            quote_budget: Available quote budget
            order_cfg: Order configuration (post_only, tif, etc.)
            market_data: Market data with 'book' and 'filters'
            meta: Metadata for logging (decision_id, session_id, etc.)

        Returns:
            Dict with order result and audit trail
        """
        try:
            # Extract market data
            book = market_data.get("book", {})
            filters_raw = market_data.get("filters", {})

            best_bid = float(book.get("best_bid", 0))
            best_ask = float(book.get("best_ask", 0))

            if best_bid <= 0:
                return {
                    "ok": False,
                    "reason": "invalid_market_data",
                    "error": "best_bid <= 0",
                    "meta": meta
                }

            # Create exchange filters
            f = ExchangeFilters(
                tickSize=float(filters_raw.get("tickSize", 0.01)),
                stepSize=float(filters_raw.get("stepSize", 0.001)),
                minNotional=float(filters_raw.get("minNotional", 10.0)),
                minQty=float(filters_raw.get("minQty", 0.0)),
            )

            # Robust sizing
            price, qty, notional, audit = size_buy_from_quote(quote_budget, best_bid, f.tickSize, f.stepSize, f.minNotional)

            if qty <= 0:
                logger.error(f"Robust sizing failed: qty={qty} for {symbol}",
                           extra={'event_type': 'SIZING_ERROR', 'audit': audit, 'meta': meta})
                return {
                    "ok": False,
                    "reason": "qty_rounded_to_zero",
                    "audit": audit,
                    "meta": meta
                }

            # Post-Only guard
            post_only = bool(order_cfg.get("post_only", False))
            if post_only and not post_only_guard_buy(price, best_bid, f):
                if order_cfg.get("force_adjust_price_for_post_only", True):
                    # Adjust price to ensure maker status
                    price = max(0.0, best_bid - f.tickSize)
                    logger.info(f"Adjusted price for post_only: {symbol} price={price}",
                               extra={'event_type': 'POST_ONLY_PRICE_ADJUST', 'meta': meta})
                else:
                    # Disable post_only
                    post_only = False
                    logger.info(f"Disabled post_only for {symbol}",
                               extra={'event_type': 'POST_ONLY_DISABLED', 'meta': meta})

            # Log ORDER_SENT event
            order_data = {
                "symbol": symbol,
                "side": "BUY",
                "type": "LIMIT",
                "time_in_force": order_cfg.get("tif", "GTC"),
                "post_only": post_only,
                "price": price,
                "qty": qty,
                "notional": notional,
                "meta": meta,
                "filters": f.__dict__,
                "audit": audit
            }

            # Emit ORDER_SENT with mandatory field validation
            _emit_order_sent(logger, {'event_type': 'ORDER_SENT', **order_data})

            # Create actual order (if not dry run)
            if not order_cfg.get("dry_run", False):
                try:
                    result = self.create_limit_order(
                        symbol=symbol,
                        side="buy",
                        amount=qty,
                        price=price,
                        time_in_force=order_cfg.get("tif", "GTC"),
                        post_only=post_only,
                        client_order_id=meta.get("client_order_id")
                    )
                    return {"ok": True, "order": result, "audit": audit, "meta": meta}
                except Exception as e:
                    logger.error(f"Order placement failed: {e}",
                               extra={'event_type': 'ORDER_FAILED', 'error': str(e), 'meta': meta})
                    return {"ok": False, "reason": "order_failed", "error": str(e), "audit": audit, "meta": meta}
            else:
                # Dry run - return simulated order
                return {"ok": True, "order": order_data, "audit": audit, "meta": meta}

        except Exception as e:
            logger.error(f"place_limit_buy_robust failed: {e}",
                       extra={'event_type': 'ORDER_PLACEMENT_ERROR', 'error': str(e), 'meta': meta})
            return {"ok": False, "reason": "placement_error", "error": str(e), "meta": meta}

    @with_backoff(max_attempts=4, base_delay=0.25, max_delay=1.0, total_time_cap_s=5.0)
    def place_limit_sell_robust(
        self,
        symbol: str,
        base_qty: float,
        order_cfg: Dict[str, Any],
        market_data: Dict[str, Any],
        meta: Dict[str, Any],
    ) -> Dict[str, Any]:
        """
        Robust SELL order placement mit inventory-aware sizing und Post-Only-Absicherung.
        """
        try:
            # Extract market data
            book = market_data.get("book", {})
            filters_raw = market_data.get("filters", {})

            best_ask = float(book.get("best_ask", 0))
            best_bid = float(book.get("best_bid", 0))

            if best_ask <= 0:
                return {
                    "ok": False,
                    "reason": "invalid_market_data",
                    "error": "best_ask <= 0",
                    "meta": meta
                }

            # Create exchange filters
            f = ExchangeFilters(
                tickSize=float(filters_raw.get("tickSize", 0.01)),
                stepSize=float(filters_raw.get("stepSize", 0.001)),
                minNotional=float(filters_raw.get("minNotional", 10.0)),
                minQty=float(filters_raw.get("minQty", 0.0)),
            )

            # Robust sizing
            price, qty, notional, audit = size_sell_from_base(base_qty, best_ask, f)

            if qty <= 0:
                logger.error(f"Robust sizing failed: qty={qty} for {symbol}",
                           extra={'event_type': 'SIZING_ERROR', 'audit': audit, 'meta': meta})
                return {
                    "ok": False,
                    "reason": "qty_rounded_to_zero",
                    "audit": audit,
                    "meta": meta
                }

            # Post-Only guard
            post_only = bool(order_cfg.get("post_only", False))
            if post_only and not post_only_guard_sell(price, best_ask, f):
                if order_cfg.get("force_adjust_price_for_post_only", True):
                    # Adjust price to ensure maker status
                    price = best_ask + f.tickSize
                    logger.info(f"Adjusted price for post_only: {symbol} price={price}",
                               extra={'event_type': 'POST_ONLY_PRICE_ADJUST', 'meta': meta})
                else:
                    # Disable post_only
                    post_only = False
                    logger.info(f"Disabled post_only for {symbol}",
                               extra={'event_type': 'POST_ONLY_DISABLED', 'meta': meta})

            # Log ORDER_SENT event
            order_data = {
                "symbol": symbol,
                "side": "SELL",
                "type": "LIMIT",
                "time_in_force": order_cfg.get("tif", "GTC"),
                "post_only": post_only,
                "price": price,
                "qty": qty,
                "notional": notional,
                "meta": meta,
                "filters": f.__dict__,
                "audit": audit
            }

            # Emit ORDER_SENT with mandatory field validation
            _emit_order_sent(logger, {'event_type': 'ORDER_SENT', **order_data})

            # Create actual order (if not dry run)
            if not order_cfg.get("dry_run", False):
                try:
                    result = self.create_limit_order(
                        symbol=symbol,
                        side="sell",
                        amount=qty,
                        price=price,
                        time_in_force=order_cfg.get("tif", "GTC"),
                        post_only=post_only,
                        client_order_id=meta.get("client_order_id")
                    )
                    return {"ok": True, "order": result, "audit": audit, "meta": meta}
                except Exception as e:
                    logger.error(f"Order placement failed: {e}",
                               extra={'event_type': 'ORDER_FAILED', 'error': str(e), 'meta': meta})
                    return {"ok": False, "reason": "order_failed", "error": str(e), "audit": audit, "meta": meta}
            else:
                # Dry run - return simulated order
                return {"ok": True, "order": order_data, "audit": audit, "meta": meta}

        except Exception as e:
            logger.error(f"place_limit_sell_robust failed: {e}",
                       extra={'event_type': 'ORDER_PLACEMENT_ERROR', 'error': str(e), 'meta': meta})
            return {"ok": False, "reason": "placement_error", "error": str(e), "meta": meta}

    # Simple V9_3-style methods (as specified in patch)
    @with_backoff(max_attempts=4, base_delay=0.25, max_delay=1.0, total_time_cap_s=5.0)
    def place_limit_buy(self, symbol, quote_budget, order_cfg, market, meta=None):
        f = market["filters"]
        bid = market["book"]["best_bid"]
        ask = market["book"]["best_ask"]
        price, qty, notional, audit = size_buy_from_quote(
            quote_budget, ask, f.tickSize, f.stepSize, f.minNotional
        )
        # Post-Only-Absicherung
        if order_cfg.get("post_only", False):
            price = min(price, bid - f.tickSize)
        evt = {
            "event_type":"ORDER_SENT","symbol":symbol,"side":"BUY",
            "price":price,"qty":qty,"notional":notional,
            "tif":order_cfg.get("tif","GTC"),"post_only":order_cfg.get("post_only", False),
            "filters":f,"audit":audit
        }
        _emit_order_sent(logger, evt)
        # >>> hier tatsächlichen Börsen-Submit ausführen <<<
        return {"ok": True, "order": evt}

    @with_backoff(max_attempts=4, base_delay=0.25, max_delay=1.0, total_time_cap_s=5.0)
    def place_limit_sell(self, symbol, base_qty, order_cfg, market, meta=None):
        f = market["filters"]
        bid = market["book"]["best_bid"]
        price = max(0.0, bid - f.tickSize)
        notional = price * base_qty
        evt = {
            "event_type":"ORDER_SENT","symbol":symbol,"side":"SELL",
            "price":price,"qty":base_qty,"notional":notional,
            "tif":order_cfg.get("tif","GTC"),"post_only":order_cfg.get("post_only", False),
            "filters":f,"audit":{"sizing":"base_qty","tickSize":f.tickSize,"stepSize":f.stepSize}
        }
        _emit_order_sent(logger, evt)
        # >>> hier tatsächlichen Börsen-Submit ausführen <<<
        return {"ok": True, "order": evt}


class MockExchange(ExchangeInterface):
    """
    Mock Exchange für Tests.
    Simuliert Exchange-Verhalten ohne echte API-Calls.
    """

    def __init__(self, initial_prices: Dict[str, float] = None):
        """
        Args:
            initial_prices: Initiale Preise für Symbole
        """
        self.prices = initial_prices or {
            "BTC/USDT": 50000.0,
            "ETH/USDT": 3000.0,
            "LTC/USDT": 200.0
        }
        self.orders = {}
        self.trades = []
        self.balance = {
            "USDT": {"free": 10000.0, "used": 0.0, "total": 10000.0},
            "BTC": {"free": 0.0, "used": 0.0, "total": 0.0},
            "ETH": {"free": 0.0, "used": 0.0, "total": 0.0}
        }
        self.markets = {
            "BTC/USDT": {
                "id": "BTCUSDT",
                "symbol": "BTC/USDT",
                "base": "BTC",
                "quote": "USDT",
                "limits": {
                    "amount": {"min": 0.000001, "max": 1000},
                    "price": {"min": 0.01, "max": 1000000},
                    "cost": {"min": 10.0, "max": 10000000}
                },
                "precision": {"amount": 6, "price": 2}
            },
            "ETH/USDT": {
                "id": "ETHUSDT",
                "symbol": "ETH/USDT",
                "base": "ETH",
                "quote": "USDT",
                "limits": {
                    "amount": {"min": 0.0001, "max": 1000},
                    "price": {"min": 0.01, "max": 100000},
                    "cost": {"min": 10.0, "max": 10000000}
                },
                "precision": {"amount": 4, "price": 2}
            }
        }
        self._order_counter = 1000

    def _generate_order_id(self) -> str:
        """Generiert neue Order-ID"""
        self._order_counter += 1
        return str(self._order_counter)

    def set_price(self, symbol: str, price: float):
        """Setzt Preis für Symbol (für Tests)"""
        self.prices[symbol] = price

    def fetch_ticker(self, symbol: str) -> Dict[str, Any]:
        """Mock ticker data"""
        price = self.prices.get(symbol, 0.0)
        spread = price * 0.001  # 0.1% spread

        timestamp = int(time.time() * 1000)
        dt = time.strftime("%Y-%m-%dT%H:%M:%S.000Z", time.gmtime(timestamp / 1000))

        return {
            "symbol": symbol,
            "timestamp": timestamp,
            "datetime": dt,
            "high": price * 1.02,
            "low": price * 0.98,
            "bid": price - spread/2,
            "ask": price + spread/2,
            "last": price,
            "close": price,
            "baseVolume": 1000.0,
            "quoteVolume": 1000.0 * price
        }

    def fetch_ohlcv(self, symbol: str, timeframe: str, limit: int = 100, since: Optional[int] = None) -> List[List]:
        """Mock OHLCV data"""
        price = self.prices.get(symbol, 0.0)
        now = int(time.time() * 1000)
        interval_ms = 60000  # 1 minute

        ohlcv = []
        for i in range(limit):
            timestamp = now - (limit - i) * interval_ms
            # Kleine zufällige Variation
            variation = random.uniform(0.99, 1.01)
            candle_price = price * variation

            ohlcv.append([
                timestamp,                    # timestamp
                candle_price * 0.999,        # open
                candle_price * 1.001,        # high
                candle_price * 0.998,        # low
                candle_price,                # close
                random.uniform(10, 100)      # volume
            ])

        return ohlcv

    def fetch_order_book(self, symbol: str, limit: int = 100) -> Dict[str, Any]:
        """Mock order book data"""
        price = self.prices.get(symbol, 0.0)
        spread = price * 0.001  # 0.1% spread

        return {
            "symbol": symbol,
            "bids": [[price - spread/2, 100.0]] * min(limit, 20),
            "asks": [[price + spread/2, 100.0]] * min(limit, 20),
            "timestamp": int(time.time() * 1000),
            "datetime": time.strftime("%Y-%m-%dT%H:%M:%S.000Z", time.gmtime())
        }

    def create_limit_order(
        self,
        symbol: str,
        side: str,
        amount: float,
        price: float,
        time_in_force: str = "GTC",
        client_order_id: Optional[str] = None,
        post_only: bool = False
    ) -> Dict[str, Any]:
        """Mock limit order creation"""
        order_id = self._generate_order_id()

        # Simuliere IOC: sofort filled wenn Preis stimmt
        current_price = self.prices.get(symbol, 0.0)
        status = "open"
        filled = 0.0

        if time_in_force == "IOC":
            if side == "buy" and price >= current_price:
                status = "closed"
                filled = amount
            elif side == "sell" and price <= current_price:
                status = "closed"
                filled = amount
            else:
                status = "canceled"  # IOC nicht ausführbar

        order = {
            "id": order_id,
            "clientOrderId": client_order_id,
            "symbol": symbol,
            "type": "limit",
            "side": side,
            "amount": amount,
            "price": price,
            "filled": filled,
            "remaining": amount - filled,
            "status": status,
            "timestamp": int(time.time() * 1000),
            "trades": []
        }

        # Trade erstellen wenn filled
        if filled > 0:
            trade_fee_cost = filled * current_price * 0.001
            trade = {
                "id": f"trade_{order_id}",
                "order": order_id,
                "symbol": symbol,
                "side": side,
                "amount": filled,
                "price": current_price,
                "cost": filled * current_price,
                "fee": {"cost": trade_fee_cost, "currency": "USDT"},
                "timestamp": int(time.time() * 1000)
            }
            order["trades"] = [trade]
            order["average"] = current_price  # FIX: Engine braucht order.average
            order["fee"] = {"cost": trade_fee_cost, "currency": "USDT"}  # FIX: Engine braucht order.fee
            order["cost"] = filled * current_price  # FIX: Vollständige Order-Struktur
            self.trades.append(trade)

        self.orders[order_id] = order
        return order

    def create_market_order(
        self,
        symbol: str,
        side: str,
        amount: float,
        time_in_force: str = "IOC",
        client_order_id: Optional[str] = None
    ) -> Dict[str, Any]:
        """Mock market order creation"""
        current_price = self.prices.get(symbol, 0.0)

        # Market orders werden immer sofort filled
        return self.create_limit_order(
            symbol, side, amount, current_price, "IOC", client_order_id
        )

    def cancel_order(self, order_id: str, symbol: str) -> Dict[str, Any]:
        """Mock order cancellation"""
        if order_id in self.orders:
            order = self.orders[order_id]
            order["status"] = "canceled"
            return order
        else:
            raise Exception(f"Order {order_id} not found")

    def fetch_order(self, order_id: str, symbol: str) -> Dict[str, Any]:
        """Mock order fetching"""
        if order_id in self.orders:
            return self.orders[order_id]
        else:
            raise Exception(f"Order {order_id} not found")

    def fetch_my_trades(self, symbol: str, since: Optional[int] = None, limit: int = 100, params: Dict = None) -> List[Dict]:
        """Mock trades fetching"""
        symbol_trades = [t for t in self.trades if t["symbol"] == symbol]

        if since:
            symbol_trades = [t for t in symbol_trades if t["timestamp"] >= since]

        return symbol_trades[-limit:] if limit else symbol_trades

    def fetch_open_orders(self, symbol: str = None) -> List[Dict]:
        """Mock open orders fetching"""
        open_orders = [o for o in self.orders.values() if o["status"] == "open"]
        if symbol:
            open_orders = [o for o in open_orders if o["symbol"] == symbol]
        return open_orders

    def fetch_orders(self, symbol: str = None, since: Optional[int] = None, limit: int = 100) -> List[Dict]:
        """Mock orders fetching"""
        orders = list(self.orders.values())
        if symbol:
            orders = [o for o in orders if o["symbol"] == symbol]
        if since:
            orders = [o for o in orders if o["timestamp"] >= since]
        return orders[-limit:] if limit else orders

    def fetch_closed_orders(self, symbol: str = None, since: Optional[int] = None, limit: int = 100) -> List[Dict]:
        """Mock closed orders fetching"""
        closed_orders = [o for o in self.orders.values() if o["status"] in ["closed", "canceled"]]
        if symbol:
            closed_orders = [o for o in closed_orders if o["symbol"] == symbol]
        if since:
            closed_orders = [o for o in closed_orders if o["timestamp"] >= since]
        return closed_orders[-limit:] if limit else closed_orders

    def fetch_balance(self) -> Dict[str, Any]:
        """Mock balance fetching"""
        return {
            "info": {},
            **self.balance
        }

    def load_markets(self, reload: bool = False) -> Dict[str, Any]:
        """Mock markets loading"""
        return self.markets

    def amount_to_precision(self, symbol: str, amount: float) -> Union[str, float]:
        """Mock amount precision"""
        market = self.markets.get(symbol, {})
        precision = market.get("precision", {}).get("amount", 6)
        return round(amount, precision)

    def price_to_precision(self, symbol: str, price: float) -> Union[str, float]:
        """Mock price precision"""
        market = self.markets.get(symbol, {})
        precision = market.get("precision", {}).get("price", 2)
        return round(price, precision)

    def get_market_info(self, symbol: str) -> Dict[str, Any]:
        """Gets market information for symbol"""
        return self.markets.get(symbol, {})

    def get_min_notional(self, symbol: str) -> float:
        """Gets minimum notional value for symbol"""
        market = self.get_market_info(symbol)
        limits = market.get('limits', {})
        cost_limits = limits.get('cost', {})
        return float(cost_limits.get('min', 0.0))

    def get_rate_limit(self) -> float:
        """Gets exchange rate limit in ms"""
        return 200 / 1000.0  # 200ms default

    @property
    def id(self) -> str:
        """Exchange ID"""
        return "mock_exchange"