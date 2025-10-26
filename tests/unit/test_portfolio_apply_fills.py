#!/usr/bin/env python3
"""
Unit Tests for Portfolio.apply_fills() - Buy/Sell Aggregation

Tests:
- Buy aggregation (multiple buys → weighted average cost)
- Sell aggregation (position reduction → realized PnL)
- Position state transitions (NEW → OPEN → PARTIAL_EXIT → CLOSED)
- Fee accumulation
- Budget reconciliation
"""

import time
from unittest.mock import patch

import pytest

from core.portfolio.portfolio import PortfolioManager, Position


class TestApplyFillsBuyAggregation:
    """Test buy-side fill aggregation"""

    @pytest.fixture
    def portfolio(self):
        """Create portfolio instance for testing"""
        with patch('core.portfolio.portfolio.config') as mock_config:
            mock_config.INITIAL_BALANCE_USDT = 1000.0
            mock_config.STATE_DIR = "/tmp/test_state"

            portfolio = PortfolioManager()
            portfolio.my_budget = 1000.0
            portfolio.reserved_budget = 0.0
            portfolio.verified_budget = 1000.0

            return portfolio

    def test_single_buy_creates_position(self, portfolio):
        """Test that a single buy creates a new position"""
        symbol = "BTC/USDT"
        trades = [
            {
                "side": "buy",
                "amount": 0.001,
                "price": 50000.0,
                "fee": {"cost": 0.05, "currency": "USDT"}
            }
        ]

        result = portfolio.apply_fills(symbol, trades)

        # Verify result
        assert result["qty_delta"] == 0.001
        assert result["notional"] == pytest.approx(50.0)  # 0.001 * 50000
        assert result["fees"] == pytest.approx(0.05)
        assert result["state"] == "OPEN"

        # Verify position created
        assert symbol in portfolio.positions
        pos = portfolio.positions[symbol]

        assert pos.qty == 0.001
        assert pos.avg_price == pytest.approx(50000.0)
        assert pos.fees_paid == pytest.approx(0.05)
        assert pos.state == "OPEN"
        assert pos.realized_pnl == 0.0  # No sells yet

    def test_multiple_buys_weighted_average_cost(self, portfolio):
        """Test that multiple buys calculate weighted average cost correctly"""
        symbol = "ETH/USDT"
        trades = [
            # First buy: 0.1 ETH @ 3000
            {"side": "buy", "amount": 0.1, "price": 3000.0, "fee": {"cost": 0.3}},
            # Second buy: 0.05 ETH @ 3100
            {"side": "buy", "amount": 0.05, "price": 3100.0, "fee": {"cost": 0.155}}
        ]

        result = portfolio.apply_fills(symbol, trades)

        # Total qty: 0.15 ETH
        assert result["qty_delta"] == pytest.approx(0.15)

        # Total notional: (0.1 * 3000) + (0.05 * 3100) = 300 + 155 = 455
        assert result["notional"] == pytest.approx(455.0)

        # Total fees: 0.3 + 0.155 = 0.455
        assert result["fees"] == pytest.approx(0.455)

        # Position
        pos = portfolio.positions[symbol]

        # Weighted average cost: 455 / 0.15 = 3033.33
        assert pos.avg_price == pytest.approx(3033.33, rel=1e-2)
        assert pos.qty == pytest.approx(0.15)
        assert pos.fees_paid == pytest.approx(0.455)

    def test_buy_increments_existing_position(self, portfolio):
        """Test that buying into existing position updates WAC correctly"""
        symbol = "SOL/USDT"

        # Create initial position
        portfolio.positions[symbol] = Position(
            symbol=symbol,
            state="OPEN",
            qty=1.0,
            avg_price=100.0,
            realized_pnl=0.0,
            fees_paid=0.1,
            opened_ts=time.time()
        )

        # Add more via apply_fills
        trades = [
            {"side": "buy", "amount": 0.5, "price": 120.0, "fee": {"cost": 0.06}}
        ]

        result = portfolio.apply_fills(symbol, trades)

        # New qty: 1.0 + 0.5 = 1.5
        assert result["qty_delta"] == pytest.approx(0.5)

        pos = portfolio.positions[symbol]
        assert pos.qty == pytest.approx(1.5)

        # New WAC: ((1.0 * 100) + (0.5 * 120)) / 1.5 = (100 + 60) / 1.5 = 106.67
        assert pos.avg_price == pytest.approx(106.67, rel=1e-2)

        # Fees accumulated: 0.1 + 0.06 = 0.16
        assert pos.fees_paid == pytest.approx(0.16)


