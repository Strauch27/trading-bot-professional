# risk_guards.py
# V10-Style ATR & Trailing-Stop Guards für deterministische, verlässliche Exits

import time
from dataclasses import dataclass
from enum import Enum
from typing import Any, Dict, Optional, Tuple

import numpy as np


class ExitReason(Enum):
    """Exit-Gründe für klare Telemetrie"""
    ATR_STOP_HIT = "EXIT_ATR_STOP_HIT"
    TRAILING_STOP_HIT = "EXIT_TRAILING_HIT"
    PROFIT_TARGET_HIT = "EXIT_PROFIT_TARGET"
    TIME_STOP_HIT = "EXIT_TIME_STOP"
    MANUAL_EXIT = "EXIT_MANUAL"

@dataclass
class StopSignals:
    """Stop-Signal mit Details für Audit"""
    triggered: bool = False
    reason: Optional[ExitReason] = None
    stop_price: float = 0.0
    current_price: float = 0.0
    distance_pct: float = 0.0
    details: Dict = None

    def __post_init__(self):
        if self.details is None:
            self.details = {}

class ATRCalculator:
    """
    Average True Range Calculator für volatilitäts-basierte Stops.
    Implementierung basierend auf v10 Logik mit Performance-Optimierungen.
    """

    def __init__(self, period: int = 14):
        self.period = period
        self.data_buffer = {}  # symbol -> deque of (high, low, close)

    def update_data(self, symbol: str, high: float, low: float, close: float, timestamp: float = None):
        """
        Fügt neue OHLC-Daten hinzu.

        Args:
            symbol: Trading symbol
            high: High price
            low: Low price
            close: Close price
            timestamp: Optional timestamp
        """
        if symbol not in self.data_buffer:
            self.data_buffer[symbol] = []

        data_point = {
            'high': float(high),
            'low': float(low),
            'close': float(close),
            'timestamp': timestamp or time.time()
        }

        self.data_buffer[symbol].append(data_point)

        # Keep only necessary data points (period + 1 for previous close)
        if len(self.data_buffer[symbol]) > self.period + 10:
            self.data_buffer[symbol] = self.data_buffer[symbol][-(self.period + 5):]

    def compute_atr(self, symbol: str) -> Optional[float]:
        """
        Berechnet ATR für Symbol basierend auf verfügbaren Daten.

        Args:
            symbol: Trading symbol

        Returns:
            ATR value oder None falls nicht genug Daten
        """
        if symbol not in self.data_buffer:
            return None

        data = self.data_buffer[symbol]
        if len(data) < self.period + 1:  # Need period + 1 for previous close
            return None

        # Convert to arrays for efficient calculation
        high_arr = np.array([d['high'] for d in data])
        low_arr = np.array([d['low'] for d in data])
        close_arr = np.array([d['close'] for d in data])

        # Calculate True Range
        hl = high_arr[1:] - low_arr[1:]  # High - Low
        hc = np.abs(high_arr[1:] - close_arr[:-1])  # |High - Previous Close|
        lc = np.abs(low_arr[1:] - close_arr[:-1])   # |Low - Previous Close|

        # True Range = max(HL, HC, LC)
        tr = np.maximum(hl, np.maximum(hc, lc))

        # ATR = Simple Moving Average of True Range over period
        if len(tr) >= self.period:
            atr = np.mean(tr[-self.period:])
            return float(atr)

        return None

    def compute_atr_bands(self, symbol: str, current_price: float,
                         multiplier: float = 2.0) -> Tuple[Optional[float], Optional[float]]:
        """
        Berechnet ATR-basierte Support/Resistance Bänder.

        Args:
            symbol: Trading symbol
            current_price: Aktueller Preis
            multiplier: ATR-Multiplikator

        Returns:
            (lower_band, upper_band) oder (None, None)
        """
        atr = self.compute_atr(symbol)
        if atr is None:
            return None, None

        lower_band = current_price - (multiplier * atr)
        upper_band = current_price + (multiplier * atr)

        return lower_band, upper_band

