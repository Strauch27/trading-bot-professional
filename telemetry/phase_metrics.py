"""
Phase Metrics for Prometheus

Exposes FSM metrics for monitoring and alerting.

Metrics:
- phase_changes_total: Counter of phase transitions
- phase_code: Gauge of current phase (as numeric code)
- phase_duration_seconds: Histogram of time spent in each phase
- stuck_in_phase_seconds: Gauge of time stuck in current phase
- phase_errors_total: Counter of errors by phase
"""

from prometheus_client import Counter, Gauge, Histogram, start_http_server
from core.fsm.phases import Phase
from core.fsm.state import CoinState
import logging
import time

logger = logging.getLogger(__name__)


# ========== Metrics Definitions ==========

# Phase Change Counter
phase_changes = Counter(
    "phase_changes_total",
    "Total number of phase transitions",
    ["symbol", "phase"]
)

# Current Phase Code (Gauge for Graphing)
phase_code = Gauge(
    "phase_code",
    "Current phase as numeric code (for graphing)",
    ["symbol"]
)

# Phase Duration Histogram
phase_duration_seconds = Histogram(
    "phase_duration_seconds",
    "Time spent in each phase (seconds)",
    ["phase"],
    buckets=(0.1, 0.5, 1.0, 2.0, 5.0, 10.0, 30.0, 60.0, 120.0, 300.0, 600.0, float("inf"))
)

# Stuck Phase Gauge (for Alerts)
stuck_in_phase_seconds = Gauge(
    "stuck_in_phase_seconds",
    "Time currently stuck in phase (seconds)",
    ["symbol", "phase"]
)

# Phase Error Counter
phase_errors_total = Counter(
    "phase_errors_total",
    "Total number of errors by phase",
    ["symbol", "phase", "error_type"]
)

# Phase Entry Counter (for success rate calculation)
phase_entries_total = Counter(
    "phase_entries_total",
    "Total number of entries into each phase",
    ["phase"]
)

# Phase Exit Counter (for success rate calculation)
phase_exits_total = Counter(
    "phase_exits_total",
    "Total number of exits from each phase",
    ["phase", "outcome"]  # outcome: success, error, timeout
)


# ========== Phase Mapping (for Gauge) ==========

PHASE_MAP = {
    Phase.WARMUP: 0,
    Phase.IDLE: 1,
    Phase.ENTRY_EVAL: 2,
    Phase.PLACE_BUY: 3,
    Phase.WAIT_FILL: 4,
    Phase.POSITION: 5,
    Phase.EXIT_EVAL: 6,
    Phase.PLACE_SELL: 7,
    Phase.WAIT_SELL_FILL: 8,
    Phase.POST_TRADE: 9,
    Phase.COOLDOWN: 10,
    Phase.ERROR: 11,
}


# ========== Helper Functions ==========

def start_metrics_server(port: int = 8000):
    """
    Start Prometheus HTTP server.

    Args:
        port: HTTP port (default: 8000)

    Metrics available at: http://localhost:8000/metrics
    """
    try:
        start_http_server(port)
        logger.info(f"Prometheus metrics server started on port {port}")
    except Exception as e:
        logger.error(f"Failed to start Prometheus metrics server: {e}")


def update_phase_code(state: CoinState):
    """
    Update phase_code gauge for a state.

    Args:
        state: CoinState to update metric for
    """
    try:
        code = PHASE_MAP.get(state.phase, -1)
        phase_code.labels(symbol=state.symbol).set(code)
    except Exception as e:
        logger.error(f"Failed to update phase_code metric: {e}")


def record_phase_duration(phase: Phase, duration_seconds: float):
    """
    Record phase duration in histogram.

    Args:
        phase: Phase that was exited
        duration_seconds: Time spent in phase
    """
    try:
        phase_duration_seconds.labels(phase=phase.value).observe(duration_seconds)
    except Exception as e:
        logger.error(f"Failed to record phase duration: {e}")


