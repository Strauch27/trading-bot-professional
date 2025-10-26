"""
Trailing Stop Service - Thread-safe trailing stop management
Extrahiert aus engine.py für bessere Modularität und Testbarkeit.
"""

import logging
import threading
import time
from typing import Optional

logger = logging.getLogger(__name__)


class TrailingStopController:
    """
    Thread-safe controller for trailing stop management.

    Verwaltet einen Trailing Stop für ein Symbol mit Aktivierungsschwelle
    und dynamischer Stop-Anpassung basierend auf dem höchsten Preis.
    """

    def __init__(self, symbol: str, initial_price: float, activation_pct: float, distance_pct: float):
        """
        Initialisiert Trailing Stop Controller.

        Args:
            symbol: Trading pair (z.B. "BTC/USDT")
            initial_price: Startpreis
            activation_pct: Schwelle für Aktivierung (z.B. 1.02 = +2%)
            distance_pct: Stop-Distanz vom höchsten Preis (z.B. 0.95 = -5%)
        """
        self.symbol = symbol
        self.initial_price = initial_price
        self.activation_pct = activation_pct
        self.distance_pct = distance_pct
        self.highest_price = initial_price
        self.activation_price = initial_price * activation_pct
        self.is_activated = False
        self.current_stop = initial_price * (1.0 - (1.0 - distance_pct))  # Initial stop
        self._lock = threading.RLock()
        self._created_at = time.time()

    def update(self, current_price: float) -> Optional[float]:
        """
        Updates trailing stop with current price.

        Args:
            current_price: Aktueller Marktpreis

        Returns:
            Neue Stop-Price wenn geändert, None sonst
        """
        with self._lock:
            if current_price <= 0:
                return None

            # Update highest price
            if current_price > self.highest_price:
                self.highest_price = current_price

            # Check activation
            if not self.is_activated and current_price >= self.activation_price:
                self.is_activated = True

                # Phase 3: Log trailing stop activation
                try:
                    from core.event_schemas import TrailingUpdate
                    from core.logger_factory import DECISION_LOG, log_event
                    from core.trace_context import Trace

                    distance_bps = int((1.0 - self.distance_pct) * 10000)

                    trailing_update = TrailingUpdate(
                        symbol=self.symbol,
                        mode="percent_bps",
                        anchor_old=self.initial_price,
                        anchor_new=self.highest_price,
                        distance_bps=distance_bps,
                        armed=True,
                        hit=False,
                        stop_price=self.current_stop
                    )

                    with Trace():
                        log_event(DECISION_LOG(), "trailing_update", **trailing_update.model_dump())

                except Exception as e:
                    logger.debug(f"Failed to log trailing_update activation for {self.symbol}: {e}")

                logger.info(f"Trailing stop activated for {self.symbol} at {current_price:.6f}")

            # Calculate new stop if activated
            if self.is_activated:
                new_stop = self.highest_price * self.distance_pct
                # Only update if stop moves up (trailing)
                if new_stop > self.current_stop:
                    old_stop = self.current_stop
                    self.current_stop = new_stop

                    # Phase 3: Log trailing stop update
                    try:
                        from core.event_schemas import TrailingUpdate
                        from core.logger_factory import DECISION_LOG, log_event
                        from core.trace_context import Trace

                        distance_bps = int((1.0 - self.distance_pct) * 10000)

                        trailing_update = TrailingUpdate(
                            symbol=self.symbol,
                            mode="percent_bps",
                            anchor_old=old_stop,
                            anchor_new=new_stop,
                            distance_bps=distance_bps,
                            armed=True,
                            hit=False,
                            stop_price=new_stop
                        )

                        with Trace():
                            log_event(DECISION_LOG(), "trailing_update", **trailing_update.model_dump())

                    except Exception as e:
                        logger.debug(f"Failed to log trailing_update for {self.symbol}: {e}")

                    logger.debug(f"Trailing stop updated for {self.symbol}: {old_stop:.6f} -> {new_stop:.6f}")
                    return new_stop

            return None

    def get_current_stop(self) -> float:
        """Returns current stop price"""
        with self._lock:
            return self.current_stop

    def is_stop_triggered(self, current_price: float) -> bool:
        """
        Checks if stop is triggered.

        Args:
            current_price: Aktueller Marktpreis

        Returns:
            True wenn Stop ausgelöst werden sollte
        """
        with self._lock:
            return self.is_activated and current_price <= self.current_stop

    def get_state(self) -> dict:
        """
        Returns current state for monitoring/debugging.

        Returns:
            Dict mit aktuellem Status
        """
        with self._lock:
            age_seconds = time.time() - self._created_at
            return {
                'symbol': self.symbol,
                'initial_price': self.initial_price,
                'highest_price': self.highest_price,
                'current_stop': self.current_stop,
                'activation_price': self.activation_price,
                'is_activated': self.is_activated,
                'activation_pct': self.activation_pct,
                'distance_pct': self.distance_pct,
                'age_seconds': age_seconds
            }

    def force_activate(self) -> None:
        """
        Forciert Aktivierung des Trailing Stops.
        Nützlich für Tests oder manuelle Aktivierung.
        """
        with self._lock:
            if not self.is_activated:
                self.is_activated = True
                logger.info(
                    f"Trailing stop force-activated for {self.symbol}",
                    extra={
                        'event_type': 'TRAILING_STOP_FORCE_ACTIVATED',
                        'symbol': self.symbol,
                        'current_price': self.highest_price
                    }
                )

    def reset(self, new_initial_price: float) -> None:
        """
        Reset trailing stop mit neuem initial price.

        Args:
            new_initial_price: Neuer Startpreis
        """
        with self._lock:
            self.initial_price = new_initial_price
            self.highest_price = new_initial_price
            self.activation_price = new_initial_price * self.activation_pct
            self.current_stop = new_initial_price * (1.0 - (1.0 - self.distance_pct))
            self.is_activated = False
            self._created_at = time.time()

            logger.info(
                f"Trailing stop reset for {self.symbol} at {new_initial_price:.6f}",
                extra={
                    'event_type': 'TRAILING_STOP_RESET',
                    'symbol': self.symbol,
                    'new_initial_price': new_initial_price
                }
            )


