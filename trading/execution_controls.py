#!/usr/bin/env python3
"""
Dynamic Execution Controls

Provides adaptive slippage and spread constraints based on:
- Market volatility (via ATR or price variance)
- Orderbook depth
- Symbol characteristics (e.g., major vs. altcoin)
- Recent fill success rates

This allows the trading system to automatically adjust execution parameters
to improve fill rates while protecting against excessive slippage.
"""

import logging
from typing import Any, Dict, Optional

import config

logger = logging.getLogger(__name__)


def determine_entry_constraints(
    symbol: str,
    side: str,
    market_data: Dict[str, Any],
    recent_stats: Optional[Dict[str, Any]] = None
) -> Dict[str, Any]:
    """
    Determine dynamic entry constraints for a trading intent.

    Args:
        symbol: Trading pair (e.g., "BTC/USDT")
        side: "buy" or "sell"
        market_data: Market data dict with orderbook, volatility, etc.
        recent_stats: Recent execution statistics (fill rates, etc.)

    Returns:
        Dict with:
            - max_slippage_bps: Maximum allowed slippage in basis points
            - max_spread_bps: Maximum allowed spread in basis points
            - reason: Explanation for the constraints chosen
    """
    # Extract market metrics
    orderbook = market_data.get("orderbook", {})
    bid_depth_usdt = orderbook.get("bid_depth_usdt", 0)
    ask_depth_usdt = orderbook.get("ask_depth_usdt", 0)
    spread_bps = orderbook.get("spread_bps", 0)

    # Volatility metrics (if available)
    volatility = market_data.get("volatility", {})
    atr_pct = volatility.get("atr_pct", 0)  # ATR as % of price
    volatility.get("variance_1h", 0)

    # Start with config defaults
    max_slippage_bps = getattr(config, 'MAX_SLIPPAGE_BPS_ENTRY', 30)
    max_spread_bps = getattr(config, 'MAX_SPREAD_BPS_ENTRY', 20)
    reason = "default_config"

    # Rule 1: Low depth -> increase tolerance
    depth = bid_depth_usdt if side == "buy" else ask_depth_usdt
    if depth < 500:  # Less than $500 depth
        max_slippage_bps = min(50, max_slippage_bps * 1.5)
        max_spread_bps = min(40, max_spread_bps * 1.5)
        reason = "low_orderbook_depth"
        logger.debug(f"{symbol} low depth ({depth:.0f} USDT) - increased tolerance")

    # Rule 2: High volatility -> increase tolerance
    if atr_pct > 5.0:  # ATR > 5% of price
        max_slippage_bps = min(60, max_slippage_bps * 1.3)
        max_spread_bps = min(50, max_spread_bps * 1.3)
        reason = "high_volatility"
        logger.debug(f"{symbol} high volatility (ATR {atr_pct:.1f}%) - increased tolerance")

    # Rule 3: Wide spread -> increase spread tolerance
    if spread_bps > max_spread_bps:
        max_spread_bps = min(100, spread_bps * 1.2)  # Allow 20% above current spread
        reason = "wide_current_spread"
        logger.debug(f"{symbol} wide spread ({spread_bps:.0f}bps) - adjusted tolerance")

    # Rule 4: Recent fill failures -> increase tolerance
    if recent_stats:
        fill_rate = recent_stats.get("fill_rate_24h", 1.0)
        if fill_rate < 0.3:  # Less than 30% fills
            max_slippage_bps = min(80, max_slippage_bps * 1.5)
            max_spread_bps = min(60, max_spread_bps * 1.5)
            reason = "low_recent_fill_rate"
            logger.info(f"{symbol} low fill rate ({fill_rate:.1%}) - increased tolerance")

    return {
        "max_slippage_bps": int(max_slippage_bps),
        "max_spread_bps": int(max_spread_bps),
        "reason": reason,
        "metrics": {
            "depth_usdt": depth,
            "spread_bps": spread_bps,
            "atr_pct": atr_pct
        }
    }


def get_adaptive_premium_bps(
    symbol: str,
    attempt: int,
    market_conditions: Dict[str, Any]
) -> int:
    """
    Calculate adaptive premium for limit orders based on attempt number and conditions.

    Args:
        symbol: Trading pair
        attempt: Current attempt number (1-indexed)
        market_conditions: Market data dict

    Returns:
        Premium in basis points to add to limit price
    """
    # Base premiums from config
    base_premium = 10  # 10bps base

    # Escalate with attempts
    escalation_factor = 1.0 + (attempt - 1) * 0.5  # 50% increase per attempt

    # Adjust for volatility
    volatility = market_conditions.get("volatility", {})
    atr_pct = volatility.get("atr_pct", 0)

    if atr_pct > 5.0:
        escalation_factor *= 1.3  # 30% boost for high volatility

    premium_bps = int(base_premium * escalation_factor)

    # Cap at reasonable maximum
    return min(premium_bps, 100)
