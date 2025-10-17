#!/usr/bin/env python3
"""
Market Snapshot Builder - Versioned MarketSnapshot Construction

Builds versioned MarketSnapshot objects from raw market data.
Single source of truth for all market data consumers.
"""

from typing import Dict, Optional


def build(
    symbol: str,
    ts: float,
    last: float,
    bid: float,
    ask: float,
    windows: Dict[str, Optional[float]],
    features: Dict[str, Optional[float]],
    spread_bps: float
) -> Dict:
    """
    Build a versioned MarketSnapshot.

    Args:
        symbol: Trading symbol
        ts: Timestamp
        last: Last trade price
        bid: Best bid price
        ask: Best ask price
        windows: Dict with peak/trough from RollingWindowManager
        features: Dict with computed features
        spread_bps: Bid-ask spread in basis points

    Returns:
        Versioned MarketSnapshot dict
    """
    # Calculate mid price
    mid = (bid + ask) / 2 if (bid and ask and bid > 0 and ask > 0) else last

    # V9_3: Calculate drop_pct from anchor (not peak)
    anchor = windows.get("anchor")
    peak = windows.get("peak")
    drop_pct = None
    if anchor is not None and anchor > 0:
        # V9_3 formula: drop_pct = 100.0 * (last / anchor - 1.0)
        drop_pct = (last - anchor) / anchor * 100.0
    elif peak is not None and peak > 0:
        # Fallback to peak if anchor not available
        drop_pct = (last - peak) / peak * 100.0

    # Calculate rise_pct from trough
    trough = windows.get("trough")
    rise_pct = None
    if trough is not None and trough > 0:
        rise_pct = (last - trough) / trough * 100.0

    return {
        "v": 1,  # Schema version
        "ts": ts,
        "symbol": symbol,
        "price": {
            "last": last,
            "bid": bid,
            "ask": ask,
            "mid": mid
        },
        "liquidity": {
            "spread_bps": spread_bps,
            "depth_usd": None,  # Can be populated if orderbook depth is available
            "imbalance": None   # Can be computed from bid/ask depth
        },
        "windows": {
            "anchor": anchor,  # V9_3: Price reference for drop trigger
            "peak": peak,
            "trough": trough,
            "drop_pct": drop_pct,
            "rise_pct": rise_pct
        },
        "features": features,
        "state": {
            "trend": "unknown",      # Can be enhanced with trend detection
            "vol_regime": "unknown",  # Can be enhanced with volatility regime
            "liq_grade": "U"         # Liquidity grade (U = unknown)
        },
        "flags": {
            "anomaly": False,  # Can detect price anomalies
            "stale": False     # Can detect stale data
        }
    }
