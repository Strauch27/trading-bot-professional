#!/usr/bin/env python3
"""
Metrics Collection and Export

Provides lightweight metrics collection with optional export to:
- Prometheus (via prometheus_client)
- StatsD (via statsd client)
- Local JSON files (always enabled)

This module tracks key trading metrics identified in the bug fixes:
- Order execution metrics (sent, filled, failed, fill rate)
- Intent lifecycle (pending, cleared, stale)
- Budget refresh performance
- Logging system health
"""

import time
import logging
from typing import Dict, Any, Optional
from collections import defaultdict
import json
import os

logger = logging.getLogger(__name__)

# Optional dependencies
try:
    from prometheus_client import Counter, Gauge, Histogram, Summary
    PROMETHEUS_AVAILABLE = True
except ImportError:
    PROMETHEUS_AVAILABLE = False

try:
    import statsd
    STATSD_AVAILABLE = True
except ImportError:
    STATSD_AVAILABLE = False


class MetricsCollector:
    """
    Lightweight metrics collector with multi-backend support.

    Tracks critical trading bot metrics and exports to configured backends.
    """

    def __init__(self, session_dir: str, enable_prometheus: bool = False,
                 enable_statsd: bool = False, statsd_host: str = "localhost",
                 statsd_port: int = 8125):
        """
        Initialize metrics collector.

        Args:
            session_dir: Session directory for local JSON export
            enable_prometheus: Enable Prometheus metrics
            enable_statsd: Enable StatsD metrics
            statsd_host: StatsD server host
            statsd_port: StatsD server port
        """
        self.session_dir = session_dir
        self.metrics_file = os.path.join(session_dir, "metrics", "summary.json")
        os.makedirs(os.path.dirname(self.metrics_file), exist_ok=True)

        # Local metrics storage
        self.counters: Dict[str, int] = defaultdict(int)
        self.gauges: Dict[str, float] = defaultdict(float)
        self.histograms: Dict[str, list] = defaultdict(list)

        # Prometheus setup
        self.prometheus_enabled = enable_prometheus and PROMETHEUS_AVAILABLE
        if self.prometheus_enabled:
            self._setup_prometheus()
        elif enable_prometheus and not PROMETHEUS_AVAILABLE:
            logger.warning("Prometheus requested but prometheus_client not installed")

        # StatsD setup
        self.statsd_enabled = enable_statsd and STATSD_AVAILABLE
        if self.statsd_enabled:
            self.statsd_client = statsd.StatsClient(statsd_host, statsd_port, prefix='trading_bot')
            logger.info(f"StatsD metrics enabled: {statsd_host}:{statsd_port}")
        elif enable_statsd and not STATSD_AVAILABLE:
            logger.warning("StatsD requested but statsd client not installed")

        logger.info(f"Metrics collector initialized (prometheus={self.prometheus_enabled}, statsd={self.statsd_enabled})")

    def _setup_prometheus(self):
        """Setup Prometheus metrics"""
        # Order execution metrics
        self.prom_orders_sent = Counter('orders_sent_total', 'Total orders sent')
        self.prom_orders_filled = Counter('orders_filled_total', 'Total orders filled')
        self.prom_orders_failed = Counter('orders_failed_total', 'Total orders failed')
        self.prom_fill_rate = Gauge('order_fill_rate', 'Current fill rate (0-1)')

        # Intent metrics
        self.prom_intents_pending = Gauge('intents_pending', 'Current pending intents')
        self.prom_intents_cleared = Counter('intents_cleared_total', 'Total intents cleared')
        self.prom_intents_stale = Counter('intents_stale_total', 'Total stale intents detected')

        # Performance metrics
        self.prom_budget_refresh_duration = Histogram(
            'budget_refresh_duration_seconds',
            'Budget refresh latency',
            buckets=[0.1, 0.5, 1.0, 2.5, 5.0, 10.0]
        )
        self.prom_order_latency = Histogram(
            'order_execution_latency_seconds',
            'Order execution latency',
            buckets=[0.1, 0.5, 1.0, 2.0, 5.0, 10.0]
        )

        # System health
        self.prom_logging_timeouts = Counter('logging_timeouts_total', 'Logging formatter timeouts')
        self.prom_budget_refresh_timeouts = Counter('budget_refresh_timeouts_total', 'Budget refresh timeouts')

        logger.info("Prometheus metrics registered")

    # =========================================================================
    # Order Execution Metrics
    # =========================================================================

    def record_order_sent(self, symbol: str, side: str):
        """Record an order being sent to the exchange"""
        self.counters['orders_sent'] += 1
        if self.prometheus_enabled:
            self.prom_orders_sent.inc()
        if self.statsd_enabled:
            self.statsd_client.incr('orders.sent')

    def record_order_filled(self, symbol: str, side: str, latency_ms: float):
        """Record a successful order fill"""
        self.counters['orders_filled'] += 1
        self.histograms['order_latency_ms'].append(latency_ms)

        if self.prometheus_enabled:
            self.prom_orders_filled.inc()
            self.prom_order_latency.observe(latency_ms / 1000.0)

        if self.statsd_enabled:
            self.statsd_client.incr('orders.filled')
            self.statsd_client.timing('orders.latency', latency_ms)

        # Update fill rate
        self._update_fill_rate()

    def record_order_failed(self, symbol: str, side: str, error_code: Optional[str] = None):
        """Record an order failure"""
        self.counters['orders_failed'] += 1

        if self.prometheus_enabled:
            self.prom_orders_failed.inc()

        if self.statsd_enabled:
            self.statsd_client.incr('orders.failed')

        # Update fill rate
        self._update_fill_rate()

    def _update_fill_rate(self):
        """Calculate and update fill rate metric"""
        sent = self.counters['orders_sent']
        filled = self.counters['orders_filled']

        if sent > 0:
            fill_rate = filled / sent
            self.gauges['fill_rate'] = fill_rate

            if self.prometheus_enabled:
                self.prom_fill_rate.set(fill_rate)

            if self.statsd_enabled:
                self.statsd_client.gauge('orders.fill_rate', fill_rate)

    # =========================================================================
    # Intent Lifecycle Metrics
    # =========================================================================

    def record_intent_pending(self, count: int):
        """Update count of pending intents"""
        self.gauges['intents_pending'] = count

        if self.prometheus_enabled:
            self.prom_intents_pending.set(count)

        if self.statsd_enabled:
            self.statsd_client.gauge('intents.pending', count)

    def record_intent_cleared(self, reason: str):
        """Record an intent being cleared"""
        self.counters['intents_cleared'] += 1

        if self.prometheus_enabled:
            self.prom_intents_cleared.inc()

        if self.statsd_enabled:
            self.statsd_client.incr('intents.cleared')

    def record_stale_intent(self, age_seconds: float):
        """Record detection of a stale intent"""
        self.counters['intents_stale'] += 1
        self.histograms['stale_intent_age_s'].append(age_seconds)

        if self.prometheus_enabled:
            self.prom_intents_stale.inc()

        if self.statsd_enabled:
            self.statsd_client.incr('intents.stale')

    # =========================================================================
    # Performance Metrics
    # =========================================================================

    def record_budget_refresh(self, duration_ms: float, timed_out: bool = False):
        """Record budget refresh performance"""
        self.histograms['budget_refresh_ms'].append(duration_ms)

        if self.prometheus_enabled:
            self.prom_budget_refresh_duration.observe(duration_ms / 1000.0)
            if timed_out:
                self.prom_budget_refresh_timeouts.inc()

        if self.statsd_enabled:
            self.statsd_client.timing('budget.refresh_duration', duration_ms)
            if timed_out:
                self.statsd_client.incr('budget.refresh_timeouts')

    def record_logging_timeout(self):
        """Record a logging formatter timeout"""
        self.counters['logging_timeouts'] += 1

        if self.prometheus_enabled:
            self.prom_logging_timeouts.inc()

        if self.statsd_enabled:
            self.statsd_client.incr('logging.timeouts')

    # =========================================================================
    # Export and Reporting
    # =========================================================================

    def get_summary(self) -> Dict[str, Any]:
        """Get current metrics summary"""
        return {
            'timestamp': time.time(),
            'counters': dict(self.counters),
            'gauges': dict(self.gauges),
            'histograms': {
                key: {
                    'count': len(values),
                    'min': min(values) if values else 0,
                    'max': max(values) if values else 0,
                    'avg': sum(values) / len(values) if values else 0
                }
                for key, values in self.histograms.items()
            }
        }

    def export_json(self):
        """Export metrics to local JSON file"""
        try:
            summary = self.get_summary()
            with open(self.metrics_file, 'w') as f:
                json.dump(summary, f, indent=2)
            logger.debug(f"Metrics exported to {self.metrics_file}")
        except Exception as e:
            logger.error(f"Failed to export metrics: {e}")

    def log_summary(self):
        """Log current metrics summary"""
        summary = self.get_summary()

        logger.info(
            "=== Metrics Summary ===",
            extra={
                'event_type': 'METRICS_SUMMARY',
                'orders_sent': summary['counters'].get('orders_sent', 0),
                'orders_filled': summary['counters'].get('orders_filled', 0),
                'orders_failed': summary['counters'].get('orders_failed', 0),
                'fill_rate': f"{summary['gauges'].get('fill_rate', 0):.1%}",
                'pending_intents': summary['gauges'].get('intents_pending', 0),
                'stale_intents': summary['counters'].get('intents_stale', 0)
            }
        )


# Global instance (optional, can be initialized in main.py)
_metrics_collector: Optional[MetricsCollector] = None


def init_metrics(session_dir: str, **kwargs) -> MetricsCollector:
    """Initialize global metrics collector"""
    global _metrics_collector
    _metrics_collector = MetricsCollector(session_dir, **kwargs)
    return _metrics_collector


def get_metrics() -> Optional[MetricsCollector]:
    """Get global metrics collector instance"""
    return _metrics_collector
