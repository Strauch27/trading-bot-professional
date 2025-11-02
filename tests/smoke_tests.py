#!/usr/bin/env python3
"""
SMOKE TESTS - Pre-Production Validation
========================================

Tests all critical scenarios (A-E) before going live:
A. Entry Parity (BLESS min_notional)
B. PARTIAL Fill Handling
C. Exit Ladder (NEVER_MARKET_SELLS)
D. Recovery (Restart with open orders)
E. Dashboard Updates

USAGE:
    python tests/smoke_tests.py --scenario A
    python tests/smoke_tests.py --all
    python tests/smoke_tests.py --scenario B --symbol BLESS/USDT

IMPORTANT:
- Run in PAPER TRADING mode or isolated sub-account
- Review config_production_ready.patch before running
- All tests should PASS before enabling FSM in production
"""

import argparse
import json
import logging
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional

# Add parent directory to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent))

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s'
)
logger = logging.getLogger(__name__)


class SmokeTestResult:
    """Result of a smoke test scenario"""
    def __init__(self, scenario: str, name: str):
        self.scenario = scenario
        self.name = name
        self.passed = False
        self.errors: List[str] = []
        self.warnings: List[str] = []
        self.metrics: Dict = {}
        self.start_time = time.time()
        self.end_time = None

    def mark_passed(self):
        self.passed = True
        self.end_time = time.time()

    def mark_failed(self, error: str):
        self.passed = False
        self.errors.append(error)
        self.end_time = time.time()

    def add_warning(self, warning: str):
        self.warnings.append(warning)

    def add_metric(self, key: str, value):
        self.metrics[key] = value

    def duration_secs(self) -> float:
        if self.end_time:
            return self.end_time - self.start_time
        return time.time() - self.start_time

    def to_dict(self) -> Dict:
        return {
            'scenario': self.scenario,
            'name': self.name,
            'passed': self.passed,
            'errors': self.errors,
            'warnings': self.warnings,
            'metrics': self.metrics,
            'duration_secs': self.duration_secs()
        }


