"""Structured JSONL logging utilities for trading events."""
from __future__ import annotations

import json
import os
import uuid
import datetime as _dt
from pathlib import Path
from typing import Any, Dict

try:  # Prefer session-specific directories from config if available
    from config import LOG_DIR, SESSION_DIR
except ImportError:  # Fallback for tooling
    LOG_DIR = os.path.join(os.getcwd(), "logs")
    SESSION_DIR = os.getcwd()

_DEFAULT_BASE_DIR = os.path.join(LOG_DIR, "jsonl")


def _utcnow_iso() -> str:
    return _dt.datetime.now(_dt.timezone.utc).isoformat()


def _ensure_dir(path: str) -> None:
    Path(path).mkdir(parents=True, exist_ok=True)


class JsonlLogger:
    """Simple JSONL writer with typed helper events."""

    def __init__(self, base_dir: str | None = None, session_id: str | None = None) -> None:
        self.base_dir = base_dir or _DEFAULT_BASE_DIR
        _ensure_dir(self.base_dir)
        self.session_id = session_id or os.environ.get("BOT_SESSION_ID") or Path(SESSION_DIR).name

    def _path(self, name: str) -> str:
        return os.path.join(self.base_dir, f"{name}.jsonl")

    def write(self, name: str, payload: Dict[str, Any]) -> None:
        record = dict(payload)
        record.setdefault("ts", _utcnow_iso())
        record.setdefault("session_id", self.session_id)
        _ensure_dir(os.path.dirname(self._path(name)))
        with open(self._path(name), "a", encoding="utf-8") as handle:
            handle.write(json.dumps(record, ensure_ascii=False) + "\n")

    # Decision lifecycle ---------------------------------------------------
    def decision_start(self, **payload: Any) -> None:
        self.write("events", {"event_type": "DECISION_START", **payload})

    def decision_end(self, **payload: Any) -> None:
        self.write("events", {"event_type": "DECISION_END", **payload})

    # Guard / cooldown -----------------------------------------------------
    def guard_block(self, **payload: Any) -> None:
        self.write("events", {"event_type": "GUARD_BLOCK", **payload})

    def guard_pass(self, **payload: Any) -> None:
        self.write("events", {"event_type": "GUARD_PASS", **payload})

    def cooldown_set(self, **payload: Any) -> None:
        self.write("events", {"event_type": "COOLDOWN_SET", **payload})

    def cooldown_release(self, **payload: Any) -> None:
        self.write("events", {"event_type": "COOLDOWN_RELEASE", **payload})

    # Order lifecycle ------------------------------------------------------
    def order_sent(self, **payload: Any) -> None:
        self.write("orders", {"event_type": "ORDER_SENT", **payload})

    def order_update(self, **payload: Any) -> None:
        self.write("orders", {"event_type": "ORDER_UPDATE", **payload})

    def order_filled(self, **payload: Any) -> None:
        self.write("orders", {"event_type": "ORDER_FILLED", **payload})

    def order_canceled(self, **payload: Any) -> None:
        self.write("orders", {"event_type": "ORDER_CANCELED", **payload})

    def order_expired(self, **payload: Any) -> None:
        self.write("orders", {"event_type": "ORDER_EXPIRED", **payload})

    def order_failed(self, **payload: Any) -> None:
        self.write("orders", {"event_type": "ORDER_FAILED", **payload})

    # Trades ---------------------------------------------------------------
    def trade_open(self, **payload: Any) -> None:
        self.write("trades", {"event_type": "TRADE_OPEN", **payload})

    def trade_close(self, **payload: Any) -> None:
        self.write("trades", {"event_type": "TRADE_CLOSE", **payload})


def new_decision_id() -> str:
    return "d_" + uuid.uuid4().hex[:12]


def new_client_order_id(decision_id: str, side: str) -> str:
    safe_side = side.upper()[:4]
    return f"cli_{safe_side}_{decision_id}_{uuid.uuid4().hex[:8]}"
