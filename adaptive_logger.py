#!/usr/bin/env python3
"""
Adaptive Logging System for Trading Bot

Implements 3-tier debug modes with context-aware auto-escalation:
- FULL: Maximum debug verbosity for initial verification
- TRADING: Production mode with trading-focused logging
- MINIMAL: Only critical events and errors

Features:
- Auto-escalation on errors/anomalies
- Startup full-debug period
- Post-trade enhanced logging
- Market data sampling
- Performance metrics aggregation
"""

import time
import logging
import threading
from enum import Enum
from typing import Dict, Any, Optional, List, Deque
from collections import deque, defaultdict
from datetime import datetime, timedelta
from dataclasses import dataclass

try:
    from config import (
        DEBUG_MODE, DEBUG_AUTO_ESCALATE, DEBUG_STARTUP_FULL_MINUTES,
        DEBUG_POST_TRADE_FULL_MINUTES, DEBUG_MARKET_DATA_SAMPLING,
        DEBUG_PERFORMANCE_AGGREGATION_SECONDS
    )
except ImportError:
    # Fallback defaults
    DEBUG_MODE = "TRADING"
    DEBUG_AUTO_ESCALATE = True
    DEBUG_STARTUP_FULL_MINUTES = 15
    DEBUG_POST_TRADE_FULL_MINUTES = 5
    DEBUG_MARKET_DATA_SAMPLING = 10
    DEBUG_PERFORMANCE_AGGREGATION_SECONDS = 60

try:
    import config
    _WINDOW_SEC = getattr(config, "GUARD_SUMMARY_WINDOW_SEC", 60)
    _MIN_SAMPLES = getattr(config, "GUARD_SUMMARY_MIN_SAMPLES", 20)
except Exception:
    _WINDOW_SEC = 60
    _MIN_SAMPLES = 20


class DebugMode(Enum):
    """Debug verbosity levels"""
    FULL = "FULL"
    TRADING = "TRADING"
    MINIMAL = "MINIMAL"


@dataclass
class EscalationTrigger:
    """Configuration for auto-escalation triggers"""
    error_threshold: int = 5  # Errors per minute
    failed_trades_threshold: int = 3  # Failed trades per hour
    guard_blocks_threshold: int = 20  # Guard blocks per hour
    escalation_duration_minutes: int = 30  # How long to stay in FULL mode


