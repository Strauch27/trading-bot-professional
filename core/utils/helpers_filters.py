# helpers_filters.py
# Zentrale, getestete Utilities für Preis-/Mengenrundung und Filter-Compliance.

from dataclasses import dataclass
from typing import Dict, Tuple
import math

@dataclass
class ExchangeFilters:
    tickSize: float
    stepSize: float
    minNotional: float
    minQty: float = 0.0

def _round_to_tick(price: float, tick: float) -> float:
    """Rundet Preis zum nächsten erlaubten Tick (nearest rounding)"""
    if tick <= 0:
        return price
    return round(price / tick) * tick

def round_price_to_tick(price, tick):
    """Nearest tick (Maker: ggf. 1 Tick unter Bid/über Ask setzen)"""
    if tick <= 0:
        return price
    rounded = round(price / tick) * tick
    # Guard: verhindere 0 durch Rundung, wenn tick > price
    return rounded if rounded > 0 else tick

def _ceil_to_step(x, step):
    """Rundet Wert zum nächsthöheren erlaubten Step (CEILING)"""
    if step <= 0:
        return x
    return math.ceil(x / step) * step

def _floor_to_step(qty: float, step: float) -> float:
    """Rundet Menge zum nächstniedrigeren erlaubten Step (FLOOR)"""
    if step <= 0:
        return qty
    return math.floor(qty / step) * step

def size_buy_from_quote(quote_budget, best_ask, tickSize, stepSize, minNotional):
    """
    Robuste BUY-Order-Sizing aus Quote-Budget mit fail-safe Logik.
    Enthält V9_3-style dynamisches Min-Notional-Sizing mit Puffer.

    Args:
        quote_budget: Verfügbares Quote-Budget
        best_ask: Aktueller bester Ask-Preis
        tickSize: Preis-Inkrement
        stepSize: Mengen-Inkrement
        minNotional: Minimaler Notional-Wert

    Returns:
        (price, qty, notional, audit): Order-Parameter mit Audit-Trail
    """
    # Dynamic Min-Notional sizing (wie V9_3) - 2% Puffer über Mindestnotional
    buffer = 1.02
    required_notional = max(quote_budget, minNotional * buffer)

    px = round_price_to_tick(best_ask, tickSize)
    # Guard gegen 0-Preis
    if px <= 0:
        px = max(best_ask or 0.0, tickSize) or 1e-9

    # Robuste Größenberechnung mit required_notional
    raw_qty = required_notional / max(px, 1e-12)
    qty = _ceil_to_step(raw_qty, stepSize)
    if qty <= 0:
        qty = stepSize  # nie 0
    notional = px * qty

    audit = {
        "sizing": "dynamic_min_notional",
        "buffer": buffer,
        "required_notional": required_notional,
        "original_quote_budget": quote_budget,
        "minNotional": minNotional,
        "tickSize": tickSize,
        "stepSize": stepSize,
        "price_adjusted": px != best_ask
    }
    return px, qty, notional, audit

def size_sell_from_base(base_qty_available: float, best_ask: float, f: ExchangeFilters) -> Tuple[float, float, float, dict]:
    """
    Bestimmt SELL-Order Parameter aus Base-Inventory.

    Args:
        base_qty_available: Verfügbare Menge in Base-Currency
        best_ask: Aktueller bester Ask-Preis
        f: Exchange Filter-Definitionen

    Returns:
        (price, qty, notional, audit): Berechnete Order-Parameter mit Audit-Info

    Logik:
        - Preis wird maker-freundlich über Ask gesetzt (+ tickSize)
        - Preis wird auf tickSize gerundet (nearest)
        - Menge wird auf stepSize gerundet (FLOOR - Inventar-begrenzt)
        - minNotional/minQty Checks (Info only, keine Hebung über Inventar)
    """
    assert base_qty_available > 0 and best_ask > 0, "Menge und Preis müssen > 0 sein."

    # Maker-freundlicher Preis (über Ask)
    price = best_ask + f.tickSize
    price = _round_to_tick(price, f.tickSize)

    # FLOOR-Rundung auf stepSize (Inventar-begrenzt)
    qty = _floor_to_step(base_qty_available, f.stepSize)
    notional = price * qty

    audit = {
        "method": "size_sell_from_base",
        "inputs": {
            "base_qty_available": base_qty_available,
            "best_ask": best_ask,
            "filters": f.__dict__
        },
        "calculations": {
            "maker_price": best_ask + f.tickSize,
            "price_rounded": price,
            "qty_floored": qty,
            "notional_final": notional
        },
        "checks": {
            "meets_min_qty": qty >= f.minQty if f.minQty else True,
            "meets_min_notional": notional >= f.minNotional
        },
        "results": {
            "price": price,
            "qty": qty,
            "notional": notional
        }
    }

    return price, qty, notional, audit

def post_only_guard_buy(price: float, best_bid: float, f: ExchangeFilters) -> bool:
    """Prüft ob BUY-Order mit Post-Only sicher ist (price <= best_bid - tickSize)"""
    return price <= (best_bid - f.tickSize)

def post_only_guard_sell(price: float, best_ask: float, f: ExchangeFilters) -> bool:
    """Prüft ob SELL-Order mit Post-Only sicher ist (price >= best_ask + tickSize)"""
    return price >= (best_ask + f.tickSize)

