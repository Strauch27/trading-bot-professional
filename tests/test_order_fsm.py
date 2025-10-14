#!/usr/bin/env python3
"""
Unit Tests for Order FSM (Finite State Machine)

Tests order lifecycle state machine with:
- State transitions (valid and invalid)
- Terminal state enforcement
- Fill accumulation
- State history tracking
- OrderFSMManager functionality
"""

import time
import unittest
from unittest.mock import Mock, patch

from core.fsm.order_fsm import (
    OrderState,
    OrderFSM,
    OrderFSMManager,
    StateTransition,
    get_order_fsm_manager
)


class TestOrderState(unittest.TestCase):
    """Test OrderState enum"""

    def test_terminal_states(self):
        """Test terminal state identification"""
        # Terminal states
        self.assertTrue(OrderState.FILLED.is_terminal())
        self.assertTrue(OrderState.CANCELED.is_terminal())
        self.assertTrue(OrderState.EXPIRED.is_terminal())
        self.assertTrue(OrderState.FAILED.is_terminal())

        # Non-terminal states
        self.assertFalse(OrderState.PENDING.is_terminal())
        self.assertFalse(OrderState.PARTIAL.is_terminal())

    def test_valid_transitions_from_pending(self):
        """Test valid transitions from PENDING state"""
        state = OrderState.PENDING

        # Valid transitions
        self.assertTrue(state.can_transition_to(OrderState.PARTIAL))
        self.assertTrue(state.can_transition_to(OrderState.FILLED))
        self.assertTrue(state.can_transition_to(OrderState.CANCELED))
        self.assertTrue(state.can_transition_to(OrderState.EXPIRED))
        self.assertTrue(state.can_transition_to(OrderState.FAILED))

        # Invalid: Cannot stay in PENDING
        self.assertFalse(state.can_transition_to(OrderState.PENDING))

    def test_valid_transitions_from_partial(self):
        """Test valid transitions from PARTIAL state"""
        state = OrderState.PARTIAL

        # Valid transitions
        self.assertTrue(state.can_transition_to(OrderState.FILLED))
        self.assertTrue(state.can_transition_to(OrderState.CANCELED))
        self.assertTrue(state.can_transition_to(OrderState.EXPIRED))

        # Invalid: Cannot go back to PENDING or FAILED
        self.assertFalse(state.can_transition_to(OrderState.PENDING))
        self.assertFalse(state.can_transition_to(OrderState.PARTIAL))

    def test_terminal_states_cannot_transition(self):
        """Test that terminal states cannot transition"""
        terminal_states = [
            OrderState.FILLED,
            OrderState.CANCELED,
            OrderState.EXPIRED,
            OrderState.FAILED
        ]

        for state in terminal_states:
            self.assertFalse(state.can_transition_to(OrderState.PENDING))
            self.assertFalse(state.can_transition_to(OrderState.PARTIAL))
            self.assertFalse(state.can_transition_to(OrderState.FILLED))
            self.assertFalse(state.can_transition_to(OrderState.CANCELED))


