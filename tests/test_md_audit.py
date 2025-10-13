#!/usr/bin/env python3
"""
Tests for services/md_audit.py

Tests JSONL audit logging and statistics analysis.
"""

import pytest
import json
import tempfile
from pathlib import Path
from services.md_audit import MarketDataAuditor, AuditStats


class TestMarketDataAuditor:
    """Test MarketDataAuditor JSONL logging"""

    def test_create_log_file(self):
        """Test that log file is created"""
        with tempfile.TemporaryDirectory() as tmpdir:
            auditor = MarketDataAuditor(log_dir=Path(tmpdir), enabled=True)

            # Log an event
            auditor.log_ticker(
                symbol="BTC/USDT",
                status="HIT",
                latency_ms=1.5,
                source="cache"
            )
            auditor.flush()

            # Check that log file exists
            log_files = list(Path(tmpdir).glob("market_data_audit_*.jsonl"))
            assert len(log_files) == 1

    def test_log_ticker_event(self):
        """Test logging ticker fetch event"""
        with tempfile.TemporaryDirectory() as tmpdir:
            auditor = MarketDataAuditor(log_dir=Path(tmpdir), enabled=True)

            auditor.log_ticker(
                symbol="BTC/USDT",
                status="HIT",
                latency_ms=1.5,
                source="cache",
                decision_id="dec_123",
                meta={"age_ms": 500}
            )
            auditor.flush()

            # Read log file
            log_files = list(Path(tmpdir).glob("market_data_audit_*.jsonl"))
            with open(log_files[0], "r") as f:
                line = f.readline()
                entry = json.loads(line)

            assert entry["schema"] == "mds_v1"
            assert entry["route"] == "ticker"
            assert entry["symbol"] == "BTC/USDT"
            assert entry["status"] == "HIT"
            assert entry["latency_ms"] == 1.5
            assert entry["source"] == "cache"
            assert entry["decision_id"] == "dec_123"
            assert entry["meta"]["age_ms"] == 500

    def test_log_ohlcv_event(self):
        """Test logging OHLCV fetch event"""
        with tempfile.TemporaryDirectory() as tmpdir:
            auditor = MarketDataAuditor(log_dir=Path(tmpdir), enabled=True)

            auditor.log_ohlcv(
                symbol="ETH/USDT",
                timeframe="1m",
                status="MISS",
                latency_ms=15.3,
                source="exchange",
                candles_count=100
            )
            auditor.flush()

            # Read log file
            log_files = list(Path(tmpdir).glob("market_data_audit_*.jsonl"))
            with open(log_files[0], "r") as f:
                line = f.readline()
                entry = json.loads(line)

            assert entry["route"] == "ohlcv"
            assert entry["symbol"] == "ETH/USDT"
            assert entry["status"] == "MISS"
            assert entry["meta"]["timeframe"] == "1m"
            assert entry["meta"]["candles_count"] == 100

    def test_log_orderbook_event(self):
        """Test logging orderbook fetch event"""
        with tempfile.TemporaryDirectory() as tmpdir:
            auditor = MarketDataAuditor(log_dir=Path(tmpdir), enabled=True)

            auditor.log_orderbook(
                symbol="BTC/USDT",
                status="HIT",
                latency_ms=2.1,
                source="cache",
                depth=20
            )
            auditor.flush()

            # Read log file
            log_files = list(Path(tmpdir).glob("market_data_audit_*.jsonl"))
            with open(log_files[0], "r") as f:
                line = f.readline()
                entry = json.loads(line)

            assert entry["route"] == "orderbook"
            assert entry["meta"]["depth"] == 20

    def test_log_error(self):
        """Test logging error event"""
        with tempfile.TemporaryDirectory() as tmpdir:
            auditor = MarketDataAuditor(log_dir=Path(tmpdir), enabled=True)

            auditor.log_error(
                route="ticker",
                symbol="BTC/USDT",
                error_type="NetworkError",
                error_msg="Connection timeout"
            )
            auditor.flush()

            # Read log file
            log_files = list(Path(tmpdir).glob("market_data_audit_*.jsonl"))
            with open(log_files[0], "r") as f:
                line = f.readline()
                entry = json.loads(line)

            assert entry["status"] == "ERROR"
            assert entry["meta"]["error_type"] == "NetworkError"
            assert entry["meta"]["error_msg"] == "Connection timeout"

    def test_buffering(self):
        """Test that events are buffered before flush"""
        with tempfile.TemporaryDirectory() as tmpdir:
            auditor = MarketDataAuditor(log_dir=Path(tmpdir), enabled=True, buffer_size=5)

            # Log 3 events (less than buffer size)
            for i in range(3):
                auditor.log_ticker(
                    symbol=f"SYM{i}",
                    status="HIT",
                    latency_ms=1.0,
                    source="cache"
                )

            # File should still be empty (not flushed)
            log_files = list(Path(tmpdir).glob("market_data_audit_*.jsonl"))
            with open(log_files[0], "r") as f:
                lines = f.readlines()
            assert len(lines) == 0

            # Flush manually
            auditor.flush()

            # Now file should have 3 entries
            with open(log_files[0], "r") as f:
                lines = f.readlines()
            assert len(lines) == 3

    def test_auto_flush_on_buffer_full(self):
        """Test automatic flush when buffer is full"""
        with tempfile.TemporaryDirectory() as tmpdir:
            auditor = MarketDataAuditor(log_dir=Path(tmpdir), enabled=True, buffer_size=5)

            # Log 5 events (fill buffer)
            for i in range(5):
                auditor.log_ticker(
                    symbol=f"SYM{i}",
                    status="HIT",
                    latency_ms=1.0,
                    source="cache"
                )

            # Buffer should auto-flush
            log_files = list(Path(tmpdir).glob("market_data_audit_*.jsonl"))
            with open(log_files[0], "r") as f:
                lines = f.readlines()
            assert len(lines) == 5

    def test_context_manager(self):
        """Test using auditor as context manager"""
        with tempfile.TemporaryDirectory() as tmpdir:
            with MarketDataAuditor(log_dir=Path(tmpdir), enabled=True) as auditor:
                auditor.log_ticker(
                    symbol="BTC/USDT",
                    status="HIT",
                    latency_ms=1.0,
                    source="cache"
                )

            # File should be flushed and closed
            log_files = list(Path(tmpdir).glob("market_data_audit_*.jsonl"))
            with open(log_files[0], "r") as f:
                lines = f.readlines()
            assert len(lines) == 1

    def test_disabled_auditor(self):
        """Test that disabled auditor doesn't write"""
        with tempfile.TemporaryDirectory() as tmpdir:
            auditor = MarketDataAuditor(log_dir=Path(tmpdir), enabled=False)

            auditor.log_ticker(
                symbol="BTC/USDT",
                status="HIT",
                latency_ms=1.0,
                source="cache"
            )
            auditor.flush()

            # No log files should be created
            log_files = list(Path(tmpdir).glob("market_data_audit_*.jsonl"))
            assert len(log_files) == 0