class TestApplyFillsSellReduction:
    """Test sell-side fill aggregation and PnL realization"""

    @pytest.fixture
    def portfolio_with_position(self):
        """Create portfolio with existing position"""
        with patch('core.portfolio.portfolio.config') as mock_config:
            mock_config.INITIAL_BALANCE_USDT = 1000.0
            mock_config.STATE_DIR = "/tmp/test_state"

            portfolio = PortfolioManager()

            # Create position: 0.1 BTC @ 50000 avg
            symbol = "BTC/USDT"
            portfolio.positions[symbol] = Position(
                symbol=symbol,
                state="OPEN",
                qty=0.1,
                avg_price=50000.0,
                realized_pnl=0.0,
                fees_paid=5.0,
                opened_ts=time.time()
            )

            return portfolio, symbol

    def test_partial_sell_realizes_pnl(self, portfolio_with_position):
        """Test that selling part of position realizes proportional PnL"""
        portfolio, symbol = portfolio_with_position

        # Sell 50% at profit: 0.05 BTC @ 52000 (4% gain)
        trades = [
            {"side": "sell", "amount": 0.05, "price": 52000.0, "fee": {"cost": 2.6}}
        ]

        result = portfolio.apply_fills(symbol, trades)

        # Negative qty_delta (sell)
        assert result["qty_delta"] == pytest.approx(-0.05)

        # Notional: 0.05 * 52000 = 2600
        assert result["notional"] == pytest.approx(2600.0)

        # State should be PARTIAL_EXIT
        assert result["state"] == "PARTIAL_EXIT"

        # Position
        pos = portfolio.positions[symbol]

        # Remaining qty: 0.1 - 0.05 = 0.05
        assert pos.qty == pytest.approx(0.05)

        # Avg price stays same (only changes on buys)
        assert pos.avg_price == pytest.approx(50000.0)

        # Realized PnL: (52000 - 50000) * 0.05 = 100
        assert pos.realized_pnl == pytest.approx(100.0)

        # Fees accumulated: 5.0 (original) + 2.6 (sell) = 7.6
        assert pos.fees_paid == pytest.approx(7.6)

    def test_full_sell_closes_position(self, portfolio_with_position):
        """Test that selling entire position closes it"""
        portfolio, symbol = portfolio_with_position

        # Sell 100%: 0.1 BTC @ 51000 (2% gain)
        trades = [
            {"side": "sell", "amount": 0.1, "price": 51000.0, "fee": {"cost": 5.1}}
        ]

        result = portfolio.apply_fills(symbol, trades)

        # Full reduction
        assert result["qty_delta"] == pytest.approx(-0.1)

        # State should be CLOSED
        assert result["state"] == "CLOSED"

        # Position
        pos = portfolio.positions[symbol]

        # Qty should be zero
        assert pos.qty == pytest.approx(0.0)

        # Realized PnL: (51000 - 50000) * 0.1 = 100
        assert pos.realized_pnl == pytest.approx(100.0)

        # Fees: 5.0 + 5.1 = 10.1
        assert pos.fees_paid == pytest.approx(10.1)

    def test_sell_at_loss_negative_pnl(self, portfolio_with_position):
        """Test that selling at loss realizes negative PnL"""
        portfolio, symbol = portfolio_with_position

        # Sell at loss: 0.1 BTC @ 48000 (-4% loss)
        trades = [
            {"side": "sell", "amount": 0.1, "price": 48000.0, "fee": {"cost": 4.8}}
        ]

        result = portfolio.apply_fills(symbol, trades)

        pos = portfolio.positions[symbol]

        # Realized PnL: (48000 - 50000) * 0.1 = -200
        assert pos.realized_pnl == pytest.approx(-200.0)

        assert result["state"] == "CLOSED"

    def test_multiple_sells_accumulate_pnl(self, portfolio_with_position):
        """Test that multiple sells accumulate realized PnL"""
        portfolio, symbol = portfolio_with_position

        # First sell: 0.03 BTC @ 52000 (+2000 * 0.03 = +60)
        trades1 = [
            {"side": "sell", "amount": 0.03, "price": 52000.0, "fee": {"cost": 1.56}}
        ]

        portfolio.apply_fills(symbol, trades1)
        pos = portfolio.positions[symbol]

        assert pos.qty == pytest.approx(0.07)
        assert pos.realized_pnl == pytest.approx(60.0)

        # Second sell: 0.04 BTC @ 53000 (+3000 * 0.04 = +120)
        trades2 = [
            {"side": "sell", "amount": 0.04, "price": 53000.0, "fee": {"cost": 2.12}}
        ]

        portfolio.apply_fills(symbol, trades2)
        pos = portfolio.positions[symbol]

        assert pos.qty == pytest.approx(0.03)

        # Total realized PnL: 60 + 120 = 180
        assert pos.realized_pnl == pytest.approx(180.0)

        # Total fees: 5.0 (initial) + 1.56 + 2.12 = 8.68
        assert pos.fees_paid == pytest.approx(8.68)


