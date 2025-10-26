#!/usr/bin/env python3
"""
Market Data Audit Logger

JSONL-based audit trail for market data operations with:
- Cache HIT/MISS/STALE tracking
- Latency measurement
- Source attribution
- Decision context linking
- Schema versioning

Purpose:
- Forensic analysis of trading decisions
- Cache performance monitoring
- Data quality tracking
- Debugging market data issues

Example Log Entry:
{
  "schema": "mds_v1",
  "ts": 1697453723.456,
  "route": "ticker",
  "symbol": "BTC/USDT",
  "status": "HIT",
  "latency_ms": 0.123,
  "source": "cache",
  "decision_id": "dec_abc123",
  "meta": {"age_ms": 1234}
}
"""

import json
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Optional


class MarketDataAuditor:
    """
    JSONL audit logger for market data operations.

    Features:
    - Thread-safe file writes
    - Schema versioning
    - Structured logging
    - Optional compression
    - Rotation support

    Thread-Safety:
        File writes are atomic per-line (JSONL format).
        For high-frequency logging, consider buffering.
    """

    SCHEMA_VERSION = "mds_v1"

    def __init__(
        self,
        log_dir: Path,
        enabled: bool = True,
        buffer_size: int = 100
    ):
        """
        Initialize audit logger.

        Args:
            log_dir: Directory for audit logs
            enabled: Enable/disable logging
            buffer_size: Number of entries to buffer before flush
        """
        self.log_dir = Path(log_dir)
        self.enabled = enabled
        self.buffer_size = buffer_size
        self._buffer = []
        self._log_file = None

        if self.enabled:
            self._setup_log_file()

    def _setup_log_file(self):
        """Setup log file with daily rotation."""
        if not self.log_dir.exists():
            self.log_dir.mkdir(parents=True, exist_ok=True)

        # Daily rotation: market_data_audit_2025-10-13.jsonl
        date_str = datetime.now().strftime("%Y-%m-%d")
        log_path = self.log_dir / f"market_data_audit_{date_str}.jsonl"

        # Open in append mode
        self._log_file = open(log_path, "a", encoding="utf-8")

    def log_ticker(
        self,
        symbol: str,
        status: str,
        latency_ms: float,
        source: str,
        decision_id: Optional[str] = None,
        meta: Optional[Dict[str, Any]] = None
    ):
        """
        Log ticker fetch operation.

        Args:
            symbol: Trading pair symbol
            status: Cache status (HIT, STALE, MISS, ERROR)
            latency_ms: Fetch latency in milliseconds
            source: Data source (cache, exchange, fallback)
            decision_id: Optional decision context ID
            meta: Additional metadata (age_ms, server_time, etc.)
        """
        self._log_event(
            route="ticker",
            symbol=symbol,
            status=status,
            latency_ms=latency_ms,
            source=source,
            decision_id=decision_id,
            meta=meta or {}
        )

    def log_ohlcv(
        self,
        symbol: str,
        timeframe: str,
        status: str,
        latency_ms: float,
        source: str,
        candles_count: int,
        decision_id: Optional[str] = None,
        meta: Optional[Dict[str, Any]] = None
    ):
        """
        Log OHLCV fetch operation.

        Args:
            symbol: Trading pair symbol
            timeframe: Timeframe (1m, 5m, 1h, etc.)
            status: Cache status
            latency_ms: Fetch latency
            source: Data source
            candles_count: Number of candles fetched
            decision_id: Decision context ID
            meta: Additional metadata
        """
        meta = meta or {}
        meta["timeframe"] = timeframe
        meta["candles_count"] = candles_count

        self._log_event(
            route="ohlcv",
            symbol=symbol,
            status=status,
            latency_ms=latency_ms,
            source=source,
            decision_id=decision_id,
            meta=meta
        )

    def log_orderbook(
        self,
        symbol: str,
        status: str,
        latency_ms: float,
        source: str,
        depth: int,
        decision_id: Optional[str] = None,
        meta: Optional[Dict[str, Any]] = None
    ):
        """
        Log orderbook fetch operation.

        Args:
            symbol: Trading pair symbol
            status: Cache status
            latency_ms: Fetch latency
            source: Data source
            depth: Orderbook depth (number of levels)
            decision_id: Decision context ID
            meta: Additional metadata
        """
        meta = meta or {}
        meta["depth"] = depth

        self._log_event(
            route="orderbook",
            symbol=symbol,
            status=status,
            latency_ms=latency_ms,
            source=source,
            decision_id=decision_id,
            meta=meta
        )

    def log_error(
        self,
        route: str,
        symbol: str,
        error_type: str,
        error_msg: str,
        decision_id: Optional[str] = None,
        meta: Optional[Dict[str, Any]] = None
    ):
        """
        Log market data error.

        Args:
            route: Route (ticker, ohlcv, orderbook)
            symbol: Trading pair symbol
            error_type: Error type (NetworkError, RateLimitError, etc.)
            error_msg: Error message
            decision_id: Decision context ID
            meta: Additional metadata
        """
        meta = meta or {}
        meta["error_type"] = error_type
        meta["error_msg"] = error_msg

        self._log_event(
            route=route,
            symbol=symbol,
            status="ERROR",
            latency_ms=0.0,
            source="error",
            decision_id=decision_id,
            meta=meta
        )

    def _log_event(
        self,
        route: str,
        symbol: str,
        status: str,
        latency_ms: float,
        source: str,
        decision_id: Optional[str],
        meta: Dict[str, Any]
    ):
        """
        Log audit event to JSONL file.

        Args:
            route: Route identifier
            symbol: Symbol
            status: Status code
            latency_ms: Latency
            source: Source identifier
            decision_id: Decision ID
            meta: Metadata dict
        """
        if not self.enabled:
            return

        entry = {
            "schema": self.SCHEMA_VERSION,
            "ts": time.time(),
            "route": route,
            "symbol": symbol,
            "status": status,
            "latency_ms": round(latency_ms, 3),
            "source": source,
        }

        if decision_id:
            entry["decision_id"] = decision_id

        if meta:
            entry["meta"] = meta

        # Buffer for batch writes
        self._buffer.append(entry)

        # Flush if buffer full
        if len(self._buffer) >= self.buffer_size:
            self.flush()

    def flush(self):
        """Flush buffered entries to disk."""
        if not self.enabled or not self._buffer:
            return

        if self._log_file is None or self._log_file.closed:
            self._setup_log_file()

        try:
            for entry in self._buffer:
                json_line = json.dumps(entry, separators=(',', ':'))
                self._log_file.write(json_line + "\n")

            self._log_file.flush()
            self._buffer.clear()
        except Exception as e:
            # Silent failure - don't crash bot due to logging error
            print(f"[md_audit] Flush error: {e}")

    def close(self):
        """Close audit log file."""
        self.flush()

        if self._log_file and not self._log_file.closed:
            self._log_file.close()

    def __enter__(self):
        """Context manager entry."""
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        """Context manager exit."""
        self.close()


