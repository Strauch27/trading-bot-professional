#!/usr/bin/env python3
"""
JSONL Writer - Telemetry Logging

Writes telemetry events to JSONL files for linear audit trail.
One file per event type for easy filtering and analysis.
"""

import json
import os
from typing import Any


class JsonlWriter:
    """
    Simple JSONL writer for telemetry events.

    Writes one JSON object per line to named files.
    Thread-safe with atomic writes (append mode).
    """

    def __init__(self, base: str = "telemetry") -> None:
        """
        Initialize JSONL writer.

        Args:
            base: Base directory for telemetry files
        """
        self.base = base
        os.makedirs(base, exist_ok=True)

    def write(self, name: str, obj: Any) -> None:
        """
        Write a telemetry event to named file.

        Args:
            name: Event type name (e.g., "market_snapshot")
            obj: Event object (will be JSON serialized)
        """
        path = os.path.join(self.base, f"{name}.jsonl")
        try:
            with open(path, "a") as f:
                f.write(json.dumps(obj, ensure_ascii=False) + "\n")
        except Exception:
            # Silent fail to prevent telemetry from disrupting main flow
            pass
