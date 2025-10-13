"""
Telemetry Module

Prometheus metrics and monitoring integration for Trading Bot FSM.
Memory metrics for heartbeat display.
"""

# Try to import phase_metrics (requires prometheus_client)
try:
    from .phase_metrics import (
        phase_changes,
        phase_code,
        phase_duration_seconds,
        stuck_in_phase_seconds,
        phase_errors_total,
        PHASE_MAP,
        start_metrics_server,
        update_phase_code,
        record_phase_duration,
        update_stuck_metric,
    )
    _PHASE_METRICS_AVAILABLE = True
except ImportError:
    _PHASE_METRICS_AVAILABLE = False
    # Define placeholders
    phase_changes = None
    phase_code = None
    phase_duration_seconds = None
    stuck_in_phase_seconds = None
    phase_errors_total = None
    PHASE_MAP = None
    start_metrics_server = None
    update_phase_code = None
    record_phase_duration = None
    update_stuck_metric = None

# Always import mem (does not require prometheus_client)
from .mem import rss_mb, percent, get_memory_info

__all__ = [
    'phase_changes',
    'phase_code',
    'phase_duration_seconds',
    'stuck_in_phase_seconds',
    'phase_errors_total',
    'PHASE_MAP',
    'start_metrics_server',
    'update_phase_code',
    'record_phase_duration',
    'update_stuck_metric',
    'rss_mb',
    'percent',
    'get_memory_info',
]
