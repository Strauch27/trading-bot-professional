#!/usr/bin/env python3
"""
Engine Monitoring Module

Contains:
- Performance metrics collection and logging
- Service statistics aggregation
- Configuration audit logging
- Final statistics reporting
"""

import logging
import time
from collections import deque
from typing import Any

import config
from core.logging.adaptive_logger import flush_performance_buffer, should_log_event, should_log_performance_metric

logger = logging.getLogger(__name__)


class EngineMonitoring:
    """
    Handles all monitoring, metrics, and statistics for the trading engine.

    Separates monitoring concerns from core engine logic.
    """

    def __init__(self, jsonl_logger, adaptive_logger):
        """Initialize monitoring with loggers"""
        self.jsonl_logger = jsonl_logger
        self.adaptive_logger = adaptive_logger

        # Performance Metrics
        self.performance_metrics = {
            'decision_times': deque(maxlen=1000),
            'order_latencies': deque(maxlen=1000),
            'market_data_latencies': deque(maxlen=1000),
            'loop_cycle_times': deque(maxlen=100)
        }

    def log_performance_metrics(self):
        """Log performance metrics summary"""
        try:
            metrics = self.performance_metrics

            # Calculate statistics
            def calc_stats(data):
                if not data:
                    return {"count": 0, "avg": 0, "min": 0, "max": 0, "p95": 0}
                arr = list(data)
                arr.sort()
                return {
                    "count": len(arr),
                    "avg": sum(arr) / len(arr),
                    "min": arr[0],
                    "max": arr[-1],
                    "p95": arr[int(0.95 * len(arr))] if len(arr) > 5 else arr[-1]
                }

            performance_summary = {
                "decision_times_ms": {k: v * 1000 for k, v in calc_stats(metrics['decision_times']).items()},
                "order_latencies_ms": {k: v * 1000 for k, v in calc_stats(metrics['order_latencies']).items()},
                "market_data_latencies_ms": {k: v * 1000 for k, v in calc_stats(metrics['market_data_latencies']).items()},
                "loop_cycle_times_ms": {k: v * 1000 for k, v in calc_stats(metrics['loop_cycle_times']).items()},
                "timestamp": time.time()
            }

            # Log to JSONL for analysis
            self.jsonl_logger.write("performance_metrics", {
                "event_type": "PERFORMANCE_SUMMARY",
                "metrics": performance_summary
            })

            logger.info(f"Performance metrics: "
                       f"decisions={performance_summary['decision_times_ms']['avg']:.1f}ms avg, "
                       f"orders={performance_summary['order_latencies_ms']['avg']:.1f}ms avg, "
                       f"market_data={performance_summary['market_data_latencies_ms']['avg']:.1f}ms avg")

        except Exception as e:
            logger.error(f"Performance metrics logging error: {e}")

    def log_configuration_snapshot(self, engine_config):
        """Log current configuration for audit trail"""
        try:
            config_snapshot = {
                # Trading Parameters
                "drop_trigger_value": getattr(config, 'DROP_TRIGGER_VALUE', None),
                "drop_trigger_mode": getattr(config, 'DROP_TRIGGER_MODE', None),
                "drop_trigger_lookback_min": getattr(config, 'DROP_TRIGGER_LOOKBACK_MIN', None),
                "take_profit_threshold": getattr(config, 'TAKE_PROFIT_THRESHOLD', None),
                "stop_loss_threshold": getattr(config, 'STOP_LOSS_THRESHOLD', None),
                "max_positions": getattr(config, 'MAX_POSITIONS', None),

                # Market Guards
                "use_btc_filter": getattr(config, 'USE_BTC_FILTER', None),
                "btc_change_threshold": getattr(config, 'BTC_CHANGE_THRESHOLD', None),
                "use_falling_coins_filter": getattr(config, 'USE_FALLING_COINS_FILTER', None),
                "falling_coins_threshold": getattr(config, 'FALLING_COINS_THRESHOLD', None),
                "use_sma_guard": getattr(config, 'USE_SMA_GUARD', None),
                "use_volume_guard": getattr(config, 'USE_VOLUME_GUARD', None),

                # Engine Settings
                "trade_ttl_min": getattr(config, 'trade_ttl_min', None),
                "ticker_cache_ttl": getattr(config, 'ticker_cache_ttl', None),
                "enable_auto_exits": getattr(config, 'enable_auto_exits', None),
                "enable_trailing_stops": getattr(config, 'enable_trailing_stops', None),

                # Feature Toggles
                "enable_top_drops_ticker": getattr(config, 'ENABLE_TOP_DROPS_TICKER', None),
                "enable_drop_trigger_minutely": getattr(config, 'ENABLE_DROP_TRIGGER_MINUTELY', None),

                # Performance Settings
                "dust_min_cost_usd": getattr(config, 'DUST_MIN_COST_USD', None),
                "buy_order_timeout_minutes": getattr(config, 'BUY_ORDER_TIMEOUT_MINUTES', None),
                "settlement_timeout": getattr(config, 'SETTLEMENT_TIMEOUT', None),

                # Trailing Configuration
                "use_relative_trailing": getattr(config, 'USE_RELATIVE_TRAILING', None),
                "tsl_activate_frac_of_tp": getattr(config, 'TSL_ACTIVATE_FRAC_OF_TP', None),
                "tsl_distance_frac_of_sl_gap": getattr(config, 'TSL_DISTANCE_FRAC_OF_SL_GAP', None),

                # Engine Config
                "engine_config": {
                    "max_positions": engine_config.max_positions,
                    "trade_ttl_min": engine_config.trade_ttl_min,
                    "ticker_cache_ttl": engine_config.ticker_cache_ttl,
                    "enable_auto_exits": engine_config.enable_auto_exits,
                    "enable_trailing_stops": engine_config.enable_trailing_stops,
                    "enable_top_drops_ticker": engine_config.enable_top_drops_ticker
                }
            }

            # Log configuration snapshot
            self.jsonl_logger.write("configuration_audit", {
                "event_type": "CONFIG_SNAPSHOT",
                "timestamp": time.time(),
                "config": config_snapshot,
                "engine_version": "V11_refactored",
                "snapshot_reason": "engine_initialization"
            })

            logger.info("Configuration snapshot logged for audit trail")

        except Exception as e:
            logger.error(f"Configuration snapshot logging error: {e}")

    def log_config_change(self, parameter: str, old_value: Any, new_value: Any, reason: str = "runtime_change"):
        """Log configuration parameter changes for audit trail"""
        try:
            self.jsonl_logger.write("configuration_audit", {
                "event_type": "CONFIG_CHANGE",
                "timestamp": time.time(),
                "parameter": parameter,
                "old_value": old_value,
                "new_value": new_value,
                "reason": reason,
                "changed_by": "engine"
            })

            logger.info(f"Config change logged: {parameter} {old_value} -> {new_value} ({reason})")

        except Exception as e:
            logger.error(f"Configuration change logging error: {e}")

    def flush_adaptive_logger_metrics(self):
        """Flush and log aggregated performance metrics from adaptive logger"""
        try:
            aggregated_metrics = flush_performance_buffer()

            for metric in aggregated_metrics:
                if should_log_performance_metric(metric['metric'], metric['avg']):
                    self.jsonl_logger.write("performance_metrics", {
                        "event_type": "PERFORMANCE_AGGREGATED",
                        **metric
                    })

            # Log adaptive logger status periodically
            status = self.adaptive_logger.get_status()
            if should_log_event("ADAPTIVE_LOGGER_STATUS"):
                self.jsonl_logger.write("system", {
                    "event_type": "ADAPTIVE_LOGGER_STATUS",
                    "status": status,
                    "timestamp": time.time()
                })

        except Exception as e:
            logger.error(f"Adaptive logger metrics flush error: {e}")

    def log_service_statistics(self, market_data, exit_manager, pnl_service):
        """Log performance statistics for all services"""
        try:
            market_stats = market_data.get_statistics()
            exit_stats = exit_manager.get_statistics()
            pnl_summary = pnl_service.get_summary()

            logger.info(f"Market Data: {market_stats['provider']['ticker_requests']} requests, "
                       f"{market_stats['ticker_cache']['hit_rate']:.1%} cache hit rate")

            logger.info(f"Exit Manager: {exit_stats['order_manager']['exits_filled']} exits, "
                       f"{exit_stats['order_manager']['fill_rate']:.1%} fill rate")

            logger.info(f"PnL: Realized ${pnl_summary.realized_pnl_net:.2f}, "
                       f"Unrealized ${pnl_summary.unrealized_pnl:.2f}")

        except Exception as e:
            logger.debug(f"Statistics logging error: {e}")

    def log_final_statistics(self, session_digest, positions, pnl_service):
        """Log final statistics on shutdown"""
        try:
            runtime = time.time() - session_digest['start_time']

            logger.info("=" * 50)
            logger.info(f"Trading Session Summary ({runtime/3600:.1f}h)")
            logger.info(f"Buys: {len(session_digest['buys'])}")
            logger.info(f"Sells: {len(session_digest['sells'])}")
            logger.info(f"Active Positions: {len(positions)}")

            # Final PnL summary
            pnl_summary = pnl_service.get_summary()
            logger.info(f"Total Realized PnL: ${pnl_summary.realized_pnl_net:.2f}")
            logger.info(f"Total Unrealized PnL: ${pnl_summary.unrealized_pnl:.2f}")
            logger.info("=" * 50)

        except Exception as e:
            logger.error(f"Final statistics error: {e}")
