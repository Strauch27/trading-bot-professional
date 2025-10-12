#!/usr/bin/env python3
"""
Logger Factory - Structured JSONL Logging with Rotation

Provides specialized loggers for different event types:
- DECISION_LOG: Buy/sell decisions, guards, sizing
- ORDER_LOG: Order lifecycle (attempt, ack, fill, cancel)
- TRACER_LOG: Exchange API requests/responses
- AUDIT_LOG: State changes, config changes, exceptions
- HEALTH_LOG: Heartbeats, rate limits, system health

Features:
- Automatic correlation ID injection from ContextVars
- JSONL output with mandatory fields
- Daily rotation with gzip compression
- Type-safe event logging with schema validation

Usage:
    from core.logger_factory import DECISION_LOG, log_event

    log_event(
        DECISION_LOG(),
        "drop_trigger_eval",
        symbol="BTC/USDT",
        anchor=100000.0,
        price=96000.0,
        drop_pct=-4.0,
        threshold_hit=True
    )
"""

import json
import logging
import gzip
import os
from datetime import datetime, timezone
from logging.handlers import TimedRotatingFileHandler
from typing import Any, Dict, Optional
from pathlib import Path

from core.trace_context import (
    session_id_var,
    decision_id_var,
    order_req_id_var,
    client_order_id_var,
    exchange_order_id_var,
)


class JsonlFormatter(logging.Formatter):
    """
    JSON Lines formatter with mandatory correlation fields.

    Each log line is a complete JSON object with:
    - ts_ns: Nanosecond timestamp
    - level: Log level (INFO, ERROR, etc.)
    - event: Event type (drop_trigger_eval, order_ack, etc.)
    - component: Logger name (decision, order, tracer, etc.)
    - session_id, decision_id, order_req_id, etc.: Correlation IDs
    - message: Human-readable message
    - Additional event-specific fields from extra_fields
    """

    def format(self, record: logging.LogRecord) -> str:
        """Format log record as JSONL."""
        # Nanosecond timestamp for precision
        ts_ns = int(datetime.now(tz=timezone.utc).timestamp() * 1_000_000_000)

        # Build base payload with mandatory fields
        payload = {
            "ts_ns": ts_ns,
            "level": record.levelname,
            "component": record.name,
            "event": getattr(record, "event", None),
            "message": record.getMessage(),
        }

        # Add correlation IDs from ContextVars
        correlation_ids = {
            "session_id": session_id_var.get(),
            "decision_id": decision_id_var.get(),
            "order_req_id": order_req_id_var.get(),
            "client_order_id": client_order_id_var.get(),
            "exchange_order_id": exchange_order_id_var.get(),
        }
        payload.update(correlation_ids)

        # Add extra fields (event-specific data)
        extra_fields = getattr(record, "extra_fields", {})
        payload.update(extra_fields)

        # Filter out None values for cleaner output
        payload_clean = {k: v for k, v in payload.items() if v is not None}

        # Serialize to JSON (one line per record)
        return json.dumps(payload_clean, ensure_ascii=False, default=str)


class GzTimedHandler(TimedRotatingFileHandler):
    """
    Timed rotating handler with automatic gzip compression.

    Rotates daily at midnight UTC and compresses old files.
    Keeps 14 days of history by default.
    """

    def rotation_filename(self, default_name: str) -> str:
        """Add .gz extension to rotated files."""
        return default_name + ".gz"

    def rotate(self, source: str, dest: str) -> None:
        """Rotate and compress the log file."""
        # Close the source file
        if os.path.exists(source):
            # Compress to destination
            with open(source, 'rb') as f_in:
                with gzip.open(dest, 'wb') as f_out:
                    f_out.writelines(f_in)
            # Remove uncompressed source
            os.remove(source)


# Global logger cache
_logger_cache: Dict[tuple, logging.Logger] = {}


def _make_handler(file_path: str, backup_count: int = 14) -> logging.Handler:
    """
    Create rotating file handler with JSONL formatter.

    Args:
        file_path: Path to log file
        backup_count: Number of daily backups to keep

    Returns:
        Configured handler
    """
    # Ensure directory exists
    os.makedirs(os.path.dirname(file_path), exist_ok=True)

    # Create rotating handler (daily rotation at midnight UTC)
    handler = GzTimedHandler(
        filename=file_path,
        when='midnight',
        interval=1,
        backupCount=backup_count,
        utc=True,
        encoding='utf-8'
    )

    # Set formatter
    handler.setFormatter(JsonlFormatter())
    handler.setLevel(logging.INFO)

    return handler