class TestOrderFSM(unittest.TestCase):
    """Test OrderFSM class"""

    def setUp(self):
        """Setup test FSM"""
        self.fsm = OrderFSM(
            order_id="test_order_123",
            symbol="BTC/USDT",
            side="buy",
            total_qty=0.1
        )

    def test_fsm_initialization(self):
        """Test FSM initializes with correct state"""
        self.assertEqual(self.fsm.order_id, "test_order_123")
        self.assertEqual(self.fsm.symbol, "BTC/USDT")
        self.assertEqual(self.fsm.side, "buy")
        self.assertEqual(self.fsm.state, OrderState.PENDING)
        self.assertEqual(self.fsm.total_qty, 0.1)
        self.assertEqual(self.fsm.filled_qty, 0.0)
        self.assertEqual(self.fsm.fill_rate, 0.0)
        self.assertIsNone(self.fsm.first_fill_ts)
        self.assertIsNone(self.fsm.completed_ts)

        # Should have initial state in history
        self.assertEqual(len(self.fsm.state_history), 1)
        self.assertEqual(self.fsm.state_history[0].from_state, OrderState.PENDING)
        self.assertEqual(self.fsm.state_history[0].to_state, OrderState.PENDING)

    def test_valid_transition(self):
        """Test valid state transition"""
        # PENDING → PARTIAL
        result = self.fsm.transition(OrderState.PARTIAL, reason="First fill received")

        self.assertTrue(result)
        self.assertEqual(self.fsm.state, OrderState.PARTIAL)
        self.assertEqual(len(self.fsm.state_history), 2)
        self.assertEqual(self.fsm.state_history[-1].to_state, OrderState.PARTIAL)
        self.assertEqual(self.fsm.state_history[-1].reason, "First fill received")

    def test_invalid_transition(self):
        """Test invalid state transition"""
        # PENDING → PENDING is invalid
        result = self.fsm.transition(OrderState.PENDING, reason="Invalid")

        self.assertFalse(result)
        self.assertEqual(self.fsm.state, OrderState.PENDING)  # State unchanged

    def test_terminal_state_transition_raises(self):
        """Test that transitioning from terminal state raises error"""
        # Move to terminal state
        self.fsm.transition(OrderState.FILLED)

        # Try to transition from terminal state
        with self.assertRaises(ValueError) as ctx:
            self.fsm.transition(OrderState.CANCELED)

        self.assertIn("terminal state", str(ctx.exception).lower())

    def test_record_fill_single(self):
        """Test recording a single fill"""
        # Record fill
        result = self.fsm.record_fill(
            fill_qty=0.05,
            fill_price=50000.0,
            fill_fee=1.25,
            auto_transition=False  # Don't auto-transition for this test
        )

        self.assertTrue(result)
        self.assertEqual(self.fsm.filled_qty, 0.05)
        self.assertEqual(self.fsm.avg_fill_price, 50000.0)
        self.assertEqual(self.fsm.total_fees, 1.25)
        self.assertEqual(self.fsm.fill_rate, 0.5)  # 50% filled
        self.assertIsNotNone(self.fsm.first_fill_ts)

    def test_record_fill_multiple_weighted_average(self):
        """Test weighted average price calculation with multiple fills"""
        # First fill: 0.05 @ 50000
        self.fsm.record_fill(0.05, 50000.0, auto_transition=False)

        # Second fill: 0.03 @ 50100
        self.fsm.record_fill(0.03, 50100.0, auto_transition=False)

        # Expected weighted average: (0.05 * 50000 + 0.03 * 50100) / 0.08
        expected_avg = (0.05 * 50000.0 + 0.03 * 50100.0) / 0.08

        self.assertEqual(self.fsm.filled_qty, 0.08)
        self.assertAlmostEqual(self.fsm.avg_fill_price, expected_avg, places=2)

    def test_record_fill_auto_transition_to_partial(self):
        """Test auto-transition to PARTIAL on first fill"""
        self.fsm.record_fill(0.05, 50000.0, auto_transition=True)

        # Should auto-transition to PARTIAL
        self.assertEqual(self.fsm.state, OrderState.PARTIAL)

    def test_record_fill_auto_transition_to_filled(self):
        """Test auto-transition to FILLED when fully filled"""
        self.fsm.record_fill(0.1, 50000.0, auto_transition=True)

        # Should auto-transition to FILLED
        self.assertEqual(self.fsm.state, OrderState.FILLED)
        self.assertTrue(self.fsm.is_fully_filled())
        self.assertIsNotNone(self.fsm.completed_ts)

    def test_is_fully_filled_with_tolerance(self):
        """Test fully filled check with tolerance"""
        # Fill 99.95% (within default 0.1% tolerance)
        self.fsm.record_fill(0.09995, 50000.0, auto_transition=False)

        self.assertTrue(self.fsm.is_fully_filled(tolerance=0.001))
        self.assertFalse(self.fsm.is_fully_filled(tolerance=0.0001))

    def test_cancel_order(self):
        """Test order cancellation"""
        result = self.fsm.cancel(reason="User requested")

        self.assertTrue(result)
        self.assertEqual(self.fsm.state, OrderState.CANCELED)
        self.assertTrue(self.fsm.is_terminal())
        self.assertTrue(self.fsm.is_canceled())
        self.assertIsNotNone(self.fsm.completed_ts)

    def test_expire_order(self):
        """Test order expiration"""
        result = self.fsm.expire(reason="IOC timeout")

        self.assertTrue(result)
        self.assertEqual(self.fsm.state, OrderState.EXPIRED)
        self.assertTrue(self.fsm.is_terminal())
        self.assertTrue(self.fsm.is_expired())

    def test_fail_order(self):
        """Test order failure"""
        result = self.fsm.fail(reason="Exchange error", error="Insufficient balance")

        self.assertTrue(result)
        self.assertEqual(self.fsm.state, OrderState.FAILED)
        self.assertTrue(self.fsm.is_terminal())
        self.assertTrue(self.fsm.is_failed())

    def test_state_query_methods(self):
        """Test all state query methods"""
        # Initially PENDING
        self.assertTrue(self.fsm.is_pending())
        self.assertFalse(self.fsm.is_partial())
        self.assertFalse(self.fsm.is_filled())
        self.assertFalse(self.fsm.is_canceled())
        self.assertFalse(self.fsm.is_expired())
        self.assertFalse(self.fsm.is_failed())
        self.assertFalse(self.fsm.is_terminal())

        # Transition to PARTIAL
        self.fsm.transition(OrderState.PARTIAL)
        self.assertFalse(self.fsm.is_pending())
        self.assertTrue(self.fsm.is_partial())
        self.assertFalse(self.fsm.is_terminal())

        # Transition to FILLED
        self.fsm.transition(OrderState.FILLED)
        self.assertTrue(self.fsm.is_filled())
        self.assertTrue(self.fsm.is_terminal())

    def test_metrics_properties(self):
        """Test metric properties"""
        # Fill partially
        self.fsm.record_fill(0.06, 50000.0, auto_transition=False)

        self.assertAlmostEqual(self.fsm.fill_rate, 0.6, places=6)
        self.assertAlmostEqual(self.fsm.remaining_qty, 0.04, places=6)
        self.assertGreater(self.fsm.age_seconds, 0)
        self.assertIsNotNone(self.fsm.fill_age_seconds)

    def test_get_statistics(self):
        """Test statistics dictionary"""
        self.fsm.record_fill(0.05, 50000.0, 1.25, auto_transition=True)

        stats = self.fsm.get_statistics()

        self.assertEqual(stats['order_id'], "test_order_123")
        self.assertEqual(stats['symbol'], "BTC/USDT")
        self.assertEqual(stats['side'], "buy")
        self.assertEqual(stats['state'], "partial")
        self.assertEqual(stats['filled_qty'], 0.05)
        self.assertEqual(stats['total_qty'], 0.1)
        self.assertEqual(stats['fill_rate'], 0.5)
        self.assertEqual(stats['avg_fill_price'], 50000.0)
        self.assertEqual(stats['total_fees'], 1.25)
        self.assertGreater(stats['num_transitions'], 0)

    def test_state_history_tracking(self):
        """Test state history is tracked correctly"""
        # PENDING → PARTIAL → FILLED
        self.fsm.transition(OrderState.PARTIAL, reason="First fill")
        time.sleep(0.01)  # Small delay to ensure different timestamps
        self.fsm.transition(OrderState.FILLED, reason="Fully filled")

        history = self.fsm.get_state_history_summary()

        # Should have 3 entries: initial PENDING, PARTIAL, FILLED
        self.assertEqual(len(history), 3)
        self.assertEqual(history[0]['to'], "pending")
        self.assertEqual(history[1]['to'], "partial")
        self.assertEqual(history[2]['to'], "filled")
        self.assertEqual(history[2]['reason'], "Fully filled")

    def test_serialization_roundtrip(self):
        """Test FSM can be serialized and deserialized"""
        # Setup FSM with some state
        self.fsm.record_fill(0.05, 50000.0, 1.25, auto_transition=True)
        self.fsm.metadata['test_key'] = 'test_value'

        # Serialize
        data = self.fsm.to_dict()

        # Deserialize
        fsm2 = OrderFSM.from_dict(data)

        # Verify
        self.assertEqual(fsm2.order_id, self.fsm.order_id)
        self.assertEqual(fsm2.symbol, self.fsm.symbol)
        self.assertEqual(fsm2.side, self.fsm.side)
        self.assertEqual(fsm2.state, self.fsm.state)
        self.assertEqual(fsm2.filled_qty, self.fsm.filled_qty)
        self.assertEqual(fsm2.total_qty, self.fsm.total_qty)
        self.assertEqual(fsm2.avg_fill_price, self.fsm.avg_fill_price)
        self.assertEqual(fsm2.total_fees, self.fsm.total_fees)
        self.assertEqual(len(fsm2.state_history), len(self.fsm.state_history))
        self.assertEqual(fsm2.metadata['test_key'], 'test_value')


