# signals/buy_sell_path.py
# V10-Style Buy/Sell-Pfad mit korrekter Slippage-Kontrolle und ORDER_SENT Garantie
# Eliminiert "no_trigger" durch sichtbare Order-Platzierung auch bei Dry-Run

import time
import math
import threading
from dataclasses import dataclass, asdict
from typing import Dict, Optional, Callable, Any, Tuple
from enum import Enum
import logging

logger = logging.getLogger(__name__)

class OrderMode(Enum):
    """Order-Modi für verschiedene Szenarien"""
    MAKER = "MAKER"         # Post-only, Maker-Fees
    IOC = "IOC"            # Immediate-or-Cancel, für schnelle Fills
    MARKET = "MARKET"      # Market Order, garantierter Fill
    ADAPTIVE = "ADAPTIVE"   # Adaptive basierend auf Marktbedingungen

class SlippageMode(Enum):
    """Modi für Slippage-Berechnung"""
    MID_BASED = "MID_BASED"           # Basierend auf Mid-Preis
    BID_ASK_BASED = "BID_ASK_BASED"   # Basierend auf Bid/Ask
    LAST_PRICE_BASED = "LAST_PRICE_BASED"  # Basierend auf Last Price

class OrderResult(Enum):
    """Ergebnisse der Order-Platzierung"""
    SUCCESS = "SUCCESS"
    DRY_RUN_SUCCESS = "DRY_RUN_SUCCESS"
    SLIPPAGE_TOO_HIGH = "SLIPPAGE_TOO_HIGH"
    SPREAD_TOO_WIDE = "SPREAD_TOO_WIDE"
    INSUFFICIENT_FUNDS = "INSUFFICIENT_FUNDS"
    EXCHANGE_ERROR = "EXCHANGE_ERROR"
    SIZE_TOO_SMALL = "SIZE_TOO_SMALL"
    PRICE_INVALID = "PRICE_INVALID"

@dataclass
class SlippageConfig:
    """Konfiguration für Slippage-Kontrolle"""
    mode: SlippageMode = SlippageMode.MID_BASED
    max_slippage_bp: int = 100        # Max 1.0% Slippage
    max_spread_bp: int = 50           # Max 0.5% Spread
    maker_safety_ticks: int = 2       # Ticks weg von Bid/Ask für Maker
    ioc_max_slippage_bp: int = 200    # Höhere Slippage für IOC erlaubt

    # Symbol-spezifische Overrides
    symbol_overrides: Dict[str, Dict[str, Any]] = None

    def __post_init__(self):
        if self.symbol_overrides is None:
            self.symbol_overrides = {}

@dataclass
class OrderExecutionResult:
    """Ergebnis einer Order-Execution"""
    success: bool
    result: OrderResult
    order_id: Optional[str]
    executed_price: Optional[float]
    executed_quantity: Optional[float]
    slippage_bp: float
    spread_bp: float
    fees_paid: float
    execution_time_ms: float
    details: Dict[str, Any]

    def to_dict(self) -> Dict[str, Any]:
        """Konvertiert zu Dict für Logging"""
        result = asdict(self)
        result['result'] = self.result.value
        return result