class AdaptiveLogger:
    """
    Adaptive logging system that adjusts verbosity based on context
    """

    def __init__(self, initial_mode: str = None):
        self.current_mode = DebugMode(initial_mode or DEBUG_MODE)
        self.startup_time = time.time()
        self.last_trade_time = None
        self.auto_escalate = DEBUG_AUTO_ESCALATE

        # State tracking for auto-escalation
        self.error_count = deque(maxlen=100)  # Last 100 errors with timestamps
        self.failed_trades = deque(maxlen=50)  # Last 50 failed trades
        self.guard_blocks = deque(maxlen=200)  # Last 200 guard blocks
        self.escalation_until = None  # When to return from escalated mode

        # Performance metrics aggregation
        self.performance_buffer = defaultdict(list)
        self.last_performance_flush = time.time()

        # Market data sampling counter
        self.market_data_counter = 0

        # Thread safety
        self._lock = threading.RLock()

        self.escalation_config = EscalationTrigger()

        logger = logging.getLogger(__name__)
        logger.info(f"Adaptive logger initialized in {self.current_mode.value} mode")

    def should_log_market_data(self, event_type: str) -> bool:
        """Determine if market data event should be logged based on current mode"""
        with self._lock:
            if self.current_mode == DebugMode.FULL:
                return True

            if self.current_mode == DebugMode.MINIMAL:
                # Only log critical market events
                critical_events = ["MARKET_ERROR", "PRICE_ANOMALY", "EXCHANGE_DISCONNECT"]
                return any(critical in event_type for critical in critical_events)

            if self.current_mode == DebugMode.TRADING:
                # Sample market data events
                if "OHLCV_FETCH" in event_type or "TICKER_UPDATE" in event_type:
                    self.market_data_counter += 1
                    return self.market_data_counter % DEBUG_MARKET_DATA_SAMPLING == 0

                # Always log trading-relevant market events
                trading_events = ["BACKFILL", "SNAPSHOT", "PRICE_ALERT"]
                return any(trading in event_type for trading in trading_events)

            return True

    def should_log_performance_metric(self, metric_name: str, value: float, labels: Dict[str, Any] = None) -> bool:
        """Determine if performance metric should be logged or buffered"""
        with self._lock:
            if self.current_mode == DebugMode.FULL:
                return True

            if self.current_mode == DebugMode.MINIMAL:
                # Only log critical performance issues
                critical_metrics = ["ERROR_", "TIMEOUT_", "FAILURE_"]
                return any(critical in metric_name for critical in critical_metrics)

            if self.current_mode == DebugMode.TRADING:
                # Buffer non-critical metrics for aggregation
                buffer_metrics = ["OHLCV_FETCH_MS", "TICKER_FETCH_MS", "ORDERBOOK_FETCH_MS"]
                if any(buffered in metric_name for buffered in buffer_metrics):
                    self._buffer_performance_metric(metric_name, value, labels)
                    return False  # Don't log immediately

                # Always log trading performance metrics
                trading_metrics = ["ORDER_LATENCY", "DECISION_TIME", "TRADE_EXECUTION"]
                return any(trading in metric_name for trading in trading_metrics)

            return True

    def _buffer_performance_metric(self, metric_name: str, value: float, labels: Dict[str, Any] = None):
        """Buffer performance metric for later aggregation"""
        timestamp = time.time()
        self.performance_buffer[metric_name].append({
            'value': value,
            'labels': labels or {},
            'timestamp': timestamp
        })

    def flush_performance_buffer(self) -> List[Dict[str, Any]]:
        """Flush and aggregate buffered performance metrics"""
        with self._lock:
            if time.time() - self.last_performance_flush < DEBUG_PERFORMANCE_AGGREGATION_SECONDS:
                return []

            aggregated_metrics = []
            current_time = time.time()

            for metric_name, values in self.performance_buffer.items():
                if not values:
                    continue

                # Calculate aggregations
                raw_values = [v['value'] for v in values]
                aggregated_metrics.append({
                    'metric': f"{metric_name}_AGG",
                    'timestamp': current_time,
                    'window_seconds': DEBUG_PERFORMANCE_AGGREGATION_SECONDS,
                    'count': len(raw_values),
                    'avg': sum(raw_values) / len(raw_values),
                    'min': min(raw_values),
                    'max': max(raw_values),
                    'p95': sorted(raw_values)[int(0.95 * len(raw_values))] if len(raw_values) > 5 else max(raw_values),
                    'sample_labels': values[0]['labels'] if values else {}
                })

            # Clear buffer
            self.performance_buffer.clear()
            self.last_performance_flush = current_time

            return aggregated_metrics

    def track_error(self, error_type: str, symbol: str = None, details: str = None):
        """Track error for escalation consideration"""
        with self._lock:
            self.error_count.append({
                'timestamp': time.time(),
                'type': error_type,
                'symbol': symbol,
                'details': details
            })

            if self.auto_escalate:
                self._check_escalation_triggers()

    def track_failed_trade(self, symbol: str, reason: str):
        """Track failed trade for escalation consideration"""
        with self._lock:
            self.failed_trades.append({
                'timestamp': time.time(),
                'symbol': symbol,
                'reason': reason
            })

            if self.auto_escalate:
                self._check_escalation_triggers()

    def track_guard_block(self, symbol: str, guards_failed: List[str]):
        """Track guard block for escalation consideration"""
        with self._lock:
            self.guard_blocks.append({
                'timestamp': time.time(),
                'symbol': symbol,
                'guards_failed': guards_failed
            })

            if self.auto_escalate:
                self._check_escalation_triggers()

    def notify_trade_completed(self, symbol: str, side: str):
        """Notify of completed trade - may trigger enhanced logging period"""
        with self._lock:
            self.last_trade_time = time.time()
            logger = logging.getLogger(__name__)
            logger.info(f"Trade completed ({symbol} {side}) - enhanced logging for {DEBUG_POST_TRADE_FULL_MINUTES}m")

    def _check_escalation_triggers(self):
        """Check if conditions warrant escalation to FULL mode"""
        current_time = time.time()

        # Count recent events
        recent_errors = sum(1 for e in self.error_count
                          if current_time - e['timestamp'] < 60)  # Last minute

        recent_failed_trades = sum(1 for t in self.failed_trades
                                 if current_time - t['timestamp'] < 3600)  # Last hour

        recent_guard_blocks = sum(1 for g in self.guard_blocks
                                if current_time - g['timestamp'] < 3600)  # Last hour

        should_escalate = (
            recent_errors >= self.escalation_config.error_threshold or
            recent_failed_trades >= self.escalation_config.failed_trades_threshold or
            recent_guard_blocks >= self.escalation_config.guard_blocks_threshold
        )

        if should_escalate and self.current_mode != DebugMode.FULL:
            self._escalate_to_full_mode()

    def _escalate_to_full_mode(self):
        """Escalate to FULL debug mode temporarily"""
        self.escalation_until = time.time() + (self.escalation_config.escalation_duration_minutes * 60)
        previous_mode = self.current_mode
        self.current_mode = DebugMode.FULL

        logger = logging.getLogger(__name__)
        logger.warning(f"Auto-escalated from {previous_mode.value} to FULL mode for {self.escalation_config.escalation_duration_minutes}m due to elevated error/failure rates")

    def get_effective_mode(self) -> DebugMode:
        """Get the current effective debug mode considering all factors"""
        with self._lock:
            current_time = time.time()

            # Check if in startup full-debug period
            startup_elapsed = (current_time - self.startup_time) / 60  # minutes
            if startup_elapsed < DEBUG_STARTUP_FULL_MINUTES:
                return DebugMode.FULL

            # Check if in post-trade enhanced period
            if self.last_trade_time:
                post_trade_elapsed = (current_time - self.last_trade_time) / 60  # minutes
                if post_trade_elapsed < DEBUG_POST_TRADE_FULL_MINUTES:
                    return DebugMode.FULL

            # Check if in auto-escalated period
            if self.escalation_until and current_time < self.escalation_until:
                return DebugMode.FULL
            elif self.escalation_until and current_time >= self.escalation_until:
                # End escalation period
                self.escalation_until = None
                logger = logging.getLogger(__name__)
                logger.info(f"Returning from auto-escalated mode to {self.current_mode.value}")

            return self.current_mode

    def should_log_event(self, event_type: str, level: str = "INFO") -> bool:
        """Determine if an event should be logged based on current effective mode"""
        effective_mode = self.get_effective_mode()

        # Always log critical events regardless of mode
        critical_events = [
            "TRADE_OPEN", "TRADE_CLOSE", "ORDER_FILLED", "ORDER_PLACED",
            "BUY_TRIGGERED", "SELL_TRIGGERED", "ERROR", "WARNING",
            "CONFIG_CHANGE", "ENGINE_START", "ENGINE_STOP",
            "GUARD_BLOCK", "DECISION_", "SESSION_START", "SESSION_END"
        ]

        if any(critical in event_type for critical in critical_events) or level in ["ERROR", "WARNING"]:
            return True

        if effective_mode == DebugMode.FULL:
            return True

        if effective_mode == DebugMode.MINIMAL:
            return False  # Only critical events (already handled above)

        if effective_mode == DebugMode.TRADING:
            # Log trading-relevant events
            trading_events = [
                "POSITION_", "PORTFOLIO_", "MARKET_CONDITIONS",
                "SIGNAL_", "TRIGGER_", "GUARD_PASS"
            ]
            return any(trading in event_type for trading in trading_events)

        return True

    def get_status(self) -> Dict[str, Any]:
        """Get current adaptive logger status"""
        with self._lock:
            current_time = time.time()
            effective_mode = self.get_effective_mode()

            status = {
                'current_mode': self.current_mode.value,
                'effective_mode': effective_mode.value,
                'startup_time': self.startup_time,
                'startup_elapsed_minutes': (current_time - self.startup_time) / 60,
                'last_trade_time': self.last_trade_time,
                'post_trade_elapsed_minutes': (
                    (current_time - self.last_trade_time) / 60
                    if self.last_trade_time else None
                ),
                'escalation_active': self.escalation_until is not None,
                'escalation_ends': self.escalation_until,
                'recent_errors': len([e for e in self.error_count
                                    if current_time - e['timestamp'] < 300]),  # Last 5 minutes
                'recent_failed_trades': len([t for t in self.failed_trades
                                           if current_time - t['timestamp'] < 3600]),  # Last hour
                'recent_guard_blocks': len([g for g in self.guard_blocks
                                          if current_time - g['timestamp'] < 3600]),  # Last hour
                'market_data_counter': self.market_data_counter,
                'performance_buffer_size': sum(len(v) for v in self.performance_buffer.values())
            }

            return status