class RiskGuardManager:
    """
    Zentraler Risk Guard Manager für alle Stop-Logik.
    Kombiniert ATR, Trailing, Profit Targets und Time-based Exits.
    """

    def __init__(self):
        self.atr_calculator = ATRCalculator()
        self.position_peaks = {}  # symbol -> peak_price
        self.position_entries = {}  # symbol -> (entry_price, entry_time)

    def update_market_data(self, symbol: str, high: float, low: float, close: float):
        """Update market data for ATR calculation"""
        self.atr_calculator.update_data(symbol, high, low, close)

    def register_position_entry(self, symbol: str, entry_price: float, entry_time: float = None):
        """
        Registriert Position Entry für Tracking.

        Args:
            symbol: Trading symbol
            entry_price: Entry-Preis
            entry_time: Entry-Zeit (default: now)
        """
        if entry_time is None:
            entry_time = time.time()

        self.position_entries[symbol] = (float(entry_price), float(entry_time))
        self.position_peaks[symbol] = float(entry_price)  # Initial peak = entry

    def update_position_peak(self, symbol: str, current_price: float, side: str = "LONG"):
        """
        Aktualisiert Peak-Preis für Position.

        Args:
            symbol: Trading symbol
            current_price: Aktueller Preis
            side: "LONG" oder "SHORT"
        """
        current_price = float(current_price)

        if symbol not in self.position_peaks:
            self.position_peaks[symbol] = current_price
            return

        if side.upper() == "LONG":
            self.position_peaks[symbol] = max(self.position_peaks[symbol], current_price)
        else:  # SHORT
            if self.position_peaks[symbol] == 0:
                self.position_peaks[symbol] = current_price
            else:
                self.position_peaks[symbol] = min(self.position_peaks[symbol], current_price)

    def check_atr_stop(self, symbol: str, current_price: float, side: str = "LONG",
                      multiplier: float = 2.0, min_distance_pct: float = 0.005) -> StopSignals:
        """
        Prüft ATR-basierten Stop.

        Args:
            symbol: Trading symbol
            current_price: Aktueller Preis
            side: Position side ("LONG" or "SHORT")
            multiplier: ATR-Multiplikator
            min_distance_pct: Minimum distance as percentage of price

        Returns:
            StopSignals mit Details
        """
        if symbol not in self.position_entries:
            return StopSignals()

        entry_price, entry_time = self.position_entries[symbol]
        atr = self.atr_calculator.compute_atr(symbol)

        if atr is None:
            # Fallback to percentage-based stop if no ATR available
            min_stop_distance = current_price * min_distance_pct
            if side.upper() == "LONG":
                stop_price = entry_price - min_stop_distance
                triggered = current_price <= stop_price
            else:  # SHORT
                stop_price = entry_price + min_stop_distance
                triggered = current_price >= stop_price

            return StopSignals(
                triggered=triggered,
                reason=ExitReason.ATR_STOP_HIT if triggered else None,
                stop_price=stop_price,
                current_price=current_price,
                distance_pct=abs(current_price - stop_price) / current_price * 100,
                details={
                    "atr_available": False,
                    "fallback_method": "percentage",
                    "entry_price": entry_price,
                    "min_distance_pct": min_distance_pct
                }
            )

        # ATR-based stop
        atr_distance = multiplier * atr
        min_distance = current_price * min_distance_pct
        stop_distance = max(atr_distance, min_distance)

        if side.upper() == "LONG":
            stop_price = entry_price - stop_distance
            triggered = current_price <= stop_price
        else:  # SHORT
            stop_price = entry_price + stop_distance
            triggered = current_price >= stop_price

        return StopSignals(
            triggered=triggered,
            reason=ExitReason.ATR_STOP_HIT if triggered else None,
            stop_price=stop_price,
            current_price=current_price,
            distance_pct=abs(current_price - stop_price) / current_price * 100,
            details={
                "atr": atr,
                "atr_distance": atr_distance,
                "multiplier": multiplier,
                "entry_price": entry_price,
                "min_distance_pct": min_distance_pct,
                "final_distance": stop_distance
            }
        )

    def check_trailing_stop(self, symbol: str, current_price: float, side: str = "LONG",
                          trail_pct: float = 0.01, min_profit_pct: float = 0.005) -> StopSignals:
        """
        Prüft Trailing Stop basierend auf Peak-Preis.

        Args:
            symbol: Trading symbol
            current_price: Aktueller Preis
            side: Position side
            trail_pct: Trailing percentage (default 1%)
            min_profit_pct: Minimum profit before trailing starts (default 0.5%)

        Returns:
            StopSignals mit Details
        """
        if symbol not in self.position_entries or symbol not in self.position_peaks:
            return StopSignals()

        entry_price, entry_time = self.position_entries[symbol]
        peak_price = self.position_peaks[symbol]
        current_price = float(current_price)

        # Update peak
        self.update_position_peak(symbol, current_price, side)
        peak_price = self.position_peaks[symbol]

        if side.upper() == "LONG":
            # Check if we have minimum profit before starting trail
            profit_pct = (peak_price - entry_price) / entry_price
            if profit_pct < min_profit_pct:
                return StopSignals(
                    triggered=False,
                    details={
                        "waiting_for_min_profit": True,
                        "current_profit_pct": profit_pct * 100,
                        "min_profit_pct": min_profit_pct * 100,
                        "peak_price": peak_price,
                        "entry_price": entry_price
                    }
                )

            # Trailing stop price: peak - (trail_pct * peak)
            # But never below entry + min_profit
            min_stop_price = entry_price * (1 + min_profit_pct)
            trailing_stop_price = max(min_stop_price, peak_price * (1 - trail_pct))

            triggered = current_price <= trailing_stop_price

        else:  # SHORT
            profit_pct = (entry_price - peak_price) / entry_price
            if profit_pct < min_profit_pct:
                return StopSignals(
                    triggered=False,
                    details={
                        "waiting_for_min_profit": True,
                        "current_profit_pct": profit_pct * 100,
                        "min_profit_pct": min_profit_pct * 100,
                        "peak_price": peak_price,
                        "entry_price": entry_price
                    }
                )

            # Trailing stop for short: peak + (trail_pct * peak)
            # But never above entry - min_profit
            max_stop_price = entry_price * (1 - min_profit_pct)
            trailing_stop_price = min(max_stop_price, peak_price * (1 + trail_pct))

            triggered = current_price >= trailing_stop_price

        distance_pct = abs(current_price - trailing_stop_price) / current_price * 100

        return StopSignals(
            triggered=triggered,
            reason=ExitReason.TRAILING_STOP_HIT if triggered else None,
            stop_price=trailing_stop_price,
            current_price=current_price,
            distance_pct=distance_pct,
            details={
                "entry_price": entry_price,
                "peak_price": peak_price,
                "trail_pct": trail_pct * 100,
                "profit_pct": profit_pct * 100,
                "side": side
            }
        )

    def check_profit_target(self, symbol: str, current_price: float, side: str = "LONG",
                          target_pct: float = 0.05) -> StopSignals:
        """
        Prüft Profit Target.

        Args:
            symbol: Trading symbol
            current_price: Aktueller Preis
            side: Position side
            target_pct: Profit target percentage (default 5%)

        Returns:
            StopSignals mit Details
        """
        if symbol not in self.position_entries:
            return StopSignals()

        entry_price, entry_time = self.position_entries[symbol]
        current_price = float(current_price)

        if side.upper() == "LONG":
            target_price = entry_price * (1 + target_pct)
            triggered = current_price >= target_price
            profit_pct = (current_price - entry_price) / entry_price
        else:  # SHORT
            target_price = entry_price * (1 - target_pct)
            triggered = current_price <= target_price
            profit_pct = (entry_price - current_price) / entry_price

        return StopSignals(
            triggered=triggered,
            reason=ExitReason.PROFIT_TARGET_HIT if triggered else None,
            stop_price=target_price,
            current_price=current_price,
            distance_pct=abs(current_price - target_price) / current_price * 100,
            details={
                "entry_price": entry_price,
                "target_price": target_price,
                "target_pct": target_pct * 100,
                "current_profit_pct": profit_pct * 100,
                "side": side
            }
        )

    def check_time_stop(self, symbol: str, max_hold_hours: float = 24.0) -> StopSignals:
        """
        Prüft Time-based Stop.

        Args:
            symbol: Trading symbol
            max_hold_hours: Maximum hold time in hours

        Returns:
            StopSignals mit Details
        """
        if symbol not in self.position_entries:
            return StopSignals()

        entry_price, entry_time = self.position_entries[symbol]
        current_time = time.time()
        hold_time_hours = (current_time - entry_time) / 3600

        triggered = hold_time_hours >= max_hold_hours

        return StopSignals(
            triggered=triggered,
            reason=ExitReason.TIME_STOP_HIT if triggered else None,
            stop_price=0.0,  # No specific price for time stop
            current_price=0.0,
            distance_pct=0.0,
            details={
                "entry_time": entry_time,
                "current_time": current_time,
                "hold_time_hours": hold_time_hours,
                "max_hold_hours": max_hold_hours
            }
        )

    def evaluate_exit_signals(self, symbol: str, current_price: float, side: str = "LONG",
                            config: Dict = None) -> Tuple[bool, Optional[StopSignals]]:
        """
        Evaluiert alle Exit-Signale in Prioritätsreihenfolge.

        Args:
            symbol: Trading symbol
            current_price: Aktueller Preis
            side: Position side
            config: Configuration dict

        Returns:
            (should_exit, first_triggered_signal)
        """
        if config is None:
            config = {}

        # Default config
        default_config = {
            "atr_multiplier": 2.0,
            "atr_min_distance_pct": 0.005,
            "trailing_pct": 0.01,
            "trailing_min_profit_pct": 0.005,
            "profit_target_pct": 0.05,
            "max_hold_hours": 24.0,
            "enable_atr_stop": True,
            "enable_trailing_stop": True,
            "enable_profit_target": False,
            "enable_time_stop": False
        }

        cfg = {**default_config, **config}

        # Check stops in priority order

        if cfg["enable_atr_stop"]:
            atr_signal = self.check_atr_stop(
                symbol, current_price, side,
                cfg["atr_multiplier"], cfg["atr_min_distance_pct"]
            )
            if atr_signal.triggered:
                return True, atr_signal

        if cfg["enable_trailing_stop"]:
            trailing_signal = self.check_trailing_stop(
                symbol, current_price, side,
                cfg["trailing_pct"], cfg["trailing_min_profit_pct"]
            )
            if trailing_signal.triggered:
                return True, trailing_signal

        if cfg["enable_profit_target"]:
            profit_signal = self.check_profit_target(
                symbol, current_price, side, cfg["profit_target_pct"]
            )
            if profit_signal.triggered:
                return True, profit_signal

        if cfg["enable_time_stop"]:
            time_signal = self.check_time_stop(symbol, cfg["max_hold_hours"])
            if time_signal.triggered:
                return True, time_signal

        return False, None

    def close_position_tracking(self, symbol: str):
        """
        Beendet Position-Tracking für Symbol.

        Args:
            symbol: Trading symbol
        """
        self.position_entries.pop(symbol, None)
        self.position_peaks.pop(symbol, None)

    def get_position_stats(self, symbol: str, current_price: float = None) -> Dict:
        """
        Gibt Position-Statistiken zurück.

        Args:
            symbol: Trading symbol
            current_price: Aktueller Preis (optional)

        Returns:
            Dict mit Position-Stats
        """
        if symbol not in self.position_entries:
            return {"active": False}

        entry_price, entry_time = self.position_entries[symbol]
        peak_price = self.position_peaks.get(symbol, entry_price)
        current_time = time.time()

        stats = {
            "active": True,
            "entry_price": entry_price,
            "entry_time": entry_time,
            "peak_price": peak_price,
            "hold_time_hours": (current_time - entry_time) / 3600,
        }

        if current_price is not None:
            current_price = float(current_price)
            stats.update({
                "current_price": current_price,
                "unrealized_pct": (current_price - entry_price) / entry_price * 100,
                "peak_drawdown_pct": (current_price - peak_price) / peak_price * 100
            })

        return stats