class TrailingStopManager:
    """
    Manager für multiple Trailing Stop Controller.
    Thread-safe Verwaltung von Trailing Stops für mehrere Symbole.
    """

    def __init__(self):
        self._controllers = {}
        self._lock = threading.RLock()

    def create_trailing_stop(
        self,
        symbol: str,
        initial_price: float,
        activation_pct: float,
        distance_pct: float
    ) -> TrailingStopController:
        """
        Erstellt neuen Trailing Stop Controller.

        Args:
            symbol: Trading pair
            initial_price: Startpreis
            activation_pct: Aktivierungsschwelle
            distance_pct: Stop-Distanz

        Returns:
            TrailingStopController Instanz
        """
        with self._lock:
            controller = TrailingStopController(symbol, initial_price, activation_pct, distance_pct)
            self._controllers[symbol] = controller
            return controller

    def get_controller(self, symbol: str) -> Optional[TrailingStopController]:
        """
        Holt Trailing Stop Controller für Symbol.

        Args:
            symbol: Trading pair

        Returns:
            TrailingStopController oder None
        """
        with self._lock:
            return self._controllers.get(symbol)

    def remove_trailing_stop(self, symbol: str) -> bool:
        """
        Entfernt Trailing Stop für Symbol.

        Args:
            symbol: Trading pair

        Returns:
            True wenn entfernt, False wenn nicht vorhanden
        """
        with self._lock:
            if symbol in self._controllers:
                del self._controllers[symbol]
                logger.info(
                    f"Trailing stop removed for {symbol}",
                    extra={'event_type': 'TRAILING_STOP_REMOVED', 'symbol': symbol}
                )
                return True
            return False

    def update_all_prices(self, prices: dict) -> dict:
        """
        Aktualisiert alle Trailing Stops mit aktuellen Preisen.

        Args:
            prices: Dict mit symbol -> price mapping

        Returns:
            Dict mit triggered stops: {symbol: stop_price}
        """
        triggered_stops = {}

        with self._lock:
            for symbol, controller in list(self._controllers.items()):
                current_price = prices.get(symbol)
                if current_price and current_price > 0:
                    # Update trailing stop
                    controller.update(current_price)

                    # Check if triggered
                    if controller.is_stop_triggered(current_price):
                        triggered_stops[symbol] = controller.get_current_stop()

        return triggered_stops

    def get_all_states(self) -> dict:
        """
        Returns states aller Trailing Stop Controller.

        Returns:
            Dict mit symbol -> state mapping
        """
        with self._lock:
            return {symbol: controller.get_state() for symbol, controller in self._controllers.items()}

    def has_trailing_stop(self, symbol: str) -> bool:
        """
        Prüft ob Trailing Stop für Symbol existiert.

        Args:
            symbol: Trading pair

        Returns:
            True wenn vorhanden
        """
        with self._lock:
            return symbol in self._controllers

    def get_active_symbols(self) -> list:
        """
        Returns Liste aller Symbole mit aktiven Trailing Stops.

        Returns:
            Liste von Symbolen
        """
        with self._lock:
            return list(self._controllers.keys())

    def clear_all(self) -> int:
        """
        Entfernt alle Trailing Stops.

        Returns:
            Anzahl entfernter Trailing Stops
        """
        with self._lock:
            count = len(self._controllers)
            self._controllers.clear()
            logger.info(
                f"All trailing stops cleared ({count} removed)",
                extra={'event_type': 'TRAILING_STOPS_CLEARED', 'count': count}
            )
            return count
