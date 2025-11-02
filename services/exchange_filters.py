#!/usr/bin/env python3
"""
Börsenfilter - Einheitliche Quelle für tick_size, step_size, min_qty, min_notional

Cacht Filter pro Symbol für Performance.
Unterstützt MEXC-spezifische Filter und CCXT-Fallbacks.
"""

import logging
from typing import Dict, Optional

logger = logging.getLogger(__name__)

# Cache: symbol -> filter dict
_cache: Dict[str, dict] = {}


def get_filters(exchange, symbol: str) -> dict:
    """
    Lade und cache Börsenfilter für Symbol.

    Args:
        exchange: CCXT exchange instance
        symbol: Trading symbol (e.g., "BTC/USDT")

    Returns:
        Dict mit:
            - tick_size: Preis-Granularität (float)
            - step_size: Mengen-Granularität (float)
            - min_qty: Minimale Menge (float or None)
            - min_notional: Minimaler Gegenwert in Quote (float or None)

    Example:
        >>> filters = get_filters(exchange, "ZBT/USDT")
        >>> filters["tick_size"]  # 0.0001
        >>> filters["min_notional"]  # 1.0
    """
    if symbol in _cache:
        return _cache[symbol]

    try:
        m = exchange.market(symbol)
    except Exception as e:
        logger.error(f"Failed to load market {symbol}: {e}")
        return _empty_filters()

    tick_size = None
    step_size = None
    min_qty = m.get("limits", {}).get("amount", {}).get("min")
    min_notional = m.get("limits", {}).get("cost", {}).get("min")

    # Parse exchange-specific filters (MEXC, Binance, etc.)
    info = m.get("info", {})
    for f in info.get("filters", []):
        f_type = f.get("filterType") or f.get("type")

        # Price filter
        if f_type in ("PRICE_FILTER", "PRICE"):
            ts = f.get("tickSize")
            if ts:
                tick_size = float(ts)

        # Lot size filter
        if f_type in ("LOT_SIZE", "MARKET_LOT_SIZE", "LOT"):
            ss = f.get("stepSize")
            mq = f.get("minQty")
            if ss:
                step_size = float(ss)
            if mq:
                min_qty = float(mq) or min_qty

        # Notional filter
        if f_type in ("MIN_NOTIONAL", "NOTIONAL"):
            mn = f.get("minNotional")
            if mn:
                min_notional = float(mn) or min_notional

    # Fallback: use CCXT precision if filters not available
    if tick_size is None:
        p = m.get("precision", {}).get("price")
        if p is not None:
            tick_size = 10 ** (-p)
        else:
            tick_size = 0.0

    if step_size is None:
        a = m.get("precision", {}).get("amount")
        if a is not None:
            step_size = 10 ** (-a)
        else:
            step_size = 0.0

    filters = {
        "tick_size": tick_size or 0.0,
        "step_size": step_size or 0.0,
        "min_qty": min_qty,
        "min_notional": min_notional,
    }

    _cache[symbol] = filters
    logger.debug(f"Loaded filters for {symbol}: {filters}")

    return filters


def _empty_filters() -> dict:
    """Return empty filters dict for error fallback."""
    return {
        "tick_size": 0.0,
        "step_size": 0.0,
        "min_qty": None,
        "min_notional": None,
    }


def clear_cache():
    """Clear filter cache (for testing or market structure changes)."""
    global _cache
    _cache.clear()
    logger.info("Filter cache cleared")
