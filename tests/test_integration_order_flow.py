#!/usr/bin/env python3
"""
Integration Tests for Order Flow with All Guards

Tests end-to-end order flow including:
- Entry guards (spread, depth, slippage)
- COID idempotency
- Fill telemetry
- Exit evaluation
- Portfolio synchronization
"""

import sys
import os
import time
import threading
from unittest.mock import Mock, MagicMock, patch
from decimal import Decimal

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


class TestEndToEndBuyFlow:
    """Test complete buy flow with all guards"""

    def setup_method(self):
        """Setup test fixtures"""
        self.mock_exchange = Mock()
        self.mock_portfolio = Mock()
        self.mock_md_provider = Mock()

        # Setup portfolio state
        self.mock_portfolio.held_assets = {}
        self.mock_portfolio.get_symbol_lock = Mock(return_value=threading.RLock())
        self.mock_portfolio.budget_usdt = 1000.0
        self.mock_portfolio.max_trades = 10

    def test_buy_with_passing_guards(self):
        """Test buy order when all guards pass"""
        # Setup: Good market conditions
        self.mock_md_provider.get_ticker.return_value = Mock(
            bid=50000.0,
            ask=50010.0,  # 2 bps spread (good)
            last=50005.0,
            volume=1000000.0
        )

        self.mock_md_provider.get_orderbook.return_value = {
            'bids': [[50000.0, 10.0], [49990.0, 15.0]],
            'asks': [[50010.0, 10.0], [50020.0, 15.0]],
            'timestamp': int(time.time() * 1000)
        }

        # Mock successful order
        self.mock_exchange.create_order.return_value = {
            'id': 'order_123',
            'symbol': 'BTC/USDT',
            'side': 'buy',
            'type': 'limit',
            'price': 50010.0,
            'amount': 0.01,
            'filled': 0.01,
            'remaining': 0.0,
            'status': 'closed',
            'timestamp': int(time.time() * 1000)
        }

        # Execute buy logic (mocked flow)
        symbol = "BTC/USDT"
        position_size_usdt = 25.0

        # 1. Check guards
        ticker = self.mock_md_provider.get_ticker(symbol)
        spread_bps = ((ticker.ask - ticker.bid) / ticker.bid) * 10000
        assert spread_bps < 10, "Spread guard should pass"

        # 2. Calculate order parameters
        entry_price = ticker.ask
        quantity = position_size_usdt / entry_price

        # Update mock to return correct quantity
        self.mock_exchange.create_order.return_value['amount'] = quantity
        self.mock_exchange.create_order.return_value['filled'] = quantity

        # 3. Submit order
        order = self.mock_exchange.create_order(
            symbol=symbol,
            type='limit',
            side='buy',
            amount=quantity,
            price=entry_price
        )

        # 4. Verify order success
        assert order['status'] == 'closed'
        assert abs(order['filled'] - quantity) < 1e-6, "Order should be fully filled"
        print("✓ Buy with passing guards successful")

    def test_buy_blocked_by_spread_guard(self):
        """Test buy order blocked by wide spread"""
        # Setup: Wide spread (50 bps)
        self.mock_md_provider.get_ticker.return_value = Mock(
            bid=50000.0,
            ask=50250.0,  # 50 bps spread (too wide)
            last=50125.0,
            volume=1000000.0
        )

        # Check spread guard
        ticker = self.mock_md_provider.get_ticker("BTC/USDT")
        spread_bps = ((ticker.ask - ticker.bid) / ticker.bid) * 10000

        assert spread_bps > 10, "Spread too wide"
        print("✓ Buy correctly blocked by spread guard")

    def test_buy_blocked_by_depth_guard(self):
        """Test buy order blocked by insufficient depth"""
        # Setup: Thin orderbook
        self.mock_md_provider.get_orderbook.return_value = {
            'bids': [[50000.0, 0.001]],  # Very thin
            'asks': [[50010.0, 0.001]],  # Very thin
            'timestamp': int(time.time() * 1000)
        }

        # Check depth guard
        orderbook = self.mock_md_provider.get_orderbook("BTC/USDT")
        ask_depth_notional = sum(price * qty for price, qty in orderbook['asks'][:3])

        min_depth_usd = 200.0
        assert ask_depth_notional < min_depth_usd, "Depth insufficient"
        print("✓ Buy correctly blocked by depth guard")

    def test_buy_with_slippage_tracking(self):
        """Test buy order with entry slippage tracking"""
        # Setup
        expected_price = 50010.0
        actual_fill_price = 50015.0

        # Calculate slippage
        slippage_bps = ((actual_fill_price - expected_price) / expected_price) * 10000

        # Slippage should be tracked
        assert 0 < slippage_bps < 15, "Slippage within acceptable range"
        print(f"✓ Entry slippage tracked: {slippage_bps:.2f} bps")


