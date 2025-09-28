from dataclasses import dataclass
from typing import Tuple
from config import FEE_RATE, MIN_ORDER_BUFFER, ALLOW_AUTO_SIZE_UP, MAX_AUTO_SIZE_UP_BPS, MAX_AUTO_SIZE_UP_ABS_USDT
from utils import (
    get_symbol_limits,
    quantize_price,
    quantize_amount,
    compute_affordable_qty,
)

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
    def __init__(self, exchange, fee_rate: float = FEE_RATE, min_order_buffer: float = MIN_ORDER_BUFFER):
        self.exchange = exchange
        self.fee_rate = fee_rate
        self.min_order_buffer = min_order_buffer

    def limits(self, symbol: str) -> SymbolLimits:
        lim = get_symbol_limits(self.exchange, symbol)
        return SymbolLimits(
            price_step=lim.get('price_step', 0.00000001),
            amount_step=lim.get('amount_step', 0.00000001),
            min_amount=lim.get('min_amount', 0.0),
            min_cost=lim.get('min_cost', 5.1),
        )

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