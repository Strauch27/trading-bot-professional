#!/usr/bin/env python3
"""
Trading Helper Functions

Contains utility functions for trading operations:
- Minimum cost calculations
- Amount/Price precision handling
- Position sizing helpers
- Balance queries
- Base currency extraction
"""

import re
import logging
from typing import Optional
from config import min_order_buffer, DUST_FACTOR
from utils import get_symbol_limits, floor_to_step

logger = logging.getLogger(__name__)


# =================================================================================
# ClientOrderId Sanitizer
# =================================================================================
def sanitize_coid(coid: str) -> str:
    """Stellt sicher dass clientOrderId MEXC-konform ist (max 32 Zeichen, nur [0-9a-zA-Z_-])"""
    coid = re.sub(r'[^0-9A-Za-z_-]', '', str(coid))
    return coid[:32]


# =================================================================================
# Helper-Funktionen für Trading
# =================================================================================
def compute_min_cost(exchange, symbol: str) -> float:
    """Berechnet Minimum-Cost mit Buffer"""
    m = exchange.markets.get(symbol, {})
    limits = m.get('limits', {}) or {}
    cost_limits = limits.get('cost', {}) or {}
    raw_min = cost_limits.get('min') or 5.0
    return float(raw_min) * float(min_order_buffer)


def size_limit_sell(exchange, symbol: str, price: float, max_amount: float) -> float:
    """Ermittelt die Mindestmenge für Sell, damit quote_value >= minCost*buffer liegt."""
    min_cost = compute_min_cost(exchange, symbol)
    min_amt = min_cost / float(price) if price > 0 else 0.0
    # auf Marktspezifikation runden
    amt = amount_to_precision(exchange, symbol, max(min_amt, 0.0))
    # nur gültige Mengen >= min_amt zurückgeben, sonst 0
    return amt if max_amount >= amt and amt > 0 else 0.0


def size_limit_buy(exchange, symbol: str, price: float, max_quote: float) -> float:
    """
    max_quote = verfügbares USDT-Budget.
    Stellt sicher, dass nach amount_to_precision der Orderwert >= minCost liegt.
    """
    min_cost = compute_min_cost(exchange, symbol)
    if max_quote < min_cost:
        return 0.0

    amt = max_quote / float(price)
    amt = amount_to_precision(exchange, symbol, amt)

    # Nach-Rundung prüfen
    val = amt * float(price)
    if val + 1e-12 < min_cost:
        # minimal anheben, wieder präzisieren
        delta = (min_cost - val) / float(price)
        amt = amount_to_precision(exchange, symbol, amt + delta * 1.05)
    return amt


def base_currency(symbol: str) -> str:
    """Extrahiert Basis-Währung aus Symbol"""
    try:
        return symbol.split('/')[0]
    except Exception:
        return symbol


def amount_to_precision(exchange, symbol, amount):
    """Konvertiert Amount zu Exchange-Präzision"""
    try:
        return float(exchange.amount_to_precision(symbol, amount))
    except Exception:
        try:
            return float(f"{amount:.8f}")
        except Exception:
            return amount


def price_to_precision(exchange, symbol, price):
    """Konvertiert Preis zu Exchange-Präzision"""
    try:
        return float(exchange.price_to_precision(symbol, price))
    except Exception:
        try:
            return float(f"{price:.8f}")
        except Exception:
            return price


def get_free(exchange, currency: str) -> float:
    """Holt freien Bestand einer Währung"""
    try:
        from utils import with_backoff
        bal = with_backoff(exchange.fetch_balance)
        # Prefer standardized structure
        free_map = bal.get("free", {})
        if isinstance(free_map, dict) and currency in free_map:
            return float(free_map.get(currency) or 0.0)
        # Fallback to raw "info" structure
        info = bal.get("info", {})
        balances = info.get("balances", [])
        for b in balances:
            if b.get("asset") == currency:
                return float(b.get("free", 0.0) or 0.0)
    except Exception:
        pass
    return 0.0


def compute_safe_sell_amount(exchange, symbol, desired_amount, held_assets, preise,
                            consider_order_remaining=True):
    """Compute safe sellable amount with proper minimum checks"""

    remaining = float(desired_amount or 0.0)

    # Get current order remaining if needed
    if consider_order_remaining:
        data = held_assets.get(symbol, {})
        oid = data.get("active_order_id")
        if oid:
            try:
                st = exchange.fetch_order(oid, symbol)
                remaining = min(remaining, float(st.get("remaining", remaining) or remaining))
            except Exception:
                pass

    # Get actual free balance
    free_base = get_free(exchange, base_currency(symbol))
    amt = min(remaining, free_base) * DUST_FACTOR

    # Get symbol limits
    limits = get_symbol_limits(exchange, symbol)

    # Round to step
    precise_amt = floor_to_step(amt, limits['amount_step'])

    # Check minimum order value
    current_price = preise.get(symbol, 0)

    # Fallback to ticker bid if no price in cache
    if current_price <= 0:
        try:
            t = exchange.fetch_ticker(symbol)
            current_price = float(t.get("bid") or t.get("last") or 0.0)
        except Exception:
            current_price = 0.0

    if current_price > 0:
        order_value = precise_amt * current_price
        min_required = limits['min_cost'] * min_order_buffer  # Mit Buffer

        if order_value < min_required:
            logger.debug(f"Sell amount für {symbol} unter Minimum: {order_value:.2f} < {min_required:.2f}",
                        extra={"event_type": "SELL_BELOW_MINIMUM",
                              "symbol": symbol,
                              "value": order_value,
                              "min_required": min_required})
            return 0  # Cannot sell

    return precise_amt


# Export legacy names for backward compatibility
_sanitize_coid = sanitize_coid
_base_currency = base_currency
_amount_to_precision = amount_to_precision
_price_to_precision = price_to_precision
_get_free = get_free
