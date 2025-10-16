#!/usr/bin/env python3
"""
Exit Intent Assembler - Exit Signal → Deterministic Intent

Builds immutable ExitIntent objects from exit signals with:
- Deterministic intent_id for idempotency
- Input hash to prevent duplicate exits
- Complete audit trail via intent logging

Exit signals come from ExitEngine.choose() and flow to OrderRouter.
"""

from dataclasses import dataclass
from typing import Dict, Optional
import hashlib
import time
import json


@dataclass(frozen=True)
class ExitIntent:
    """
    Immutable exit intent for order execution.

    Attributes:
        intent_id: Deterministic ID format: EXIT-{timestamp_ms}-{SYMBOL}-{hash}
        symbol: Trading pair (e.g., "BTC/USDT")
        side: Order side ("buy" to close short, "sell" to close long)
        qty: Quantity to exit (absolute value)
        limit_price: Limit price (None for market order)
        reason: Exit reason (e.g., "max_loss_reached", "target_reached")
        rule_code: Exit rule code (e.g., "HARD_SL", "HARD_TP")
        inputs_hash: Hash of exit signal inputs for deduplication
    """
    intent_id: str
    symbol: str
    side: str
    qty: float
    limit_price: Optional[float]
    reason: str
    rule_code: str
    inputs_hash: str


def _inputs_hash(exit_signal: Dict) -> str:
    """
    Generate deterministic hash from exit signal inputs.

    Uses symbol, rule_code, and reason to detect duplicate exit signals.
    Excludes limit_price as it may fluctuate with market movement.

    Args:
        exit_signal: Exit signal dict from ExitEngine.choose()

    Returns:
        8-character hex hash for intent_id uniqueness
    """
    # Extract stable fields for hash
    stable = {
        "symbol": exit_signal.get("symbol"),
        "rule_code": exit_signal.get("rule_code"),
        "reason": exit_signal.get("reason"),
        "qty": exit_signal.get("qty")
    }

    # Deterministic JSON encoding
    canonical = json.dumps(stable, sort_keys=True, separators=(",", ":"))
    full_hash = hashlib.sha256(canonical.encode("utf-8")).hexdigest()

    # Use first 8 chars for compactness
    return full_hash[:8]


def assemble(exit_signal: Dict) -> Optional[ExitIntent]:
    """
    Assemble exit signal into immutable ExitIntent.

    Validates exit signal and generates deterministic intent_id for idempotency.

    Args:
        exit_signal: Exit signal dict from ExitEngine.choose() with:
            - symbol: Trading pair
            - side: Order side
            - qty: Quantity to exit
            - limit_price: Optional limit price
            - reason: Exit reason
            - rule_code: Exit rule code
            - strength: Rule strength (for telemetry, not used here)

    Returns:
        ExitIntent if signal is valid, None otherwise
    """
    # Validate exit signal
    if not exit_signal:
        return None

    symbol = exit_signal.get("symbol")
    side = exit_signal.get("side")
    qty = exit_signal.get("qty", 0.0)
    limit_price = exit_signal.get("limit_price")
    reason = exit_signal.get("reason", "unknown")
    rule_code = exit_signal.get("rule_code", "UNKNOWN")

    # Validation checks
    if not symbol or not side:
        return None

    if qty <= 0:
        return None

    if side not in ("buy", "sell"):
        return None

    # Generate deterministic intent_id
    timestamp_ms = int(time.time() * 1000)
    symbol_clean = symbol.replace("/", "")
    inputs_hash = _inputs_hash(exit_signal)

    intent_id = f"EXIT-{timestamp_ms}-{symbol_clean}-{inputs_hash}"

    # Build immutable intent
    return ExitIntent(
        intent_id=intent_id,
        symbol=symbol,
        side=side,
        qty=qty,
        limit_price=limit_price,
        reason=reason,
        rule_code=rule_code,
        inputs_hash=inputs_hash
    )
