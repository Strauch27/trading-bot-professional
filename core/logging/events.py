#!/usr/bin/env python3
"""
Strukturiertes Event-Logging für FSM Parity

Emittiert kurze, eindeutige JSONL-Events für Nachvollziehbarkeit.
Verwendet von buy_service, wait_fill, und FSM Transitions.
"""

import json
import sys
import time
import logging

logger = logging.getLogger(__name__)


def emit(event: str, **kw):
    """
    Emit structured event as JSONL to stdout.

    Args:
        event: Event type (e.g., "buy_submit", "buy_filled", "order_canceled")
        **kw: Event metadata

    Example:
        >>> emit("buy_submit", symbol="BTC/USDT", price=50000, amount=0.05)
        {"t": "buy_submit", "ts": 1730563200.123, "symbol": "BTC/USDT", ...}
    """
    kw.update({
        "t": event,
        "ts": time.time()
    })

    # Write to stdout as JSONL
    try:
        sys.stdout.write(json.dumps(kw) + "\n")
        sys.stdout.flush()
    except Exception as e:
        logger.debug(f"Failed to emit event {event}: {e}")

    # Also log to standard logger for correlation
    try:
        logger.info(
            f"[EVENT] {event}",
            extra={"event_type": "FSM_PARITY_EVENT", **kw}
        )
    except Exception:
        pass