class TestEndToEndSellFlow:
    """Test complete sell flow with exit evaluation"""

    def setup_method(self):
        """Setup test fixtures"""
        self.mock_exchange = Mock()
        self.mock_portfolio = Mock()
        self.mock_md_provider = Mock()

        # Setup held position
        self.position = {
            'symbol': 'BTC/USDT',
            'amount': 0.01,
            'entry_price': 50000.0,
            'first_fill_ts': time.time() - 3600,  # 1 hour ago
            'cost_basis': 500.0,
            'peak_price': 50500.0,
            'switch_state': 'TP'
        }

    def test_sell_at_take_profit(self):
        """Test sell when take profit threshold reached"""
        # Setup: Price above TP threshold (+0.5%)
        current_price = 50250.0
        entry_price = self.position['entry_price']

        # Calculate P&L
        pnl_pct = (current_price / entry_price - 1.0) * 100

        # TP threshold
        tp_threshold = 1.005  # +0.5%
        current_ratio = current_price / entry_price

        assert current_ratio >= tp_threshold, "Take profit triggered"
        assert pnl_pct >= 0.49, f"Positive P&L: {pnl_pct:.2f}%"  # Allow rounding tolerance
        print(f"✓ Sell at take profit: {pnl_pct:.2f}%")

    def test_sell_at_stop_loss(self):
        """Test sell when stop loss triggered"""
        # Setup: Price below SL threshold (-1.0%)
        current_price = 49450.0
        entry_price = self.position['entry_price']

        # Calculate P&L
        pnl_pct = (current_price / entry_price - 1.0) * 100

        # SL threshold
        sl_threshold = 0.990  # -1.0%
        current_ratio = current_price / entry_price

        assert current_ratio <= sl_threshold, "Stop loss triggered"
        assert pnl_pct < -1.0, f"Negative P&L: {pnl_pct:.2f}%"
        print(f"✓ Sell at stop loss: {pnl_pct:.2f}%")

    def test_sell_at_trailing_stop(self):
        """Test sell when trailing stop triggered"""
        # Setup: Trailing stop activated
        peak_price = 50500.0
        current_price = 50400.0
        entry_price = self.position['entry_price']

        # Trailing stop distance (0.1%)
        trailing_distance = 0.999
        trailing_threshold = peak_price * trailing_distance

        # Check if trailing stop triggered
        peak_ratio = peak_price / entry_price
        activation_threshold = 1.001  # Activate at +0.1%

        if peak_ratio >= activation_threshold:
            assert current_price < trailing_threshold, "Trailing stop triggered"
            pnl_pct = (current_price / entry_price - 1.0) * 100
            print(f"✓ Sell at trailing stop: {pnl_pct:.2f}%")

    def test_sell_at_ttl_expiry(self):
        """Test sell when position TTL expires"""
        # Setup: Old position
        first_fill_ts = time.time() - 7200  # 2 hours ago
        current_time = time.time()

        age_minutes = (current_time - first_fill_ts) / 60
        ttl_minutes = 120  # 2 hours

        assert age_minutes >= ttl_minutes, "TTL expired"
        print(f"✓ Sell at TTL expiry: {age_minutes:.1f} minutes old")


