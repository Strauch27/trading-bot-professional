#!/usr/bin/env python3
"""
Pre-Flight Validierung - Quantisierung + Notional-Check vor Submit

Erzwingt min_qty und min_notional.
Auto-Bump für min_notional falls möglich.
"""

import logging
from typing import Tuple, Dict, Any

from services.quantize import q_price, q_amount

logger = logging.getLogger(__name__)


def preflight(
    symbol: str,
    price: float,
    amount: float,
    f: dict
) -> Tuple[bool, Dict[str, Any]]:
    """
    Pre-Flight Validierung mit Auto-Quantisierung und min_notional-Bump.

    Args:
        symbol: Trading symbol
        price: Raw price
        amount: Raw amount
        f: Filter dict from get_filters()

    Returns:
        Tuple[bool, dict]:
            - bool: True if valid (ready to submit)
            - dict: Either error details or {"price": qp, "amount": qa}

    Example:
        >>> f = {"tick_size": 0.0001, "step_size": 0.01, "min_qty": 1, "min_notional": 5.0}
        >>> ok, data = preflight("ZBT/USDT", 0.012345, 123.456, f)
        >>> ok  # True
        >>> data  # {"price": 0.0123, "amount": 123.45}
    """
    # 1. Quantize price and amount (FLOOR)
    qp = q_price(price, f["tick_size"]) if f["tick_size"] else price
    qa = q_amount(amount, f["step_size"]) if f["step_size"] else amount

    # 2. Check min_qty
    min_qty = f.get("min_qty")
    if min_qty and qa < min_qty:
        logger.warning(
            f"[PRE_FLIGHT] {symbol} FAILED: min_qty violation "
            f"(need {min_qty}, got {qa})"
        )
        return False, {
            "reason": "min_qty",
            "need": min_qty,
            "got": qa
        }

    # 3. Check min_notional
    min_notional = f.get("min_notional")
    if min_notional and qp * qa < min_notional:
        # Try to auto-bump amount to meet min_notional
        need_amt = min_notional / max(qp, 1e-18)
        qa2 = q_amount(max(qa, need_amt), f["step_size"]) if f["step_size"] else max(qa, need_amt)

        # Verify bumped amount still meets min_notional
        if qp * qa2 < min_notional:
            logger.warning(
                f"[PRE_FLIGHT] {symbol} FAILED: min_notional violation "
                f"(need {min_notional}, got {qp * qa2:.6f} even after bump)"
            )
            return False, {
                "reason": "min_notional",
                "need": min_notional,
                "got": qp * qa2
            }

        # Auto-bump successful
        logger.info(
            f"[PRE_FLIGHT] {symbol} AUTO-BUMP: {qa:.6f} → {qa2:.6f} "
            f"to meet min_notional {min_notional}"
        )
        qa = qa2

    # 4. Success
    logger.info(
        f"[PRE_FLIGHT] {symbol} PASSED "
        f"(price={qp:.8f}, amount={qa:.6f}, notional={qp * qa:.2f})"
    )

    return True, {
        "price": qp,
        "amount": qa
    }
