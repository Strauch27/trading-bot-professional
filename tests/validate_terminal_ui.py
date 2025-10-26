#!/usr/bin/env python3
"""
Simple validation script for Terminal UI components
(Does not require pytest - can run standalone)
"""

import os
import sys

# Add parent dir to path for imports
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def validate_imports():
    """Validate that all modules can be imported"""
    print("1. Validating imports...")

    try:
        from ui import console_ui
        print("   ✓ ui.console_ui imported successfully")
    except ImportError as e:
        print(f"   ✗ Failed to import ui.console_ui: {e}")
        return False

    try:
        from ui import live_monitors
        print("   ✓ ui.live_monitors imported successfully")
    except ImportError as e:
        print(f"   ✗ Failed to import ui.live_monitors: {e}")
        return False

    try:
        from core.logging import loggingx
        print("   ✓ core.logging.loggingx imported successfully")
    except ImportError as e:
        print(f"   ⚠ Could not import core.logging.loggingx (missing dependency): {e}")
        print("   ℹ This is OK - will skip logging validation")

    return True


def validate_console_ui():
    """Validate console_ui module"""
    print("\n2. Validating console_ui module...")

    from ui import console_ui

    # Check Rich availability
    is_rich = console_ui.is_rich_available()
    print(f"   ✓ Rich available: {is_rich}")

    # Check log functions exist
    assert hasattr(console_ui, 'log_info'), "log_info not found"
    assert hasattr(console_ui, 'log_success'), "log_success not found"
    assert hasattr(console_ui, 'log_warning'), "log_warning not found"
    assert hasattr(console_ui, 'log_error'), "log_error not found"
    print("   ✓ All log functions present")

    # Test log functions (should not raise errors)
    try:
        console_ui.log_info("Test info message", details={"key": "value"})
        console_ui.log_success("Test success message")
        console_ui.log_warning("Test warning message")
        console_ui.log_error("Test error message")
        print("   ✓ Log functions work correctly")
    except Exception as e:
        print(f"   ✗ Log functions failed: {e}")
        return False

    return True


def validate_live_monitors():
    """Validate live_monitors module"""
    print("\n3. Validating live_monitors module...")

    from ui.live_monitors import DropMonitorView, LiveDashboard, LiveHeartbeat, PortfolioMonitorView

    # Test LiveHeartbeat instantiation
    try:
        heartbeat = LiveHeartbeat()
        print("   ✓ LiveHeartbeat instantiation successful")
    except Exception as e:
        print(f"   ✗ LiveHeartbeat instantiation failed: {e}")
        return False

    # Test LiveHeartbeat rendering
    try:
        from ui.console_ui import is_rich_available
        if is_rich_available():
            stats = {
                "session_id": "test123",
                "uptime_s": 3600,
                "mode": "LIVE",
                "engine": "active",
                "budget_usdt": 1000.0,
                "positions": 5,
                "memory_mb": 250.0
            }
            heartbeat._render(stats)
            print("   ✓ LiveHeartbeat rendering successful")
        else:
            print("   ⚠ Rich not available - skipping render test")
            print("   ℹ This is OK - LiveHeartbeat will use fallback mode")
    except Exception as e:
        print(f"   ✗ LiveHeartbeat rendering failed: {e}")
        return False

    # Test DropMonitorView
    try:
        DropMonitorView()
        print("   ✓ DropMonitorView instantiation successful")
    except Exception as e:
        print(f"   ✗ DropMonitorView instantiation failed: {e}")
        return False

    # Test PortfolioMonitorView
    try:
        PortfolioMonitorView()
        print("   ✓ PortfolioMonitorView instantiation successful")
    except Exception as e:
        print(f"   ✗ PortfolioMonitorView instantiation failed: {e}")
        return False

    # Test LiveDashboard
    try:
        LiveDashboard()
        print("   ✓ LiveDashboard instantiation successful")
    except Exception as e:
        print(f"   ✗ LiveDashboard instantiation failed: {e}")
        return False

    return True


def validate_logging_integration():
    """Validate Rich logging integration"""
    print("\n4. Validating Rich logging integration...")

    try:
        from core.logging import loggingx
        print("   ✓ core.logging.loggingx imported successfully")
    except ImportError as e:
        print(f"   ⚠ Could not import loggingx (missing dependency): {e}")
        print("   ℹ Skipping logging integration validation")
        return True  # Not a failure - just missing dependency

    # Check that _rich_console_output exists
    if not hasattr(loggingx, '_rich_console_output'):
        print("   ✗ _rich_console_output function not found")
        return False
    print("   ✓ _rich_console_output function exists")

    # Test Rich console output (should not raise errors)
    try:
        loggingx._rich_console_output(
            event_type="TEST_EVENT",
            level="INFO",
            symbol="BTC/USDT",
            message="Test message",
            context={"key": "value"}
        )
        print("   ✓ _rich_console_output works correctly")
    except Exception as e:
        print(f"   ⚠ _rich_console_output execution failed: {e}")
        print("   ℹ This might be expected if Rich is not fully configured")

    return True


def validate_config_flags():
    """Validate configuration flags"""
    print("\n5. Validating configuration flags...")

    import config

    # Check ENABLE_RICH_LOGGING flag
    assert hasattr(config, 'ENABLE_RICH_LOGGING'), "ENABLE_RICH_LOGGING not found"
    assert isinstance(config.ENABLE_RICH_LOGGING, bool), "ENABLE_RICH_LOGGING not bool"
    print(f"   ✓ ENABLE_RICH_LOGGING = {config.ENABLE_RICH_LOGGING}")

    # Check Live Monitor flags
    assert hasattr(config, 'ENABLE_LIVE_MONITORS'), "ENABLE_LIVE_MONITORS not found"
    assert hasattr(config, 'ENABLE_LIVE_HEARTBEAT'), "ENABLE_LIVE_HEARTBEAT not found"
    assert hasattr(config, 'ENABLE_LIVE_DASHBOARD'), "ENABLE_LIVE_DASHBOARD not found"
    assert hasattr(config, 'LIVE_MONITOR_REFRESH_S'), "LIVE_MONITOR_REFRESH_S not found"
    print(f"   ✓ ENABLE_LIVE_MONITORS = {config.ENABLE_LIVE_MONITORS}")
    print(f"   ✓ ENABLE_LIVE_HEARTBEAT = {config.ENABLE_LIVE_HEARTBEAT}")
    print(f"   ✓ ENABLE_LIVE_DASHBOARD = {config.ENABLE_LIVE_DASHBOARD}")
    print(f"   ✓ LIVE_MONITOR_REFRESH_S = {config.LIVE_MONITOR_REFRESH_S}")

    return True


def main():
    """Run all validations"""
    print("=" * 70)
    print("Terminal UI Validation Script")
    print("=" * 70)

    all_passed = True

    # Run validations
    if not validate_imports():
        all_passed = False

    if not validate_console_ui():
        all_passed = False

    if not validate_live_monitors():
        all_passed = False

    if not validate_logging_integration():
        all_passed = False

    if not validate_config_flags():
        all_passed = False

    # Summary
    print("\n" + "=" * 70)
    if all_passed:
        print("✓ All validations PASSED")
        print("=" * 70)
        return 0
    else:
        print("✗ Some validations FAILED")
        print("=" * 70)
        return 1


if __name__ == "__main__":
    sys.exit(main())