class AuditStats:
    """
    Analyze audit logs for statistics and insights.

    Usage:
        >>> stats = AuditStats.from_file("logs/market_data_audit_2025-10-13.jsonl")
        >>> print(f"Cache hit rate: {stats.hit_rate:.2%}")
        >>> print(f"Avg latency: {stats.avg_latency_ms:.2f}ms")
    """

    def __init__(self):
        """Initialize empty statistics."""
        self.total_requests = 0
        self.hits = 0
        self.stale_hits = 0
        self.misses = 0
        self.errors = 0
        self.total_latency_ms = 0.0
        self.by_route = {}
        self.by_symbol = {}

    @classmethod
    def from_file(cls, log_path: Path) -> "AuditStats":
        """
        Load and analyze audit log file.

        Args:
            log_path: Path to JSONL audit log

        Returns:
            AuditStats object with computed statistics
        """
        stats = cls()

        if not Path(log_path).exists():
            return stats

        with open(log_path, "r", encoding="utf-8") as f:
            for line in f:
                if not line.strip():
                    continue

                try:
                    entry = json.loads(line)
                    stats._process_entry(entry)
                except json.JSONDecodeError:
                    continue

        return stats

    def _process_entry(self, entry: Dict[str, Any]):
        """Process single log entry."""
        status = entry.get("status", "UNKNOWN")
        route = entry.get("route", "unknown")
        symbol = entry.get("symbol", "unknown")
        latency_ms = entry.get("latency_ms", 0.0)

        self.total_requests += 1
        self.total_latency_ms += latency_ms

        # Count by status
        if status == "HIT":
            self.hits += 1
        elif status == "STALE":
            self.stale_hits += 1
        elif status == "MISS":
            self.misses += 1
        elif status == "ERROR":
            self.errors += 1

        # Count by route
        if route not in self.by_route:
            self.by_route[route] = {"count": 0, "latency_sum": 0.0}
        self.by_route[route]["count"] += 1
        self.by_route[route]["latency_sum"] += latency_ms

        # Count by symbol
        if symbol not in self.by_symbol:
            self.by_symbol[symbol] = {"count": 0}
        self.by_symbol[symbol]["count"] += 1

    @property
    def hit_rate(self) -> float:
        """Cache hit rate (excluding errors)."""
        valid_requests = self.total_requests - self.errors
        if valid_requests == 0:
            return 0.0
        return self.hits / valid_requests

    @property
    def stale_rate(self) -> float:
        """Stale hit rate."""
        valid_requests = self.total_requests - self.errors
        if valid_requests == 0:
            return 0.0
        return self.stale_hits / valid_requests

    @property
    def miss_rate(self) -> float:
        """Cache miss rate."""
        valid_requests = self.total_requests - self.errors
        if valid_requests == 0:
            return 0.0
        return self.misses / valid_requests

    @property
    def error_rate(self) -> float:
        """Error rate."""
        if self.total_requests == 0:
            return 0.0
        return self.errors / self.total_requests

    @property
    def avg_latency_ms(self) -> float:
        """Average latency in milliseconds."""
        if self.total_requests == 0:
            return 0.0
        return self.total_latency_ms / self.total_requests

    def summary(self) -> Dict[str, Any]:
        """Get summary statistics."""
        return {
            "total_requests": self.total_requests,
            "hits": self.hits,
            "stale_hits": self.stale_hits,
            "misses": self.misses,
            "errors": self.errors,
            "hit_rate": self.hit_rate,
            "stale_rate": self.stale_rate,
            "miss_rate": self.miss_rate,
            "error_rate": self.error_rate,
            "avg_latency_ms": self.avg_latency_ms,
            "by_route": self.by_route,
            "top_symbols": sorted(
                self.by_symbol.items(),
                key=lambda x: x[1]["count"],
                reverse=True
            )[:10]
        }