class TestOrderFSMManager(unittest.TestCase):
    """Test OrderFSMManager class"""

    def setUp(self):
        """Setup test manager"""
        self.manager = OrderFSMManager()

    def test_manager_initialization(self):
        """Test manager initializes correctly"""
        self.assertIsNotNone(self.manager)
        self.assertEqual(len(self.manager._fsms), 0)
        self.assertEqual(len(self.manager._by_symbol), 0)

    def test_create_order(self):
        """Test creating order FSM"""
        fsm = self.manager.create_order(
            order_id="order_1",
            symbol="BTC/USDT",
            side="buy",
            total_qty=0.1,
            limit_price=50000.0
        )

        self.assertIsNotNone(fsm)
        self.assertEqual(fsm.order_id, "order_1")
        self.assertEqual(fsm.symbol, "BTC/USDT")
        self.assertEqual(fsm.total_qty, 0.1)
        self.assertEqual(fsm.limit_price, 50000.0)

    def test_create_duplicate_order_returns_existing(self):
        """Test creating duplicate order returns existing FSM"""
        fsm1 = self.manager.create_order("order_1", "BTC/USDT", "buy", 0.1)
        fsm2 = self.manager.create_order("order_1", "BTC/USDT", "buy", 0.2)

        # Should return same instance
        self.assertIs(fsm1, fsm2)
        self.assertEqual(fsm1.total_qty, 0.1)  # Original qty preserved

    def test_get_order(self):
        """Test retrieving order by ID"""
        self.manager.create_order("order_1", "BTC/USDT", "buy", 0.1)

        fsm = self.manager.get_order("order_1")
        self.assertIsNotNone(fsm)
        self.assertEqual(fsm.order_id, "order_1")

        # Non-existent order
        self.assertIsNone(self.manager.get_order("nonexistent"))

    def test_get_orders_by_symbol(self):
        """Test retrieving orders by symbol"""
        self.manager.create_order("order_1", "BTC/USDT", "buy", 0.1)
        self.manager.create_order("order_2", "BTC/USDT", "sell", 0.05)
        self.manager.create_order("order_3", "ETH/USDT", "buy", 1.0)

        btc_orders = self.manager.get_orders_by_symbol("BTC/USDT")
        eth_orders = self.manager.get_orders_by_symbol("ETH/USDT")

        self.assertEqual(len(btc_orders), 2)
        self.assertEqual(len(eth_orders), 1)
        self.assertEqual(eth_orders[0].order_id, "order_3")

    def test_get_active_orders(self):
        """Test retrieving active (non-terminal) orders"""
        # Create 3 orders
        fsm1 = self.manager.create_order("order_1", "BTC/USDT", "buy", 0.1)
        fsm2 = self.manager.create_order("order_2", "BTC/USDT", "buy", 0.1)
        fsm3 = self.manager.create_order("order_3", "BTC/USDT", "buy", 0.1)

        # Mark one as filled (terminal)
        fsm2.transition(OrderState.FILLED)

        active = self.manager.get_active_orders()

        self.assertEqual(len(active), 2)
        self.assertIn(fsm1, active)
        self.assertNotIn(fsm2, active)
        self.assertIn(fsm3, active)

    def test_get_terminal_orders(self):
        """Test retrieving terminal orders"""
        fsm1 = self.manager.create_order("order_1", "BTC/USDT", "buy", 0.1)
        fsm2 = self.manager.create_order("order_2", "BTC/USDT", "buy", 0.1)
        fsm3 = self.manager.create_order("order_3", "BTC/USDT", "buy", 0.1)

        # Mark two as terminal
        fsm1.transition(OrderState.FILLED)
        fsm3.transition(OrderState.CANCELED)

        terminal = self.manager.get_terminal_orders()

        self.assertEqual(len(terminal), 2)
        self.assertIn(fsm1, terminal)
        self.assertNotIn(fsm2, terminal)
        self.assertIn(fsm3, terminal)

    def test_cleanup_terminal_orders(self):
        """Test cleanup of old terminal orders"""
        # Create orders
        fsm1 = self.manager.create_order("order_1", "BTC/USDT", "buy", 0.1)
        fsm2 = self.manager.create_order("order_2", "BTC/USDT", "buy", 0.1)
        fsm3 = self.manager.create_order("order_3", "BTC/USDT", "buy", 0.1)

        # Mark as terminal with different completion times
        fsm1.transition(OrderState.FILLED)
        fsm1.completed_ts = time.time() - 7200  # 2 hours ago

        fsm2.transition(OrderState.FILLED)
        fsm2.completed_ts = time.time() - 1800  # 30 minutes ago

        # fsm3 stays active

        # Cleanup orders older than 1 hour
        cleaned = self.manager.cleanup_terminal_orders(age_threshold_seconds=3600)

        self.assertEqual(cleaned, 1)  # Only fsm1 should be cleaned
        self.assertIsNone(self.manager.get_order("order_1"))
        self.assertIsNotNone(self.manager.get_order("order_2"))
        self.assertIsNotNone(self.manager.get_order("order_3"))

    def test_get_statistics(self):
        """Test aggregate statistics"""
        # Create mixed orders
        fsm1 = self.manager.create_order("order_1", "BTC/USDT", "buy", 0.1)
        fsm2 = self.manager.create_order("order_2", "BTC/USDT", "buy", 0.1)
        fsm3 = self.manager.create_order("order_3", "ETH/USDT", "buy", 1.0)

        # Transition some
        fsm1.transition(OrderState.PARTIAL)
        fsm2.transition(OrderState.FILLED)

        stats = self.manager.get_statistics()

        self.assertEqual(stats['total_orders'], 3)
        self.assertEqual(stats['active_orders'], 2)  # PENDING and PARTIAL
        self.assertEqual(stats['terminal_orders'], 1)  # FILLED
        self.assertEqual(stats['symbols_tracked'], 2)  # BTC/USDT and ETH/USDT
        self.assertEqual(stats['state_counts']['pending'], 1)
        self.assertEqual(stats['state_counts']['partial'], 1)
        self.assertEqual(stats['state_counts']['filled'], 1)


