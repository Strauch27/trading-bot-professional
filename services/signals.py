"""
Exit Signal Service - Priority-based exit signal processing
Extrahiert aus engine.py für bessere Modularität und Testbarkeit.
"""

import threading
import time
from typing import Dict, List, Optional, Any
import logging

logger = logging.getLogger(__name__)


class ExitSignalQueue:
    """
    Priority-based exit signal processing.

    Verwaltet Exit-Signale mit Prioritäten und thread-sicherer Verarbeitung.
    Höhere Priorität (niedrigere Zahl) wird zuerst verarbeitet.
    """

    SIGNAL_PRIORITIES = {
        'PANIC_SELL': 1,       # Höchste Priorität
        'TIMEOUT': 2,          # TTL-basierte Exits
        'STOP_LOSS': 3,        # Stop-Loss Trigger
        'TRAILING_STOP': 4,    # Trailing Stop Trigger
        'TAKE_PROFIT': 5,      # Take-Profit Trigger
        'MANUAL': 6,           # Manuelle Exits
        'REBALANCE': 7,        # Portfolio-Rebalancing
        'LOW_PRIORITY': 99     # Niedrigste Priorität
    }

    def __init__(self):
        self._queue = []
        self._lock = threading.RLock()
        self._signal_count = 0  # für Statistiken

    def add_signal(self, symbol: str, signal_type: str, data: Dict = None) -> None:
        """
        Adds exit signal with priority.

        Args:
            symbol: Trading pair (z.B. "BTC/USDT")
            signal_type: Signal-Typ aus SIGNAL_PRIORITIES
            data: Zusätzliche Signal-Daten
        """
        with self._lock:
            priority = self.SIGNAL_PRIORITIES.get(signal_type, 99)
            signal = {
                'symbol': symbol,
                'type': signal_type,
                'priority': priority,
                'timestamp': time.time(),
                'data': data or {},
                'id': self._signal_count
            }
            self._signal_count += 1

            self._queue.append(signal)
            # Sort by priority (lower number = higher priority), then by timestamp
            self._queue.sort(key=lambda x: (x['priority'], x['timestamp']))

            logger.debug(
                f"Exit signal added: {signal_type} for {symbol}",
                extra={
                    'event_type': 'EXIT_SIGNAL_ADDED',
                    'symbol': symbol,
                    'signal_type': signal_type,
                    'priority': priority,
                    'queue_size': len(self._queue)
                }
            )

    def get_next_signal(self, symbol: str = None) -> Optional[Dict]:
        """
        Gets next signal, optionally filtered by symbol.

        Args:
            symbol: Optional - nur Signale für dieses Symbol

        Returns:
            Signal-Dict oder None wenn keine vorhanden
        """
        with self._lock:
            if not self._queue:
                return None

            if symbol:
                # Suche erstes Signal für das spezifische Symbol
                for i, signal in enumerate(self._queue):
                    if signal['symbol'] == symbol:
                        retrieved_signal = self._queue.pop(i)
                        logger.debug(
                            f"Exit signal retrieved for {symbol}: {retrieved_signal['type']}",
                            extra={
                                'event_type': 'EXIT_SIGNAL_RETRIEVED',
                                'symbol': symbol,
                                'signal_type': retrieved_signal['type'],
                                'remaining_queue_size': len(self._queue)
                            }
                        )
                        return retrieved_signal
                return None
            else:
                # Nimm Signal mit höchster Priorität
                retrieved_signal = self._queue.pop(0)
                logger.debug(
                    f"Exit signal retrieved: {retrieved_signal['type']} for {retrieved_signal['symbol']}",
                    extra={
                        'event_type': 'EXIT_SIGNAL_RETRIEVED',
                        'symbol': retrieved_signal['symbol'],
                        'signal_type': retrieved_signal['type'],
                        'remaining_queue_size': len(self._queue)
                    }
                )
                return retrieved_signal

    def has_signal(self, symbol: str) -> bool:
        """
        Checks if symbol has pending exit signal.

        Args:
            symbol: Trading pair

        Returns:
            True wenn Signal vorhanden
        """
        with self._lock:
            return any(s['symbol'] == symbol for s in self._queue)

    def get_signal_count(self, symbol: str = None) -> int:
        """
        Returns count of pending signals.

        Args:
            symbol: Optional - nur für dieses Symbol zählen

        Returns:
            Anzahl pending Signale
        """
        with self._lock:
            if symbol:
                return sum(1 for s in self._queue if s['symbol'] == symbol)
            return len(self._queue)

    def get_highest_priority_signal(self, symbol: str) -> Optional[Dict]:
        """
        Returns highest priority signal for symbol (ohne es zu entfernen).

        Args:
            symbol: Trading pair

        Returns:
            Signal-Dict oder None
        """
        with self._lock:
            for signal in self._queue:
                if signal['symbol'] == symbol:
                    return signal
            return None

    def clear_signals(self, symbol: str) -> int:
        """
        Clears all signals for symbol.

        Args:
            symbol: Trading pair

        Returns:
            Anzahl entfernter Signale
        """
        with self._lock:
            initial_count = len(self._queue)
            self._queue = [s for s in self._queue if s['symbol'] != symbol]
            removed_count = initial_count - len(self._queue)

            if removed_count > 0:
                logger.info(
                    f"Cleared {removed_count} exit signals for {symbol}",
                    extra={
                        'event_type': 'EXIT_SIGNALS_CLEARED',
                        'symbol': symbol,
                        'cleared_count': removed_count,
                        'remaining_queue_size': len(self._queue)
                    }
                )

            return removed_count

    def clear_all_signals(self) -> int:
        """
        Clears all signals.

        Returns:
            Anzahl entfernter Signale
        """
        with self._lock:
            count = len(self._queue)
            self._queue.clear()

            if count > 0:
                logger.info(
                    f"Cleared all exit signals ({count} removed)",
                    extra={
                        'event_type': 'ALL_EXIT_SIGNALS_CLEARED',
                        'cleared_count': count
                    }
                )

            return count

    def get_all_signals(self) -> List[Dict]:
        """
        Returns copy of all pending signals (für Debugging/Monitoring).

        Returns:
            Liste aller Signale
        """
        with self._lock:
            return self._queue.copy()

    def get_signals_by_type(self, signal_type: str) -> List[Dict]:
        """
        Returns all signals of specific type.

        Args:
            signal_type: Signal-Typ

        Returns:
            Liste der Signale
        """
        with self._lock:
            return [s for s in self._queue if s['type'] == signal_type]

    def get_signals_by_symbol(self, symbol: str) -> List[Dict]:
        """
        Returns all signals for specific symbol.

        Args:
            symbol: Trading pair

        Returns:
            Liste der Signale
        """
        with self._lock:
            return [s for s in self._queue if s['symbol'] == symbol]

    def peek_next_signal(self, symbol: str = None) -> Optional[Dict]:
        """
        Peek at next signal without removing it.

        Args:
            symbol: Optional - nur für dieses Symbol

        Returns:
            Signal-Dict oder None
        """
        with self._lock:
            if not self._queue:
                return None

            if symbol:
                for signal in self._queue:
                    if signal['symbol'] == symbol:
                        return signal
                return None
            else:
                return self._queue[0]

    def get_statistics(self) -> Dict[str, Any]:
        """
        Returns queue statistics.

        Returns:
            Statistik-Dict
        """
        with self._lock:
            stats = {
                'total_pending': len(self._queue),
                'total_processed': self._signal_count - len(self._queue),
                'by_type': {},
                'by_symbol': {},
                'by_priority': {}
            }

            # Statistiken nach Typ
            for signal in self._queue:
                signal_type = signal['type']
                symbol = signal['symbol']
                priority = signal['priority']

                stats['by_type'][signal_type] = stats['by_type'].get(signal_type, 0) + 1
                stats['by_symbol'][symbol] = stats['by_symbol'].get(symbol, 0) + 1
                stats['by_priority'][priority] = stats['by_priority'].get(priority, 0) + 1

            return stats

    def is_empty(self) -> bool:
        """
        Checks if queue is empty.

        Returns:
            True wenn leer
        """
        with self._lock:
            return len(self._queue) == 0

    def remove_signal_by_id(self, signal_id: int) -> bool:
        """
        Removes specific signal by ID.

        Args:
            signal_id: Signal ID

        Returns:
            True wenn entfernt
        """
        with self._lock:
            for i, signal in enumerate(self._queue):
                if signal['id'] == signal_id:
                    removed_signal = self._queue.pop(i)
                    logger.debug(
                        f"Exit signal removed by ID: {removed_signal['type']} for {removed_signal['symbol']}",
                        extra={
                            'event_type': 'EXIT_SIGNAL_REMOVED_BY_ID',
                            'signal_id': signal_id,
                            'symbol': removed_signal['symbol'],
                            'signal_type': removed_signal['type']
                        }
                    )
                    return True
            return False