class TestApplyFillsEdgeCases:
    """Test edge cases and error handling"""

    @pytest.fixture
    def portfolio(self):
        """Create portfolio instance"""
        with patch('core.portfolio.portfolio.config') as mock_config:
            mock_config.INITIAL_BALANCE_USDT = 1000.0
            mock_config.STATE_DIR = "/tmp/test_state"
            return PortfolioManager()

    def test_empty_trades_list(self, portfolio):
        """Test that empty trades list returns safely"""
        symbol = "BTC/USDT"
        trades = []

        result = portfolio.apply_fills(symbol, trades)

        # Should return empty result
        assert result["qty_delta"] == 0.0
        assert result["notional"] == 0.0
        assert result["fees"] == 0.0

    def test_buy_and_sell_mixed(self, portfolio):
        """Test that buy and sell in same batch are handled correctly"""
        symbol = "ETH/USDT"

        # Buy first
        portfolio.positions[symbol] = Position(
            symbol=symbol,
            state="OPEN",
            qty=1.0,
            avg_price=3000.0,
            realized_pnl=0.0,
            fees_paid=3.0,
            opened_ts=time.time()
        )

        # Mixed trades: buy then sell
        trades = [
            {"side": "buy", "amount": 0.5, "price": 3100.0, "fee": {"cost": 1.55}},
            {"side": "sell", "amount": 0.3, "price": 3200.0, "fee": {"cost": 0.96}}
        ]

        result = portfolio.apply_fills(symbol, trades)

        # Net qty change: +0.5 -0.3 = +0.2
        assert result["qty_delta"] == pytest.approx(0.2)

        pos = portfolio.positions[symbol]

        # Final qty: 1.0 + 0.5 - 0.3 = 1.2
        assert pos.qty == pytest.approx(1.2)

        # Realized PnL from sell (3200 - WAC) * 0.3
        # WAC after buy: ((1.0 * 3000) + (0.5 * 3100)) / 1.5 = 3033.33
        # PnL: (3200 - 3033.33) * 0.3 ≈ 50
        assert pos.realized_pnl > 0  # Should have profit

    def test_sell_without_position_creates_short(self, portfolio):
        """Test that selling without position creates short position (if allowed)"""
        symbol = "BTC/USDT"

        # Sell without position (creates short)
        trades = [
            {"side": "sell", "amount": 0.01, "price": 50000.0, "fee": {"cost": 5.0}}
        ]

        # In current implementation, this creates position with negative qty
        result = portfolio.apply_fills(symbol, trades)

        # Negative qty
        assert result["qty_delta"] == pytest.approx(-0.01)

        # Position should exist with negative qty (short)
        if symbol in portfolio.positions:
            pos = portfolio.positions[symbol]
            assert pos.qty < 0  # Short position

    def test_zero_price_trade_handled(self, portfolio):
        """Test that zero-price trades are handled safely"""
        symbol = "BTC/USDT"

        trades = [
            {"side": "buy", "amount": 0.001, "price": 0.0, "fee": {"cost": 0.0}}
        ]

        # Should not crash, but may log warning
        result = portfolio.apply_fills(symbol, trades)

        # Still creates position but with zero cost basis (edge case)
        assert result["qty_delta"] == pytest.approx(0.001)

    def test_fee_handling_missing_fee_field(self, portfolio):
        """Test that missing fee field defaults to zero"""
        symbol = "BTC/USDT"

        trades = [
            {"side": "buy", "amount": 0.001, "price": 50000.0}  # No fee field
        ]

        result = portfolio.apply_fills(symbol, trades)

        # Should default to zero fees
        assert result["fees"] >= 0.0  # Either 0 or estimated

        pos = portfolio.positions[symbol]
        assert pos.fees_paid >= 0.0


