#!/usr/bin/env python3
"""
Unit Tests for FSM Parity Modules

Tests quantization, order validation, and min_notional handling.
"""

import pytest
from services.quantize import q_price, q_amount
from services.order_validation import preflight


class TestQuantization:
    """Test FLOOR-based quantization."""

    def test_price_quantization_floor(self):
        """Test price quantization floors correctly."""
        assert q_price(0.0379874, 0.000001) == 0.037987
        assert q_price(0.012345, 0.0001) == 0.0123
        assert q_price(50000.789, 0.01) == 50000.78

    def test_amount_quantization_floor(self):
        """Test amount quantization floors correctly."""
        assert q_amount(12.3456, 1) == 12.0
        assert q_amount(123.456, 0.01) == 123.45
        assert q_amount(0.05678, 0.001) == 0.056

    def test_zero_step_passthrough(self):
        """Test that zero step_size returns original value."""
        assert q_price(0.123456, 0) == 0.123456
        assert q_amount(123.456, 0) == 123.456


class TestOrderValidation:
    """Test pre-flight validation and min_notional handling."""

    def test_basic_validation_pass(self):
        """Test basic validation passes with valid inputs."""
        f = {
            "tick_size": 0.0001,
            "step_size": 0.01,
            "min_qty": 1,
            "min_notional": 1.0
        }
        ok, data = preflight("TEST/USDT", 0.012345, 123.456, f)
        assert ok is True
        assert data["price"] == 0.0123  # Floored
        assert data["amount"] == 123.45  # Floored
        assert data["price"] * data["amount"] >= 1.0  # Meets min_notional

    def test_min_notional_auto_bump(self):
        """Test min_notional auto-bump increases amount."""
        f = {
            "tick_size": 0.000001,
            "step_size": 1,
            "min_qty": 1,
            "min_notional": 5.0
        }
        # 0.038 * 1 = 0.038 < 5.0 → should bump to ~132
        ok, data = preflight("BLESS/USDT", 0.038, 1, f)
        assert ok is True
        assert data["amount"] >= 132  # 0.038 * 132 ≈ 5.016
        assert data["price"] * data["amount"] >= 5.0

    def test_min_qty_violation(self):
        """Test min_qty violation fails."""
        f = {
            "tick_size": 0.01,
            "step_size": 0.1,
            "min_qty": 10.0,
            "min_notional": None
        }
        # Amount 5.0 < min_qty 10.0
        ok, data = preflight("TEST/USDT", 1.0, 5.0, f)
        assert ok is False
        assert data["reason"] == "min_qty"
        assert data["need"] == 10.0
        assert data["got"] == 5.0

    def test_min_notional_violation_unfixable(self):
        """Test min_notional violation that can't be fixed."""
        f = {
            "tick_size": 0.000001,
            "step_size": 1,
            "min_qty": 1,
            "min_notional": 1000.0  # Unreasonably high
        }
        # Even with max bump, can't reach 1000 USDT at 0.01 price
        ok, data = preflight("TEST/USDT", 0.01, 1, f)
        assert ok is False
        assert data["reason"] == "min_notional"
        assert data["need"] == 1000.0


class TestRealWorldCases:
    """Test real-world cases from logs."""

    def test_zbt_usdt_case(self):
        """Test ZBT/USDT case from session logs."""
        # Market: tick=0.0001, step=0.01, min_notional=1.0
        f = {
            "tick_size": 0.0001,
            "step_size": 0.01,
            "min_qty": None,
            "min_notional": 1.0
        }
        # Raw: 0.012345 price, 123.456 amount
        ok, data = preflight("ZBT/USDT", 0.012345, 123.456, f)
        assert ok is True
        assert data["price"] == 0.0123
        assert data["amount"] == 123.45
        assert data["price"] * data["amount"] >= 1.0

    def test_bless_usdt_case(self):
        """Test BLESS/USDT case from session logs."""
        # Market: tick=0.000001, step=1, min_notional=5.0
        f = {
            "tick_size": 0.000001,
            "step_size": 1,
            "min_qty": 1,
            "min_notional": 5.0
        }
        # Raw: 0.0379874 price, ~132 amount needed
        ok, data = preflight("BLESS/USDT", 0.0379874, 132, f)
        assert ok is True
        assert data["price"] == 0.037987
        assert data["amount"] == 132.0
        assert data["price"] * data["amount"] >= 5.0


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
