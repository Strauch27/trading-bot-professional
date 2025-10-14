#!/usr/bin/env python3
"""
Tests for Terminal UI components

Tests Rich Console integration, Live Monitors, and logging bridge.
"""

import pytest
import sys
import os
from unittest.mock import Mock, patch, MagicMock
from io import StringIO

# Add parent dir to path for imports
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


class TestRichConsoleBasics:
    """Test basic Rich Console functionality"""

    def test_console_ui_imports(self):
        """Test that console_ui module can be imported"""
        from ui import console_ui
        assert hasattr(console_ui, 'console')
        assert hasattr(console_ui, 'log_info')
        assert hasattr(console_ui, 'log_success')
        assert hasattr(console_ui, 'log_warning')
        assert hasattr(console_ui, 'log_error')

    def test_rich_availability(self):
        """Test Rich library availability detection"""
        from ui.console_ui import is_rich_available
        # Should return True or False (not None)
        assert isinstance(is_rich_available(), bool)

    def test_log_functions_callable(self):
        """Test that all log functions are callable"""
        from ui.console_ui import log_info, log_success, log_warning, log_error

        # All should be callable functions
        assert callable(log_info)
        assert callable(log_success)
        assert callable(log_warning)
        assert callable(log_error)


class TestRichLoggingIntegration:
    """Test Rich Console integration with logging system"""

    def test_loggingx_imports(self):
        """Test that loggingx module can be imported"""
        from core.logging import loggingx
        assert hasattr(loggingx, 'log_event')

    @patch('core.logging.loggingx.RICH_AVAILABLE', True)
    @patch('core.logging.loggingx.ENABLE_RICH_LOGGING', True)
    @patch('core.logging.loggingx.log_info')
    def test_log_event_calls_rich_info(self, mock_log_info):
        """Test that log_event calls Rich Console for INFO level"""
        from core.logging.loggingx import _rich_console_output

        _rich_console_output(
            event_type="TEST_EVENT",
            level="INFO",
            symbol="BTC/USDT",
            message="Test message",
            context={"key": "value"}
        )

        # Should call log_info with formatted message
        mock_log_info.assert_called_once()
        call_args = mock_log_info.call_args
        assert "TEST_EVENT" in call_args[0][0]
        assert "[BTC/USDT]" in call_args[0][0]

    @patch('core.logging.loggingx.RICH_AVAILABLE', True)
    @patch('core.logging.loggingx.ENABLE_RICH_LOGGING', True)
    @patch('core.logging.loggingx.log_error')
    def test_log_event_calls_rich_error(self, mock_log_error):
        """Test that log_event calls Rich Console for ERROR level"""
        from core.logging.loggingx import _rich_console_output

        _rich_console_output(
            event_type="ERROR_EVENT",
            level="ERROR",
            symbol="ETH/USDT",
            message="Error occurred",
            context={"error": "timeout"}
        )

        # Should call log_error with formatted message
        mock_log_error.assert_called_once()
        call_args = mock_log_error.call_args
        assert "ERROR_EVENT" in call_args[0][0]


