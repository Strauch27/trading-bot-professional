#!/usr/bin/env python3
"""
Intent Assembler - Decision vs. Execution Separation

Assembles trading intents from signals, guards, and risk checks.
An Intent is a declarative trade request that gets executed by OrderRouter.

Pipeline: Signal + Guards + Risk → Intent → OrderRouter
"""

import hashlib
import time
from dataclasses import asdict, dataclass
from typing import Any, Dict, Optional


@dataclass(frozen=True)
class Intent:
    """
    Immutable trade intent representing a trading decision.

    This is the contract between Decision (buy_decision.py) and Execution (order_router.py).

    Attributes:
        intent_id: Unique identifier for idempotency (timestamp + symbol + inputs_hash)
        symbol: Trading pair (e.g., "BTC/USDT")
        side: Order side ("buy" or "sell")
        qty: Base quantity to trade
        limit_price: Optional limit price (None for market orders)
        reason: Signal reason for audit trail
        inputs_hash: Hash of decision inputs for change detection
    """
    intent_id: str
    symbol: str
    side: str
    qty: float
    limit_price: Optional[float]
    reason: str
    inputs_hash: str

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dict for EventBus transmission."""
        return asdict(self)


def _hash_inputs(d: Dict) -> str:
    """
    Create deterministic hash of decision inputs.

    Used for:
    - Idempotency: same inputs = same intent_id
    - Change detection: detect when decision factors change

    Args:
        d: Dict of decision inputs

    Returns:
        16-character hex hash
    """
    canonical = repr(sorted(d.items()))
    return hashlib.sha256(canonical.encode()).hexdigest()[:16]


def assemble(
    signal: Dict[str, Any],
    guards: Dict[str, Any],
    risk: Dict[str, Any]
) -> Optional[Intent]:
    """
    Assemble a trading intent from decision components.

    This is the single point where trading decisions are converted to execution intents.

    Args:
        signal: Signal from SignalEngine with symbol, side, reason, limit_price
        guards: Guard results with "passed" boolean
        risk: Risk check results with "allowed_qty" and budget info

    Returns:
        Intent if all checks pass, None otherwise

    Example:
        >>> signal = {"symbol": "BTC/USDT", "side": "buy", "reason": "DROP_TRIGGER", "limit_price": 50000.0}
        >>> guards = {"passed": True}
        >>> risk = {"allowed_qty": 0.001, "budget": 50.0}
        >>> intent = assemble(signal, guards, risk)
        >>> print(intent.intent_id)
        1697123456789-BTCUSDT-a3f5c2d1e4b8f9a2
    """
    # Guard check: must pass all guards
    if not guards.get("passed", False):
        return None

    # Risk check: must have allowed quantity
    allowed_qty = risk.get("allowed_qty", 0.0)
    if allowed_qty <= 0.0:
        return None

    # Extract signal components
    symbol = signal.get("symbol")
    side = signal.get("side", "buy")
    reason = signal.get("reason", "UNKNOWN")
    limit_price = signal.get("limit_price")

    # Validate required fields
    if not symbol or not side:
        return None

    # Build intent payload for hashing
    payload = {
        "symbol": symbol,
        "side": side,
        "qty": allowed_qty,
        "px": limit_price,
        "why": reason
    }

    # Generate deterministic hash
    inputs_hash = _hash_inputs(payload)

    # Generate unique intent ID
    # Format: timestamp_ms-SYMBOL-hash
    # Example: 1697123456789-BTCUSDT-a3f5c2d1e4b8f9a2
    timestamp_ms = int(time.time() * 1000)
    symbol_clean = symbol.replace("/", "")
    intent_id = f"{timestamp_ms}-{symbol_clean}-{inputs_hash}"

    # Create immutable intent
    return Intent(
        intent_id=intent_id,
        symbol=symbol,
        side=side,
        qty=allowed_qty,
        limit_price=limit_price,
        reason=reason,
        inputs_hash=inputs_hash
    )
