#!/usr/bin/env python3
"""
Exchange Recorder – zentraler Mitschnitt aller CCXT/MEXC-Aufrufe.

Ermöglicht:
* vollständiges Tracking aller Exchange-Methoden (inkl. Dauer & Fehler)
* Export der Aufrufe als JSONL für spätere Mock-/Replay-Szenarien
* thread-sichere Nutzung während des Bot-Laufs
"""

from __future__ import annotations

import copy
import json
import logging
import os
import threading
import time
from functools import wraps
from typing import Any, Dict, Iterable, List, Optional, Tuple

logger = logging.getLogger(__name__)


def _json_serialiser(obj: Any) -> Any:
    """Fallback-Serialisierer für JSON dumps."""
    if isinstance(obj, (bytes, bytearray)):
        return obj.decode("utf-8", errors="replace")
    return repr(obj)


def _safe_clone(value: Any) -> Any:
    """Versucht, den übergebenen Wert JSON-kompatibel zu kopieren."""
    try:
        return copy.deepcopy(value)
    except Exception:
        try:
            return json.loads(json.dumps(value, default=_json_serialiser))
        except Exception:
            return repr(value)


class ExchangeCallRecorder:
    """Thread-sicherer Recorder für Exchange-Aufrufe."""

    def __init__(self) -> None:
        self._enabled = False
        self._records: List[Dict[str, Any]] = []
        self._lock = threading.RLock()
        self._output_path: Optional[str] = None
        self._metadata: Dict[str, Any] = {}
        self._counter = 0

    # ------------------------------------------------------------------ #
    # State
    # ------------------------------------------------------------------ #
    @property
    def enabled(self) -> bool:
        return self._enabled

    def enable(self) -> None:
        with self._lock:
            self._enabled = True
            logger.info("ExchangeCallRecorder enabled")

    def disable(self) -> None:
        with self._lock:
            self._enabled = False
            logger.info("ExchangeCallRecorder disabled")

    def clear(self) -> None:
        with self._lock:
            self._records.clear()
            self._counter = 0

    # ------------------------------------------------------------------ #
    # Recording
    # ------------------------------------------------------------------ #
    def record(
        self,
        method: str,
        args: Tuple[Any, ...],
        kwargs: Dict[str, Any],
        result: Any,
        duration_s: float,
        error: Optional[BaseException]
    ) -> None:
        """Speichert einen Exchange-Aufruf."""
        if not self.enabled:
            return

        entry = {
            "type": "call",
            "id": None,
            "ts": time.time(),
            "thread": threading.current_thread().name,
            "method": method,
            "duration_ms": round(duration_s * 1000.0, 3),
            "args": _safe_clone(args),
            "kwargs": _safe_clone(kwargs),
        }

        if error is not None:
            entry["error"] = {
                "type": error.__class__.__name__,
                "message": str(error)
            }
        else:
            entry["result"] = _safe_clone(result)

        with self._lock:
            self._counter += 1
            entry["id"] = self._counter
            self._records.append(entry)

    # ------------------------------------------------------------------ #
    # Metadata & Export
    # ------------------------------------------------------------------ #
    def set_metadata(self, **metadata: Any) -> None:
        with self._lock:
            self._metadata.update(metadata)

    def set_output_path(self, path: str) -> None:
        with self._lock:
            self._output_path = path

    def get_records(self) -> List[Dict[str, Any]]:
        with self._lock:
            return copy.deepcopy(self._records)

    def flush_to_disk(self) -> None:
        with self._lock:
            path = self._output_path
            if not path:
                logger.debug("ExchangeCallRecorder.flush_to_disk: Kein Zielpfad gesetzt, überspringe.")
                return

            records = list(self._records)
            metadata = dict(self._metadata)

        if not records:
            logger.debug("ExchangeCallRecorder.flush_to_disk: Keine Aufzeichnungen vorhanden.")
            return

        os.makedirs(os.path.dirname(path), exist_ok=True)
        try:
            with open(path, "w", encoding="utf-8") as fh:
                if metadata:
                    fh.write(json.dumps({"type": "metadata", "data": metadata}, ensure_ascii=False, default=_json_serialiser))
                    fh.write("\n")
                for entry in records:
                    fh.write(json.dumps(entry, ensure_ascii=False, default=_json_serialiser))
                    fh.write("\n")
            logger.info("ExchangeCallRecorder: %s Einträge nach %s geschrieben", len(records), path)
        except Exception as exc:
            logger.error("ExchangeCallRecorder: Schreiben nach %s fehlgeschlagen: %s", path, exc)


class RecordingExchangeProxy:
    """
    Leitet alle Attribute an den echten Exchange durch und interceptet
    aufrufbare Attribute für den Recorder.
    """

    __slots__ = ("_exchange", "_recorder")

    def __init__(self, exchange: Any, recorder: ExchangeCallRecorder) -> None:
        object.__setattr__(self, "_exchange", exchange)
        object.__setattr__(self, "_recorder", recorder)

    # ------------------------------------------------------------------ #
    # Attribute Proxying
    # ------------------------------------------------------------------ #
    def __getattr__(self, item: str) -> Any:
        target = getattr(self._exchange, item)
        if callable(target):
            return self._wrap_callable(item, target)
        return target

    def __setattr__(self, key: str, value: Any) -> None:
        setattr(self._exchange, key, value)

    def __dir__(self) -> Iterable[str]:
        return dir(self._exchange)

    def __repr__(self) -> str:
        return f"RecordingExchangeProxy({self._exchange!r})"

    @property
    def __class__(self):  # type: ignore[override]
        return self._exchange.__class__

    # ------------------------------------------------------------------ #
    # Helpers
    # ------------------------------------------------------------------ #
    def _wrap_callable(self, name: str, func):
        @wraps(func)
        def wrapper(*args, **kwargs):
            start = time.perf_counter()
            try:
                result = func(*args, **kwargs)
                self._recorder.record(name, args, kwargs, result, time.perf_counter() - start, None)
                return result
            except Exception as exc:  # pragma: no cover - defensive logging
                self._recorder.record(name, args, kwargs, None, time.perf_counter() - start, exc)
                raise

        return wrapper

    def unwrap(self) -> Any:
        return self._exchange


# ============================================================================
# Global Recorder Singleton
# ============================================================================
_RECORDER = ExchangeCallRecorder()


def get_exchange_recorder() -> ExchangeCallRecorder:
    return _RECORDER


def is_exchange_recording_enabled() -> bool:
    return _RECORDER.enabled


def activate_exchange_recording(exchange: Any) -> Any:
    """
    Aktiviert den Recorder und liefert ggf. eine Proxy-Instanz zurück.
    Bei mehrfacher Aktivierung wird der Exchange nicht erneut umwickelt.
    """
    recorder = get_exchange_recorder()
    if not recorder.enabled:
        recorder.enable()

    if isinstance(exchange, RecordingExchangeProxy):
        return exchange

    return RecordingExchangeProxy(exchange, recorder)


def unwrap_exchange(exchange: Any) -> Any:
    """Gibt den ursprünglichen Exchange zurück."""
    if isinstance(exchange, RecordingExchangeProxy):
        return exchange.unwrap()
    return exchange
