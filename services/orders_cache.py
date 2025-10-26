"""
Order Cache - Einfacher, thread-sicherer Cache für Order-Daten
Reduziert Exchange-API-Calls und verbessert Performance.
"""

import logging
import threading
import time
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)


class OrderCache:
    """
    Thread-sicherer Cache für Order-Daten mit TTL.

    Cached Order-Informationen um wiederholte API-Calls zu vermeiden.
    Besonders nützlich für Order-Status-Abfragen.
    """

    def __init__(self, default_ttl: float = 30.0, max_size: int = 1000):
        """
        Args:
            default_ttl: Standard TTL in Sekunden
            max_size: Maximale Cache-Größe
        """
        self.default_ttl = default_ttl
        self.max_size = max_size
        self._cache = {}  # order_id -> (order_data, timestamp, ttl)
        self._lock = threading.RLock()

        # Statistiken
        self._stats = {
            'hits': 0,
            'misses': 0,
            'stores': 0,
            'evictions': 0,
            'cleanups': 0
        }

    def store_order(self, order: Dict[str, Any], ttl: Optional[float] = None) -> None:
        """
        Speichert Order im Cache.

        Args:
            order: Order-Dictionary
            ttl: Optional TTL override
        """
        if not order or 'id' not in order:
            return

        order_id = order['id']
        ttl = ttl or self.default_ttl
        now = time.time()

        with self._lock:
            # Cache-Größe überwachen
            if len(self._cache) >= self.max_size:
                self._evict_oldest()

            self._cache[order_id] = (order.copy(), now, ttl)
            self._stats['stores'] += 1

            logger.debug(
                f"Order cached: {order_id}",
                extra={
                    'event_type': 'ORDER_CACHED',
                    'order_id': order_id,
                    'ttl': ttl,
                    'cache_size': len(self._cache)
                }
            )

    def get_order(self, order_id: str) -> Optional[Dict[str, Any]]:
        """
        Holt Order aus Cache.

        Args:
            order_id: Order ID

        Returns:
            Order-Dict oder None wenn nicht gefunden/abgelaufen
        """
        with self._lock:
            if order_id not in self._cache:
                self._stats['misses'] += 1
                return None

            order_data, timestamp, ttl = self._cache[order_id]
            now = time.time()

            # TTL prüfen
            if now - timestamp > ttl:
                del self._cache[order_id]
                self._stats['misses'] += 1
                logger.debug(
                    f"Order cache entry expired: {order_id}",
                    extra={
                        'event_type': 'ORDER_CACHE_EXPIRED',
                        'order_id': order_id,
                        'age': now - timestamp,
                        'ttl': ttl
                    }
                )
                return None

            self._stats['hits'] += 1
            return order_data.copy()

    def update_order_status(self, order_id: str, status: str) -> bool:
        """
        Aktualisiert Order-Status im Cache.

        Args:
            order_id: Order ID
            status: Neuer Status

        Returns:
            True wenn aktualisiert
        """
        with self._lock:
            if order_id not in self._cache:
                return False

            order_data, timestamp, ttl = self._cache[order_id]
            order_data['status'] = status

            # Timestamp aktualisieren für finale Status
            if status in ('filled', 'canceled', 'expired', 'rejected'):
                timestamp = time.time()

            self._cache[order_id] = (order_data, timestamp, ttl)

            logger.debug(
                f"Order status updated in cache: {order_id} -> {status}",
                extra={
                    'event_type': 'ORDER_CACHE_STATUS_UPDATED',
                    'order_id': order_id,
                    'status': status
                }
            )

            return True

    def is_stale(self, order_id: str, max_age: Optional[float] = None) -> bool:
        """
        Prüft ob Cache-Eintrag veraltet ist.

        Args:
            order_id: Order ID
            max_age: Optional maximales Alter (sonst TTL)

        Returns:
            True wenn veraltet
        """
        with self._lock:
            if order_id not in self._cache:
                return True

            order_data, timestamp, ttl = self._cache[order_id]
            now = time.time()
            age_limit = max_age or ttl

            return (now - timestamp) > age_limit

    def remove_order(self, order_id: str) -> bool:
        """
        Entfernt Order aus Cache.

        Args:
            order_id: Order ID

        Returns:
            True wenn entfernt
        """
        with self._lock:
            if order_id in self._cache:
                del self._cache[order_id]
                logger.debug(
                    f"Order removed from cache: {order_id}",
                    extra={
                        'event_type': 'ORDER_CACHE_REMOVED',
                        'order_id': order_id
                    }
                )
                return True
            return False

    def cleanup_expired(self) -> int:
        """
        Entfernt abgelaufene Cache-Einträge.

        Returns:
            Anzahl entfernter Einträge
        """
        with self._lock:
            now = time.time()
            expired_orders = []

            for order_id, (order_data, timestamp, ttl) in self._cache.items():
                if now - timestamp > ttl:
                    expired_orders.append(order_id)

            for order_id in expired_orders:
                del self._cache[order_id]

            if expired_orders:
                self._stats['cleanups'] += 1
                logger.debug(
                    f"Cache cleanup: {len(expired_orders)} expired orders removed",
                    extra={
                        'event_type': 'ORDER_CACHE_CLEANUP',
                        'expired_count': len(expired_orders),
                        'remaining_size': len(self._cache)
                    }
                )

            return len(expired_orders)

    def _evict_oldest(self) -> None:
        """Entfernt ältesten Cache-Eintrag."""
        if not self._cache:
            return

        oldest_order_id = min(
            self._cache.keys(),
            key=lambda oid: self._cache[oid][1]  # timestamp
        )

        del self._cache[oldest_order_id]
        self._stats['evictions'] += 1

        logger.debug(
            f"Cache eviction: {oldest_order_id}",
            extra={
                'event_type': 'ORDER_CACHE_EVICTED',
                'order_id': oldest_order_id,
                'cache_size': len(self._cache)
            }
        )

    def get_statistics(self) -> Dict[str, Any]:
        """
        Returns Cache-Statistiken.

        Returns:
            Statistik-Dict
        """
        with self._lock:
            total_requests = self._stats['hits'] + self._stats['misses']
            hit_rate = (self._stats['hits'] / total_requests) if total_requests > 0 else 0.0

            return {
                **self._stats.copy(),
                'size': len(self._cache),
                'hit_rate': hit_rate,
                'max_size': self.max_size,
                'default_ttl': self.default_ttl
            }

    def get_all_orders(self) -> Dict[str, Dict[str, Any]]:
        """
        Returns alle gecachten Orders (für Debugging).

        Returns:
            Dict mit order_id -> order_data mapping
        """
        with self._lock:
            return {
                order_id: order_data.copy()
                for order_id, (order_data, timestamp, ttl) in self._cache.items()
            }

    def get_orders_by_symbol(self, symbol: str) -> Dict[str, Dict[str, Any]]:
        """
        Returns alle Orders für ein Symbol.

        Args:
            symbol: Trading pair

        Returns:
            Dict mit order_id -> order_data mapping
        """
        with self._lock:
            return {
                order_id: order_data.copy()
                for order_id, (order_data, timestamp, ttl) in self._cache.items()
                if order_data.get('symbol') == symbol
            }

    def get_orders_by_status(self, status: str) -> Dict[str, Dict[str, Any]]:
        """
        Returns alle Orders mit bestimmtem Status.

        Args:
            status: Order-Status

        Returns:
            Dict mit order_id -> order_data mapping
        """
        with self._lock:
            return {
                order_id: order_data.copy()
                for order_id, (order_data, timestamp, ttl) in self._cache.items()
                if order_data.get('status') == status
            }

    def clear_cache(self) -> int:
        """
        Leert kompletten Cache.

        Returns:
            Anzahl entfernter Einträge
        """
        with self._lock:
            count = len(self._cache)
            self._cache.clear()

            logger.info(
                f"Order cache cleared: {count} entries removed",
                extra={
                    'event_type': 'ORDER_CACHE_CLEARED',
                    'cleared_count': count
                }
            )

            return count

    def set_ttl(self, order_id: str, new_ttl: float) -> bool:
        """
        Setzt neue TTL für Cache-Eintrag.

        Args:
            order_id: Order ID
            new_ttl: Neue TTL in Sekunden

        Returns:
            True wenn erfolgreich
        """
        with self._lock:
            if order_id not in self._cache:
                return False

            order_data, timestamp, old_ttl = self._cache[order_id]
            self._cache[order_id] = (order_data, timestamp, new_ttl)

            logger.debug(
                f"Cache TTL updated: {order_id} {old_ttl}s -> {new_ttl}s",
                extra={
                    'event_type': 'ORDER_CACHE_TTL_UPDATED',
                    'order_id': order_id,
                    'old_ttl': old_ttl,
                    'new_ttl': new_ttl
                }
            )

            return True

    def get_cache_info(self, order_id: str) -> Optional[Dict[str, Any]]:
        """
        Returns Cache-Metadaten für Order.

        Args:
            order_id: Order ID

        Returns:
            Cache-Info-Dict oder None
        """
        with self._lock:
            if order_id not in self._cache:
                return None

            order_data, timestamp, ttl = self._cache[order_id]
            now = time.time()
            age = now - timestamp

            return {
                'order_id': order_id,
                'cached_at': timestamp,
                'age_seconds': age,
                'ttl_seconds': ttl,
                'expires_in': max(0, ttl - age),
                'is_expired': age > ttl
            }
