#!/usr/bin/env python3
"""
Zentrale Quantisierung - FLOOR-Rundung für Preis/Menge

Verwendet Decimal für präzise Rundung ohne Float-Fehler.
FLOOR-Strategie verhindert Überschreitung von max_amount/max_notional.
"""

from decimal import Decimal, getcontext

# Setze Präzision auf 28 Stellen für Krypto-Genauigkeit
getcontext().prec = 28


def _floor_step(x: Decimal, step: Decimal) -> Decimal:
    """
    Floor value to nearest step (quantize down).

    Args:
        x: Value to quantize
        step: Step size

    Returns:
        Floored value: (x // step) * step
    """
    if step == 0:
        return x
    return (x // step) * step


def q_price(price: float, tick_size: float) -> float:
    """
    Quantize price to tick_size (FLOOR).

    Args:
        price: Raw price
        tick_size: Exchange tick size (e.g., 0.0001)

    Returns:
        Quantized price floored to tick_size

    Example:
        >>> q_price(0.012345, 0.0001)
        0.0123
    """
    if tick_size == 0:
        return price
    return float(_floor_step(Decimal(str(price)), Decimal(str(tick_size))))


def q_amount(amount: float, step_size: float) -> float:
    """
    Quantize amount to step_size (FLOOR).

    Args:
        amount: Raw amount
        step_size: Exchange step size (e.g., 0.01)

    Returns:
        Quantized amount floored to step_size

    Example:
        >>> q_amount(123.456, 0.01)
        123.45
    """
    if step_size == 0:
        return amount
    return float(_floor_step(Decimal(str(amount)), Decimal(str(step_size))))