class TestAuditStats:
    """Test AuditStats analysis"""

    def test_stats_from_file(self):
        """Test loading and analyzing audit log"""
        with tempfile.TemporaryDirectory() as tmpdir:
            log_path = Path(tmpdir) / "test_audit.jsonl"

            # Create sample log
            events = [
                {"schema": "mds_v1", "ts": 1.0, "route": "ticker", "symbol": "BTC/USDT", "status": "HIT", "latency_ms": 0.5, "source": "cache"},
                {"schema": "mds_v1", "ts": 2.0, "route": "ticker", "symbol": "ETH/USDT", "status": "STALE", "latency_ms": 0.8, "source": "cache"},
                {"schema": "mds_v1", "ts": 3.0, "route": "ticker", "symbol": "BTC/USDT", "status": "MISS", "latency_ms": 15.0, "source": "exchange"},
                {"schema": "mds_v1", "ts": 4.0, "route": "ticker", "symbol": "BTC/USDT", "status": "ERROR", "latency_ms": 0.0, "source": "error"},
            ]

            with open(log_path, "w") as f:
                for event in events:
                    f.write(json.dumps(event) + "\n")

            # Analyze
            stats = AuditStats.from_file(log_path)

            assert stats.total_requests == 4
            assert stats.hits == 1
            assert stats.stale_hits == 1
            assert stats.misses == 1
            assert stats.errors == 1

    def test_stats_rates(self):
        """Test calculating hit/miss/error rates"""
        with tempfile.TemporaryDirectory() as tmpdir:
            log_path = Path(tmpdir) / "test_audit.jsonl"

            events = [
                {"schema": "mds_v1", "ts": 1.0, "route": "ticker", "symbol": "BTC/USDT", "status": "HIT", "latency_ms": 0.5, "source": "cache"},
                {"schema": "mds_v1", "ts": 2.0, "route": "ticker", "symbol": "BTC/USDT", "status": "HIT", "latency_ms": 0.5, "source": "cache"},
                {"schema": "mds_v1", "ts": 3.0, "route": "ticker", "symbol": "BTC/USDT", "status": "MISS", "latency_ms": 10.0, "source": "exchange"},
                {"schema": "mds_v1", "ts": 4.0, "route": "ticker", "symbol": "BTC/USDT", "status": "ERROR", "latency_ms": 0.0, "source": "error"},
            ]

            with open(log_path, "w") as f:
                for event in events:
                    f.write(json.dumps(event) + "\n")

            stats = AuditStats.from_file(log_path)

            # Hit rate: 2 hits out of 3 valid requests (excluding error)
            assert stats.hit_rate == pytest.approx(2.0 / 3.0)
            assert stats.miss_rate == pytest.approx(1.0 / 3.0)
            assert stats.error_rate == pytest.approx(1.0 / 4.0)

    def test_stats_avg_latency(self):
        """Test average latency calculation"""
        with tempfile.TemporaryDirectory() as tmpdir:
            log_path = Path(tmpdir) / "test_audit.jsonl"

            events = [
                {"schema": "mds_v1", "ts": 1.0, "route": "ticker", "symbol": "BTC/USDT", "status": "HIT", "latency_ms": 1.0, "source": "cache"},
                {"schema": "mds_v1", "ts": 2.0, "route": "ticker", "symbol": "BTC/USDT", "status": "MISS", "latency_ms": 10.0, "source": "exchange"},
                {"schema": "mds_v1", "ts": 3.0, "route": "ticker", "symbol": "BTC/USDT", "status": "HIT", "latency_ms": 2.0, "source": "cache"},
            ]

            with open(log_path, "w") as f:
                for event in events:
                    f.write(json.dumps(event) + "\n")

            stats = AuditStats.from_file(log_path)

            # Avg: (1.0 + 10.0 + 2.0) / 3 = 4.33
            assert stats.avg_latency_ms == pytest.approx(13.0 / 3.0)

    def test_stats_by_route(self):
        """Test statistics by route"""
        with tempfile.TemporaryDirectory() as tmpdir:
            log_path = Path(tmpdir) / "test_audit.jsonl"

            events = [
                {"schema": "mds_v1", "ts": 1.0, "route": "ticker", "symbol": "BTC/USDT", "status": "HIT", "latency_ms": 1.0, "source": "cache"},
                {"schema": "mds_v1", "ts": 2.0, "route": "ohlcv", "symbol": "BTC/USDT", "status": "MISS", "latency_ms": 20.0, "source": "exchange"},
                {"schema": "mds_v1", "ts": 3.0, "route": "ticker", "symbol": "ETH/USDT", "status": "HIT", "latency_ms": 2.0, "source": "cache"},
            ]

            with open(log_path, "w") as f:
                for event in events:
                    f.write(json.dumps(event) + "\n")

            stats = AuditStats.from_file(log_path)

            assert stats.by_route["ticker"]["count"] == 2
            assert stats.by_route["ohlcv"]["count"] == 1

    def test_stats_summary(self):
        """Test summary statistics"""
        with tempfile.TemporaryDirectory() as tmpdir:
            log_path = Path(tmpdir) / "test_audit.jsonl"

            events = [
                {"schema": "mds_v1", "ts": 1.0, "route": "ticker", "symbol": "BTC/USDT", "status": "HIT", "latency_ms": 1.0, "source": "cache"},
                {"schema": "mds_v1", "ts": 2.0, "route": "ticker", "symbol": "BTC/USDT", "status": "MISS", "latency_ms": 10.0, "source": "exchange"},
            ]

            with open(log_path, "w") as f:
                for event in events:
                    f.write(json.dumps(event) + "\n")

            stats = AuditStats.from_file(log_path)
            summary = stats.summary()

            assert "total_requests" in summary
            assert "hit_rate" in summary
            assert "avg_latency_ms" in summary
            assert "by_route" in summary

    def test_stats_empty_file(self):
        """Test stats from empty file"""
        with tempfile.TemporaryDirectory() as tmpdir:
            log_path = Path(tmpdir) / "empty.jsonl"
            log_path.touch()

            stats = AuditStats.from_file(log_path)
            assert stats.total_requests == 0
            assert stats.hit_rate == 0.0

    def test_stats_nonexistent_file(self):
        """Test stats from nonexistent file"""
        stats = AuditStats.from_file(Path("/nonexistent/file.jsonl"))
        assert stats.total_requests == 0


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
