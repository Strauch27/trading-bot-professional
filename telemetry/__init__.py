"""
Telemetry Module

Prometheus metrics and monitoring integration for Trading Bot FSM.
"""

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
]