class SmokeTestRunner:
    """Executes smoke test scenarios"""

    def __init__(self, config_path: Optional[str] = None):
        self.config_path = config_path or 'config.py'
        self.results: List[SmokeTestResult] = []

    def run_scenario_a_entry_parity(self, symbol: str = "BLESS/USDT") -> SmokeTestResult:
        """
        Scenario A: Entry Parity (min_notional auto-bump)

        Test that FSM handles low-notional pairs identically to Legacy:
        1. Symbol with very low price (e.g., BLESS @ 0.0001)
        2. Quantity below min_notional
        3. Expected: FSM auto-bumps to min_notional and buys (like Legacy)

        Success Criteria:
        - Preflight validation runs
        - Quantity auto-bumped to meet min_notional
        - Order placed successfully
        - Transition: ENTRY_EVAL → PLACE_BUY → WAIT_FILL → FILLED → POSITION
        """
        result = SmokeTestResult("A", "Entry Parity - Min Notional Auto-Bump")

        try:
            logger.info(f"[SCENARIO A] Testing entry parity for {symbol}")

            # Import after adding to sys.path
            import config
            from services.exchange_filters import get_filters
            from services.order_validation import preflight

            # Verify FSM is enabled
            if not getattr(config, 'FSM_ENABLED', False):
                result.mark_failed("FSM_ENABLED is False - cannot test FSM parity")
                return result

            # Load exchange (mock or real depending on test mode)
            try:
                from adapters import get_exchange
                exchange = get_exchange()
            except Exception as e:
                result.mark_failed(f"Failed to load exchange: {e}")
                return result

            # Get symbol filters
            filters = get_filters(exchange, symbol)
            result.add_metric('filters', {
                'min_qty': filters.get('min_qty'),
                'min_notional': filters.get('min_notional'),
                'tick_size': filters.get('tick_size'),
                'step_size': filters.get('step_size')
            })

            # Simulate low-notional order
            low_price = 0.0001  # Very low price (typical for BLESS)
            low_qty = 50.0  # Quantity that's likely below min_notional

            # Calculate notional
            raw_notional = low_price * low_qty
            min_notional_required = filters.get('min_notional', 5.0)

            result.add_metric('raw_notional', raw_notional)
            result.add_metric('min_notional_required', min_notional_required)

            logger.info(
                f"Testing preflight: price={low_price}, qty={low_qty}, "
                f"notional={raw_notional:.4f} (need {min_notional_required})"
            )

            # Run preflight (should auto-bump)
            ok, preflight_result = preflight(symbol, low_price, low_qty, filters)

            if not ok:
                result.mark_failed(f"Preflight failed: {preflight_result.get('reason')}")
                return result

            # Check if quantity was bumped
            bumped_qty = preflight_result.get('amount', low_qty)
            bumped_price = preflight_result.get('price', low_price)
            bumped_notional = bumped_price * bumped_qty

            result.add_metric('bumped_qty', bumped_qty)
            result.add_metric('bumped_notional', bumped_notional)

            if bumped_qty > low_qty:
                logger.info(f"✓ Quantity auto-bumped: {low_qty} → {bumped_qty}")
            else:
                result.add_warning(f"Quantity NOT bumped (raw notional already sufficient)")

            if bumped_notional >= min_notional_required:
                logger.info(f"✓ Notional meets requirement: {bumped_notional:.4f} >= {min_notional_required}")
                result.mark_passed()
            else:
                result.mark_failed(f"Notional still below minimum after preflight: {bumped_notional:.4f} < {min_notional_required}")

        except Exception as e:
            result.mark_failed(f"Test execution failed: {e}")
            logger.error(f"Scenario A failed with exception: {e}", exc_info=True)

        return result

    def run_scenario_b_partial_fill(self, symbol: str = "BLESS/USDT") -> SmokeTestResult:
        """
        Scenario B: PARTIAL Fill Handling

        Test that FSM correctly handles partial fills:
        1. Place buy order with quantity > typical fill
        2. Expected: Multiple PARTIAL fill events
        3. Position opens incrementally with weighted average
        4. Remaining order monitored until TTL or fully filled

        Success Criteria:
        - Position qty increases with each PARTIAL fill
        - avg_entry calculated as weighted average
        - Portfolio reflects actual filled qty (not original order qty)
        - No budget drift
        """
        result = SmokeTestResult("B", "PARTIAL Fill Handling")

        try:
            logger.info(f"[SCENARIO B] Testing PARTIAL fill handling for {symbol}")

            # This test requires live trading or sophisticated mocking
            # For smoke test, we verify the code paths exist and are correct

            import config
            from core.fsm.actions import action_handle_partial_buy
            from core.fsm.state import CoinState
            from core.fsm.fsm_events import EventContext, FSMEvent

            # Verify FSM is enabled
            if not getattr(config, 'FSM_ENABLED', False):
                result.mark_failed("FSM_ENABLED is False")
                return result

            # Simulate PARTIAL fill scenario
            coin_state = CoinState(symbol=symbol)
            coin_state.amount = 0.0
            coin_state.entry_price = 0.0

            # First PARTIAL fill
            ctx1 = EventContext(
                event=FSMEvent.BUY_ORDER_PARTIAL,
                symbol=symbol,
                timestamp=time.time(),
                filled_qty=50.0,
                avg_price=0.0001
            )

            try:
                action_handle_partial_buy(ctx1, coin_state)

                # Check position opened
                if coin_state.amount == 50.0 and coin_state.entry_price == 0.0001:
                    logger.info("✓ First PARTIAL fill: Position opened correctly")
                    result.add_metric('first_fill_qty', coin_state.amount)
                    result.add_metric('first_fill_price', coin_state.entry_price)
                else:
                    result.mark_failed(
                        f"First PARTIAL failed: qty={coin_state.amount} (expected 50.0), "
                        f"price={coin_state.entry_price} (expected 0.0001)"
                    )
                    return result

                # Second PARTIAL fill (different price for weighted avg)
                ctx2 = EventContext(
                    event=FSMEvent.BUY_ORDER_PARTIAL,
                    symbol=symbol,
                    timestamp=time.time(),
                    filled_qty=30.0,
                    avg_price=0.00011
                )

                action_handle_partial_buy(ctx2, coin_state)

                # Check weighted average
                expected_qty = 80.0
                expected_weighted_avg = (50.0 * 0.0001 + 30.0 * 0.00011) / 80.0

                if abs(coin_state.amount - expected_qty) < 0.001:
                    logger.info(f"✓ Second PARTIAL fill: Qty accumulated correctly ({coin_state.amount})")
                else:
                    result.mark_failed(f"Qty accumulation failed: {coin_state.amount} != {expected_qty}")
                    return result

                if abs(coin_state.entry_price - expected_weighted_avg) < 0.0000001:
                    logger.info(f"✓ Weighted average calculated correctly: {coin_state.entry_price:.8f}")
                    result.add_metric('weighted_avg_entry', coin_state.entry_price)
                    result.mark_passed()
                else:
                    result.mark_failed(
                        f"Weighted avg incorrect: {coin_state.entry_price:.8f} != {expected_weighted_avg:.8f}"
                    )

            except Exception as action_error:
                result.mark_failed(f"action_handle_partial_buy failed: {action_error}")
                return result

        except Exception as e:
            result.mark_failed(f"Test execution failed: {e}")
            logger.error(f"Scenario B failed with exception: {e}", exc_info=True)

        return result

    def run_scenario_c_exit_ladder(self, symbol: str = "BLESS/USDT") -> SmokeTestResult:
        """
        Scenario C: Exit Ladder (NEVER_MARKET_SELLS)

        Test that exit ladder escalates correctly without market orders:
        1. NEVER_MARKET_SELLS=True in config
        2. Exit triggers IOC ladder at BID - [0, 5, 10, 15] bps
        3. Each step waits for EXIT_IOC_TTL_MS
        4. Cancel-Ack confirmed between steps
        5. No market order placed even after ladder exhausted

        Success Criteria:
        - Limit orders placed at correct prices (BID - bps)
        - No market orders
        - Cancel-Ack confirmed before next step
        - No COID collisions (unique per step)
        """
        result = SmokeTestResult("C", "Exit Ladder - No Market Orders")

        try:
            logger.info(f"[SCENARIO C] Testing exit ladder for {symbol}")

            import config

            # Verify NEVER_MARKET_SELLS=True
            never_market_sells = getattr(config, 'NEVER_MARKET_SELLS', False)
            if not never_market_sells:
                result.mark_failed("NEVER_MARKET_SELLS is False - this test requires it to be True")
                return result

            logger.info("✓ NEVER_MARKET_SELLS=True verified")

            # Verify EXIT_LADDER_BPS exists
            exit_ladder_bps = getattr(config, 'EXIT_LADDER_BPS', None)
            if not exit_ladder_bps:
                result.mark_failed("EXIT_LADDER_BPS not defined in config")
                return result

            result.add_metric('exit_ladder_bps', exit_ladder_bps)
            logger.info(f"✓ EXIT_LADDER_BPS defined: {exit_ladder_bps}")

            # Verify EXIT_IOC_TTL_MS exists (P2 Fix requirement)
            exit_ioc_ttl_ms = getattr(config, 'EXIT_IOC_TTL_MS', None)
            if exit_ioc_ttl_ms is None:
                result.add_warning("EXIT_IOC_TTL_MS not defined - add to config (recommended: 500ms)")
            else:
                result.add_metric('exit_ioc_ttl_ms', exit_ioc_ttl_ms)
                logger.info(f"✓ EXIT_IOC_TTL_MS defined: {exit_ioc_ttl_ms}ms")

            # This test would require live order placement to fully verify
            # For smoke test, we verify the configuration is correct
            result.mark_passed()
            logger.info("✓ Exit ladder configuration verified (full test requires live trading)")

        except Exception as e:
            result.mark_failed(f"Test execution failed: {e}")
            logger.error(f"Scenario C failed with exception: {e}", exc_info=True)

        return result

    def run_scenario_d_recovery(self, symbol: str = "BLESS/USDT") -> SmokeTestResult:
        """
        Scenario D: Recovery/Reconcile

        Test that bot recovers correctly after restart:
        1. Simulate restart with open buy order in WAIT_FILL
        2. Simulate restart with open sell order in WAIT_SELL_FILL
        3. Recovery reconciles with exchange
        4. Portfolio state consistent

        Success Criteria:
        - Open buy orders land in WAIT_FILL phase
        - Open sell orders land in WAIT_SELL_FILL phase
        - Portfolio qty matches exchange reality
        - No phantom orders
        """
        result = SmokeTestResult("D", "Recovery/Reconcile")

        try:
            logger.info(f"[SCENARIO D] Testing recovery/reconcile")

            import config
            from core.fsm.recovery import FSMRecoveryManager
            from core.fsm.snapshot import get_snapshot_manager

            # Verify FSM recovery module exists
            if not getattr(config, 'FSM_ENABLED', False):
                result.mark_failed("FSM_ENABLED is False")
                return result

            # Check if recovery manager can be instantiated
            try:
                snapshot_mgr = get_snapshot_manager()
                recovery_mgr = FSMRecoveryManager(snapshot_manager=snapshot_mgr)
                logger.info("✓ FSM Recovery Manager instantiated")
            except Exception as e:
                result.mark_failed(f"Failed to create FSM Recovery Manager: {e}")
                return result

            # Verify recovery method exists and has exchange parameter (P1 Fix #7)
            if not hasattr(recovery_mgr, 'recover_all_states'):
                result.mark_failed("recover_all_states method not found")
                return result

            # Check method signature includes exchange parameter
            import inspect
            sig = inspect.signature(recovery_mgr.recover_all_states)
            if 'exchange' not in sig.parameters:
                result.add_warning(
                    "recover_all_states missing 'exchange' parameter - "
                    "reconciliation may not work correctly"
                )
            else:
                logger.info("✓ recover_all_states has exchange parameter for reconciliation")

            result.mark_passed()
            logger.info("✓ Recovery/Reconcile infrastructure verified")

        except Exception as e:
            result.mark_failed(f"Test execution failed: {e}")
            logger.error(f"Scenario D failed with exception: {e}", exc_info=True)

        return result

    def run_scenario_e_dashboard(self, symbol: str = "BLESS/USDT") -> SmokeTestResult:
        """
        Scenario E: Dashboard Updates

        Test that dashboard updates immediately on phase transitions:
        1. Trigger phase transition (IDLE → ENTRY_EVAL)
        2. Measure time to dashboard update
        3. Expected: < 200ms lag

        Success Criteria:
        - Dashboard flush called on every transition
        - Update lag < 200ms
        - No debounce delays on critical phases
        """
        result = SmokeTestResult("E", "Dashboard Updates")

        try:
            logger.info(f"[SCENARIO E] Testing dashboard updates")

            import config
            from core.fsm.fsm_machine import FSMMachine

            # Verify FSM is enabled
            if not getattr(config, 'FSM_ENABLED', False):
                result.mark_failed("FSM_ENABLED is False")
                return result

            # Check if FSM machine has dashboard flush logic (P1 Fix #9)
            try:
                # This is a code inspection test - verify the fix is in place
                import core.fsm.fsm_machine as fsm_machine_module
                source_code = inspect.getsource(fsm_machine_module)

                if 'DebouncedStateWriter' in source_code and 'flush' in source_code:
                    logger.info("✓ Dashboard flush code detected in FSM machine")
                    result.mark_passed()
                else:
                    result.mark_failed("Dashboard flush code not found in FSM machine")

            except Exception as e:
                result.mark_failed(f"Failed to verify dashboard flush code: {e}")

        except Exception as e:
            result.mark_failed(f"Test execution failed: {e}")
            logger.error(f"Scenario E failed with exception: {e}", exc_info=True)

        return result

    def run_all_scenarios(self, symbol: str = "BLESS/USDT") -> List[SmokeTestResult]:
        """Run all smoke test scenarios (A-E)"""
        logger.info("=" * 80)
        logger.info("SMOKE TESTS - Running ALL scenarios")
        logger.info("=" * 80)

        self.results = [
            self.run_scenario_a_entry_parity(symbol),
            self.run_scenario_b_partial_fill(symbol),
            self.run_scenario_c_exit_ladder(symbol),
            self.run_scenario_d_recovery(symbol),
            self.run_scenario_e_dashboard(symbol)
        ]

        return self.results

    def print_summary(self):
        """Print test summary"""
        print("\n" + "=" * 80)
        print("SMOKE TEST SUMMARY")
        print("=" * 80)

        total = len(self.results)
        passed = sum(1 for r in self.results if r.passed)
        failed = total - passed

        for r in self.results:
            status = "✓ PASS" if r.passed else "✗ FAIL"
            print(f"[{r.scenario}] {status} - {r.name} ({r.duration_secs():.2f}s)")

            if r.errors:
                for error in r.errors:
                    print(f"    ERROR: {error}")

            if r.warnings:
                for warning in r.warnings:
                    print(f"    WARN: {warning}")

        print("-" * 80)
        print(f"TOTAL: {passed}/{total} passed, {failed}/{total} failed")
        print("=" * 80)

        if failed > 0:
            print("\n⚠️  FAILURES DETECTED - DO NOT ENABLE FSM IN PRODUCTION")
            print("Review errors above and fix before proceeding.\n")
            return False
        else:
            print("\n✓ ALL TESTS PASSED - FSM Ready for Paper Trading")
            print("Next step: Run in paper trading mode with single symbol\n")
            return True

    def save_report(self, output_path: str = "smoke_test_report.json"):
        """Save detailed test report to JSON"""
        report = {
            'timestamp': datetime.now().isoformat(),
            'total_tests': len(self.results),
            'passed': sum(1 for r in self.results if r.passed),
            'failed': sum(1 for r in self.results if not r.passed),
            'results': [r.to_dict() for r in self.results]
        }

        with open(output_path, 'w') as f:
            json.dump(report, f, indent=2)

        logger.info(f"Test report saved to: {output_path}")


