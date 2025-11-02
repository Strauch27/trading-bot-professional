#!/usr/bin/env python3
"""
Wait-Fill Policy - Deterministisch ohne Rehydrations-Loop

Klare Timeouts, Cancel-Policy für Partial-Fills.
Kein Start ohne order_id.
"""

import logging
import time
from typing import Optional, Dict, Any

from core.logging.events import emit

logger = logging.getLogger(__name__)

# Configuration
WAIT_FILL_TIMEOUT_S = 30
POLL_INTERVAL_S = 0.5
PARTIAL_MAX_AGE_S = 10


def wait_for_fill(
    exchange,
    symbol: str,
    order_id: str
) -> Optional[Dict[str, Any]]:
    """
    Warte auf Order-Fill mit Timeout und Cancel-Policy.

    Args:
        exchange: CCXT exchange instance
        symbol: Trading symbol
        order_id: Order ID (MUST NOT BE None)

    Returns:
        Order dict if filled, None if canceled/timeout

    Policy:
        - Timeout after 30s → cancel
        - Partial fill stuck >10s → cancel
        - Canceled/Rejected/Expired → return None
        - Filled → return order

    Example:
        >>> order = wait_for_fill(exchange, "BTC/USDT", "1234567")
        >>> order["status"]  # "closed"
        >>> order["filled"]  # 0.05
    """
    # Critical: Abort if no order_id
    if not order_id:
        emit("buy_aborted", symbol=symbol, reason="no_order_id")
        logger.error(f"[WAIT_FILL] {symbol} ABORTED: no order_id")
        return None

    t0 = time.time()
    last_partial = None

    logger.info(f"[WAIT_FILL] {symbol} order_id={order_id} waiting...")

    while time.time() - t0 < WAIT_FILL_TIMEOUT_S:
        try:
            o = exchange.fetch_order(order_id, symbol)
        except Exception as e:
            logger.warning(f"[WAIT_FILL] {symbol} fetch_order error: {e}")
            time.sleep(POLL_INTERVAL_S)
            continue

        st = o.get("status", "")
        filled = float(o.get("filled") or 0)
        amount = float(o.get("amount") or 0)

        # Success: Order filled
        if st == "closed":
            emit("buy_filled", symbol=symbol, order_id=order_id, filled=filled)
            logger.info(f"[WAIT_FILL] {symbol} FILLED: {filled:.6f}")
            return o

        # Failure: Order canceled/rejected/expired
        if st in ("canceled", "rejected", "expired"):
            emit("order_canceled", symbol=symbol, order_id=order_id, status=st)
            logger.warning(f"[WAIT_FILL] {symbol} CANCELED: status={st}")
            return None

        # Partial fill: Track age and cancel if stuck
        if 0 < filled < amount:
            if last_partial is None:
                last_partial = time.time()
                emit("buy_partial", symbol=symbol, order_id=order_id, filled=filled, amount=amount)
                logger.info(f"[WAIT_FILL] {symbol} PARTIAL: {filled:.6f}/{amount:.6f}")
            elif time.time() - last_partial > PARTIAL_MAX_AGE_S:
                # Cancel stuck partial fill
                logger.warning(f"[WAIT_FILL] {symbol} PARTIAL TIMEOUT: canceling")
                try:
                    exchange.cancel_order(order_id, symbol)
                except Exception as e:
                    logger.debug(f"Cancel failed: {e}")
                emit("order_canceled", symbol=symbol, order_id=order_id, status="partial_timeout")
                return None

        time.sleep(POLL_INTERVAL_S)

    # Timeout: Cancel order
    logger.warning(f"[WAIT_FILL] {symbol} TIMEOUT: canceling after {WAIT_FILL_TIMEOUT_S}s")
    try:
        exchange.cancel_order(order_id, symbol)
    except Exception as e:
        logger.debug(f"Cancel after timeout failed: {e}")

    emit("order_canceled", symbol=symbol, order_id=order_id, status="timeout")
    return None