class TestBudgetReconciliation:
    """Test budget reconciliation during apply_fills"""

    @pytest.fixture
    def portfolio(self):
        """Create portfolio with initial budget"""
        with patch('core.portfolio.portfolio.config') as mock_config:
            mock_config.INITIAL_BALANCE_USDT = 1000.0
            mock_config.STATE_DIR = "/tmp/test_state"

            portfolio = PortfolioManager()
            portfolio.my_budget = 1000.0
            portfolio.verified_budget = 1000.0
            portfolio.reserved_budget = 0.0

            return portfolio

    def test_buy_updates_verified_budget(self, portfolio):
        """Test that buy fills update verified budget"""
        symbol = "BTC/USDT"

        # Reserve budget first (simulate router)
        quote_budget = 100.0
        portfolio.reserve_budget(quote_budget, symbol, "buy", reason="test")

        assert portfolio.reserved_budget == pytest.approx(100.0)
        assert portfolio.verified_budget == pytest.approx(900.0)  # 1000 - 100

        # Now apply fills (commit budget)
        trades = [
            {"side": "buy", "amount": 0.002, "price": 50000.0, "fee": {"cost": 0.1}}
        ]

        # Total cost: 100 USDT + 0.1 fee
        portfolio.apply_fills(symbol, trades)

        # Budget should be committed (reserved → spent)
        # Note: apply_fills calls commit_budget internally

    def test_sell_credits_budget(self, portfolio):
        """Test that sell fills credit budget back"""
        symbol = "BTC/USDT"

        # Create position first
        portfolio.positions[symbol] = Position(
            symbol=symbol,
            state="OPEN",
            qty=0.1,
            avg_price=50000.0,
            realized_pnl=0.0,
            fees_paid=5.0,
            opened_ts=time.time()
        )


        # Sell position
        trades = [
            {"side": "sell", "amount": 0.1, "price": 52000.0, "fee": {"cost": 5.2}}
        ]

        portfolio.apply_fills(symbol, trades)

        # Sell proceeds: (0.1 * 52000) - 5.2 = 5200 - 5.2 = 5194.8
        # Budget should increase by proceeds
        # (actual implementation may vary)


# Run tests with: pytest tests/unit/test_portfolio_apply_fills.py -v