class TestGlobalSingleton(unittest.TestCase):
    """Test global singleton getter"""

    def test_get_order_fsm_manager_singleton(self):
        """Test global manager is singleton"""
        manager1 = get_order_fsm_manager()
        manager2 = get_order_fsm_manager()

        self.assertIs(manager1, manager2)


class TestEdgeCases(unittest.TestCase):
    """Test edge cases and error conditions"""

    def test_zero_quantity_order(self):
        """Test order with zero quantity"""
        fsm = OrderFSM("order_1", "BTC/USDT", "buy", total_qty=0.0)

        self.assertEqual(fsm.fill_rate, 0.0)
        self.assertFalse(fsm.is_fully_filled())

    def test_overfill_protection(self):
        """Test that overfilling is handled gracefully"""
        fsm = OrderFSM("order_1", "BTC/USDT", "buy", total_qty=0.1)

        # Try to fill more than total_qty
        fsm.record_fill(0.15, 50000.0, auto_transition=False)

        # Fill rate should be > 1.0
        self.assertGreater(fsm.fill_rate, 1.0)
        self.assertTrue(fsm.is_fully_filled())

    def test_multiple_transitions_same_state(self):
        """Test attempting same transition multiple times"""
        fsm = OrderFSM("order_1", "BTC/USDT", "buy", total_qty=0.1)

        # First transition
        result1 = fsm.transition(OrderState.PARTIAL)
        self.assertTrue(result1)

        # Second transition to same state (invalid)
        result2 = fsm.transition(OrderState.PARTIAL)
        self.assertFalse(result2)

    def test_fill_age_before_first_fill(self):
        """Test fill_age_seconds before any fills"""
        fsm = OrderFSM("order_1", "BTC/USDT", "buy", total_qty=0.1)

        self.assertIsNone(fsm.fill_age_seconds)

    def test_age_calculation_for_terminal_order(self):
        """Test age calculation uses completed_ts for terminal orders"""
        fsm = OrderFSM("order_1", "BTC/USDT", "buy", total_qty=0.1)

        time.sleep(0.1)
        fsm.transition(OrderState.FILLED)

        age_at_completion = fsm.age_seconds
        time.sleep(0.1)
        age_after_wait = fsm.age_seconds

        # Age should be frozen at completion time
        self.assertAlmostEqual(age_at_completion, age_after_wait, delta=0.05)


if __name__ == "__main__":
    print("=" * 70)
    print("Order FSM Unit Tests")
    print("=" * 70)

    # Run tests
    loader = unittest.TestLoader()
    suite = loader.loadTestsFromModule(__import__(__name__))
    runner = unittest.TextTestRunner(verbosity=2)
    result = runner.run(suite)

    # Summary
    print("\n" + "=" * 70)
    print("Test Summary")
    print("=" * 70)
    print(f"Total tests: {result.testsRun}")
    print(f"Passed: {result.testsRun - len(result.failures) - len(result.errors)}")
    print(f"Failed: {len(result.failures)}")
    print(f"Errors: {len(result.errors)}")

    if result.wasSuccessful():
        print("\n✅ All Order FSM tests PASSED!")
    else:
        print("\n❌ Some tests FAILED")

    print("=" * 70)
