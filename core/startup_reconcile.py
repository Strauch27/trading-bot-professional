#!/usr/bin/env python3
"""
Startup Reconciliation - Phase 2

Reconciles pending COIDs against exchange state on startup.
Ensures no duplicate orders after crash/restart.

Usage:
    from core.startup_reconcile import reconcile_pending_coids

    # In main.py or startup sequence:
    reconcile_pending_coids(exchange, symbols=watchlist)
"""

import logging
from typing import List, Optional

logger = logging.getLogger(__name__)


def reconcile_pending_coids(
    exchange,
    symbols: Optional[List[str]] = None,
    enabled: bool = True
) -> dict:
    """
    Reconcile pending COIDs against exchange on startup.

    Queries exchange for open orders and updates COID statuses.
    This prevents duplicate orders after crash/restart.

    Args:
        exchange: CCXT exchange instance
        symbols: List of symbols to reconcile (None = all)
        enabled: Enable reconciliation (can be disabled via config)

    Returns:
        Dict with reconciliation stats:
        {
            'enabled': bool,
            'reconciled_count': int,
            'pending_count': int,
            'errors': List[str]
        }
    """
    if not enabled:
        logger.info("COID reconciliation disabled")
        return {
            'enabled': False,
            'reconciled_count': 0,
            'pending_count': 0,
            'errors': []
        }

    logger.info("=" * 80)
    logger.info("Phase 2: Starting COID Reconciliation")
    logger.info("=" * 80)

    try:
        # Import COID manager
        from core.coid import get_coid_manager

        coid_manager = get_coid_manager()

        # Get stats before reconciliation
        stats_before = coid_manager.get_stats()
        pending_before = stats_before.get('pending_count', 0)

        logger.info("COID Store Stats (before):")
        logger.info(f"  Total entries: {stats_before.get('total_entries', 0)}")
        logger.info(f"  Pending: {pending_before}")
        logger.info(f"  Terminal: {stats_before.get('terminal_count', 0)}")

        if pending_before == 0:
            logger.info("No pending COIDs to reconcile")
            return {
                'enabled': True,
                'reconciled_count': 0,
                'pending_count': 0,
                'errors': []
            }

        # Reconcile against exchange
        logger.info(f"Reconciling {pending_before} pending COIDs against exchange...")
        reconciled_count = coid_manager.reconcile_with_exchange(exchange, symbols)

        # Get stats after reconciliation
        stats_after = coid_manager.get_stats()
        pending_after = stats_after.get('pending_count', 0)

        logger.info("COID Store Stats (after):")
        logger.info(f"  Total entries: {stats_after.get('total_entries', 0)}")
        logger.info(f"  Pending: {pending_after}")
        logger.info(f"  Terminal: {stats_after.get('terminal_count', 0)}")
        logger.info(f"  Reconciled: {reconciled_count}")

        # Cleanup old entries (optional)
        try:
            cleaned = coid_manager.cleanup_old_entries(max_age_days=7)
            if cleaned > 0:
                logger.info(f"Cleaned up {cleaned} old terminal COID entries")
        except Exception as e:
            logger.warning(f"COID cleanup failed: {e}")

        logger.info("=" * 80)
        logger.info("Phase 2: COID Reconciliation Complete")
        logger.info("=" * 80)

        return {
            'enabled': True,
            'reconciled_count': reconciled_count,
            'pending_count': pending_after,
            'errors': []
        }

    except ImportError:
        error_msg = "core.coid module not available, reconciliation skipped"
        logger.warning(error_msg)
        return {
            'enabled': False,
            'reconciled_count': 0,
            'pending_count': 0,
            'errors': [error_msg]
        }

    except Exception as e:
        error_msg = f"COID reconciliation failed: {e}"
        logger.error(error_msg, exc_info=True)
        return {
            'enabled': True,
            'reconciled_count': 0,
            'pending_count': 0,
            'errors': [error_msg]
        }


def print_coid_summary():
    """
    Print COID manager summary (for debugging/monitoring).

    Call this after reconciliation to see current state.
    """
    try:
        from core.coid import get_coid_manager

        coid_manager = get_coid_manager()
        stats = coid_manager.get_stats()

        print("\n" + "=" * 80)
        print("COID Manager Summary")
        print("=" * 80)
        print(f"Total Entries:    {stats.get('total_entries', 0)}")
        print(f"Pending:          {stats.get('pending_count', 0)}")
        print(f"Terminal:         {stats.get('terminal_count', 0)}")
        print()
        print("By Status:")
        for status, count in stats.get('by_status', {}).items():
            print(f"  {status:20s} {count:5d}")
        print("=" * 80 + "\n")

    except ImportError:
        print("COID manager not available")
    except Exception as e:
        print(f"Failed to print COID summary: {e}")