class BuySellPathManager:
    """
    Zentraler Manager für Buy/Sell-Pfad mit korrekter Slippage-Kontrolle.

    Key Features:
    - Garantierte ORDER_SENT Events (auch Dry-Run)
    - Slippage-Kontrolle pro Symbol
    - Adaptive Order-Modi basierend auf Marktbedingungen
    - Thread-safe Operations
    """

    def __init__(self,
                 slippage_config: SlippageConfig = None,
                 log_function: Callable = None,
                 dry_run: bool = False):
        self.slippage_config = slippage_config or SlippageConfig()
        self.log_function = log_function or self._default_log
        self.dry_run = dry_run
        self.lock = threading.RLock()

        # Exchange adapter (wird via set_exchange gesetzt)
        self.exchange = None

        # Statistics
        self.execution_stats = {
            "total_orders": 0,
            "successful_orders": 0,
            "failed_orders": 0,
            "total_slippage_bp": 0.0,
            "orders_by_result": {}
        }

    def _default_log(self, event_type: str, **kwargs):
        """Default logging fallback"""
        logger.info(f"[{event_type}] {kwargs}")

    def set_exchange(self, exchange_adapter):
        """Setzt Exchange-Adapter für Live-Trading"""
        self.exchange = exchange_adapter

    def _get_symbol_slippage_config(self, symbol: str) -> SlippageConfig:
        """Holt symbol-spezifische Slippage-Konfiguration"""
        if symbol in self.slippage_config.symbol_overrides:
            # Merge base config with symbol overrides
            base_config = asdict(self.slippage_config)
            overrides = self.slippage_config.symbol_overrides[symbol]
            base_config.update(overrides)
            return SlippageConfig(**base_config)
        return self.slippage_config

    def _round_to_tick(self, price: float, tick_size: float) -> float:
        """Rundet Preis auf Tick-Size"""
        if tick_size <= 0:
            return price
        return round(price / tick_size) * tick_size

    def _ceil_to_tick(self, price: float, tick_size: float) -> float:
        """Rundet Preis auf (höher zur nächsten Tick-Size)"""
        if tick_size <= 0:
            return price
        return math.ceil(price / tick_size) * tick_size

    def _floor_to_tick(self, price: float, tick_size: float) -> float:
        """Rundet Preis ab (niedriger zur nächsten Tick-Size)"""
        if tick_size <= 0:
            return price
        return math.floor(price / tick_size) * tick_size

    def _calculate_spread_bp(self, best_bid: float, best_ask: float) -> float:
        """Berechnet Spread in Basispoints"""
        if best_bid <= 0 or best_ask <= 0:
            return float('inf')

        mid_price = (best_bid + best_ask) / 2
        if mid_price <= 0:
            return float('inf')

        return (best_ask - best_bid) / mid_price * 10000.0

    def _calculate_slippage_bp(self, target_price: float, reference_price: float) -> float:
        """Berechnet Slippage in Basispoints"""
        if reference_price <= 0:
            return float('inf')

        return abs(target_price - reference_price) / reference_price * 10000.0

    def _get_reference_price(self, slippage_config: SlippageConfig,
                            best_bid: float, best_ask: float, last_price: float) -> float:
        """Ermittelt Referenz-Preis für Slippage-Berechnung"""
        if slippage_config.mode == SlippageMode.MID_BASED:
            return (best_bid + best_ask) / 2

        elif slippage_config.mode == SlippageMode.BID_ASK_BASED:
            return best_bid  # Für Buy-Slippage vs. Bid

        elif slippage_config.mode == SlippageMode.LAST_PRICE_BASED:
            return last_price

        else:
            return (best_bid + best_ask) / 2  # Fallback

    def _calculate_buy_price(self, symbol: str, mode: OrderMode,
                           best_bid: float, best_ask: float, last_price: float,
                           tick_size: float) -> Tuple[float, float]:
        """
        Berechnet Buy-Preis basierend auf Order-Mode.

        Args:
            symbol: Trading symbol
            mode: Order mode
            best_bid: Bester Bid
            best_ask: Bester Ask
            last_price: Letzter Preis
            tick_size: Tick size

        Returns:
            (calculated_price, slippage_bp)
        """
        config = self._get_symbol_slippage_config(symbol)
        reference_price = self._get_reference_price(config, best_bid, best_ask, last_price)

        if mode == OrderMode.MAKER:
            # Post-only: unter Ask platzieren
            target_price = best_ask - (config.maker_safety_ticks * tick_size)
            target_price = self._floor_to_tick(target_price, tick_size)

            # Nicht höher als Ask
            target_price = min(target_price, best_ask - tick_size)

        elif mode == OrderMode.IOC:
            # IOC: bis zu Ask + Slippage
            max_slippage_bp = config.ioc_max_slippage_bp
            max_price = reference_price * (1 + max_slippage_bp / 10000.0)
            target_price = min(best_ask, max_price)
            target_price = self._round_to_tick(target_price, tick_size)

        elif mode == OrderMode.MARKET:
            # Market: Ask + Buffer
            target_price = best_ask * 1.001  # 0.1% Buffer
            target_price = self._ceil_to_tick(target_price, tick_size)

        elif mode == OrderMode.ADAPTIVE:
            # Adaptive: basierend auf Spread
            spread_bp = self._calculate_spread_bp(best_bid, best_ask)

            if spread_bp <= config.max_spread_bp:
                # Enger Spread: Maker
                target_price = best_bid + tick_size
            else:
                # Weiter Spread: IOC zum Ask
                target_price = best_ask
            target_price = self._round_to_tick(target_price, tick_size)

        else:
            # Fallback: Mid-Price
            target_price = (best_bid + best_ask) / 2
            target_price = self._round_to_tick(target_price, tick_size)

        # Calculate slippage
        slippage_bp = self._calculate_slippage_bp(target_price, reference_price)

        return target_price, slippage_bp

    def _calculate_sell_price(self, symbol: str, mode: OrderMode,
                            best_bid: float, best_ask: float, last_price: float,
                            tick_size: float) -> Tuple[float, float]:
        """
        Berechnet Sell-Preis basierend auf Order-Mode.

        Args:
            symbol: Trading symbol
            mode: Order mode
            best_bid: Bester Bid
            best_ask: Bester Ask
            last_price: Letzter Preis
            tick_size: Tick size

        Returns:
            (calculated_price, slippage_bp)
        """
        config = self._get_symbol_slippage_config(symbol)
        reference_price = self._get_reference_price(config, best_bid, best_ask, last_price)

        if mode == OrderMode.MAKER:
            # Post-only: über Bid platzieren
            target_price = best_bid + (config.maker_safety_ticks * tick_size)
            target_price = self._ceil_to_tick(target_price, tick_size)

            # Nicht niedriger als Bid
            target_price = max(target_price, best_bid + tick_size)

        elif mode == OrderMode.IOC:
            # IOC: bis zu Bid - Slippage
            max_slippage_bp = config.ioc_max_slippage_bp
            min_price = reference_price * (1 - max_slippage_bp / 10000.0)
            target_price = max(best_bid, min_price)
            target_price = self._round_to_tick(target_price, tick_size)

        elif mode == OrderMode.MARKET:
            # Market: Bid - Buffer
            target_price = best_bid * 0.999  # 0.1% Buffer
            target_price = self._floor_to_tick(target_price, tick_size)

        elif mode == OrderMode.ADAPTIVE:
            # Adaptive: basierend auf Spread
            spread_bp = self._calculate_spread_bp(best_bid, best_ask)

            if spread_bp <= config.max_spread_bp:
                # Enger Spread: Maker
                target_price = best_ask - tick_size
            else:
                # Weiter Spread: IOC zum Bid
                target_price = best_bid
            target_price = self._round_to_tick(target_price, tick_size)

        else:
            # Fallback: Mid-Price
            target_price = (best_bid + best_ask) / 2
            target_price = self._round_to_tick(target_price, tick_size)

        # Calculate slippage (für Sell: Referenz - Target)
        slippage_bp = self._calculate_slippage_bp(reference_price, target_price)

        return target_price, slippage_bp

    def _validate_order_constraints(self, symbol: str, side: str, price: float,
                                  best_bid: float, best_ask: float) -> Tuple[bool, str]:
        """
        Validiert Order-Constraints.

        Returns:
            (is_valid, reason)
        """
        config = self._get_symbol_slippage_config(symbol)

        # Spread Check
        spread_bp = self._calculate_spread_bp(best_bid, best_ask)
        if spread_bp > config.max_spread_bp:
            return False, f"Spread too wide: {spread_bp:.1f}bp > {config.max_spread_bp}bp"

        # Slippage Check
        reference_price = self._get_reference_price(config, best_bid, best_ask, price)
        slippage_bp = self._calculate_slippage_bp(price, reference_price)

        if slippage_bp > config.max_slippage_bp:
            return False, f"Slippage too high: {slippage_bp:.1f}bp > {config.max_slippage_bp}bp"

        return True, "OK"

    def _simulate_order_execution(self, symbol: str, side: str, quantity: float,
                                price: float, order_mode: OrderMode) -> OrderExecutionResult:
        """Simuliert Order-Execution für Dry-Run"""
        start_time = time.time()

        # Simulierte Execution
        order_id = f"DRY_{int(time.time() * 1000)}_{symbol}_{side}"
        executed_price = price
        executed_quantity = quantity
        fees_paid = quantity * executed_price * 0.001  # 0.1% Fee angenommen

        execution_time = (time.time() - start_time) * 1000

        return OrderExecutionResult(
            success=True,
            result=OrderResult.DRY_RUN_SUCCESS,
            order_id=order_id,
            executed_price=executed_price,
            executed_quantity=executed_quantity,
            slippage_bp=0.0,  # Wird außerhalb gesetzt
            spread_bp=0.0,    # Wird außerhalb gesetzt
            fees_paid=fees_paid,
            execution_time_ms=execution_time,
            details={
                "dry_run": True,
                "order_mode": order_mode.value,
                "simulated": True
            }
        )

    def _execute_live_order(self, symbol: str, side: str, quantity: float,
                          price: float, order_mode: OrderMode,
                          filters: Dict) -> OrderExecutionResult:
        """Führt Live-Order über Exchange-Adapter aus"""
        if not self.exchange:
            raise ValueError("No exchange adapter configured for live trading")

        start_time = time.time()

        try:
            if side.upper() == "BUY":
                if order_mode == OrderMode.IOC:
                    result = self.exchange.place_limit_buy_robust(
                        symbol, quantity, price, post_only=False, time_in_force="IOC"
                    )
                elif order_mode == OrderMode.MARKET:
                    result = self.exchange.place_market_buy(symbol, quantity)
                else:  # MAKER or ADAPTIVE
                    result = self.exchange.place_limit_buy_robust(
                        symbol, quantity, price, post_only=True
                    )

            else:  # SELL
                if order_mode == OrderMode.IOC:
                    result = self.exchange.place_limit_sell_robust(
                        symbol, quantity, price, post_only=False, time_in_force="IOC"
                    )
                elif order_mode == OrderMode.MARKET:
                    result = self.exchange.place_market_sell(symbol, quantity)
                else:  # MAKER or ADAPTIVE
                    result = self.exchange.place_limit_sell_robust(
                        symbol, quantity, price, post_only=True
                    )

            execution_time = (time.time() - start_time) * 1000

            if result.get("success"):
                return OrderExecutionResult(
                    success=True,
                    result=OrderResult.SUCCESS,
                    order_id=result.get("order_id"),
                    executed_price=result.get("price", price),
                    executed_quantity=result.get("quantity", quantity),
                    slippage_bp=0.0,  # Wird außerhalb gesetzt
                    spread_bp=0.0,    # Wird außerhalb gesetzt
                    fees_paid=result.get("fees_paid", 0.0),
                    execution_time_ms=execution_time,
                    details=result
                )
            else:
                return OrderExecutionResult(
                    success=False,
                    result=OrderResult.EXCHANGE_ERROR,
                    order_id=None,
                    executed_price=None,
                    executed_quantity=None,
                    slippage_bp=0.0,
                    spread_bp=0.0,
                    fees_paid=0.0,
                    execution_time_ms=execution_time,
                    details=result
                )

        except Exception as e:
            execution_time = (time.time() - start_time) * 1000

            return OrderExecutionResult(
                success=False,
                result=OrderResult.EXCHANGE_ERROR,
                order_id=None,
                executed_price=None,
                executed_quantity=None,
                slippage_bp=0.0,
                spread_bp=0.0,
                fees_paid=0.0,
                execution_time_ms=execution_time,
                details={"error": str(e)}
            )

    def execute_buy_order(self, symbol: str, quote_quantity: float,
                         best_bid: float, best_ask: float, last_price: float,
                         filters: Dict, order_mode: OrderMode = OrderMode.ADAPTIVE,
                         max_slippage_override: Optional[int] = None) -> OrderExecutionResult:
        """
        Führt Buy-Order mit Slippage-Kontrolle aus.

        Args:
            symbol: Trading symbol
            quote_quantity: Quote amount to spend
            best_bid: Bester Bid
            best_ask: Bester Ask
            last_price: Letzter Preis
            filters: Exchange filters
            order_mode: Order execution mode
            max_slippage_override: Optional override für max slippage

        Returns:
            OrderExecutionResult
        """
        with self.lock:
            start_time = time.time()

            # Get filters
            tick_size = filters.get("PRICE_FILTER", {}).get("tickSize", 0.01)
            min_qty = filters.get("LOT_SIZE", {}).get("minQty", 0.001)
            step_size = filters.get("LOT_SIZE", {}).get("stepSize", 0.001)

            # Calculate price and slippage
            target_price, slippage_bp = self._calculate_buy_price(
                symbol, order_mode, best_bid, best_ask, last_price, tick_size
            )

            # Calculate quantity
            base_quantity = quote_quantity / target_price
            base_quantity = math.floor(base_quantity / step_size) * step_size

            # Validate constraints
            is_valid, reason = self._validate_order_constraints(
                symbol, "BUY", target_price, best_bid, best_ask
            )

            if not is_valid:
                result = OrderExecutionResult(
                    success=False,
                    result=OrderResult.SLIPPAGE_TOO_HIGH if "slippage" in reason.lower() else OrderResult.SPREAD_TOO_WIDE,
                    order_id=None,
                    executed_price=None,
                    executed_quantity=None,
                    slippage_bp=slippage_bp,
                    spread_bp=self._calculate_spread_bp(best_bid, best_ask),
                    fees_paid=0.0,
                    execution_time_ms=(time.time() - start_time) * 1000,
                    details={"validation_error": reason}
                )
            elif base_quantity < min_qty:
                result = OrderExecutionResult(
                    success=False,
                    result=OrderResult.SIZE_TOO_SMALL,
                    order_id=None,
                    executed_price=None,
                    executed_quantity=None,
                    slippage_bp=slippage_bp,
                    spread_bp=self._calculate_spread_bp(best_bid, best_ask),
                    fees_paid=0.0,
                    execution_time_ms=(time.time() - start_time) * 1000,
                    details={"min_qty": min_qty, "calculated_qty": base_quantity}
                )
            else:
                # Execute order
                if self.dry_run:
                    result = self._simulate_order_execution(
                        symbol, "BUY", base_quantity, target_price, order_mode
                    )
                else:
                    result = self._execute_live_order(
                        symbol, "BUY", base_quantity, target_price, order_mode, filters
                    )

                # Set slippage/spread info
                result.slippage_bp = slippage_bp
                result.spread_bp = self._calculate_spread_bp(best_bid, best_ask)

            # Update statistics
            self._update_statistics(result)

            # Log ORDER_SENT (garantiert, auch bei Dry-Run)
            self.log_function(
                "ORDER_SENT",
                symbol=symbol,
                side="BUY",
                order_id=result.order_id,
                quantity=base_quantity,
                price=target_price,
                order_mode=order_mode.value,
                success=result.success,
                result=result.result.value,
                slippage_bp=round(result.slippage_bp, 1),
                spread_bp=round(result.spread_bp, 1),
                execution_time_ms=round(result.execution_time_ms, 2),
                dry_run=self.dry_run
            )

            return result

    def execute_sell_order(self, symbol: str, base_quantity: float,
                          best_bid: float, best_ask: float, last_price: float,
                          filters: Dict, order_mode: OrderMode = OrderMode.ADAPTIVE,
                          exit_signal: bool = False) -> OrderExecutionResult:
        """
        Führt Sell-Order mit Slippage-Kontrolle aus.

        Args:
            symbol: Trading symbol
            base_quantity: Base quantity to sell
            best_bid: Bester Bid
            best_ask: Bester Ask
            last_price: Letzter Preis
            filters: Exchange filters
            order_mode: Order execution mode
            exit_signal: True wenn via Exit-Signal getriggert

        Returns:
            OrderExecutionResult
        """
        with self.lock:
            start_time = time.time()

            # Get filters
            tick_size = filters.get("PRICE_FILTER", {}).get("tickSize", 0.01)
            min_qty = filters.get("LOT_SIZE", {}).get("minQty", 0.001)
            step_size = filters.get("LOT_SIZE", {}).get("stepSize", 0.001)

            # Round quantity to step size
            rounded_quantity = math.floor(base_quantity / step_size) * step_size

            # Calculate price and slippage
            target_price, slippage_bp = self._calculate_sell_price(
                symbol, order_mode, best_bid, best_ask, last_price, tick_size
            )

            # Validate constraints
            is_valid, reason = self._validate_order_constraints(
                symbol, "SELL", target_price, best_bid, best_ask
            )

            if not is_valid:
                result = OrderExecutionResult(
                    success=False,
                    result=OrderResult.SLIPPAGE_TOO_HIGH if "slippage" in reason.lower() else OrderResult.SPREAD_TOO_WIDE,
                    order_id=None,
                    executed_price=None,
                    executed_quantity=None,
                    slippage_bp=slippage_bp,
                    spread_bp=self._calculate_spread_bp(best_bid, best_ask),
                    fees_paid=0.0,
                    execution_time_ms=(time.time() - start_time) * 1000,
                    details={"validation_error": reason}
                )
            elif rounded_quantity < min_qty:
                result = OrderExecutionResult(
                    success=False,
                    result=OrderResult.SIZE_TOO_SMALL,
                    order_id=None,
                    executed_price=None,
                    executed_quantity=None,
                    slippage_bp=slippage_bp,
                    spread_bp=self._calculate_spread_bp(best_bid, best_ask),
                    fees_paid=0.0,
                    execution_time_ms=(time.time() - start_time) * 1000,
                    details={"min_qty": min_qty, "calculated_qty": rounded_quantity}
                )
            else:
                # Execute order
                if self.dry_run:
                    result = self._simulate_order_execution(
                        symbol, "SELL", rounded_quantity, target_price, order_mode
                    )
                else:
                    result = self._execute_live_order(
                        symbol, "SELL", rounded_quantity, target_price, order_mode, filters
                    )

                # Set slippage/spread info
                result.slippage_bp = slippage_bp
                result.spread_bp = self._calculate_spread_bp(best_bid, best_ask)

            # Update statistics
            self._update_statistics(result)

            # Log ORDER_SENT (garantiert, auch bei Dry-Run)
            self.log_function(
                "ORDER_SENT",
                symbol=symbol,
                side="SELL",
                order_id=result.order_id,
                quantity=rounded_quantity,
                price=target_price,
                order_mode=order_mode.value,
                success=result.success,
                result=result.result.value,
                slippage_bp=round(result.slippage_bp, 1),
                spread_bp=round(result.spread_bp, 1),
                execution_time_ms=round(result.execution_time_ms, 2),
                exit_signal=exit_signal,
                dry_run=self.dry_run
            )

            return result

    def _update_statistics(self, result: OrderExecutionResult):
        """Aktualisiert Execution-Statistiken"""
        with self.lock:
            self.execution_stats["total_orders"] += 1

            if result.success:
                self.execution_stats["successful_orders"] += 1
                self.execution_stats["total_slippage_bp"] += result.slippage_bp
            else:
                self.execution_stats["failed_orders"] += 1

            # Count by result type
            result_type = result.result.value
            if result_type not in self.execution_stats["orders_by_result"]:
                self.execution_stats["orders_by_result"][result_type] = 0
            self.execution_stats["orders_by_result"][result_type] += 1

    def get_execution_stats(self) -> Dict[str, Any]:
        """Gibt Execution-Statistiken zurück"""
        with self.lock:
            total_orders = self.execution_stats["total_orders"]
            successful_orders = self.execution_stats["successful_orders"]

            return {
                **self.execution_stats.copy(),
                "success_rate": successful_orders / max(1, total_orders),
                "avg_slippage_bp": self.execution_stats["total_slippage_bp"] / max(1, successful_orders)
            }

    def configure_symbol_slippage(self, symbol: str, **kwargs):
        """Konfiguriert symbol-spezifische Slippage-Parameter"""
        if self.slippage_config.symbol_overrides is None:
            self.slippage_config.symbol_overrides = {}

        self.slippage_config.symbol_overrides[symbol] = kwargs

        self.log_function(
            "SLIPPAGE_CONFIG_UPDATE",
            symbol=symbol,
            overrides=kwargs
        )