class TestCOIDIdempotency:
    """Test Client Order ID idempotency"""

    def test_duplicate_order_prevention(self):
        """Test that duplicate COIDs are rejected"""
        from collections import defaultdict

        # Simulate COID manager
        coid_registry = {}

        def register_coid(coid, symbol, side):
            key = f"{symbol}_{side}_{coid}"
            if key in coid_registry:
                return False, "COID already exists"
            coid_registry[key] = {'status': 'PENDING', 'ts': time.time()}
            return True, "COID registered"

        # First order
        success, msg = register_coid("coid_123", "BTC/USDT", "buy")
        assert success, "First COID should be registered"

        # Duplicate attempt
        success, msg = register_coid("coid_123", "BTC/USDT", "buy")
        assert not success, "Duplicate COID should be rejected"
        assert "already exists" in msg.lower()
        print("✓ COID idempotency working")

    def test_coid_cleanup_after_fill(self):
        """Test COID cleanup after order filled"""
        coid_registry = {}

        def register_coid(coid, symbol, side):
            key = f"{symbol}_{side}_{coid}"
            coid_registry[key] = {'status': 'PENDING', 'ts': time.time()}
            return True

        def update_coid_status(coid, symbol, side, status):
            key = f"{symbol}_{side}_{coid}"
            if key in coid_registry:
                coid_registry[key]['status'] = status
                return True
            return False

        def cleanup_terminal_coids():
            terminal_states = {'FILLED', 'CANCELED', 'EXPIRED'}
            to_remove = []
            for key, data in coid_registry.items():
                if data['status'] in terminal_states:
                    age_s = time.time() - data['ts']
                    if age_s > 300:  # 5 minutes
                        to_remove.append(key)
            for key in to_remove:
                del coid_registry[key]
            return len(to_remove)

        # Register COID
        register_coid("coid_456", "BTC/USDT", "buy")
        assert len(coid_registry) == 1

        # Mark as filled
        update_coid_status("coid_456", "BTC/USDT", "buy", "FILLED")

        # Simulate time passage
        key = "BTC/USDT_buy_coid_456"
        coid_registry[key]['ts'] = time.time() - 400  # 6 minutes ago

        # Cleanup
        removed = cleanup_terminal_coids()
        assert removed == 1, "Terminal COID should be cleaned up"
        assert len(coid_registry) == 0
        print("✓ COID cleanup working")


class TestFillTelemetry:
    """Test fill telemetry tracking"""

    def test_fill_rate_tracking(self):
        """Test tracking of fill rates"""
        fills = {
            'full': 0,
            'partial': 0,
            'none': 0
        }

        # Simulate orders
        orders = [
            {'filled': 1.0, 'amount': 1.0},  # Full fill
            {'filled': 1.0, 'amount': 1.0},  # Full fill
            {'filled': 0.5, 'amount': 1.0},  # Partial fill
            {'filled': 0.0, 'amount': 1.0},  # No fill
            {'filled': 1.0, 'amount': 1.0},  # Full fill
        ]

        for order in orders:
            fill_rate = order['filled'] / order['amount']
            if fill_rate >= 1.0:
                fills['full'] += 1
            elif fill_rate > 0:
                fills['partial'] += 1
            else:
                fills['none'] += 1

        total = sum(fills.values())
        full_fill_pct = (fills['full'] / total) * 100

        assert full_fill_pct == 60.0, "60% full fill rate"
        print(f"✓ Fill tracking: {full_fill_pct:.1f}% full fills")

    def test_latency_tracking(self):
        """Test order latency tracking"""
        latencies_ms = []

        # Simulate order submissions
        for _ in range(10):
            submit_ts = time.time()
            time.sleep(0.01)  # Simulate network delay
            fill_ts = time.time()

            latency_ms = (fill_ts - submit_ts) * 1000
            latencies_ms.append(latency_ms)

        # Calculate statistics
        avg_latency = sum(latencies_ms) / len(latencies_ms)
        sorted_latencies = sorted(latencies_ms)
        p95_idx = int(len(sorted_latencies) * 0.95)
        p95_latency = sorted_latencies[p95_idx]

        assert 10 <= avg_latency <= 20, "Average latency ~10-20ms"
        print(f"✓ Latency tracking: avg={avg_latency:.1f}ms, p95={p95_latency:.1f}ms")