class TestLiveMonitors:
    """Test Live Monitor components"""

    def test_live_monitors_imports(self):
        """Test that live_monitors module can be imported"""
        from ui import live_monitors
        assert hasattr(live_monitors, 'LiveHeartbeat')
        assert hasattr(live_monitors, 'DropMonitorView')
        assert hasattr(live_monitors, 'PortfolioMonitorView')
        assert hasattr(live_monitors, 'LiveDashboard')

    def test_live_heartbeat_instantiation(self):
        """Test LiveHeartbeat can be instantiated"""
        from ui.live_monitors import LiveHeartbeat

        heartbeat = LiveHeartbeat()
        assert heartbeat is not None

    def test_live_heartbeat_with_providers(self):
        """Test LiveHeartbeat with mock providers"""
        from ui.live_monitors import LiveHeartbeat

        # Mock market data provider
        mock_md_provider = Mock()
        mock_md_provider.get_statistics.return_value = {
            "ticker": {"requests": 100, "hits": 80, "stale": 5},
            "coalescing": {"total": 100, "coalesced": 20, "rate_pct": 20.0},
            "rate_limiting": {"acquired": 100, "throttled": 5, "throttle_pct": 5.0}
        }

        # Mock fill tracker
        mock_fill_tracker = Mock()
        mock_fill_tracker.get_statistics.return_value = {
            "total_orders": 50,
            "filled_orders": 40,
            "partial_orders": 5,
            "fill_rate_pct": 80.0,
            "avg_latency_ms": 150.0
        }

        heartbeat = LiveHeartbeat(
            market_data_provider=mock_md_provider,
            fill_tracker=mock_fill_tracker
        )

        # Should be able to create stats dict
        stats = {
            "session_id": "test123",
            "uptime_s": 3600,
            "mode": "LIVE",
            "engine": "active",
            "budget_usdt": 1000.0,
            "positions": 5,
            "memory_mb": 250.0
        }

        # Should render without error
        rendered = heartbeat._render(stats)
        assert rendered is not None

    def test_drop_monitor_view_instantiation(self):
        """Test DropMonitorView can be instantiated"""
        from ui.live_monitors import DropMonitorView

        mock_drop_tracker = Mock()
        drop_monitor = DropMonitorView(mock_drop_tracker)
        assert drop_monitor is not None

    def test_portfolio_monitor_view_instantiation(self):
        """Test PortfolioMonitorView can be instantiated"""
        from ui.live_monitors import PortfolioMonitorView

        mock_portfolio = Mock()
        mock_md_provider = Mock()

        portfolio_monitor = PortfolioMonitorView(
            portfolio=mock_portfolio,
            market_data_provider=mock_md_provider
        )
        assert portfolio_monitor is not None

    def test_live_dashboard_instantiation(self):
        """Test LiveDashboard can be instantiated"""
        from ui.live_monitors import LiveDashboard

        dashboard = LiveDashboard()
        assert dashboard is not None

    def test_live_dashboard_update(self):
        """Test LiveDashboard update with mock data"""
        from ui.live_monitors import LiveDashboard

        dashboard = LiveDashboard()

        system_stats = {
            "session_id": "test123",
            "uptime_s": 3600,
            "mode": "LIVE",
            "engine": "active",
            "budget_usdt": 1000.0,
            "positions": 5,
            "memory_mb": 250.0
        }

        drop_rows = [
            ("BTC/USDT", -2.5, "1.2M", "2m 15s"),
            ("ETH/USDT", -1.8, "850K", "1m 42s")
        ]

        portfolio_data = {
            "positions": [
                {
                    "symbol": "BTC/USDT",
                    "qty": 0.1,
                    "entry_price": 50000.0,
                    "current_price": 50150.0,
                    "pnl": 15.0
                }
            ],
            "total_pnl": 15.0
        }

        # Should update without error (won't actually display in test)
        dashboard.update(system_stats, drop_rows, portfolio_data)


class TestConfigFlags:
    """Test configuration flags for Terminal UI"""

    def test_rich_logging_flag_exists(self):
        """Test that ENABLE_RICH_LOGGING flag exists in config"""
        import config
        assert hasattr(config, 'ENABLE_RICH_LOGGING')
        assert isinstance(config.ENABLE_RICH_LOGGING, bool)

    def test_live_monitors_flags_exist(self):
        """Test that Live Monitor flags exist in config"""
        import config

        assert hasattr(config, 'ENABLE_LIVE_MONITORS')
        assert hasattr(config, 'ENABLE_LIVE_HEARTBEAT')
        assert hasattr(config, 'ENABLE_LIVE_DASHBOARD')
        assert hasattr(config, 'LIVE_MONITOR_REFRESH_S')

        # Check types
        assert isinstance(config.ENABLE_LIVE_MONITORS, bool)
        assert isinstance(config.ENABLE_LIVE_HEARTBEAT, bool)
        assert isinstance(config.ENABLE_LIVE_DASHBOARD, bool)
        assert isinstance(config.LIVE_MONITOR_REFRESH_S, (int, float))


class TestColorHelpers:
    """Test color helper functions"""

    def test_color_value_helper_exists(self):
        """Test that color_value helper exists in live_monitors"""
        from ui.live_monitors import color_value
        assert callable(color_value)

    def test_color_value_good_threshold(self):
        """Test color_value returns green for good values"""
        from ui.live_monitors import color_value

        # Value below good threshold should be green
        result = color_value(50.0, good_threshold=70.0, warn_threshold=50.0)
        assert "[green]" in result or "[green bold]" in result

    def test_color_latency_helper_exists(self):
        """Test that color_latency helper exists"""
        from ui.live_monitors import color_latency
        assert callable(color_latency)


class TestFallbackBehavior:
    """Test fallback behavior when Rich is not available"""

    @patch('ui.console_ui.RICH_AVAILABLE', False)
    def test_log_functions_work_without_rich(self):
        """Test that log functions work even without Rich"""
        # Reload module to apply patch
        import importlib
        from ui import console_ui
        importlib.reload(console_ui)

        # Should not raise errors
        try:
            console_ui.log_info("Test message")
            console_ui.log_success("Success message")
            console_ui.log_warning("Warning message")
            console_ui.log_error("Error message")
        except Exception as e:
            pytest.fail(f"Log functions failed without Rich: {e}")


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
