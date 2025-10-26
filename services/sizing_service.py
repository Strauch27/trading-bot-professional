import logging
from dataclasses import dataclass
from typing import Optional, Tuple

from config import (
    ALLOW_AUTO_SIZE_UP,
    FEE_RATE,
    MAX_AUTO_SIZE_UP_ABS_USDT,
    MAX_AUTO_SIZE_UP_BPS,
    MAX_PER_SYMBOL_USD,
    MIN_ORDER_BUFFER,
    MIN_SLOT_USDT,
    POSITION_SIZE_USDT,
)
from core.utils import (
    compute_affordable_qty,
    get_symbol_limits,
    quantize_amount,
    quantize_price,
)

logger = logging.getLogger(__name__)

@dataclass
class SymbolLimits:
    price_step: float
    amount_step: float
    min_amount: float
    min_cost: float

class SizingService:
    """
    Zentrale Präzisions- & MinNotional-Logik für BUY & SELL.
    Nutzt deine vorhandenen Utils (keine neue Börsenlogik).
    """
    def __init__(self, exchange, fee_rate: float = FEE_RATE, min_order_buffer: float = MIN_ORDER_BUFFER, portfolio_manager=None):
        self.exchange = exchange
        self.fee_rate = fee_rate
        self.min_order_buffer = min_order_buffer
        self.portfolio_manager = portfolio_manager

    def limits(self, symbol: str) -> SymbolLimits:
        lim = get_symbol_limits(self.exchange, symbol)
        return SymbolLimits(
            price_step=lim.get('price_step', 0.00000001),
            amount_step=lim.get('amount_step', 0.00000001),
            min_amount=lim.get('min_amount', 0.0),
            min_cost=lim.get('min_cost', 5.1),
        )

    def calculate_position_size(self, symbol: str, current_price: float, usdt_balance: float) -> Optional[float]:
        """
        Berechnet die optimale Positionsgröße unter Berücksichtigung von:
        - Konfiguriertem POSITION_SIZE_USDT
        - Verfügbarem Budget (inklusive Reservierungen)
        - MAX_PER_SYMBOL_USD Limit
        - Bestehender Exposure für das Symbol
        - MIN_SLOT_USDT als Minimum

        Args:
            symbol: Trading pair (z.B. "BTC/USDT")
            current_price: Aktueller Preis
            usdt_balance: Totales USDT Balance (wird ignoriert, wir nutzen portfolio)

        Returns:
            float: Quote budget in USDT oder None wenn nicht genug Budget
        """
        if not self.portfolio_manager:
            # Fallback wenn kein portfolio_manager verfügbar (should not happen)
            logger.warning(f"No portfolio_manager available for {symbol}, using simplified sizing")
            return min(usdt_balance * 0.1, 100.0)

        # 1. Hole verfügbares Budget (nach Reservierungen)
        available_budget = self.portfolio_manager.get_free_usdt()

        if available_budget < MIN_SLOT_USDT:
            logger.info(f"Insufficient budget for {symbol}: {available_budget:.2f} < {MIN_SLOT_USDT:.2f} USDT",
                       extra={'event_type': 'INSUFFICIENT_BUDGET', 'symbol': symbol,
                             'available': available_budget, 'min_required': MIN_SLOT_USDT})
            return None

        # 2. Start mit konfiguriertem POSITION_SIZE_USDT
        target_size = POSITION_SIZE_USDT

        # 3. Prüfe MAX_PER_SYMBOL_USD Limit
        existing_exposure = self.portfolio_manager.get_symbol_exposure_usdt(symbol)
        remaining_symbol_capacity = MAX_PER_SYMBOL_USD - existing_exposure

        if remaining_symbol_capacity < MIN_SLOT_USDT:
            logger.info(f"Symbol capacity exhausted for {symbol}: exposure={existing_exposure:.2f}, max={MAX_PER_SYMBOL_USD:.2f}",
                       extra={'event_type': 'SYMBOL_CAPACITY_EXHAUSTED', 'symbol': symbol,
                             'existing_exposure': existing_exposure, 'max_per_symbol': MAX_PER_SYMBOL_USD})
            return None

        # 4. Begrenze auf verfügbare Symbol-Kapazität
        target_size = min(target_size, remaining_symbol_capacity)

        # 5. Begrenze auf verfügbares Budget
        target_size = min(target_size, available_budget)

        # 6. Finale Prüfung gegen MIN_SLOT_USDT
        if target_size < MIN_SLOT_USDT:
            logger.info(f"Calculated position size too small for {symbol}: {target_size:.2f} < {MIN_SLOT_USDT:.2f}",
                       extra={'event_type': 'POSITION_SIZE_TOO_SMALL', 'symbol': symbol,
                             'calculated_size': target_size, 'min_slot': MIN_SLOT_USDT})
            return None

        logger.debug(f"Position size calculated for {symbol}: {target_size:.2f} USDT "
                    f"(available: {available_budget:.2f}, exposure: {existing_exposure:.2f}/{MAX_PER_SYMBOL_USD:.2f})",
                    extra={'event_type': 'POSITION_SIZE_CALCULATED', 'symbol': symbol,
                          'position_size': target_size, 'available_budget': available_budget,
                          'existing_exposure': existing_exposure})

        return target_size

    def affordable_buy_qty(self, symbol: str, price: float, quote_usdt: float) -> Tuple[float, float, float, dict | None]:
        """
        Liefert (qty, est_cost, min_required, reason)
        – Wrap um compute_affordable_qty mit deinen Defaults
        """
        return compute_affordable_qty(
            self.exchange,
            symbol,
            quote_usdt,
            price,
            fee_rate=self.fee_rate,
            slippage_headroom=0.0,
            min_order_buffer=self.min_order_buffer,
        )

    def validate_sell_qty(self, symbol: str, qty: float, price: float) -> Tuple[float, bool]:
        """
        Rundet Menge/Preis auf Exchange-Precision & prüft MinNotional.
        """
        qty_q = quantize_amount(symbol, qty, self.exchange)
        price_q = quantize_price(symbol, price, self.exchange)
        notional = qty_q * price_q * (1 - self.fee_rate)
        ok = notional >= self.limits(symbol).min_cost
        return qty_q, ok

    def maybe_autosize(self, symbol: str, price: float, qty: float, notional: float):
        """
        Hebt die Menge minimal an, wenn wir knapp unter minNotional landen.
        Rückgabe: (qty_adj, notional_adj, reason|None)
        """
        lim = self.limits(symbol)
        if not ALLOW_AUTO_SIZE_UP:
            return qty, notional, None

        min_cost = lim.min_cost
        if notional >= min_cost:
            return qty, notional, None

        shortfall = min_cost - notional
        cap_abs = MAX_AUTO_SIZE_UP_ABS_USDT
        cap_rel = notional * (MAX_AUTO_SIZE_UP_BPS / 10_000.0)
        add = min(shortfall, cap_abs, cap_rel)

        if add <= 0:
            return qty, notional, None

        # auf Schrittweite runden
        qty_new = quantize_amount(symbol, (notional + add) / price, self.exchange)
        notional_new = qty_new * price * (1 + self.fee_rate)
        if notional_new >= min_cost:
            return qty_new, notional_new, f"auto_upsize:+{add:.4f}USDT"
        return qty, notional, None