# Global adaptive logger instance
_adaptive_logger = None


def get_adaptive_logger() -> AdaptiveLogger:
    """Get the global adaptive logger instance"""
    global _adaptive_logger
    if _adaptive_logger is None:
        _adaptive_logger = AdaptiveLogger()
    return _adaptive_logger


def should_log_market_data(event_type: str) -> bool:
    """Convenience function to check if market data should be logged"""
    return get_adaptive_logger().should_log_market_data(event_type)


def should_log_performance_metric(metric_name: str, value: float, labels: Dict[str, Any] = None) -> bool:
    """Convenience function to check if performance metric should be logged"""
    return get_adaptive_logger().should_log_performance_metric(metric_name, value, labels)


def should_log_event(event_type: str, level: str = "INFO") -> bool:
    """Convenience function to check if event should be logged"""
    return get_adaptive_logger().should_log_event(event_type, level)


def track_error(error_type: str, symbol: str = None, details: str = None):
    """Convenience function to track error"""
    get_adaptive_logger().track_error(error_type, symbol, details)


def track_failed_trade(symbol: str, reason: str):
    """Convenience function to track failed trade"""
    get_adaptive_logger().track_failed_trade(symbol, reason)


def track_guard_block(symbol: str, guards_failed: List[str]):
    """Convenience function to track guard block"""
    get_adaptive_logger().track_guard_block(symbol, guards_failed)


