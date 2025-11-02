#!/usr/bin/env python3
"""
Exchange Compliance Module - Precision & MinCost Auto-Fix

Ensures orders comply with exchange rules:
- Price tick size (e.g., 0.0001 for ZBT/USDT)
- Amount step size (e.g., 0.01 for ZBT/USDT)
- Minimum notional value (cost.min, typically >= 1.0 USDT)

Auto-fixes violations by quantizing to nearest valid values.
"""

import math
from typing import List, Optional


class ComplianceResult:
    """Result of exchange compliance validation and auto-fix."""
    __slots__ = ("price", "amount", "violations", "min_cost_hit", "auto_fixed")

    def __init__(
        self,
        price: float,
        amount: float,
        violations: List[str],
        min_cost_hit: bool,
        auto_fixed: bool = False
    ):
        self.price = price
        self.amount = amount
        self.violations = violations
        self.min_cost_hit = min_cost_hit
        self.auto_fixed = auto_fixed

    def is_valid(self) -> bool:
        """Check if order is valid (no critical violations)."""
        critical = {"invalid_amount_after_quantize", "min_cost_violation"}
        return not any(v in critical for v in self.violations)

    def __repr__(self) -> str:
        return (
            f"ComplianceResult(price={self.price:.8f}, amount={self.amount:.6f}, "
            f"violations={self.violations}, min_cost_hit={self.min_cost_hit}, "
            f"auto_fixed={self.auto_fixed})"
        )


def _floor_to_step(value: float, step: Optional[float]) -> float:
    """
    Floor value to nearest step (quantize down).

    Args:
        value: Raw value to quantize
        step: Step size (e.g., 0.01 for amount, 0.0001 for price)

    Returns:
        Quantized value floored to step
    """
    if step is None or step == 0:
        return value
    return math.floor(value / step) * step


def quantize_and_validate(
    raw_price: float,
    raw_amount: float,
    market: dict,
    fee_buf: float = 1.01
) -> ComplianceResult:
    """
    Quantize order parameters to exchange precision and validate compliance.

    Auto-fixes violations:
    1. Rounds price to tick_size (floor)
    2. Rounds amount to step_size (floor)
    3. Increases amount if notional < min_cost

    Args:
        raw_price: Raw price before quantization
        raw_amount: Raw amount before quantization
        market: CCXT market dict with precision and limits
        fee_buf: Buffer for min_cost (1.01 = 1% buffer for fees)

    Returns:
        ComplianceResult with quantized values and violation flags

    Example:
        >>> market = {
        ...     "precision": {"price": 0.0001, "amount": 0.01},
        ...     "limits": {"cost": {"min": 1.0}}
        ... }
        >>> result = quantize_and_validate(0.012345, 123.456, market)
        >>> result.price  # 0.0123
        >>> result.amount  # 123.45
        >>> result.violations  # ['tick_size_violation', 'amount_step_violation']
    """
    violations = []

    # Extract precision from market
    precision = market.get("precision", {}) or {}
    tick = precision.get("price")
    step = precision.get("amount")

    # Extract min_cost from market
    limits = market.get("limits", {}) or {}
    cost_limits = limits.get("cost", {}) or {}
    min_cost = cost_limits.get("min", 0) or 0

    # Quantize price to tick
    q_price = _floor_to_step(raw_price, tick)
    if tick and abs(raw_price - q_price) > 1e-12:
        violations.append("tick_size_violation")

    # Quantize amount to step
    q_amount = _floor_to_step(raw_amount, step)
    if step and abs(raw_amount - q_amount) > 1e-12:
        violations.append("amount_step_violation")

    # Check if quantized values are valid
    if not q_price or q_price <= 0:
        violations.append("invalid_price")
        return ComplianceResult(0, 0, violations, min_cost > 0, auto_fixed=True)

    if not q_amount or q_amount <= 0:
        violations.append("invalid_amount_after_quantize")
        return ComplianceResult(0, 0, violations, min_cost > 0, auto_fixed=True)

    # MinCost auto-fix: Increase amount if notional too low
    auto_fixed = False
    min_cost_hit = False

    if min_cost > 0:
        notional = q_price * q_amount
        required_notional = min_cost * fee_buf

        if notional < required_notional:
            # Calculate required amount
            needed_amount = required_notional / q_price
            new_amount = _floor_to_step(needed_amount, step)

            # Verify new amount still meets min_cost
            if new_amount * q_price >= min_cost:
                q_amount = new_amount
                violations.append("min_cost_auto_fixed")
                auto_fixed = True
            else:
                # Even after fix, still below min_cost
                violations.append("min_cost_violation")
                min_cost_hit = True

    return ComplianceResult(q_price, q_amount, violations, min_cost_hit, auto_fixed)


def validate_order_params(
    symbol: str,
    side: str,
    price: float,
    amount: float,
    market: dict
) -> tuple[bool, str, dict]:
    """
    Validate order parameters against exchange rules (no auto-fix).

    This is a validation-only function for final pre-flight checks.
    Use quantize_and_validate() for auto-fixing.

    Args:
        symbol: Trading symbol
        side: "buy" or "sell"
        price: Order price
        amount: Order amount
        market: CCXT market dict

    Returns:
        Tuple of (is_valid, reason, details)
    """
    details = {
        "symbol": symbol,
        "side": side,
        "price": price,
        "amount": amount
    }

    # Basic checks
    if amount <= 0:
        return False, "amount_zero_or_negative", details

    if price <= 0:
        return False, "price_zero_or_negative", details

    # Extract limits
    precision = market.get("precision", {}) or {}
    limits = market.get("limits", {}) or {}

    tick = precision.get("price")
    step = precision.get("amount")
    min_cost = (limits.get("cost", {}) or {}).get("min", 0) or 0

    # Check tick size compliance
    if tick and tick > 0:
        remainder = abs(price % tick)
        if remainder > 1e-12:
            details["tick_size_violation"] = {
                "tick": tick,
                "remainder": remainder
            }
            return False, "invalid_tick_size", details

    # Check step size compliance
    if step and step > 0:
        remainder = abs(amount % step)
        if remainder > 1e-12:
            details["step_size_violation"] = {
                "step": step,
                "remainder": remainder
            }
            return False, "invalid_step_size", details

    # Check min notional
    notional = price * amount
    if min_cost > 0 and notional < min_cost:
        details["min_notional_violation"] = {
            "required": min_cost,
            "actual": notional
        }
        return False, "below_min_notional", details

    details["notional"] = notional
    return True, "valid", details
