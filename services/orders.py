"""
Order Service - Zentrale Order-Platzierung und -Verwaltung
Kapselt Order-Logic mit Retry, Cache und einheitlichem Error-Handling.
"""

import logging
import threading
import time
from typing import Any, Dict, Optional, Tuple

logger = logging.getLogger(__name__)


class OrderService:
    """
    Zentrale Order-Verwaltung mit Retry-Logic und Cache.

    Kapselt alle Order-Operationen (Place/Replace/Cancel) und bietet
    einheitliche API für verschiedene Order-Typen.
    """

    def __init__(self, exchange, cache=None, max_retries: int = 3):
        """
        Args:
            exchange: ExchangeAdapter Instanz
            cache: Optional OrderCache Instanz
            max_retries: Maximale Retry-Versuche
        """
        self.exchange = exchange
        self.cache = cache
        self.max_retries = max_retries
        self._lock = threading.RLock()

        # Statistiken
        self._stats = {
            'orders_placed': 0,
            'orders_canceled': 0,
            'orders_replaced': 0,
            'retries_total': 0,
            'errors_total': 0
        }

    def place_order(
        self,
        symbol: str,
        side: str,
        amount: float,
        price: float,
        order_type: str = "limit",
        client_order_id: Optional[str] = None,
        **kwargs
    ) -> Dict[str, Any]:
        """
        Place order with specified type (dispatcher for FSM compatibility).

        This method provides a unified interface for FSMOrderRouter to place orders.
        It dispatches to place_limit_ioc() or place_market_ioc() based on order_type.

        Args:
            symbol: Trading pair (e.g., "BTC/USDT")
            side: "buy" or "sell"
            amount: Order quantity
            price: Limit price (ignored for market orders)
            order_type: "limit", "ioc", or "market" (default: "limit")
            client_order_id: Client order ID for idempotency
            **kwargs: Additional parameters

        Returns:
            CCXT order dict with keys: id, status, filled, average, etc.

        Raises:
            ValueError: If order_type is not supported
            Exception: If order placement fails
        """
        if order_type in ("limit", "ioc"):
            # Use limit IOC order
            result = self.place_limit_ioc(
                symbol=symbol,
                side=side,
                amount=amount,
                price=price,
                client_order_id=client_order_id
            )
        elif order_type == "market":
            # Use market IOC order
            result = self.place_market_ioc(
                symbol=symbol,
                side=side,
                amount=amount,
                client_order_id=client_order_id
            )
        else:
            raise ValueError(f"Unsupported order_type: {order_type}")

        if result is None:
            raise Exception(f"Order placement failed: No result returned")

        return result

    def place_limit_ioc(
        self,
        symbol: str,
        side: str,
        amount: float,
        price: float,
        client_order_id: Optional[str] = None,
        decision_id: Optional[str] = None  # CRITICAL FIX (P3 Issue #10): Add decision_id for tracing
    ) -> Optional[Dict[str, Any]]:
        """
        Platziert Limit IOC Order.

        CRITICAL FIX (P3 Issue #10): Added decision_id parameter for complete tracing.

        Args:
            symbol: Trading pair
            side: "buy" oder "sell"
            amount: Order-Menge
            price: Limit-Preis
            client_order_id: Optional Client Order ID
            decision_id: Optional decision ID for tracing

        Returns:
            Order-Dict oder None bei Fehler
        """
        with self._lock:
            try:
                # Finale Quantisierung direkt vor Order-Call (wirklich unmittelbar davor)
                px = float(self.exchange.price_to_precision(symbol, price))
                qty = float(self.exchange.amount_to_precision(symbol, amount))

                # Sicherheitsnetz nach der Quantisierung
                if qty <= 0 or px <= 0:
                    raise ValueError(f"quantized qty/price invalid: qty={qty}, px={px}")

                order = self.exchange.create_limit_order(
                    symbol=symbol,
                    side=side,
                    amount=qty,
                    price=px,
                    time_in_force="IOC",
                    client_order_id=client_order_id,
                    post_only=False
                )

                self._stats['orders_placed'] += 1

                # Phase 0: Check and log order fills
                try:
                    from trading.orders import check_and_log_order_fills
                    check_and_log_order_fills(order, symbol)
                except Exception as fill_log_error:
                    logger.debug(f"Fill logging failed: {fill_log_error}")

                # In Cache speichern
                if self.cache:
                    self.cache.store_order(order)

                logger.info(
                    f"Limit IOC order placed: {side} {amount} {symbol} @ {price}",
                    extra={
                        'event_type': 'ORDER_PLACED',
                        'order_type': 'limit_ioc',
                        'symbol': symbol,
                        'side': side,
                        'amount': amount,
                        'price': price,
                        'order_id': order.get('id'),
                        'client_order_id': client_order_id
                    }
                )

                return order

            except Exception as e:
                self._stats['errors_total'] += 1
                logger.error(
                    f"Failed to place limit IOC order: {e}",
                    extra={
                        'event_type': 'ORDER_ERROR',
                        'order_type': 'limit_ioc',
                        'symbol': symbol,
                        'side': side,
                        'amount': amount,
                        'price': price,
                        'error': str(e)
                    }
                )
                return None

    def place_market_ioc(
        self,
        symbol: str,
        side: str,
        amount: float,
        client_order_id: Optional[str] = None
    ) -> Optional[Dict[str, Any]]:
        """
        Platziert Market IOC Order.

        Args:
            symbol: Trading pair
            side: "buy" oder "sell"
            amount: Order-Menge
            client_order_id: Optional Client Order ID

        Returns:
            Order-Dict oder None bei Fehler
        """
        with self._lock:
            try:
                # Finale Quantisierung direkt vor Order-Call (wirklich unmittelbar davor)
                qty = float(self.exchange.amount_to_precision(symbol, amount))

                # Sicherheitsnetz nach der Quantisierung
                if qty <= 0:
                    raise ValueError(f"quantized qty invalid: qty={qty}")

                order = self.exchange.create_market_order(
                    symbol=symbol,
                    side=side,
                    amount=qty,
                    time_in_force="IOC",
                    client_order_id=client_order_id
                )

                self._stats['orders_placed'] += 1

                # Phase 0: Check and log order fills
                try:
                    from trading.orders import check_and_log_order_fills
                    check_and_log_order_fills(order, symbol)
                except Exception as fill_log_error:
                    logger.debug(f"Fill logging failed: {fill_log_error}")

                # In Cache speichern
                if self.cache:
                    self.cache.store_order(order)

                logger.info(
                    f"Market IOC order placed: {side} {amount} {symbol}",
                    extra={
                        'event_type': 'ORDER_PLACED',
                        'order_type': 'market_ioc',
                        'symbol': symbol,
                        'side': side,
                        'amount': amount,
                        'order_id': order.get('id'),
                        'client_order_id': client_order_id
                    }
                )

                return order

            except Exception as e:
                self._stats['errors_total'] += 1
                logger.error(
                    f"Failed to place market IOC order: {e}",
                    extra={
                        'event_type': 'ORDER_ERROR',
                        'order_type': 'market_ioc',
                        'symbol': symbol,
                        'side': side,
                        'amount': amount,
                        'error': str(e)
                    }
                )
                return None

    def place_limit_gtc(
        self,
        symbol: str,
        side: str,
        amount: float,
        price: float,
        client_order_id: Optional[str] = None,
        post_only: bool = False
    ) -> Optional[Dict[str, Any]]:
        """
        Platziert Limit GTC Order.

        Args:
            symbol: Trading pair
            side: "buy" oder "sell"
            amount: Order-Menge
            price: Limit-Preis
            client_order_id: Optional Client Order ID
            post_only: Nur als Maker platzieren

        Returns:
            Order-Dict oder None bei Fehler
        """
        with self._lock:
            try:
                # Finale Quantisierung direkt vor Order-Call (wirklich unmittelbar davor)
                px = float(self.exchange.price_to_precision(symbol, price))
                qty = float(self.exchange.amount_to_precision(symbol, amount))

                # Sicherheitsnetz nach der Quantisierung
                if qty <= 0 or px <= 0:
                    raise ValueError(f"quantized qty/price invalid: qty={qty}, px={px}")

                order = self.exchange.create_limit_order(
                    symbol=symbol,
                    side=side,
                    amount=qty,
                    price=px,
                    time_in_force="GTC",
                    client_order_id=client_order_id,
                    post_only=post_only
                )

                self._stats['orders_placed'] += 1

                # Phase 0: Check and log order fills
                try:
                    from trading.orders import check_and_log_order_fills
                    check_and_log_order_fills(order, symbol)
                except Exception as fill_log_error:
                    logger.debug(f"Fill logging failed: {fill_log_error}")

                # In Cache speichern
                if self.cache:
                    self.cache.store_order(order)

                logger.info(
                    f"Limit GTC order placed: {side} {amount} {symbol} @ {price}",
                    extra={
                        'event_type': 'ORDER_PLACED',
                        'order_type': 'limit_gtc',
                        'symbol': symbol,
                        'side': side,
                        'amount': amount,
                        'price': price,
                        'post_only': post_only,
                        'order_id': order.get('id'),
                        'client_order_id': client_order_id
                    }
                )

                return order

            except Exception as e:
                self._stats['errors_total'] += 1
                logger.error(
                    f"Failed to place limit GTC order: {e}",
                    extra={
                        'event_type': 'ORDER_ERROR',
                        'order_type': 'limit_gtc',
                        'symbol': symbol,
                        'side': side,
                        'amount': amount,
                        'price': price,
                        'error': str(e)
                    }
                )
                return None

    def cancel_order(self, order_id: str, symbol: str, reason: str = "manual_cancel") -> bool:
        """
        Storniert Order mit strukturiertem Event-Tracking.

        Args:
            order_id: Order ID
            symbol: Trading pair
            reason: Cancellation reason (default: "manual_cancel")

        Returns:
            True wenn erfolgreich storniert
        """
        with self._lock:
            try:
                # Phase 0: Fetch order before cancel for tracking
                order = None
                filled_before_cancel = 0.0
                remaining_qty = 0.0
                age_seconds = None

                try:
                    order = self.exchange.fetch_order(order_id, symbol)
                    filled_before_cancel = float(order.get('filled', 0))
                    remaining_qty = float(order.get('remaining', 0))

                    # Calculate order age
                    if order.get('timestamp'):
                        import time
                        age_seconds = time.time() - (order['timestamp'] / 1000)
                except Exception as fetch_error:
                    logger.debug(f"Failed to fetch order before cancel: {fetch_error}")

                # Cancel order
                self.exchange.cancel_order(order_id, symbol)

                self._stats['orders_canceled'] += 1

                # Cache aktualisieren
                if self.cache:
                    self.cache.update_order_status(order_id, "canceled")

                # Phase 0: Log structured order_cancel event
                try:
                    from trading.orders import log_order_cancel
                    log_order_cancel(
                        exchange_order_id=order_id,
                        symbol=symbol,
                        reason=reason,
                        filled_before_cancel=filled_before_cancel,
                        remaining_qty=remaining_qty,
                        age_seconds=age_seconds
                    )
                except Exception as log_error:
                    logger.debug(f"Failed to log order_cancel: {log_error}")

                logger.info(
                    f"Order canceled: {order_id} for {symbol} (reason: {reason})",
                    extra={
                        'event_type': 'ORDER_CANCELED',
                        'order_id': order_id,
                        'symbol': symbol,
                        'reason': reason
                    }
                )

                return True

            except Exception as e:
                self._stats['errors_total'] += 1

                # Phase 0: Log failed cancellation
                try:
                    from trading.orders import log_order_cancel
                    log_order_cancel(
                        exchange_order_id=order_id,
                        symbol=symbol,
                        reason=f"{reason}_failed",
                        filled_before_cancel=0.0,
                        remaining_qty=0.0,
                        age_seconds=None
                    )
                except Exception:
                    pass

                logger.error(
                    f"Failed to cancel order {order_id}: {e}",
                    extra={
                        'event_type': 'ORDER_CANCEL_ERROR',
                        'order_id': order_id,
                        'symbol': symbol,
                        'error': str(e)
                    }
                )
                return False

    def replace_order_with_retry(
        self,
        old_order_id: str,
        symbol: str,
        side: str,
        amount: float,
        new_price: float,
        client_order_id: Optional[str] = None
    ) -> Optional[Dict[str, Any]]:
        """
        Ersetzt Order mit Retry-Logic.

        Args:
            old_order_id: Zu ersetzende Order ID
            symbol: Trading pair
            side: "buy" oder "sell"
            amount: Neue Order-Menge
            new_price: Neuer Limit-Preis
            client_order_id: Optional Client Order ID

        Returns:
            Neue Order oder None bei Fehler
        """
        with self._lock:
            for attempt in range(self.max_retries):
                try:
                    # Alte Order stornieren (mit "replaced" reason für tracking)
                    cancel_success = self.cancel_order(old_order_id, symbol, reason="replaced")

                    if cancel_success:
                        # Neue Order platzieren
                        new_order = self.place_limit_gtc(
                            symbol=symbol,
                            side=side,
                            amount=amount,
                            price=new_price,
                            client_order_id=client_order_id
                        )

                        if new_order:
                            self._stats['orders_replaced'] += 1

                            logger.info(
                                f"Order replaced: {old_order_id} -> {new_order.get('id')} @ {new_price}",
                                extra={
                                    'event_type': 'ORDER_REPLACED',
                                    'old_order_id': old_order_id,
                                    'new_order_id': new_order.get('id'),
                                    'symbol': symbol,
                                    'new_price': new_price,
                                    'attempt': attempt + 1
                                }
                            )

                            return new_order

                except Exception as e:
                    self._stats['retries_total'] += 1

                    if attempt < self.max_retries - 1:
                        delay = 0.5 * (2 ** attempt)  # Exponential backoff

                        # Phase 2: Structured retry event logging
                        try:
                            from core.event_schemas import RetryAttempt
                            from core.logger_factory import ORDER_LOG, log_event

                            retry_event = RetryAttempt(
                                symbol=symbol,
                                operation="replace_order",
                                attempt=attempt + 1,
                                max_retries=self.max_retries,
                                error_class=type(e).__name__,
                                error_message=str(e),
                                backoff_ms=int(delay * 1000),
                                will_retry=True
                            )

                            log_event(ORDER_LOG(), "retry_attempt", **retry_event.model_dump())
                        except Exception as log_error:
                            logger.debug(f"Failed to log retry_attempt event: {log_error}")

                        logger.warning(
                            f"Order replace attempt {attempt + 1} failed, retrying in {delay}s: {e}",
                            extra={
                                'event_type': 'ORDER_REPLACE_RETRY',
                                'old_order_id': old_order_id,
                                'symbol': symbol,
                                'attempt': attempt + 1,
                                'delay': delay,
                                'error': str(e)
                            }
                        )
                        time.sleep(delay)

            # Nach allen Versuchen fehlgeschlagen
            self._stats['errors_total'] += 1

            # Phase 2: Log final retry failure
            try:
                from core.event_schemas import RetryAttempt
                from core.logger_factory import ORDER_LOG, log_event

                final_retry_event = RetryAttempt(
                    symbol=symbol,
                    operation="replace_order",
                    attempt=self.max_retries,
                    max_retries=self.max_retries,
                    error_class="MaxRetriesExceeded",
                    error_message=f"Failed after {self.max_retries} attempts",
                    backoff_ms=0,
                    will_retry=False
                )

                log_event(ORDER_LOG(), "retry_attempt", **final_retry_event.model_dump())
            except Exception as log_error:
                logger.debug(f"Failed to log final retry_attempt event: {log_error}")

            logger.error(
                f"Failed to replace order {old_order_id} after {self.max_retries} attempts",
                extra={
                    'event_type': 'ORDER_REPLACE_FAILED',
                    'old_order_id': old_order_id,
                    'symbol': symbol,
                    'max_retries': self.max_retries
                }
            )
            return None

    def fetch_order_status(self, order_id: str, symbol: str, use_cache: bool = True) -> Optional[Dict[str, Any]]:
        """
        Holt Order-Status, optional aus Cache.

        Args:
            order_id: Order ID
            symbol: Trading pair
            use_cache: Cache verwenden wenn verfügbar

        Returns:
            Order-Dict oder None
        """
        # Erst Cache prüfen
        if use_cache and self.cache:
            cached_order = self.cache.get_order(order_id)
            if cached_order and not self.cache.is_stale(order_id):
                return cached_order

        # Vom Exchange holen
        try:
            order = self.exchange.fetch_order(order_id, symbol)

            # Cache aktualisieren
            if self.cache:
                self.cache.store_order(order)

            return order

        except Exception as e:
            logger.error(
                f"Failed to fetch order status {order_id}: {e}",
                extra={
                    'event_type': 'ORDER_FETCH_ERROR',
                    'order_id': order_id,
                    'symbol': symbol,
                    'error': str(e)
                }
            )
            return None

    def get_statistics(self) -> Dict[str, Any]:
        """
        Returns Service-Statistiken.

        Returns:
            Statistik-Dict
        """
        with self._lock:
            stats = self._stats.copy()

            if self.cache:
                cache_stats = self.cache.get_statistics()
                stats.update({
                    'cache_hits': cache_stats.get('hits', 0),
                    'cache_misses': cache_stats.get('misses', 0),
                    'cache_size': cache_stats.get('size', 0)
                })

            return stats

    def validate_order_params(
        self,
        symbol: str,
        side: str,
        amount: float,
        price: Optional[float] = None
    ) -> Tuple[bool, Optional[str]]:
        """
        Validiert Order-Parameter gegen Exchange-Limits.

        Args:
            symbol: Trading pair
            side: "buy" oder "sell"
            amount: Order-Menge
            price: Optional Limit-Preis

        Returns:
            (is_valid, error_message)
        """
        try:
            market = self.exchange.get_market_info(symbol)

            if not market:
                return False, f"Market {symbol} not found"

            limits = market.get('limits', {})

            # Amount-Limits prüfen
            amount_limits = limits.get('amount', {})
            min_amount = amount_limits.get('min', 0)
            max_amount = amount_limits.get('max', float('inf'))

            if amount < min_amount:
                return False, f"Amount {amount} below minimum {min_amount}"

            if amount > max_amount:
                return False, f"Amount {amount} above maximum {max_amount}"

            # Price-Limits prüfen (wenn Limit-Order)
            if price is not None:
                price_limits = limits.get('price', {})
                min_price = price_limits.get('min', 0)
                max_price = price_limits.get('max', float('inf'))

                if price < min_price:
                    return False, f"Price {price} below minimum {min_price}"

                if price > max_price:
                    return False, f"Price {price} above maximum {max_price}"

                # Notional-Limits prüfen
                notional = amount * price
                cost_limits = limits.get('cost', {})
                min_notional = cost_limits.get('min', 0)
                max_notional = cost_limits.get('max', float('inf'))

                if notional < min_notional:
                    return False, f"Notional {notional} below minimum {min_notional}"

                if notional > max_notional:
                    return False, f"Notional {notional} above maximum {max_notional}"

            return True, None

        except Exception as e:
            return False, f"Validation error: {e}"

    def precision_adjust(self, symbol: str, amount: float, price: Optional[float] = None) -> Tuple[float, Optional[float]]:
        """
        Passt Amount und Price an Exchange-Precision an.

        Args:
            symbol: Trading pair
            amount: Order-Menge
            price: Optional Limit-Preis

        Returns:
            (adjusted_amount, adjusted_price)
        """
        try:
            adjusted_amount = float(self.exchange.amount_to_precision(symbol, amount))
            adjusted_price = None

            if price is not None:
                adjusted_price = float(self.exchange.price_to_precision(symbol, price))

            return adjusted_amount, adjusted_price

        except Exception as e:
            logger.warning(
                f"Precision adjustment failed for {symbol}: {e}",
                extra={
                    'event_type': 'PRECISION_ADJUSTMENT_ERROR',
                    'symbol': symbol,
                    'amount': amount,
                    'price': price,
                    'error': str(e)
                }
            )
            return amount, price