def notify_trade_completed(symbol: str, side: str):
    """Convenience function to notify trade completion"""
    get_adaptive_logger().notify_trade_completed(symbol, side)


def flush_performance_buffer() -> List[Dict[str, Any]]:
    """Convenience function to flush performance buffer"""
    return get_adaptive_logger().flush_performance_buffer()


def get_logger_status() -> Dict[str, Any]:
    """Convenience function to get logger status"""
    return get_adaptive_logger().get_status()


# ==========================
# Rollierende Guard-Statistiken
# ==========================
class GuardRollingStats:
    """
    Sammeln pass/fail pro Guard mit Zeitstempel; prunen auf rolling window.
    Liefert kompakte Summaries für Konsole & JSONL.
    """
    def __init__(self, window_sec: int = _WINDOW_SEC):
        self.window_sec = window_sec
        # pro guard -> deque[(ts, passed: bool)]
        self._data = defaultdict(deque)
        self._last_summary_ts = 0.0

    def record_details(self, details: dict):
        """
        details: output von get_detailed_guard_status(...)
        erwartet details['guards'] mit pro Guard {'passes': bool, ...}
        """
        now = time.time()
        guards = (details or {}).get("guards", {})
        for gname, ginfo in guards.items():
            passed = bool(ginfo.get("passes", False))
            dq = self._data[gname]
            dq.append((now, passed))
            # prune alt
            cutoff = now - self.window_sec
            while dq and dq[0][0] < cutoff:
                dq.popleft()

    def _guard_stats(self, dq: deque) -> tuple[int, int, float]:
        """returns (total, blocks, block_rate)"""
        total = len(dq)
        if not total:
            return 0, 0, 0.0
        blocks = sum(1 for _, passed in dq if not passed)
        rate = blocks / total
        return total, blocks, rate

    def summarize_if_due(self, force: bool = False) -> dict | None:
        now = time.time()
        if not force and now - self._last_summary_ts < self.window_sec:
            return None
        # baue Summary
        summary = []
        json_payload = {"window_sec": self.window_sec, "guards": {}}
        total_samples_all = 0
        for gname, dq in self._data.items():
            total, blocks, rate = self._guard_stats(dq)
            total_samples_all += total
            json_payload["guards"][gname] = {
                "samples": total, "blocks": blocks, "block_rate": round(rate, 4)
            }
            # Einzeiler-Teil
            pct = f"{rate*100:.1f}%"
            summary.append(f"{gname}: {blocks}/{total} blocked ({pct})")

        if total_samples_all < _MIN_SAMPLES and not force:
            return None

        # Console-Einzeiler: wichtigste Guards sortiert nach Blockrate (absteigend)
        ordered = sorted(json_payload["guards"].items(),
                         key=lambda kv: (kv[1]["block_rate"], kv[1]["samples"]),
                         reverse=True)
        top = " | ".join(f"{k}: {v['blocks']}/{v['samples']} ({v['block_rate']*100:.0f}%)"
                         for k, v in ordered[:4])
        msg = f"Guard 60s summary — total_samples={total_samples_all}: {top}"

        # Log in Konsole & JSONL
        logger = logging.getLogger(__name__)
        logger.info(msg, extra={'event_type': 'GUARD_ROLLING_SUMMARY'})
        try:
            from logger import JsonlLogger
            JsonlLogger().write("guard_summary", json_payload)
        except Exception:
            # JSONL-Logger optional – kein Crash, wenn nicht verfügbar
            pass

        self._last_summary_ts = now
        return json_payload

# global Singleton
_GUARD_STATS = GuardRollingStats()

def guard_stats_record(details: dict):
    """Extern aufrufbar: pro Decision/Guardcheck aufrufen."""
    try:
        _GUARD_STATS.record_details(details)
    except Exception:
        pass

def guard_stats_maybe_summarize(force: bool = False):
    """Extern aufrufbar: z. B. im Heartbeat oder alle N Zyklen."""
    try:
        return _GUARD_STATS.summarize_if_due(force=force)
    except Exception:
        return None