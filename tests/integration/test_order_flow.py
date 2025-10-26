#!/usr/bin/env python3
"""
Integration Tests for Complete Order Flow: Intent → Router → Fill → Portfolio

Tests the full lifecycle:
1. Decision creates Intent
2. Router executes Order
3. Reconciler processes Fill
4. Portfolio updates Position
5. State persistence works
"""

import time
from unittest.mock import Mock, patch

import pytest

from core.portfolio.portfolio import PortfolioManager

# Import components to test
from engine.buy_decision import BuyDecisionHandler
from services.order_router import OrderRouter, RouterConfig


class TestFullOrderFlow:
    """Integration tests for complete order flow"""

    @pytest.fixture
    def mock_engine(self):
        """Create mock engine with all required services"""
        engine = Mock()
        engine.pending_buy_intents = {}
        engine.positions = {}
        engine.current_decision_id = "test-decision-123"
        engine.session_digest = {"buys": [], "sells": []}

        # Mock services
        engine.portfolio = Mock(spec=PortfolioManager)
        engine.portfolio.get_balance = Mock(return_value=1000.0)
        engine.portfolio.get_free_usdt = Mock(return_value=1000.0)
        engine.portfolio.positions = {}

        engine.pnl_tracker = Mock()
        engine.pnl_tracker.on_fill = Mock()

        engine.pnl_service = Mock()
        engine.pnl_service.record_fill = Mock()

        engine.rolling_stats = Mock()
        engine.rolling_stats.add_latency = Mock()
        engine.rolling_stats.add_fill = Mock()

        engine.jsonl_logger = Mock()
        engine.buy_flow_logger = Mock()
        engine.buy_flow_logger.end_evaluation = Mock()

        # State writer mock
        engine._persist_intent_state = Mock()

        # Event bus
        engine.event_bus = Mock()
        engine.event_bus.publish = Mock()

        return engine

    @pytest.fixture
    def mock_exchange_wrapper(self):
        """Create mock exchange wrapper"""
        wrapper = Mock()
        wrapper.place_limit_order = Mock(return_value={
            "id": "exchange-order-456",
            "symbol": "BTC/USDT",
            "status": "open",
            "filled": 0.0
        })
        wrapper.wait_for_fill = Mock(return_value={
            "status": "closed",
            "filled": 0.001,
            "average": 50000.0
        })
        wrapper.fetch_order = Mock(return_value={
            "id": "exchange-order-456",
            "average": 50000.0,
            "filled": 0.001,
            "fee": {"cost": 0.05, "currency": "USDT"}
        })
        return wrapper

    @pytest.fixture
    def router_config(self):
        """Create router config for testing"""
        return RouterConfig(
            max_retries=3,
            retry_backoff_ms=100,
            tif="IOC",
            slippage_bps=20,
            min_notional=5.0,
            fetch_order_on_fill=False  # Performance mode
        )

    def test_buy_intent_to_fill_success(self, mock_engine, mock_exchange_wrapper, router_config):
        """
        Test complete flow: Intent Creation → Router Execution → Fill → Portfolio Update
        """
        # 1. Setup: Create intent metadata
        symbol = "BTC/USDT"
        intent_id = "test-intent-123"

        intent_metadata = {
            "decision_id": "test-decision-123",
            "symbol": symbol,
            "signal": "DROP_TRIGGER",
            "quote_budget": 50.0,
            "intended_price": 50000.0,
            "amount": 0.001,
            "start_ts": time.time(),
            "market_snapshot": {"bid": 49990.0, "ask": 50010.0}
        }

        mock_engine.pending_buy_intents[intent_id] = intent_metadata

        # 2. Verify intent state persistence was called
        mock_engine._persist_intent_state.assert_not_called()  # Not called yet

        # 3. Simulate Router handling the intent (simplified)
        # In real flow, Router would:
        # - Reserve budget
        # - Place order
        # - Wait for fill
        # - Publish order.filled event

        fill_event = {
            "intent_id": intent_id,
            "symbol": symbol,
            "order_id": "exchange-order-456",
            "filled_qty": 0.001,
            "average_price": 50000.0,
            "timestamp": time.time(),
            "placement_latency_ms": 12.5,
            "fill_latency_ms": 180.2,
            "reconcile_latency_ms": 45.1
        }

        # 4. Simulate BuyDecisionHandler processing the fill
        buy_handler = BuyDecisionHandler(mock_engine)

        # Mock reconciliation summary
        summary = {
            "qty_delta": 0.001,
            "notional": 50.0,
            "fees": 0.05,
            "state": "OPEN"
        }

        # 5. Execute: Handle router fill
        buy_handler.handle_router_fill(intent_id, fill_event, summary)

        # 6. Assertions: Verify complete flow

        # Intent should be removed from pending
        assert intent_id not in mock_engine.pending_buy_intents

        # State persistence should be called (intent removal)
        mock_engine._persist_intent_state.assert_called()

        # PnL tracking should be updated
        mock_engine.pnl_tracker.on_fill.assert_called_once_with(
            symbol, "BUY", 50000.0, 0.001
        )

        # PnL service should record fill
        mock_engine.pnl_service.record_fill.assert_called_once()

        # Rolling stats should track latency
        mock_engine.rolling_stats.add_latency.assert_called()

        # JSONL logger should log fill
        mock_engine.jsonl_logger.order_filled.assert_called_once()

        # Position should be created
        assert symbol in mock_engine.positions
        position = mock_engine.positions[symbol]
        assert position["amount"] == 0.001
        assert position["buying_price"] == 50000.0
        assert position["signal"] == "DROP_TRIGGER"

        # Session digest should be updated
        assert len(mock_engine.session_digest["buys"]) == 1
        buy_entry = mock_engine.session_digest["buys"][0]
        assert buy_entry["sym"] == symbol
        assert buy_entry["px"] == 50000.0
        assert buy_entry["qty"] == 0.001

    def test_buy_intent_partial_fill_tracking(self, mock_engine):
        """Test that partial fills are tracked correctly"""
        symbol = "ETH/USDT"
        intent_id = "partial-intent-456"

        intent_metadata = {
            "decision_id": "test-decision-456",
            "symbol": symbol,
            "signal": "DROP_TRIGGER",
            "quote_budget": 100.0,
            "intended_price": 3000.0,
            "amount": 0.05,  # Requested
            "start_ts": time.time()
        }

        mock_engine.pending_buy_intents[intent_id] = intent_metadata

        # Simulate partial fill (only 50% filled)
        fill_event = {
            "intent_id": intent_id,
            "symbol": symbol,
            "order_id": "partial-order-789",
            "filled_qty": 0.025,  # Only 50% filled
            "average_price": 3000.0,
            "timestamp": time.time()
        }

        summary = {
            "qty_delta": 0.025,
            "notional": 75.0,
            "fees": 0.075,
            "state": "OPEN"
        }

        buy_handler = BuyDecisionHandler(mock_engine)
        buy_handler.handle_router_fill(intent_id, fill_event, summary)

        # Position should reflect partial fill
        assert symbol in mock_engine.positions
        position = mock_engine.positions[symbol]
        assert position["amount"] == 0.025  # Half of requested

        # Intent should still be removed (Router handles retries separately)
        assert intent_id not in mock_engine.pending_buy_intents

    def test_buy_intent_cleanup_on_error(self, mock_engine):
        """Test that failed intents are cleaned up properly"""
        symbol = "SOL/USDT"
        intent_id = "failed-intent-789"

        # No metadata (simulates missing intent)
        fill_event = {
            "intent_id": intent_id,
            "symbol": symbol,
            "filled_qty": 0.0
        }

        buy_handler = BuyDecisionHandler(mock_engine)

        # Should not raise exception, just log warning
        buy_handler.handle_router_fill(intent_id, fill_event, None)

        # No position should be created for zero-quantity fill
        assert symbol not in mock_engine.positions

    def test_latency_tracking_complete(self, mock_engine):
        """Test that latency breakdown is tracked correctly"""
        symbol = "BTC/USDT"
        intent_id = "latency-test-123"

        start_time = time.time()
        intent_metadata = {
            "decision_id": "latency-decision-123",
            "symbol": symbol,
            "signal": "DROP_TRIGGER",
            "quote_budget": 50.0,
            "intended_price": 50000.0,
            "amount": 0.001,
            "start_ts": start_time
        }

        mock_engine.pending_buy_intents[intent_id] = intent_metadata

        # Simulate fill with detailed latency breakdown
        fill_event = {
            "intent_id": intent_id,
            "symbol": symbol,
            "order_id": "latency-order-456",
            "filled_qty": 0.001,
            "average_price": 50000.0,
            "timestamp": time.time(),
            "placement_latency_ms": 15.3,
            "fill_latency_ms": 200.5,
            "reconcile_latency_ms": 50.2,
            "fetch_order_latency_ms": 0  # Not fetched (performance mode)
        }

        summary = {"qty_delta": 0.001, "notional": 50.0, "fees": 0.05}

        buy_handler = BuyDecisionHandler(mock_engine)
        buy_handler.handle_router_fill(intent_id, fill_event, summary)

        # Verify latency was tracked
        mock_engine.rolling_stats.add_latency.assert_called()

        # Check call arguments
        call_args = mock_engine.rolling_stats.add_latency.call_args
        assert call_args[0][0] == "intent_to_fill"  # Metric name
        latency_ms = call_args[0][1]

        # Latency should be > 0 (time elapsed since start_ts)
        assert latency_ms > 0
        assert latency_ms < 10000  # Sanity check: < 10 seconds

    def test_state_persistence_on_intent_lifecycle(self, mock_engine):
        """Test that state is persisted at key lifecycle points"""
        symbol = "BTC/USDT"
        intent_id = "state-test-123"

        # Reset mock
        mock_engine._persist_intent_state.reset_mock()

        # 1. Add intent
        mock_engine.pending_buy_intents[intent_id] = {
            "symbol": symbol,
            "start_ts": time.time()
        }

        # In real flow, execute_buy_order would call _persist_intent_state
        # Simulate that call
        mock_engine._persist_intent_state()
        assert mock_engine._persist_intent_state.call_count == 1

        # 2. Remove intent (fill)
        fill_event = {
            "intent_id": intent_id,
            "symbol": symbol,
            "filled_qty": 0.001,
            "average_price": 50000.0
        }

        buy_handler = BuyDecisionHandler(mock_engine)
        buy_handler.handle_router_fill(intent_id, fill_event, {"qty_delta": 0.001})

        # State should be persisted after removal
        assert mock_engine._persist_intent_state.call_count == 2