# Global Risk Guard Manager Instance
_risk_guard_manager = None

def get_risk_guard_manager() -> RiskGuardManager:
    """Singleton Pattern für globalen Risk Guard Manager"""
    global _risk_guard_manager
    if _risk_guard_manager is None:
        _risk_guard_manager = RiskGuardManager()
    return _risk_guard_manager

# Convenience Functions
def register_position(symbol: str, entry_price: float, entry_time: float = None):
    """Register new position for tracking"""
    manager = get_risk_guard_manager()
    manager.register_position_entry(symbol, entry_price, entry_time)

def update_market_data(symbol: str, high: float, low: float, close: float):
    """Update market data for ATR calculation"""
    manager = get_risk_guard_manager()
    manager.update_market_data(symbol, high, low, close)

def check_exit_signals(symbol: str, current_price: float, side: str = "LONG", config: Dict = None) -> Tuple[bool, Optional[StopSignals]]:
    """Check all exit signals for position"""
    manager = get_risk_guard_manager()
    return manager.evaluate_exit_signals(symbol, current_price, side, config)

def close_position(symbol: str):
    """Close position tracking"""
    manager = get_risk_guard_manager()
    manager.close_position_tracking(symbol)


# Phase 10: Consolidated Exit Evaluation
def evaluate_all_exits(
    symbol: str,
    current_price: float,
    side: str = "LONG",
    config: Dict = None
) -> Tuple[bool, Optional[StopSignals], Dict[str, Any]]:
    """
    Phase 10: Unified exit evaluation combining all exit types.

    Consolidates:
    - ATR stops
    - Trailing stops
    - Profit targets
    - Time-based exits

    Args:
        symbol: Trading symbol
        current_price: Current market price
        side: Position side ("LONG" or "SHORT")
        config: Exit configuration dict

    Returns:
        (should_exit, exit_signal, evaluation_context)
    """
    manager = get_risk_guard_manager()

    # Check if position is tracked
    stats = manager.get_position_stats(symbol, current_price)
    if not stats.get("active"):
        return False, None, {"error": "position_not_tracked"}

    # Evaluate all exit signals
    should_exit, exit_signal = manager.evaluate_exit_signals(
        symbol=symbol,
        current_price=current_price,
        side=side,
        config=config
    )

    # Build evaluation context
    eval_ctx = {
        "symbol": symbol,
        "current_price": current_price,
        "position_stats": stats,
        "exit_triggered": should_exit,
        "exit_reason": exit_signal.reason.value if exit_signal and exit_signal.reason else None,
        "exit_details": exit_signal.details if exit_signal else {}
    }

    return should_exit, exit_signal, eval_ctx


