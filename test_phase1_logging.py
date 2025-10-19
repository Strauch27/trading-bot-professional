#!/usr/bin/env python3
"""
Phase 1 Logging Test Script

Demonstrates usage of the new structured logging system:
- Correlation IDs via ContextVars
- JSONL event logging
- Exchange API tracing

Run this script to verify Phase 1 implementation works correctly.
"""

import sys
import os

# Add project root to path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from core.trace_context import (
    Trace,
    set_session_id,
    new_decision_id,
    new_order_req_id,
)
from core.logger_factory import (
    DECISION_LOG,
    ORDER_LOG,
    TRACER_LOG,
    AUDIT_LOG,
    HEALTH_LOG,
    log_event,
    install_global_excepthook,
)


def test_correlation_ids():
    """Test correlation ID propagation."""
    print("\n=== Testing Correlation IDs ===")

    # Set global session ID
    set_session_id("test_session_123")

    # Test decision context
    with Trace(decision_id="dec_test_001") as tr:
        log_event(
            DECISION_LOG(),
            "drop_trigger_eval",
            message="Testing drop trigger evaluation",
            symbol="BTC/USDT",
            anchor=100000.0,
            current_price=96000.0,
            drop_pct=-4.0,
            threshold=-4.0,
            threshold_hit=True,
        )

        # Add order context
        tr.set(order_req_id="oreq_test_001", client_order_id="cli_order_001")

        log_event(
            ORDER_LOG(),
            "order_attempt",
            message="Attempting to place order",
            symbol="BTC/USDT",
            side="buy",
            type="limit",
            price=96000.0,
            qty=0.001,
            notional=96.0,
        )

    print("[OK] Correlation IDs test passed - check logs/decisions/decision.jsonl")
    print("[OK] Correlation IDs test passed - check logs/orders/order.jsonl")


def test_decision_events():
    """Test decision event logging."""
    print("\n=== Testing Decision Events ===")

    with Trace(decision_id=new_decision_id()):
        # Anchor update
        log_event(
            DECISION_LOG(),
            "anchor_update",
            symbol="ETH/USDT",
            anchor_old=3500.0,
            anchor_new=3600.0,
            source="first_tick",
        )

        # Guards evaluation
        log_event(
            DECISION_LOG(),
            "guards_eval",
            symbol="ETH/USDT",
            guards=[
                {"name": "sma_guard", "passed": True, "value": 3580.0, "threshold": 3500.0},
                {"name": "spread_guard", "passed": True, "value": 25, "threshold": 35},
                {"name": "volume_guard", "passed": False, "value": 0.95, "threshold": 1.02},
            ],
            all_passed=False,
        )

        # Sizing calculation
        log_event(
            DECISION_LOG(),
            "sizing_calc",
            symbol="ETH/USDT",
            position_size_usdt_cfg=25.0,
            quote_budget=58.63,
            min_notional=26.0,
            fees_bps=10,
            slippage_bps=5,
            qty_raw=0.00694444,
            qty_rounded=0.00694,
            quote_after_round=24.98,
            passed=False,
            fail_reason="insufficient_budget_after_sizing",
        )

        # Decision outcome
        log_event(
            DECISION_LOG(),
            "decision_outcome",
            symbol="ETH/USDT",
            action="skip",
            reason="guards_failed: volume_guard",
        )

    print("[OK] Decision events test passed - check logs/decisions/decision.jsonl")


def test_order_lifecycle():
    """Test order lifecycle events."""
    print("\n=== Testing Order Lifecycle ===")

    decision_id = new_decision_id()
    order_req_id = new_order_req_id()
    client_order_id = "cli_order_12345"

    with Trace(decision_id=decision_id, order_req_id=order_req_id, client_order_id=client_order_id) as tr:
        # Order attempt
        log_event(
            ORDER_LOG(),
            "order_attempt",
            symbol="SOL/USDT",
            side="buy",
            type="limit",
            tif="IOC",
            price=150.0,
            qty=0.166,
            notional=24.9,
        )

        # Simulate order acknowledgment
        tr.set(exchange_order_id="exch_order_98765")

        log_event(
            ORDER_LOG(),
            "order_ack",
            symbol="SOL/USDT",
            exchange_order_id="exch_order_98765",
            latency_ms=285,
            exchange_raw={
                "id": "exch_order_98765",
                "status": "open",
                "filled": 0.0,
                "remaining": 0.166,
            },
        )

        # Order fill
        log_event(
            ORDER_LOG(),
            "order_update",
            symbol="SOL/USDT",
            status="closed",
            filled_qty=0.166,
            avg_price=150.05,
            trades=[
                {"trade_id": "tr_001", "qty": 0.166, "price": 150.05, "fee": 0.025}
            ],
        )

        # Order done
        log_event(
            ORDER_LOG(),
            "order_done",
            symbol="SOL/USDT",
            final_status="closed",
            latency_ms_total=450,
        )

    print("[OK] Order lifecycle test passed - check logs/orders/order.jsonl")


def test_health_events():
    """Test health and audit events."""
    print("\n=== Testing Health & Audit Events ===")

    # Heartbeat
    log_event(
        HEALTH_LOG(),
        "heartbeat",
        threads_alive=["MainThread", "Worker-1"],
        queue_lengths={"signals": 0, "orders": 0},
        cpu_pct=15.2,
        mem_pct=42.8,
        symbols_active=91,
    )

    # Rate limit hit
    log_event(
        HEALTH_LOG(),
        "rate_limit_hit",
        symbol="BTC/USDT",
        backoff_ms=1000,
        remaining_weight=5,
    )

    # Config change
    log_event(
        AUDIT_LOG(),
        "config_change",
        from_hash="abc123",
        to_hash="def456",
        changed_params=["POSITION_SIZE_USDT", "MAX_AUTO_SIZE_UP_BPS"],
    )

    print("[OK] Health events test passed - check logs/health/health.jsonl")
    print("[OK] Audit events test passed - check logs/audit/audit.jsonl")


def test_exception_logging():
    """Test exception logging."""
    print("\n=== Testing Exception Logging ===")

    # Install global exception hook
    install_global_excepthook()

    # Log a handled exception
    try:
        # Simulate an error
        raise ValueError("Test exception for logging")
    except Exception as e:
        import traceback
        log_event(
            AUDIT_LOG(),
            "exception",
            message=f"Handled exception: {type(e).__name__}: {e}",
            exception_class=type(e).__name__,
            exception_message=str(e),
            stacktrace=traceback.format_exc(),
            component="test_script",
        )

    print("[OK] Exception logging test passed - check logs/audit/audit.jsonl")


def main():
    """Run all tests."""
    print("\n" + "=" * 60)
    print("PHASE 1 LOGGING SYSTEM TEST")
    print("=" * 60)

    try:
        test_correlation_ids()
        test_decision_events()
        test_order_lifecycle()
        test_health_events()
        test_exception_logging()

        print("\n" + "=" * 60)
        print("✅ ALL TESTS PASSED")
        print("=" * 60)
        print("\nCheck the following log files:")
        print("  - logs/decisions/decision.jsonl")
        print("  - logs/orders/order.jsonl")
        print("  - logs/health/health.jsonl")
        print("  - logs/audit/audit.jsonl")
        print("\nYou can inspect them with:")
        print("  cat logs/decisions/decision.jsonl | jq")
        print("\n")

    except Exception as e:
        print(f"\n❌ TEST FAILED: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)


if __name__ == "__main__":
    main()
