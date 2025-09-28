#!/usr/bin/env python3
"""
MarketGuards Service - Market Filter Implementation

Implements all market guard filters that determine if a buy signal should be allowed:
- BTC Trend Filter: Block buys when Bitcoin falls below threshold
- Falling Coins Filter: Block buys when too many coins are declining
- SMA Guard: Ensure price is above Simple Moving Average
- Volume Guard: Check volume requirements
- Spread Guard: Verify bid-ask spread is reasonable
- Volatility Sigma Guard: Check price volatility requirements

Thread-safe implementation with comprehensive logging.
"""

import logging
import threading
from collections import deque
from datetime import datetime, timedelta
from typing import Dict, Optional, List, Any, Tuple
import numpy as np

logger = logging.getLogger(__name__)


class MarketGuards:
    """
    Service for evaluating market guard conditions before buy signals.

    Provides comprehensive market filtering with configurable thresholds
    and detailed rejection logging.
    """

    def __init__(self,
                 # BTC Filter
                 use_btc_filter: bool = True,
                 btc_change_threshold: float = 0.995,  # -0.5% threshold

                 # Falling Coins Filter
                 use_falling_coins_filter: bool = False,
                 falling_coins_threshold: float = 0.55,  # Max 55% falling

                 # SMA Guard
                 use_sma_guard: bool = False,
                 sma_guard_window: int = 20,
                 sma_guard_min_ratio: float = 0.98,

                 # Volume Guard
                 use_volume_guard: bool = False,
                 volume_guard_window: int = 10,
                 volume_guard_factor: float = 1.5,

                 # Spread Guard
                 use_spread_guard: bool = False,
                 guard_max_spread_bps: int = 50,  # 0.5% max spread

                 # Volatility Sigma Guard
                 use_vol_sigma_guard: bool = False,
                 vol_sigma_window: int = 30,
                 require_vol_sigma_bps_min: int = 100,
                 verbose: bool = False):  # Min 1% volatility
        """
        Initialize MarketGuards service.
        """
        # Configuration
        self.use_btc_filter = use_btc_filter
        self.btc_change_threshold = btc_change_threshold
        self.use_falling_coins_filter = use_falling_coins_filter
        self.falling_coins_threshold = falling_coins_threshold
        self.use_sma_guard = use_sma_guard
        self.sma_guard_window = sma_guard_window
        self.sma_guard_min_ratio = sma_guard_min_ratio
        self.use_volume_guard = use_volume_guard
        self.volume_guard_window = volume_guard_window
        self.volume_guard_factor = volume_guard_factor
        self.use_spread_guard = use_spread_guard
        self.guard_max_spread_bps = guard_max_spread_bps
        self.use_vol_sigma_guard = use_vol_sigma_guard
        self.vol_sigma_window = vol_sigma_window
        self.require_vol_sigma_bps_min = require_vol_sigma_bps_min
        self._verbose = verbose

        # Thread safety
        self._lock = threading.RLock()

        # Market data storage
        self._price_history: Dict[str, deque] = {}  # symbol -> [(timestamp, price), ...]
        self._volume_history: Dict[str, deque] = {}  # symbol -> [(timestamp, volume), ...]
        self._orderbook_data: Dict[str, Tuple[float, float, datetime]] = {}  # symbol -> (bid, ask, timestamp)

        # Market conditions cache
        self._market_conditions: Dict[str, Any] = {}
        self._market_conditions_timestamp: Optional[datetime] = None

        logger.info("MarketGuards service initialized with filters: "
                   f"BTC={use_btc_filter}, Falling={use_falling_coins_filter}, "
                   f"SMA={use_sma_guard}, Volume={use_volume_guard}, "
                   f"Spread={use_spread_guard}, VolSigma={use_vol_sigma_guard}")

    # -------- Formatting helpers (keine API-Änderung) --------
    @staticmethod
    def _fmt_pct(x):
        return "n/a" if x is None else f"{x*100:.2f}%"
    @staticmethod
    def _fmt_bps(x):
        return "n/a" if x is None else f"{int(x)}bps"
    @staticmethod
    def _fmt4(x):
        return "n/a" if x is None else f"{x:.4f}"

    def _build_summary(self, symbol: str, price: float, details: dict, failed: list) -> str:
        parts = [f"{symbol} @ {price:.6f}"]
        g = details.get("guards", {})
        # BTC
        if "btc_filter" in g:
            cur = self._fmt4(g["btc_filter"].get("current_value"))
            thr = self._fmt4(g["btc_filter"].get("threshold"))
            mark = "✓" if g["btc_filter"].get("passes") else "✗"
            parts.append(f"btc {mark} {cur}≥{thr}")
        # Falling coins
        if "falling_coins_filter" in g:
            cur = self._fmt_pct(g["falling_coins_filter"].get("current_value"))
            thr = self._fmt_pct(g["falling_coins_filter"].get("threshold"))
            mark = "✓" if g["falling_coins_filter"].get("passes") else "✗"
            parts.append(f"falling {mark} {cur}≤{thr}")
        # SMA
        if "sma_guard" in g:
            cur = g["sma_guard"].get("current_price")
            minp = g["sma_guard"].get("min_price")
            mark = "✓" if g["sma_guard"].get("passes") else "✗"
            parts.append(f"sma {mark} {cur:.6f}≥{minp:.6f}")
        # Volume
        if "volume_guard" in g:
            cur = g["volume_guard"].get("current_volume")
            minv = g["volume_guard"].get("min_volume")
            mark = "✓" if g["volume_guard"].get("passes") else "✗"
            parts.append(f"vol {mark} {cur:.0f}≥{minv:.0f}")
        # Spread
        if "spread_guard" in g:
            cur = g["spread_guard"].get("spread_pct")
            thr = g["spread_guard"].get("max_spread_pct")
            mark = "✓" if g["spread_guard"].get("passes") else "✗"
            parts.append(f"spread {mark} {cur:.2f}%≤{thr:.2f}%")
        # Vol sigma
        if "vol_sigma_guard" in g:
            cur = g["vol_sigma_guard"].get("volatility_bps")
            thr = g["vol_sigma_guard"].get("min_volatility_bps")
            mark = "✓" if g["vol_sigma_guard"].get("passes") else "✗"
            parts.append(f"volσ {mark} {self._fmt_bps(cur)}≥{self._fmt_bps(thr)}")
        if failed:
            parts.append(f"blocked_by={failed}")
        return " | ".join(parts)

    def update_price_data(self, symbol: str, price: float, volume: Optional[float] = None,
                         timestamp: Optional[datetime] = None) -> None:
        """
        Update price and volume data for a symbol.

        Args:
            symbol: Trading symbol
            price: Current price
            volume: Current volume (optional)
            timestamp: Data timestamp (uses current time if None)
        """
        if timestamp is None:
            timestamp = datetime.utcnow()

        with self._lock:
            # Initialize price history
            if symbol not in self._price_history:
                self._price_history[symbol] = deque(maxlen=max(self.sma_guard_window,
                                                              self.vol_sigma_window, 60) + 10)

            self._price_history[symbol].append((timestamp, price))

            # Update volume history if provided
            if volume is not None:
                if symbol not in self._volume_history:
                    self._volume_history[symbol] = deque(maxlen=self.volume_guard_window + 10)
                self._volume_history[symbol].append((timestamp, volume))

    def update_orderbook(self, symbol: str, bid: float, ask: float,
                        timestamp: Optional[datetime] = None) -> None:
        """
        Update orderbook data for spread calculations.

        Args:
            symbol: Trading symbol
            bid: Best bid price
            ask: Best ask price
            timestamp: Data timestamp (uses current time if None)
        """
        if timestamp is None:
            timestamp = datetime.utcnow()

        with self._lock:
            self._orderbook_data[symbol] = (bid, ask, timestamp)

    def update_market_conditions(self, btc_change_factor: Optional[float] = None,
                               percentage_falling: Optional[float] = None) -> None:
        """
        Update overall market conditions.

        Args:
            btc_change_factor: BTC price change factor (1.0 = no change)
            percentage_falling: Percentage of coins falling (0.0-1.0)
        """
        with self._lock:
            self._market_conditions_timestamp = datetime.utcnow()

            if btc_change_factor is not None:
                self._market_conditions['btc_change_factor'] = btc_change_factor

            if percentage_falling is not None:
                self._market_conditions['percentage_falling'] = percentage_falling

    def passes_all_guards(self, symbol: str, price: float) -> Tuple[bool, List[str]]:
        """
        Check if symbol passes all enabled guard filters.

        Args:
            symbol: Trading symbol
            price: Current price

        Returns:
            Tuple of (passes_all, list_of_failed_guards)
        """
        failed_guards = []

        # BTC Trend Filter
        if self.use_btc_filter:
            if not self._passes_btc_guard(symbol):
                failed_guards.append("btc_trend")

        # Falling Coins Filter
        if self.use_falling_coins_filter:
            if not self._passes_falling_coins_guard(symbol):
                failed_guards.append("falling_coins")

        # SMA Guard
        if self.use_sma_guard:
            if not self._passes_sma_guard(symbol, price):
                failed_guards.append("sma")

        # Volume Guard
        if self.use_volume_guard:
            if not self._passes_volume_guard(symbol):
                failed_guards.append("volume")

        # Spread Guard
        if self.use_spread_guard:
            if not self._passes_spread_guard(symbol):
                failed_guards.append("spread")

        # Volatility Sigma Guard
        if self.use_vol_sigma_guard:
            if not self._passes_vol_sigma_guard(symbol):
                failed_guards.append("vol_sigma")

        passes_all = len(failed_guards) == 0

        # Explizit Guard-Block loggen für bessere Transparenz
        if not passes_all:
            logger.info(f"[GUARD_BLOCK] {symbol} failed: {','.join(failed_guards)} @ {price:.8f}")
            # Simple JSON log event ohne komplexe Imports
            logger.info(f"GUARD_BLOCK_EVENT: symbol={symbol}, price={price:.8f}, failed={failed_guards}",
                       extra={'event_type': 'GUARD_BLOCK_EVENT', 'symbol': symbol, 'failed': failed_guards})

        # Detailstatus & sprechende Zusammenfassung
        details = self.get_detailed_guard_status(symbol, price)
        details["passes_all"] = passes_all
        details["blocked_by"] = failed_guards
        details["summary"] = self._build_summary(symbol, price, details, failed_guards)

        # Log nur, wenn explizit gewünscht oder blockiert
        if self._verbose or not passes_all:
            level = logging.INFO if not passes_all else logging.DEBUG
            logger.log(level, details["summary"], extra={
                'event_type': 'GUARD_BLOCK_SUMMARY' if not passes_all else 'GUARD_PASS_SUMMARY',
                'symbol': symbol,
                'blocked_by': failed_guards if not passes_all else [],
            })

        return passes_all, failed_guards

    def _passes_btc_guard(self, symbol: str) -> bool:
        """Check BTC trend filter."""
        btc_change = self._market_conditions.get('btc_change_factor')
        if btc_change is None:
            logger.warning("BTC change factor not available, allowing buy")
            return True

        if btc_change < self.btc_change_threshold:
            logger.debug(f"BTC filter blocks {symbol}: {btc_change:.4f} < {self.btc_change_threshold:.4f}")
            return False

        return True

    def _passes_falling_coins_guard(self, symbol: str) -> bool:
        """Check falling coins percentage filter."""
        falling_pct = self._market_conditions.get('percentage_falling')
        if falling_pct is None:
            logger.warning("Falling coins percentage not available, allowing buy")
            return True

        if falling_pct > self.falling_coins_threshold:
            logger.debug(f"Falling coins filter blocks {symbol}: "
                        f"{falling_pct:.1%} > {self.falling_coins_threshold:.1%}")
            return False

        return True

    def _passes_sma_guard(self, symbol: str, price: float) -> bool:
        """Check Simple Moving Average guard."""
        with self._lock:
            if symbol not in self._price_history:
                logger.warning(f"No price history for SMA guard: {symbol}")
                return True

            prices = [p for _, p in self._price_history[symbol]]
            if len(prices) < self.sma_guard_window:
                logger.debug(f"Insufficient price history for SMA guard: {symbol}")
                return True

            sma = np.mean(prices[-self.sma_guard_window:])
            min_price = sma * self.sma_guard_min_ratio

            if price < min_price:
                logger.debug(f"SMA guard blocks {symbol}: {price:.6f} < {min_price:.6f} "
                           f"(SMA: {sma:.6f}, ratio: {self.sma_guard_min_ratio:.3f})")
                return False

            return True

    def _passes_volume_guard(self, symbol: str) -> bool:
        """Check volume guard."""
        with self._lock:
            if symbol not in self._volume_history:
                logger.warning(f"No volume history for volume guard: {symbol}")
                return True

            volumes = [v for _, v in self._volume_history[symbol]]
            if len(volumes) < self.volume_guard_window:
                logger.debug(f"Insufficient volume history for volume guard: {symbol}")
                return True

            # Current volume should be above average by the factor
            current_volume = volumes[-1]
            avg_volume = np.mean(volumes[:-1]) if len(volumes) > 1 else current_volume
            min_volume = avg_volume * self.volume_guard_factor

            if current_volume < min_volume:
                logger.debug(f"Volume guard blocks {symbol}: {current_volume:.2f} < {min_volume:.2f} "
                           f"(avg: {avg_volume:.2f}, factor: {self.volume_guard_factor:.2f})")
                return False

            return True

    def _passes_spread_guard(self, symbol: str) -> bool:
        """Check bid-ask spread guard."""
        with self._lock:
            if symbol not in self._orderbook_data:
                logger.warning(f"No orderbook data for spread guard: {symbol}")
                return True

            bid, ask, timestamp = self._orderbook_data[symbol]

            # Check if data is recent (within 5 minutes)
            if datetime.utcnow() - timestamp > timedelta(minutes=5):
                logger.warning(f"Stale orderbook data for spread guard: {symbol}")
                return True

            if ask <= bid:
                logger.warning(f"Invalid spread data for {symbol}: bid={bid}, ask={ask}")
                return True

            spread_pct = (ask - bid) / bid
            spread_bps = int(spread_pct * 10000)

            if spread_bps > self.guard_max_spread_bps:
                logger.debug(f"Spread guard blocks {symbol}: {spread_bps}bps > {self.guard_max_spread_bps}bps")
                return False

            return True

    def _passes_vol_sigma_guard(self, symbol: str) -> bool:
        """Check volatility sigma guard."""
        with self._lock:
            if symbol not in self._price_history:
                logger.warning(f"No price history for vol sigma guard: {symbol}")
                return True

            prices = [p for _, p in self._price_history[symbol]]
            if len(prices) < self.vol_sigma_window:
                logger.debug(f"Insufficient price history for vol sigma guard: {symbol}")
                return True

            # Calculate price changes (returns)
            price_array = np.array(prices[-self.vol_sigma_window:])
            returns = np.diff(price_array) / price_array[:-1]

            if len(returns) == 0:
                return True

            # Calculate volatility (standard deviation of returns)
            volatility = np.std(returns)
            volatility_bps = int(volatility * 10000)

            if volatility_bps < self.require_vol_sigma_bps_min:
                logger.debug(f"Vol sigma guard blocks {symbol}: {volatility_bps}bps < {self.require_vol_sigma_bps_min}bps")
                return False

            return True

    def get_guard_status(self, symbol: str) -> Dict[str, Any]:
        """
        Get detailed status of all guards for a symbol.

        Returns:
            Dictionary with guard statuses and values
        """
        status = {}

        # BTC Filter
        if self.use_btc_filter:
            btc_change = self._market_conditions.get('btc_change_factor')
            status['btc_filter'] = {
                'enabled': True,
                'current_value': btc_change,
                'threshold': self.btc_change_threshold,
                'passes': btc_change >= self.btc_change_threshold if btc_change else None
            }

        # Falling Coins Filter
        if self.use_falling_coins_filter:
            falling_pct = self._market_conditions.get('percentage_falling')
            status['falling_coins_filter'] = {
                'enabled': True,
                'current_value': falling_pct,
                'threshold': self.falling_coins_threshold,
                'passes': falling_pct <= self.falling_coins_threshold if falling_pct else None
            }

        # Additional guard details would be added here for SMA, Volume, etc.
        # For brevity, showing the pattern with the two most important filters

        return status

    def get_detailed_guard_status(self, symbol: str, price: float) -> Dict[str, Any]:
        """
        Get detailed status of all guards for a specific symbol and price.
        Used for comprehensive audit logging.
        """
        with self._lock:
            status = {
                'symbol': symbol,
                'price': price,
                'timestamp': datetime.utcnow().isoformat(),
                'guards': {}
            }

            # BTC Filter Details
            if self.use_btc_filter:
                btc_change = self._market_conditions.get('btc_change_factor')
                passes = btc_change >= self.btc_change_threshold if btc_change else None
                status['guards']['btc_filter'] = {
                    'enabled': True,
                    'current_value': btc_change,
                    'threshold': self.btc_change_threshold,
                    'passes': passes,
                    'reason': None if passes else f"BTC change {btc_change or 'None'} < threshold {self.btc_change_threshold:.4f}"
                }

            # Falling Coins Filter Details
            if self.use_falling_coins_filter:
                falling_pct = self._market_conditions.get('percentage_falling')
                passes = falling_pct <= self.falling_coins_threshold if falling_pct else None
                status['guards']['falling_coins_filter'] = {
                    'enabled': True,
                    'current_value': falling_pct,
                    'threshold': self.falling_coins_threshold,
                    'passes': passes,
                    'reason': None if passes else f"Too many falling coins {falling_pct or 'None'}% > threshold {self.falling_coins_threshold:.1f}%"
                }

            # SMA Guard Details
            if self.use_sma_guard and symbol in self._price_history:
                prices = [p for _, p in self._price_history[symbol]]
                if len(prices) >= self.sma_guard_window:
                    sma = np.mean(prices[-self.sma_guard_window:])
                    min_price = sma * self.sma_guard_min_ratio
                    passes = price >= min_price
                    status['guards']['sma_guard'] = {
                        'enabled': True,
                        'current_price': price,
                        'sma': sma,
                        'min_price': min_price,
                        'min_ratio': self.sma_guard_min_ratio,
                        'window': self.sma_guard_window,
                        'passes': passes,
                        'reason': None if passes else f"Price {price:.6f} < SMA min {min_price:.6f}"
                    }

            # Volume Guard Details
            if self.use_volume_guard and symbol in self._volume_history:
                volumes = [v for _, v in self._volume_history[symbol]]
                if len(volumes) >= self.volume_guard_window:
                    current_volume = volumes[-1]
                    avg_volume = np.mean(volumes[:-1]) if len(volumes) > 1 else current_volume
                    min_volume = avg_volume * self.volume_guard_factor
                    passes = current_volume >= min_volume
                    status['guards']['volume_guard'] = {
                        'enabled': True,
                        'current_volume': current_volume,
                        'avg_volume': avg_volume,
                        'min_volume': min_volume,
                        'factor': self.volume_guard_factor,
                        'passes': passes,
                        'reason': None if passes else f"Volume {current_volume:.0f} < min {min_volume:.0f}"
                    }

            # Spread Guard Details
            if self.use_spread_guard and symbol in self._orderbook_data:
                bid, ask, ob_timestamp = self._orderbook_data[symbol]
                data_age = (datetime.utcnow() - ob_timestamp).total_seconds()
                if data_age <= 300:  # 5 minutes
                    spread_pct = ((ask - bid) / ask) * 100 if ask > bid else None
                    passes = spread_pct <= self.spread_guard_max_pct if spread_pct else False
                    status['guards']['spread_guard'] = {
                        'enabled': True,
                        'bid': bid,
                        'ask': ask,
                        'spread_pct': spread_pct,
                        'max_spread_pct': self.spread_guard_max_pct,
                        'data_age_seconds': data_age,
                        'passes': passes,
                        'reason': None if passes else f"Spread {spread_pct:.2f}% > max {self.spread_guard_max_pct:.2f}%"
                    }

            # Am Ende eine kompakte Summary (wird in passes_all_guards gefüllt)
            status.setdefault('passes_all', None)
            status.setdefault('blocked_by', [])
            status.setdefault('summary', None)
            return status

    def get_statistics(self) -> Dict[str, Any]:
        """Get service statistics and configuration."""
        with self._lock:
            return {
                "symbols_tracked": len(self._price_history),
                "orderbook_symbols": len(self._orderbook_data),
                "market_conditions_age": (
                    (datetime.utcnow() - self._market_conditions_timestamp).total_seconds()
                    if self._market_conditions_timestamp else None
                ),
                "config": {
                    "use_btc_filter": self.use_btc_filter,
                    "btc_change_threshold": self.btc_change_threshold,
                    "use_falling_coins_filter": self.use_falling_coins_filter,
                    "falling_coins_threshold": self.falling_coins_threshold,
                    "use_sma_guard": self.use_sma_guard,
                    "sma_guard_window": self.sma_guard_window,
                    "use_volume_guard": self.use_volume_guard,
                    "use_spread_guard": self.use_spread_guard,
                    "use_vol_sigma_guard": self.use_vol_sigma_guard
                },
                "current_market_conditions": self._market_conditions.copy()
            }