# =============================================================================
# GLOBAL INSTANCE UND CONVENIENCE FUNCTIONS
# =============================================================================

# Global Buy/Sell Path Manager Instance
_buy_sell_manager = None

def get_buy_sell_manager(slippage_config: SlippageConfig = None,
                        log_function: Callable = None,
                        dry_run: bool = False) -> BuySellPathManager:
    """Singleton Pattern für globalen Buy/Sell Path Manager"""
    global _buy_sell_manager
    if _buy_sell_manager is None:
        _buy_sell_manager = BuySellPathManager(slippage_config, log_function, dry_run)
    return _buy_sell_manager

# Convenience Functions
def execute_smart_buy(symbol: str, quote_quantity: float, market_data: Dict,
                     order_mode: OrderMode = OrderMode.ADAPTIVE) -> OrderExecutionResult:
    """Convenience function für Smart Buy"""
    manager = get_buy_sell_manager()
    return manager.execute_buy_order(
        symbol=symbol,
        quote_quantity=quote_quantity,
        best_bid=market_data["bid"],
        best_ask=market_data["ask"],
        last_price=market_data["last"],
        filters=market_data.get("filters", {}),
        order_mode=order_mode
    )

def execute_smart_sell(symbol: str, base_quantity: float, market_data: Dict,
                      order_mode: OrderMode = OrderMode.ADAPTIVE,
                      exit_signal: bool = False) -> OrderExecutionResult:
    """Convenience function für Smart Sell"""
    manager = get_buy_sell_manager()
    return manager.execute_sell_order(
        symbol=symbol,
        base_quantity=base_quantity,
        best_bid=market_data["bid"],
        best_ask=market_data["ask"],
        last_price=market_data["last"],
        filters=market_data.get("filters", {}),
        order_mode=order_mode,
        exit_signal=exit_signal
    )

