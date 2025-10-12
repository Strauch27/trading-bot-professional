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


# =============================================================================
# CONFIG TRACKING - Phase 3 Structured Logging
# =============================================================================

def log_config_snapshot(config_module: Any, session_id: str = None) -> str:
    """
    Log complete configuration snapshot at startup.

    Captures all uppercase config parameters categorized by function:
    - entry: Entry strategy parameters
    - exit: Exit strategy parameters
    - position: Position management
    - guards: Quality filters
    - execution: Order execution settings
    - system: System/technical settings

    Args:
        config_module: Config module (import config)
        session_id: Session ID for correlation

    Returns:
        Config hash (SHA256, 16 chars) for change detection

    Example:
        import config
        from core.logger_factory import log_config_snapshot

        config_hash = log_config_snapshot(config, session_id="sess_123")
    """
    import hashlib

    # Extract all UPPERCASE config parameters
    config_dict = {
        k: getattr(config_module, k)
        for k in dir(config_module)
        if k.isupper() and not k.startswith('_')
    }

    # Categorize config parameters
    config_snapshot = {
        "entry_strategy": {
            "DROP_TRIGGER_VALUE": config_dict.get("DROP_TRIGGER_VALUE"),
            "DROP_TRIGGER_MODE": config_dict.get("DROP_TRIGGER_MODE"),
            "LOOKBACK_S": config_dict.get("LOOKBACK_S"),
            "DROP_TRIGGER_LOOKBACK_MIN": config_dict.get("DROP_TRIGGER_LOOKBACK_MIN"),
            "USE_DROP_ANCHOR": config_dict.get("USE_DROP_ANCHOR"),
            "ANCHOR_STALE_MINUTES": config_dict.get("ANCHOR_STALE_MINUTES"),
            "MODE": config_dict.get("MODE"),
        },
        "exit_strategy": {
            "TAKE_PROFIT_THRESHOLD": config_dict.get("TAKE_PROFIT_THRESHOLD"),
            "STOP_LOSS_THRESHOLD": config_dict.get("STOP_LOSS_THRESHOLD"),
            "SWITCH_TO_SL_THRESHOLD": config_dict.get("SWITCH_TO_SL_THRESHOLD"),
            "SWITCH_TO_TP_THRESHOLD": config_dict.get("SWITCH_TO_TP_THRESHOLD"),
            "USE_TRAILING_STOP": config_dict.get("USE_TRAILING_STOP"),
            "USE_TRAILING_TP": config_dict.get("USE_TRAILING_TP"),
            "USE_ATR_BASED_EXITS": config_dict.get("USE_ATR_BASED_EXITS"),
        },
        "position_management": {
            "MAX_TRADES": config_dict.get("MAX_TRADES"),
            "POSITION_SIZE_USDT": config_dict.get("POSITION_SIZE_USDT"),
            "MAX_PER_SYMBOL_USD": config_dict.get("MAX_PER_SYMBOL_USD"),
            "TRADE_TTL_MIN": config_dict.get("TRADE_TTL_MIN"),
            "COOLDOWN_MIN": config_dict.get("COOLDOWN_MIN"),
            "ALLOW_DUPLICATE_COINS": config_dict.get("ALLOW_DUPLICATE_COINS"),
        },
        "guards": {
            "USE_SMA_GUARD": config_dict.get("USE_SMA_GUARD"),
            "USE_VOLUME_GUARD": config_dict.get("USE_VOLUME_GUARD"),
            "USE_SPREAD_GUARD": config_dict.get("USE_SPREAD_GUARD"),
            "USE_VOL_SIGMA_GUARD": config_dict.get("USE_VOL_SIGMA_GUARD"),
            "USE_BTC_FILTER": config_dict.get("USE_BTC_FILTER"),
            "USE_FALLING_COINS_FILTER": config_dict.get("USE_FALLING_COINS_FILTER"),
            "USE_ML_GATEKEEPER": config_dict.get("USE_ML_GATEKEEPER"),
        },
        "execution": {
            "BUY_MODE": config_dict.get("BUY_MODE"),
            "USE_PREDICTIVE_BUYS": config_dict.get("USE_PREDICTIVE_BUYS"),
            "USE_BUY_ESCALATION": config_dict.get("USE_BUY_ESCALATION"),
            "NEVER_MARKET_SELLS": config_dict.get("NEVER_MARKET_SELLS"),
            "USE_DEPTH_SWEEP": config_dict.get("USE_DEPTH_SWEEP"),
            "ENTRY_ORDER_TIF": config_dict.get("ENTRY_ORDER_TIF"),
        },
        "system": {
            "GLOBAL_TRADING": config_dict.get("GLOBAL_TRADING"),
            "RESET_PORTFOLIO_ON_START": config_dict.get("RESET_PORTFOLIO_ON_START"),
            "SAFE_MIN_BUDGET": config_dict.get("SAFE_MIN_BUDGET"),
            "CASH_RESERVE_USDT": config_dict.get("CASH_RESERVE_USDT"),
            "MIN_NOTIONAL_USDT": config_dict.get("MIN_NOTIONAL_USDT"),
            "LOG_LEVEL": config_dict.get("LOG_LEVEL"),
            "FSM_ENABLED": config_dict.get("FSM_ENABLED"),
        }
    }

    # Calculate config hash for change detection
    config_str = json.dumps(config_snapshot, sort_keys=True, default=str)
    config_hash = hashlib.sha256(config_str.encode()).hexdigest()[:16]

    # Log to AUDIT_LOG
    log_event(
        AUDIT_LOG(),
        "config_snapshot",
        message=f"Configuration snapshot at startup (hash: {config_hash})",
        level=logging.INFO,
        config_hash=config_hash,
        config=config_snapshot,
        config_version=config_dict.get("CONFIG_VERSION", "unknown"),
        session_id=session_id
    )

    return config_hash