def update_stuck_metric(state: CoinState):
    """
    Update stuck_in_phase_seconds gauge.

    Args:
        state: CoinState to check
    """
    try:
        age = state.age_seconds()
        stuck_in_phase_seconds.labels(
            symbol=state.symbol,
            phase=state.phase.value
        ).set(age)
    except Exception as e:
        logger.error(f"Failed to update stuck metric: {e}")


def clear_stuck_metric(symbol: str, phase: Phase):
    """
    Clear stuck_in_phase_seconds gauge (when phase changes).

    Args:
        symbol: Trading symbol
        phase: Previous phase
    """
    try:
        stuck_in_phase_seconds.labels(
            symbol=symbol,
            phase=phase.value
        ).set(0)
    except Exception as e:
        logger.error(f"Failed to clear stuck metric: {e}")


def record_phase_entry(phase: Phase):
    """
    Record entry into a phase.

    Args:
        phase: Phase being entered
    """
    try:
        phase_entries_total.labels(phase=phase.value).inc()
    except Exception as e:
        logger.error(f"Failed to record phase entry: {e}")


def record_phase_exit(phase: Phase, outcome: str):
    """
    Record exit from a phase.

    Args:
        phase: Phase being exited
        outcome: Exit outcome ("success", "error", "timeout")
    """
    try:
        phase_exits_total.labels(phase=phase.value, outcome=outcome).inc()
    except Exception as e:
        logger.error(f"Failed to record phase exit: {e}")


def record_phase_error(symbol: str, phase: Phase, error_type: str):
    """
    Record phase error.

    Args:
        symbol: Trading symbol
        phase: Phase where error occurred
        error_type: Type of error (e.g. "timeout", "api_error", "validation_error")
    """
    try:
        phase_errors_total.labels(
            symbol=symbol,
            phase=phase.value,
            error_type=error_type
        ).inc()
    except Exception as e:
        logger.error(f"Failed to record phase error: {e}")


# ========== Batch Update Functions ==========

def update_all_stuck_metrics(states: dict):
    """
    Update stuck metrics for all states.

    Args:
        states: Dict mapping symbol -> CoinState
    """
    current_time = time.time()

    for state in states.values():
        try:
            if state.phase in [Phase.IDLE, Phase.WARMUP, Phase.COOLDOWN]:
                # Don't track stuck time for idle phases
                continue

            age = state.age_seconds()
            stuck_in_phase_seconds.labels(
                symbol=state.symbol,
                phase=state.phase.value
            ).set(age)
        except Exception as e:
            logger.error(f"Failed to update stuck metric for {state.symbol}: {e}")


def get_phase_success_rate(phase: Phase) -> float:
    """
    Calculate success rate for a phase.

    Returns:
        Success rate (0.0 - 1.0)
    """
    try:
        # Get metrics from Prometheus client
        entries = phase_entries_total.labels(phase=phase.value)._value.get() or 0
        successes = phase_exits_total.labels(phase=phase.value, outcome="success")._value.get() or 0

        if entries == 0:
            return 0.0

        return successes / entries
    except Exception as e:
        logger.error(f"Failed to calculate success rate: {e}")
        return 0.0


# ========== Prometheus Query Helpers ==========

# For Grafana dashboards, use these queries:
#
# Phase Distribution:
#   sum(phase_code) by (phase)
#
# Phase Change Rate:
#   rate(phase_changes_total[5m])
#
# Stuck Symbols (alert):
#   stuck_in_phase_seconds{phase!="idle"} > 60
#
# Error Rate by Phase:
#   rate(phase_errors_total[5m])
#
# Phase Duration P95:
#   histogram_quantile(0.95, rate(phase_duration_seconds_bucket[5m]))
#
# Entry Success Rate:
#   rate(phase_entries_total{phase="position"}[1h]) / rate(phase_entries_total{phase="entry_eval"}[1h])