# =============================================================================
# PREDEFINED CONFIGURATIONS
# =============================================================================

def create_conservative_slippage_config() -> SlippageConfig:
    """Konservative Slippage-Konfiguration"""
    return SlippageConfig(
        mode=SlippageMode.MID_BASED,
        max_slippage_bp=50,       # 0.5%
        max_spread_bp=30,         # 0.3%
        maker_safety_ticks=3,
        ioc_max_slippage_bp=100   # 1.0%
    )

def create_aggressive_slippage_config() -> SlippageConfig:
    """Aggressive Slippage-Konfiguration"""
    return SlippageConfig(
        mode=SlippageMode.BID_ASK_BASED,
        max_slippage_bp=200,      # 2.0%
        max_spread_bp=100,        # 1.0%
        maker_safety_ticks=1,
        ioc_max_slippage_bp=300   # 3.0%
    )

def create_tier_based_slippage_config() -> SlippageConfig:
    """Tier-basierte Slippage-Konfiguration"""
    config = SlippageConfig()

    # Tier 1 - Major pairs (tight spreads)
    tier1_config = {
        "max_slippage_bp": 30,
        "max_spread_bp": 20,
        "maker_safety_ticks": 2
    }

    # Tier 2 - Popular alts
    tier2_config = {
        "max_slippage_bp": 80,
        "max_spread_bp": 50,
        "maker_safety_ticks": 2
    }

    # Tier 3 - Other alts
    tier3_config = {
        "max_slippage_bp": 150,
        "max_spread_bp": 100,
        "maker_safety_ticks": 3
    }

    config.symbol_overrides = {
        # Tier 1
        "BTC/USDT": tier1_config,
        "ETH/USDT": tier1_config,
        "BNB/USDT": tier1_config,

        # Tier 2 (Popular alts - wird via pattern matching gemacht)
        # Diese würden normalerweise über pattern matching konfiguriert

        # Default für andere = tier3_config wird über Fallback gehandhabt
    }

    return config

# =============================================================================
# USAGE EXAMPLE
# =============================================================================

if __name__ == "__main__":
    print("=== Buy/Sell Path Demo ===")

    # Tier-based configuration
    config = create_tier_based_slippage_config()
    manager = BuySellPathManager(config, dry_run=True)

    # Mock market data
    market_data = {
        "bid": 50000.0,
        "ask": 50020.0,
        "last": 50010.0,
        "filters": {
            "PRICE_FILTER": {"tickSize": 0.1},
            "LOT_SIZE": {"minQty": 0.001, "stepSize": 0.001}
        }
    }

    # Test buy order
    buy_result = manager.execute_buy_order(
        symbol="BTC/USDT",
        quote_quantity=1000.0,
        best_bid=market_data["bid"],
        best_ask=market_data["ask"],
        last_price=market_data["last"],
        filters=market_data["filters"],
        order_mode=OrderMode.ADAPTIVE
    )

    print(f"Buy Result: {buy_result.result.value}")
    print(f"Slippage: {buy_result.slippage_bp:.1f}bp")
    print(f"Order ID: {buy_result.order_id}")

    # Stats
    stats = manager.get_execution_stats()
    print(f"Stats: {stats}")

    print("✓ Buy/Sell Path Demo completed!")