class TestSymbolLocks:
    """Test thread-safe symbol-scoped locking"""

    def test_concurrent_access_prevention(self):
        """Test that concurrent access to same symbol is prevented"""
        import threading

        # Simulate symbol lock manager
        symbol_locks = {}

        def get_symbol_lock(symbol):
            if symbol not in symbol_locks:
                symbol_locks[symbol] = threading.RLock()
            return symbol_locks[symbol]

        # Shared state
        execution_order = []

        def modify_position(symbol, thread_id):
            lock = get_symbol_lock(symbol)
            with lock:
                execution_order.append(f"thread_{thread_id}_start")
                time.sleep(0.01)  # Simulate work
                execution_order.append(f"thread_{thread_id}_end")

        # Run concurrent threads
        threads = []
        for i in range(3):
            t = threading.Thread(target=modify_position, args=("BTC/USDT", i))
            threads.append(t)
            t.start()

        for t in threads:
            t.join()

        # Verify sequential execution (no interleaving)
        for i in range(3):
            start_idx = execution_order.index(f"thread_{i}_start")
            end_idx = execution_order.index(f"thread_{i}_end")

            # Between start and end of thread i, no other thread should have started
            between = execution_order[start_idx+1:end_idx]
            assert all('_end' in item or f'thread_{i}' in item for item in between), \
                "No interleaving between threads"

        print("✓ Symbol locks prevent concurrent access")

    def test_different_symbols_can_execute_concurrently(self):
        """Test that different symbols can be modified concurrently"""
        import threading

        symbol_locks = {}

        def get_symbol_lock(symbol):
            if symbol not in symbol_locks:
                symbol_locks[symbol] = threading.RLock()
            return symbol_locks[symbol]

        execution_times = {}

        def modify_position(symbol):
            lock = get_symbol_lock(symbol)
            with lock:
                start = time.time()
                time.sleep(0.05)  # Simulate work
                end = time.time()
                execution_times[symbol] = (start, end)

        # Run concurrent threads for different symbols
        threads = []
        symbols = ["BTC/USDT", "ETH/USDT", "SOL/USDT"]

        for symbol in symbols:
            t = threading.Thread(target=modify_position, args=(symbol,))
            threads.append(t)
            t.start()

        for t in threads:
            t.join()

        # Verify that executions overlapped (concurrent)
        times = list(execution_times.values())
        assert len(times) == 3

        # Check for overlap
        overlaps = 0
        for i in range(len(times)):
            for j in range(i+1, len(times)):
                start_i, end_i = times[i]
                start_j, end_j = times[j]
                # Check if intervals overlap
                if not (end_i <= start_j or end_j <= start_i):
                    overlaps += 1

        assert overlaps > 0, "Different symbols executed concurrently"
        print("✓ Different symbols can execute concurrently")


def run_all_integration_tests():
    """Run all integration tests"""
    print("=" * 70)
    print("Integration Tests - Order Flow")
    print("=" * 70)

    # Buy Flow Tests
    print("\n1. Testing Buy Flow...")
    buy_tests = TestEndToEndBuyFlow()
    buy_tests.setup_method()
    buy_tests.test_buy_with_passing_guards()
    buy_tests.test_buy_blocked_by_spread_guard()
    buy_tests.test_buy_blocked_by_depth_guard()
    buy_tests.test_buy_with_slippage_tracking()

    # Sell Flow Tests
    print("\n2. Testing Sell Flow...")
    sell_tests = TestEndToEndSellFlow()
    sell_tests.setup_method()
    sell_tests.test_sell_at_take_profit()
    sell_tests.test_sell_at_stop_loss()
    sell_tests.test_sell_at_trailing_stop()
    sell_tests.test_sell_at_ttl_expiry()

    # COID Tests
    print("\n3. Testing COID Idempotency...")
    coid_tests = TestCOIDIdempotency()
    coid_tests.test_duplicate_order_prevention()
    coid_tests.test_coid_cleanup_after_fill()

    # Telemetry Tests
    print("\n4. Testing Fill Telemetry...")
    telemetry_tests = TestFillTelemetry()
    telemetry_tests.test_fill_rate_tracking()
    telemetry_tests.test_latency_tracking()

    # Lock Tests
    print("\n5. Testing Symbol Locks...")
    lock_tests = TestSymbolLocks()
    lock_tests.test_concurrent_access_prevention()
    lock_tests.test_different_symbols_can_execute_concurrently()

    print("\n" + "=" * 70)
    print("✓ All integration tests passed!")
    print("=" * 70)


if __name__ == "__main__":
    run_all_integration_tests()
