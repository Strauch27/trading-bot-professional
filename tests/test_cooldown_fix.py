#!/usr/bin/env python3
"""
Test for Symbol Cooldown after Failed Orders Fix

Tests that symbols are properly cooled down after order cancellations
to prevent infinite retry loops.
"""

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

from services.cooldown import CooldownManager
import time


def test_cooldown_manager_basic():
    """Test basic cooldown functionality"""
    manager = CooldownManager()

    # Initially no cooldown
    assert not manager.is_active("BTC/USDT")

    # Set cooldown
    manager.set("BTC/USDT", 2.0)  # 2 seconds

    # Should be active
    assert manager.is_active("BTC/USDT")

    # Check remaining time
    remaining = manager.get_remaining("BTC/USDT")
    assert 1.5 < remaining <= 2.0

    # Wait for expiration
    time.sleep(2.1)

    # Should be inactive now
    assert not manager.is_active("BTC/USDT")
    print("✓ test_cooldown_manager_basic PASSED")


def test_cooldown_multiple_symbols():
    """Test cooldown with multiple symbols"""
    manager = CooldownManager()

    # Set cooldown for multiple symbols
    manager.set("BTC/USDT", 1.0)
    manager.set("ETH/USDT", 2.0)
    manager.set("SOL/USDT", 3.0)

    # All should be active
    assert manager.is_active("BTC/USDT")
    assert manager.is_active("ETH/USDT")
    assert manager.is_active("SOL/USDT")

    # Get active symbols
    active = manager.get_active_symbols()
    assert len(active) == 3

    # Wait for first to expire
    time.sleep(1.1)

    # BTC should be inactive, others still active
    assert not manager.is_active("BTC/USDT")
    assert manager.is_active("ETH/USDT")
    assert manager.is_active("SOL/USDT")

    print("✓ test_cooldown_multiple_symbols PASSED")


def test_cooldown_clear():
    """Test manual cooldown clearing"""
    manager = CooldownManager()

    manager.set("BTC/USDT", 100.0)  # Long cooldown
    assert manager.is_active("BTC/USDT")

    # Clear it
    result = manager.clear("BTC/USDT")
    assert result is True
    assert not manager.is_active("BTC/USDT")

    # Clearing non-existent cooldown
    result = manager.clear("ETH/USDT")
    assert result is False

    print("✓ test_cooldown_clear PASSED")


def test_cooldown_stats():
    """Test cooldown statistics"""
    manager = CooldownManager()

    manager.set("BTC/USDT", 10.0)
    manager.set("ETH/USDT", 20.0)

    stats = manager.get_stats()

    assert stats['active_count'] == 2
    assert 'BTC/USDT' in stats['active_symbols']
    assert 'ETH/USDT' in stats['active_symbols']

    # Check details
    assert 'BTC/USDT' in stats['details']
    assert stats['details']['BTC/USDT']['remaining_s'] > 0

    print("✓ test_cooldown_stats PASSED")


def test_config_integration():
    """Test that config parameter is accessible"""
    import config

    # Check if our new config parameter exists
    assert hasattr(config, 'SYMBOL_COOLDOWN_AFTER_FAILED_ORDER_S')
    assert config.SYMBOL_COOLDOWN_AFTER_FAILED_ORDER_S == 60

    # Check that MAX_CONCURRENT_POSITIONS was updated
    assert hasattr(config, 'MAX_CONCURRENT_POSITIONS')
    assert config.MAX_CONCURRENT_POSITIONS == 3

    print("✓ test_config_integration PASSED")


if __name__ == '__main__':
    print("Running Cooldown Fix Tests...")
    print("=" * 60)

    test_cooldown_manager_basic()
    test_cooldown_multiple_symbols()
    test_cooldown_clear()
    test_cooldown_stats()
    test_config_integration()

    print("=" * 60)
    print("✓ ALL TESTS PASSED")