def validate_order_params(symbol: str, side: str, price: float, qty: float, f: ExchangeFilters) -> Tuple[bool, str, dict]:
    """
    Umfassende Validierung von Order-Parametern gegen Exchange-Filter.

    Returns:
        (is_valid, reason, details): Validierungsergebnis
    """
    details = {
        "symbol": symbol,
        "side": side,
        "price": price,
        "qty": qty,
        "filters": f.__dict__
    }

    # Qty > 0 Check
    if qty <= 0:
        return False, "qty_zero_or_negative", details

    # Price > 0 Check
    if price <= 0:
        return False, "price_zero_or_negative", details

    # minQty Check
    if f.minQty and qty < f.minQty:
        details["min_qty_violation"] = {"required": f.minQty, "actual": qty}
        return False, "below_min_qty", details

    # minNotional Check
    notional = price * qty
    if notional < f.minNotional:
        details["min_notional_violation"] = {"required": f.minNotional, "actual": notional}
        return False, "below_min_notional", details

    # Step/Tick Precision Checks
    if f.stepSize > 0:
        remainder = qty % f.stepSize
        if remainder > 1e-12:  # Floating point tolerance
            details["step_size_violation"] = {"step": f.stepSize, "remainder": remainder}
            return False, "invalid_step_size", details

    if f.tickSize > 0:
        remainder = price % f.tickSize
        if remainder > 1e-12:  # Floating point tolerance
            details["tick_size_violation"] = {"tick": f.tickSize, "remainder": remainder}
            return False, "invalid_tick_size", details

    details["notional"] = notional
    return True, "valid", details

def apply_exchange_filters(symbol: str, side: str, price: float, qty: float,
                          market_data: dict, force_compliance: bool = True) -> dict:
    """
    Wendet Exchange-Filter auf Order-Parameter an und korrigiert sie falls nötig.

    Args:
        symbol: Trading-Symbol
        side: "BUY" oder "SELL"
        price: Gewünschter Preis
        qty: Gewünschte Menge
        market_data: Dict mit 'filters' und 'book' Daten
        force_compliance: Ob Parameter automatisch korrigiert werden sollen

    Returns:
        dict mit korrigierten Parametern und Audit-Trail
    """
    filters = market_data.get("filters", {})
    book = market_data.get("book", {})

    f = ExchangeFilters(
        tickSize=float(filters.get("tickSize", 0.01)),
        stepSize=float(filters.get("stepSize", 0.001)),
        minNotional=float(filters.get("minNotional", 10.0)),
        minQty=float(filters.get("minQty", 0.0))
    )

    # Original-Parameter merken
    original = {"price": price, "qty": qty}

    if force_compliance:
        # Preis auf Tick runden
        price = _round_to_tick(price, f.tickSize)

        # Menge auf Step runden (abhängig von Side)
        if side.upper() == "BUY":
            qty = _ceil_to_step(qty, f.stepSize)
        else:
            qty = _floor_to_step(qty, f.stepSize)

        # minQty enforcement
        if f.minQty and qty < f.minQty:
            qty = _ceil_to_step(f.minQty, f.stepSize)

        # minNotional enforcement (nur bei BUY)
        if side.upper() == "BUY":
            notional = price * qty
            if notional < f.minNotional:
                qty = _ceil_to_step(f.minNotional / price, f.stepSize)

    # Final validation
    is_valid, reason, validation_details = validate_order_params(symbol, side, price, qty, f)

    return {
        "symbol": symbol,
        "side": side,
        "price": price,
        "qty": qty,
        "notional": price * qty,
        "filters_applied": force_compliance,
        "is_valid": is_valid,
        "validation_reason": reason,
        "validation_details": validation_details,
        "corrections": {
            "price_changed": abs(price - original["price"]) > 1e-12,
            "qty_changed": abs(qty - original["qty"]) > 1e-12,
            "original": original
        },
        "audit": {
            "timestamp": int(time.time() * 1000),
            "filters": f.__dict__,
            "market_data": market_data
        }
    }

# Convenience function für direkten Import
def create_filters_from_market(market_info: dict) -> ExchangeFilters:
    """Erstellt ExchangeFilters aus CCXT Market-Info.
    WICHTIG: CCXT liefert precision.price/amount in der Regel als Tick-/Step-**Größe** (z. B. 0.0001),
    nicht als Anzahl Dezimalstellen."""
    limits = market_info.get("limits", {}) or {}
    precision = market_info.get("precision", {}) or {}

    def _to_increment(val, fallback):
        try:
            v = float(val)
            if v <= 0:
                return fallback
            # Wenn v < 1 ⇒ bereits Inkrement; wenn v >= 1 ⇒ als Dezimalstellen interpretieren
            return v if v < 1 else (10.0 ** (-int(v)))
        except Exception:
            return fallback

    tick_size = _to_increment(precision.get("price"), 0.01)
    step_size = _to_increment(precision.get("amount"), 0.001)
    min_notional = float((limits.get("cost") or {}).get("min") or 10.0)
    min_qty = float((limits.get("amount") or {}).get("min") or 0.0)

    return ExchangeFilters(
        tickSize=tick_size,
        stepSize=step_size,
        minNotional=min_notional,
        minQty=min_qty,
    )

import time