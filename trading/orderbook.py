#!/usr/bin/env python3
"""
Orderbook Analysis Module

Contains functions for orderbook depth analysis:
- Top-of-book fetching (best bid/ask)
- Depth sweep for limit price calculation
- Cumulative level pricing
- Spread calculations
"""

import logging
from typing import Dict, List, Optional, Tuple

from .helpers import price_to_precision

logger = logging.getLogger(__name__)


# =================================================================================
# Orderbuch-Analyse Funktionen
# =================================================================================

def fetch_top_of_book(exchange, symbol, depth=5) -> Tuple[Optional[float], Optional[float]]:
    """
    Returns (best_bid, best_ask) from order book (top-of-book).
    Falls kein Orderbuch verfügbar ist, (None, None) zurückgeben.
    """
    try:
        ob = exchange.fetch_order_book(symbol, limit=depth)
        best_bid = ob['bids'][0][0] if ob.get('bids') else None
        best_ask = ob['asks'][0][0] if ob.get('asks') else None
        return best_bid, best_ask
    except Exception:
        return None, None


def _fetch_order_book_depth(exchange, symbol: str, limit: int = 20) -> Tuple[List, List]:
    """Liest Orderbuch bis 'limit' Levels; return (bids, asks) als [[price, qty], ...]."""
    try:
        ob = exchange.fetch_order_book(symbol, limit=limit)
        return ob.get("bids") or [], ob.get("asks") or []
    except Exception:
        return [], []


def _cumulative_level_price(levels, target_qty: float) -> Tuple[Optional[float], float]:
    """Akkumuliert Tiefe, bis target_qty gedeckt ist. Liefert (limit_price, cum_qty)."""
    cum = 0.0
    limit_px = None
    for px, q in levels:
        if px is None or q is None:
            continue
        cum += float(q)
        limit_px = float(px)
        if cum >= float(target_qty):
            break
    return limit_px, cum


def _bps(a: float, b: float) -> float:
    """Berechnet Basispunkte Differenz"""
    if a is None or b is None or b == 0:
        return float("inf")
    return (abs(a - b) / b) * 10000.0


def compute_sweep_limit_price(exchange, symbol: str, side: str, target_qty: float,
                              max_slippage_bps: float, levels: int = 20) -> Optional[Dict]:
    """
    Berechnet ein Limit, das genug Tiefe 'kreuzt', um target_qty zu füllen.
    BUY: asks aufsummieren; SELL: bids aufsummieren.
    Slippage wird ggü. Best-Ask/Bid gedeckelt.

    Returns:
        Dict with:
            - price: float
            - best_ref: float (best bid/ask reference)
            - slippage_bps: float
            - cum_qty: float
            - had_enough_depth: bool
        or None if insufficient data
    """
    side_up = str(side).upper()
    bids, asks = _fetch_order_book_depth(exchange, symbol, limit=levels)
    best_bid = bids[0][0] if bids else None
    best_ask = asks[0][0] if asks else None

    if side_up == "BUY":
        limit_px, cum_qty = _cumulative_level_price(asks, target_qty)
        ref = best_ask
        if limit_px is None:
            return None
        max_px = ref * (1.0 + max_slippage_bps / 10000.0) if ref else limit_px
        px = min(limit_px, max_px)
        had_enough = cum_qty >= target_qty
    else:
        limit_px, cum_qty = _cumulative_level_price(bids, target_qty)
        ref = best_bid
        if limit_px is None:
            return None
        min_px = ref * (1.0 - max_slippage_bps / 10000.0) if ref else limit_px
        px = max(limit_px, min_px)
        had_enough = cum_qty >= target_qty

    px = price_to_precision(exchange, symbol, px)
    return {
        "price": float(px),
        "best_ref": float(ref) if ref else None,
        "slippage_bps": _bps(px, ref) if ref else None,
        "cum_qty": float(cum_qty),
        "had_enough_depth": bool(had_enough),
    }


def compute_limit_buy_price_from_book(exchange, symbol, reference_last, aggressiveness=0.25) -> float:
    """
    Nutze Best-Ask + leichte Aggressivität, um Fills zu erleichtern.
    aggressiveness in %, z.B. 0.25 → 0.25% über Best-Bid/Ask.
    """
    best_bid, best_ask = fetch_top_of_book(exchange, symbol, depth=5)
    if best_ask:
        px = best_ask * (1.0 + aggressiveness / 100.0)  # etwas über Ask, damit IOC/Fill wahrscheinlicher
    elif best_bid:
        px = best_bid * (1.0 + aggressiveness / 100.0)  # kein Ask? → über Bid kaufen
    else:
        px = reference_last  # Fallback
    return price_to_precision(exchange, symbol, px)
