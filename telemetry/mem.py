#!/usr/bin/env python3
"""
Memory Helper - Lightweight Memory Metrics

Provides simple memory metrics for heartbeat display:
- rss_mb(): RSS memory in MB
- percent(): System memory percentage

Uses psutil if available, otherwise falls back to dummy values.
Integrates with existing services.memory_manager if available.
"""

import logging

logger = logging.getLogger(__name__)

# Try to import psutil
try:
    import psutil
    PSUTIL_AVAILABLE = True
    _proc = psutil.Process()
except ImportError:
    PSUTIL_AVAILABLE = False
    _proc = None
    logger.warning("psutil not available - memory metrics will return dummy values")


def rss_mb() -> float:
    """
    Get current RSS (Resident Set Size) memory in MB.

    Returns:
        RSS memory in MB, or 0.0 if unavailable
    """
    if not PSUTIL_AVAILABLE or _proc is None:
        return 0.0

    try:
        # Try to use existing memory manager first
        try:
            from services.memory_manager import get_memory_manager
            memory_manager = get_memory_manager()
            memory_info = memory_manager.get_memory_status()
            return memory_info.get('current_memory', {}).get('rss_mb', 0.0)
        except (ImportError, Exception):
            # Fallback to direct psutil
            return _proc.memory_info().rss / (1024 * 1024)
    except Exception as e:
        logger.debug(f"Error getting RSS memory: {e}")
        return 0.0


def percent() -> float:
    """
    Get system-wide memory usage percentage.

    Returns:
        Memory percentage (0-100), or 0.0 if unavailable
    """
    if not PSUTIL_AVAILABLE:
        return 0.0

    try:
        # Try to use existing memory manager first
        try:
            from services.memory_manager import get_memory_manager
            memory_manager = get_memory_manager()
            memory_info = memory_manager.get_memory_status()
            return memory_info.get('current_memory', {}).get('percent', 0.0)
        except (ImportError, Exception):
            # Fallback to direct psutil (system-wide memory)
            return psutil.virtual_memory().percent
    except Exception as e:
        logger.debug(f"Error getting memory percentage: {e}")
        return 0.0


def get_memory_info() -> dict:
    """
    Get detailed memory information.

    Returns:
        Dict with keys:
            - rss_mb: RSS memory in MB
            - vms_mb: VMS memory in MB
            - percent: System memory percentage
            - available_mb: Available system memory in MB
    """
    info = {
        'rss_mb': 0.0,
        'vms_mb': 0.0,
        'percent': 0.0,
        'available_mb': 0.0
    }

    if not PSUTIL_AVAILABLE or _proc is None:
        return info

    try:
        # Process memory
        mem_info = _proc.memory_info()
        info['rss_mb'] = mem_info.rss / (1024 * 1024)
        info['vms_mb'] = mem_info.vms / (1024 * 1024)

        # System memory
        sys_mem = psutil.virtual_memory()
        info['percent'] = sys_mem.percent
        info['available_mb'] = sys_mem.available / (1024 * 1024)

    except Exception as e:
        logger.debug(f"Error getting detailed memory info: {e}")

    return info