# Simplified Guards (as specified in requirements)
def atr_stop(entry: float, last: float, atr: float, k: float = 2.0):
    """Simplified ATR stop check - direct from feedback"""
    stop = entry - k*atr
    return last <= stop, {"reason":"EXIT_ATR_STOP_HIT","stop_price":stop}

def atr_stop_hit(position, atr, mult):
    """
    Prüft ob ATR-basierter Stop-Loss getroffen wurde.

    Args:
        position: Position-Objekt mit entry_price und last_price
        atr: Average True Range
        mult: ATR-Multiplier

    Returns:
        Tuple von (hit: bool, info: dict)
    """
    if not position or not hasattr(position, 'entry_price') or not hasattr(position, 'last_price'):
        return False, {"reason": "invalid_position_data"}

    if atr <= 0:
        return False, {"reason": "invalid_atr"}

    stop = position.entry_price - atr * mult
    hit = position.last_price <= stop

    return hit, {
        "reason": "EXIT_ATR_STOP_HIT" if hit else "atr_stop_ok",
        "stop": stop,
        "atr": atr,
        "multiplier": mult,
        "entry_price": position.entry_price,
        "current_price": position.last_price
    }


def trailing_stop(entry: float, peak: float, last: float, pct: float = 0.01):
    """Simplified trailing stop check - direct from feedback"""
    stop = max(entry*(1-pct), peak*(1-pct))
    return last <= stop, {"reason":"EXIT_TRAILING_HIT","stop_price":stop}

def trailing_stop_hit(state):
    """
    Prüft ob Trailing-Stop-Loss getroffen wurde.

    Args:
        state: Trailing-Stop-State mit activation, distance, high_since_entry, etc.

    Returns:
        Tuple von (hit: bool, info: dict)
    """
    required_attrs = ['last_price', 'entry_price', 'activation_pct', 'distance_pct', 'high_since_entry']
    if not all(hasattr(state, attr) for attr in required_attrs):
        return False, {"reason": "invalid_state_data"}

    # Trailing Stop nur aktiv wenn Aktivierungs-Level erreicht
    activation_price = state.entry_price * state.activation_pct
    if state.last_price < activation_price:
        return False, {
            "reason": "trailing_not_activated",
            "activation_price": activation_price,
            "current_price": state.last_price
        }

    # Berechne dynamischen Stop basierend auf High-since-Entry
    dyn_stop = state.high_since_entry * state.distance_pct
    hit = state.last_price <= dyn_stop

    return hit, {
        "reason": "EXIT_TRAILING_HIT" if hit else "trailing_ok",
        "stop": dyn_stop,
        "high_since_entry": state.high_since_entry,
        "distance_pct": state.distance_pct,
        "current_price": state.last_price
    }