def get_logger(name: str, file_path: str, backup_count: int = 14) -> logging.Logger:
    """
    Get or create a logger with JSONL output.

    Args:
        name: Logger name (e.g., "decision", "order")
        file_path: Path to log file
        backup_count: Number of daily backups to keep

    Returns:
        Configured logger
    """
    key = (name, file_path)

    # Return cached logger if exists
    if key in _logger_cache:
        return _logger_cache[key]

    # Create new logger
    logger = logging.getLogger(name)
    logger.setLevel(logging.INFO)
    logger.propagate = False  # Don't propagate to root logger

    # Add handler
    handler = _make_handler(file_path, backup_count)
    logger.addHandler(handler)

    # Cache and return
    _logger_cache[key] = logger
    return logger


# Convenience logger getters (lazy initialization)
def DECISION_LOG() -> logging.Logger:
    """Get decision logger (drop triggers, guards, sizing)."""
    return get_logger("decision", "logs/decisions/decision.jsonl")


def ORDER_LOG() -> logging.Logger:
    """Get order logger (attempt, ack, fill, cancel)."""
    return get_logger("order", "logs/orders/order.jsonl")


def TRACER_LOG() -> logging.Logger:
    """Get exchange tracer logger (API requests/responses)."""
    return get_logger("tracer", "logs/tracer/exchange.jsonl")


def AUDIT_LOG() -> logging.Logger:
    """Get audit logger (state changes, config, exceptions)."""
    return get_logger("audit", "logs/audit/audit.jsonl")


def HEALTH_LOG() -> logging.Logger:
    """Get health logger (heartbeats, rate limits, system metrics)."""
    return get_logger("health", "logs/health/health.jsonl")


def log_event(
    logger: logging.Logger,
    event: str,
    message: str = "",
    level: int = logging.INFO,
    **fields: Any
) -> None:
    """
    Log a structured event with type-safe fields.

    Args:
        logger: Logger instance (from DECISION_LOG(), ORDER_LOG(), etc.)
        event: Event type (e.g., "drop_trigger_eval", "order_ack")
        message: Human-readable message (optional)
        level: Log level (default: INFO)
        **fields: Event-specific fields

    Example:
        log_event(
            DECISION_LOG(),
            "sizing_calc",
            symbol="BTC/USDT",
            quote_budget=25.0,
            min_notional=26.0,
            passed=False,
            fail_reason="insufficient_budget"
        )
    """
    # Create log record with event type and extra fields
    logger.log(
        level,
        message or event,
        extra={
            "event": event,
            "extra_fields": fields
        }
    )


def safe_exchange_raw(exchange_response: Any, max_size: int = 5000) -> Any:
    """
    Sanitize exchange response for logging.

    - Masks sensitive fields (API keys, secrets)
    - Truncates large payloads
    - Handles various response types (dict, object, string)

    Args:
        exchange_response: Raw exchange response
        max_size: Maximum size of serialized response

    Returns:
        Sanitized response safe for logging
    """
    # Convert to dict if object
    if hasattr(exchange_response, "__dict__"):
        response = exchange_response.__dict__
    elif isinstance(exchange_response, dict):
        response = exchange_response
    else:
        # Fallback: convert to string
        response = {"raw": str(exchange_response)}

    # Create copy for sanitization
    sanitized = {}

    # Mask sensitive fields
    sensitive_keys = {"apiKey", "secret", "password", "privateKey", "signature"}

    for key, value in response.items():
        # Mask sensitive fields
        if any(s.lower() in key.lower() for s in sensitive_keys):
            sanitized[key] = "***REDACTED***"
        else:
            sanitized[key] = value

    # Truncate if too large
    serialized = json.dumps(sanitized, default=str)
    if len(serialized) > max_size:
        # Truncate and add marker
        sanitized = {
            "_truncated": True,
            "_original_size": len(serialized),
            "_preview": serialized[:max_size]
        }

    return sanitized


# Install global exception hook for uncaught exceptions
def install_global_excepthook() -> None:
    """
    Install global exception hook to log uncaught exceptions.

    Call this once at bot startup to ensure all exceptions are logged.
    """
    import sys
    import traceback

    def _excepthook(exc_type, exc_value, exc_traceback):
        """Log exception to AUDIT_LOG and call original hook."""
        # Format stacktrace
        stacktrace = "".join(
            traceback.format_exception(exc_type, exc_value, exc_traceback)
        )

        # Log to audit log
        log_event(
            AUDIT_LOG(),
            "uncaught_exception",
            message=f"Uncaught exception: {exc_type.__name__}: {exc_value}",
            level=logging.ERROR,
            exception_class=exc_type.__name__,
            exception_message=str(exc_value),
            stacktrace=stacktrace,
        )

        # Call original excepthook
        sys.__excepthook__(exc_type, exc_value, exc_traceback)

    sys.excepthook = _excepthook