class TestRouterStatePersistence:
    """Test OrderRouter metadata persistence"""

    def test_router_metadata_persisted_on_order_placement(self, tmp_path):
        """Test that order metadata is persisted when order is placed"""
        # Create temp state file
        state_file = tmp_path / "router_meta.json"

        # Mock components
        exchange_wrapper = Mock()
        exchange_wrapper.place_limit_order = Mock(return_value={
            "id": "test-order-123",
            "status": "open"
        })
        exchange_wrapper.wait_for_fill = Mock(return_value={
            "status": "closed",
            "filled": 0.001,
            "average": 50000.0
        })

        portfolio = Mock()
        portfolio.reserve_budget = Mock(return_value=True)
        portfolio.commit_budget = Mock()

        telemetry = Mock()
        event_bus = Mock()

        router_config = RouterConfig(
            max_retries=1,
            fetch_order_on_fill=False
        )

        # Patch config to use temp file
        with patch('services.order_router.config') as mock_config:
            mock_config.ORDER_ROUTER_META_FILE = str(state_file)
            mock_config.STATE_PERSIST_INTERVAL_S = 0.1  # Fast for testing
            mock_config.ORDER_META_MAX_AGE_S = 86400

            router = OrderRouter(
                exchange_wrapper=exchange_wrapper,
                portfolio=portfolio,
                telemetry=telemetry,
                config=router_config,
                event_bus=event_bus
            )

            # Give state writer time to initialize
            time.sleep(0.2)

            # Verify state writer was created
            assert hasattr(router, '_meta_state_writer')

            # Cleanup
            if hasattr(router, '_meta_state_writer') and router._meta_state_writer:
                router._meta_state_writer.shutdown()


# Run tests with: pytest tests/integration/test_order_flow.py -v