class SignalManager:
    """
    High-level manager für Exit Signals mit erweiterten Features.
    """

    def __init__(self):
        self.queue = ExitSignalQueue()
        self._signal_history = []  # für Audit Trail
        self._max_history = 1000
        self._lock = threading.RLock()

    def add_exit_signal(self, symbol: str, signal_type: str, reason: str = None, **kwargs) -> None:
        """
        Fügt Exit-Signal hinzu mit erweiterten Metadaten.

        Args:
            symbol: Trading pair
            signal_type: Signal-Typ
            reason: Grund für Signal
            **kwargs: Zusätzliche Daten
        """
        data = kwargs.copy()
        data['reason'] = reason
        data['added_at'] = time.time()

        # Signal zur Queue hinzufügen
        self.queue.add_signal(symbol, signal_type, data)

        # History für Audit Trail
        with self._lock:
            self._signal_history.append({
                'symbol': symbol,
                'type': signal_type,
                'data': data,
                'timestamp': time.time(),
                'action': 'ADDED'
            })

            # History begrenzen
            if len(self._signal_history) > self._max_history:
                self._signal_history = self._signal_history[-self._max_history:]

    def process_next_signal(self, symbol: str = None) -> Optional[Dict]:
        """
        Verarbeitet nächstes Signal und fügt zur History hinzu.

        Args:
            symbol: Optional - nur für dieses Symbol

        Returns:
            Verarbeitetes Signal oder None
        """
        signal = self.queue.get_next_signal(symbol)

        if signal:
            # Zur History hinzufügen
            with self._lock:
                self._signal_history.append({
                    'symbol': signal['symbol'],
                    'type': signal['type'],
                    'data': signal['data'],
                    'timestamp': time.time(),
                    'action': 'PROCESSED'
                })

                # History begrenzen
                if len(self._signal_history) > self._max_history:
                    self._signal_history = self._signal_history[-self._max_history:]

        return signal

    def get_signal_history(self, symbol: str = None, limit: int = 100) -> List[Dict]:
        """
        Returns Signal-History.

        Args:
            symbol: Optional - nur für dieses Symbol
            limit: Maximale Anzahl

        Returns:
            Liste von History-Einträgen
        """
        with self._lock:
            history = self._signal_history

            if symbol:
                history = [h for h in history if h['symbol'] == symbol]

            return history[-limit:] if limit else history

    def clear_history(self) -> int:
        """
        Löscht Signal-History.

        Returns:
            Anzahl gelöschter Einträge
        """
        with self._lock:
            count = len(self._signal_history)
            self._signal_history.clear()
            return count