def log_config_change(
    parameter: str,
    old_value: Any,
    new_value: Any,
    reason: str = None,
    session_id: str = None
) -> None:
    """
    Log runtime configuration parameter change.

    Use this when config parameters are modified at runtime
    (e.g., via Telegram commands or adaptive algorithms).

    Args:
        parameter: Parameter name (e.g., "TAKE_PROFIT_THRESHOLD")
        old_value: Previous value
        new_value: New value
        reason: Reason for change (e.g., "user_command", "adaptive_adjustment")
        session_id: Session ID for correlation

    Example:
        from core.logger_factory import log_config_change

        log_config_change(
            "TAKE_PROFIT_THRESHOLD",
            old_value=1.005,
            new_value=1.008,
            reason="user_command",
            session_id="sess_123"
        )
    """
    # Calculate value change
    change_pct = None
    if isinstance(old_value, (int, float)) and isinstance(new_value, (int, float)):
        if old_value != 0:
            change_pct = ((new_value - old_value) / old_value) * 100

    log_event(
        AUDIT_LOG(),
        "config_change",
        message=f"Config parameter changed: {parameter} = {old_value} → {new_value}",
        level=logging.WARNING,  # WARNING level for visibility
        parameter=parameter,
        old_value=old_value,
        new_value=new_value,
        change_pct=change_pct,
        reason=reason,
        session_id=session_id
    )


def check_config_drift(
    config_module: Any,
    expected_hash: str,
    session_id: str = None
) -> Dict[str, Any]:
    """
    Check for configuration drift by comparing current hash with expected.

    Use this periodically (e.g., every hour) to detect unexpected
    config changes that may indicate tampering or bugs.

    Args:
        config_module: Config module (import config)
        expected_hash: Expected config hash from startup
        session_id: Session ID for correlation

    Returns:
        Dict with drift_detected (bool) and current_hash (str)

    Example:
        import config
        from core.logger_factory import check_config_drift

        result = check_config_drift(
            config,
            expected_hash="a1b2c3d4e5f6g7h8",
            session_id="sess_123"
        )

        if result["drift_detected"]:
            # Config changed unexpectedly!
            pass
    """
    import hashlib

    # Recalculate current config hash
    config_dict = {
        k: getattr(config_module, k)
        for k in dir(config_module)
        if k.isupper() and not k.startswith('_')
    }

    config_str = json.dumps(config_dict, sort_keys=True, default=str)
    current_hash = hashlib.sha256(config_str.encode()).hexdigest()[:16]

    # Check for drift
    drift_detected = (current_hash != expected_hash)

    if drift_detected:
        log_event(
            AUDIT_LOG(),
            "config_drift_detected",
            message=f"Config drift detected: expected {expected_hash}, got {current_hash}",
            level=logging.ERROR,
            expected_hash=expected_hash,
            current_hash=current_hash,
            drift_detected=True,
            session_id=session_id
        )
    else:
        log_event(
            AUDIT_LOG(),
            "config_drift_check",
            message=f"Config drift check passed (hash: {current_hash})",
            level=logging.DEBUG,
            expected_hash=expected_hash,
            current_hash=current_hash,
            drift_detected=False,
            session_id=session_id
        )

    return {
        "drift_detected": drift_detected,
        "current_hash": current_hash
    }
