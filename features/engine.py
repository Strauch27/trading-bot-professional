#!/usr/bin/env python3
"""
Feature Engine - Technical Indicator Computation

Computes features from price observations for market snapshots.
Minimal implementation with ATR-like volatility estimate.
"""

from typing import Dict, Iterable, Optional, Tuple


def _atr_like(view: Iterable[Tuple[float, float]]) -> Optional[float]:
    """
    Compute ATR-like volatility from price observations.

    Args:
        view: Iterable of (timestamp, price) tuples

    Returns:
        Average absolute price change or None if insufficient data
    """
    seq = list(view)
    if len(seq) < 2:
        return None

    # Compute absolute price changes
    diffs = [abs(seq[i][1] - seq[i-1][1]) for i in range(1, len(seq))]

    return sum(diffs) / len(diffs) if diffs else None


def compute(view: Iterable[Tuple[float, float]]) -> Dict[str, Optional[float]]:
    """
    Compute all features from price view.

    Args:
        view: Iterable of (timestamp, price) tuples

    Returns:
        Dict with computed features
    """
    return {
        "atr": _atr_like(view)
    }