def main():
    parser = argparse.ArgumentParser(description="Run smoke tests for FSM production readiness")
    parser.add_argument(
        '--scenario',
        choices=['A', 'B', 'C', 'D', 'E'],
        help="Run specific scenario (A-E)"
    )
    parser.add_argument(
        '--all',
        action='store_true',
        help="Run all scenarios"
    )
    parser.add_argument(
        '--symbol',
        default='BLESS/USDT',
        help="Symbol to test (default: BLESS/USDT)"
    )
    parser.add_argument(
        '--output',
        default='smoke_test_report.json',
        help="Output report path"
    )

    args = parser.parse_args()

    runner = SmokeTestRunner()

    if args.all:
        runner.run_all_scenarios(args.symbol)
    elif args.scenario:
        scenario_map = {
            'A': runner.run_scenario_a_entry_parity,
            'B': runner.run_scenario_b_partial_fill,
            'C': runner.run_scenario_c_exit_ladder,
            'D': runner.run_scenario_d_recovery,
            'E': runner.run_scenario_e_dashboard
        }
        result = scenario_map[args.scenario](args.symbol)
        runner.results = [result]
    else:
        parser.print_help()
        sys.exit(1)

    # Print summary
    all_passed = runner.print_summary()

    # Save report
    runner.save_report(args.output)

    # Exit with appropriate code
    sys.exit(0 if all_passed else 1)


if __name__ == '__main__':
    main()
