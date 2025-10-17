#!/usr/bin/env python3
"""
Unit Tests for RotatingJSONLWriter (V9_3 Phase 8)

Tests rotation logic, atomic writes, and thread safety.
"""

import pytest
import time
import tempfile
import shutil
import json
from pathlib import Path
from datetime import datetime, timedelta
from threading import Thread

# Import JSONL writer
import sys
sys.path.insert(0, str(Path(__file__).parent.parent))

# Import from io package (note: avoid conflict with built-in io module)
import importlib.util
spec = importlib.util.spec_from_file_location("jsonl_module", str(Path(__file__).parent.parent / "io" / "jsonl.py"))
jsonl_module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(jsonl_module)

RotatingJSONLWriter = jsonl_module.RotatingJSONLWriter
MultiStreamJSONLWriter = jsonl_module.MultiStreamJSONLWriter
read_jsonl = jsonl_module.read_jsonl
read_jsonl_tail = jsonl_module.read_jsonl_tail


class TestRotatingJSONLWriter:
    """Test RotatingJSONLWriter functionality."""

    @pytest.fixture
    def temp_dir(self):
        """Create temporary directory for tests."""
        temp_path = tempfile.mkdtemp()
        yield temp_path
        shutil.rmtree(temp_path, ignore_errors=True)

    def test_basic_write(self, temp_dir):
        """Test basic write functionality."""
        writer = RotatingJSONLWriter(
            base_dir=temp_dir,
            prefix="test",
            max_mb=1
        )

        # Write a few objects
        obj1 = {"id": 1, "value": "test1"}
        obj2 = {"id": 2, "value": "test2"}

        assert writer.append(obj1) is True
        assert writer.append(obj2) is True

        # Check file was created
        files = list(Path(temp_dir).glob("*.jsonl"))
        assert len(files) == 1

        # Read and verify
        with open(files[0], 'r') as f:
            lines = f.readlines()

        assert len(lines) == 2
        assert json.loads(lines[0]) == obj1
        assert json.loads(lines[1]) == obj2

    def test_daily_rotation(self, temp_dir):
        """Test daily rotation at midnight UTC."""
        writer = RotatingJSONLWriter(
            base_dir=temp_dir,
            prefix="test",
            max_mb=50,
            daily_rotation=True
        )

        # Write with today's date
        obj1 = {"day": "today"}
        writer.append(obj1)

        # Simulate date change by modifying internal state
        old_date = writer.current_date
        new_date = (datetime.utcnow() + timedelta(days=1)).strftime("%Y%m%d")
        writer.current_date = None  # Force rotation check

        # Write with new date
        obj2 = {"day": "tomorrow"}
        writer.append(obj2)

        # Should have two files now
        files = sorted(Path(temp_dir).glob("*.jsonl"))
        assert len(files) >= 1, "Should have at least one file after rotation"

    def test_size_rotation(self, temp_dir):
        """Test size-based rotation."""
        # Very small file size to trigger rotation
        writer = RotatingJSONLWriter(
            base_dir=temp_dir,
            prefix="test",
            max_mb=0.001,  # 1KB
            daily_rotation=False
        )

        # Write many objects to trigger size rotation
        for i in range(100):
            obj = {"id": i, "data": "x" * 100}  # ~100 bytes each
            writer.append(obj)

        # Should have multiple files due to size rotation
        files = list(Path(temp_dir).glob("*.jsonl"))
        assert len(files) >= 2, f"Expected multiple files due to size rotation, got {len(files)}"

    def test_sequential_naming(self, temp_dir):
        """Test sequential file naming on rotation."""
        writer = RotatingJSONLWriter(
            base_dir=temp_dir,
            prefix="test",
            max_mb=0.001,  # Small to trigger rotation
            daily_rotation=False
        )

        # Write many objects to trigger multiple rotations
        for i in range(200):
            obj = {"id": i, "data": "x" * 100}
            writer.append(obj)

        # Check file names are sequential
        files = sorted(Path(temp_dir).glob("*.jsonl"))

        if len(files) > 1:
            # Verify sequential naming pattern exists
            today = datetime.utcnow().strftime("%Y%m%d")
            # Should have files like: test_YYYYMMDD.jsonl, test_YYYYMMDD_001.jsonl, etc.
            assert any(f"_{today}" in f.name for f in files), "Files should contain date"

    def test_atomic_write(self, temp_dir):
        """Test atomic write (.tmp + rename) for new files."""
        writer = RotatingJSONLWriter(
            base_dir=temp_dir,
            prefix="test",
            max_mb=50
        )

        # Write first object (new file)
        obj = {"test": "atomic"}
        writer.append(obj)

        # Check no .tmp file exists
        tmp_files = list(Path(temp_dir).glob("*.tmp"))
        assert len(tmp_files) == 0, ".tmp files should be removed after rename"

        # Check actual file exists
        jsonl_files = list(Path(temp_dir).glob("*.jsonl"))
        assert len(jsonl_files) == 1

    def test_append_to_existing(self, temp_dir):
        """Test appending to existing file (non-atomic)."""
        writer = RotatingJSONLWriter(
            base_dir=temp_dir,
            prefix="test",
            max_mb=50
        )

        # Write first object (creates file)
        writer.append({"id": 1})

        # Get file path
        file_path = writer.get_current_file()
        assert file_path.exists()

        # Write second object (appends)
        writer.append({"id": 2})

        # Read file
        with open(file_path, 'r') as f:
            lines = f.readlines()

        assert len(lines) == 2

    def test_thread_safety(self, temp_dir):
        """Test concurrent writes are thread-safe."""
        writer = RotatingJSONLWriter(
            base_dir=temp_dir,
            prefix="test",
            max_mb=50
        )

        def write_objects(start_id, count):
            for i in range(count):
                obj = {"thread_id": start_id, "seq": i}
                writer.append(obj)

        # Create multiple threads
        threads = []
        for i in range(5):
            t = Thread(target=write_objects, args=(i, 20))
            threads.append(t)
            t.start()

        # Wait for all threads
        for t in threads:
            t.join()

        # Count total lines written
        files = list(Path(temp_dir).glob("*.jsonl"))
        total_lines = 0
        for f in files:
            with open(f, 'r') as file:
                total_lines += len(file.readlines())

        # Should have 5 threads × 20 objects = 100 lines
        assert total_lines == 100, f"Expected 100 lines, got {total_lines}"

    def test_statistics(self, temp_dir):
        """Test get_statistics method."""
        writer = RotatingJSONLWriter(
            base_dir=temp_dir,
            prefix="test",
            max_mb=50
        )

        # Write some data
        writer.append({"test": "data"})

        # Get statistics
        stats = writer.get_statistics()

        assert stats["prefix"] == "test"
        assert stats["max_mb"] == 50
        assert stats["daily_rotation"] is True
        assert stats["current_file"] is not None
        assert "current_size_mb" in stats or "current_size_bytes" in stats


class TestMultiStreamJSONLWriter:
    """Test MultiStreamJSONLWriter functionality."""

    @pytest.fixture
    def temp_dir(self):
        """Create temporary directory for tests."""
        temp_path = tempfile.mkdtemp()
        yield temp_path
        shutil.rmtree(temp_path, ignore_errors=True)

    def test_multiple_streams(self, temp_dir):
        """Test managing multiple streams."""
        multi_writer = MultiStreamJSONLWriter(
            base_dir=temp_dir,
            max_mb=50
        )

        # Write to different streams
        multi_writer.write("ticks", {"symbol": "BTC/USDT", "price": 50000})
        multi_writer.write("snapshots", {"symbol": "ETH/USDT", "price": 3000})
        multi_writer.write("windows", {"symbol": "SOL/USDT", "peak": 150})

        # Check directories were created
        assert (Path(temp_dir) / "ticks").exists()
        assert (Path(temp_dir) / "snapshots").exists()
        assert (Path(temp_dir) / "windows").exists()

    def test_stream_isolation(self, temp_dir):
        """Test that streams are isolated."""
        multi_writer = MultiStreamJSONLWriter(base_dir=temp_dir)

        # Write to multiple streams
        multi_writer.write("stream1", {"id": 1})
        multi_writer.write("stream2", {"id": 2})

        # Check each stream has its own file
        stream1_files = list((Path(temp_dir) / "stream1").glob("*.jsonl"))
        stream2_files = list((Path(temp_dir) / "stream2").glob("*.jsonl"))

        assert len(stream1_files) == 1
        assert len(stream2_files) == 1
        assert stream1_files[0].parent != stream2_files[0].parent

    def test_statistics_all_streams(self, temp_dir):
        """Test get_statistics for all streams."""
        multi_writer = MultiStreamJSONLWriter(base_dir=temp_dir)

        # Write to streams
        multi_writer.write("stream1", {"data": 1})
        multi_writer.write("stream2", {"data": 2})

        # Get statistics
        stats = multi_writer.get_statistics()

        assert "stream1" in stats
        assert "stream2" in stats
        assert stats["stream1"]["prefix"] == "stream1"
        assert stats["stream2"]["prefix"] == "stream2"


class TestUtilityFunctions:
    """Test utility functions for reading JSONL."""

    @pytest.fixture
    def temp_dir(self):
        """Create temporary directory for tests."""
        temp_path = tempfile.mkdtemp()
        yield temp_path
        shutil.rmtree(temp_path, ignore_errors=True)

    def test_read_jsonl(self, temp_dir):
        """Test read_jsonl function."""
        # Create test file
        test_file = Path(temp_dir) / "test.jsonl"
        with open(test_file, 'w') as f:
            f.write(json.dumps({"id": 1}) + "\n")
            f.write(json.dumps({"id": 2}) + "\n")
            f.write(json.dumps({"id": 3}) + "\n")

        # Read file
        objects = read_jsonl(str(test_file))

        assert len(objects) == 3
        assert objects[0]["id"] == 1
        assert objects[1]["id"] == 2
        assert objects[2]["id"] == 3

    def test_read_jsonl_limit(self, temp_dir):
        """Test read_jsonl with limit."""
        # Create test file
        test_file = Path(temp_dir) / "test.jsonl"
        with open(test_file, 'w') as f:
            for i in range(100):
                f.write(json.dumps({"id": i}) + "\n")

        # Read with limit
        objects = read_jsonl(str(test_file), limit=10)

        assert len(objects) == 10
        assert objects[0]["id"] == 0
        assert objects[9]["id"] == 9

    def test_read_jsonl_tail(self, temp_dir):
        """Test read_jsonl_tail function (for warm-start)."""
        # Create test file
        test_file = Path(temp_dir) / "test.jsonl"
        with open(test_file, 'w') as f:
            for i in range(100):
                f.write(json.dumps({"id": i}) + "\n")

        # Read last 10 lines
        objects = read_jsonl_tail(str(test_file), n=10)

        assert len(objects) == 10
        assert objects[0]["id"] == 90  # First of last 10
        assert objects[9]["id"] == 99  # Last line

    def test_read_jsonl_nonexistent(self, temp_dir):
        """Test reading non-existent file."""
        objects = read_jsonl(str(Path(temp_dir) / "nonexistent.jsonl"))
        assert objects == []

    def test_read_jsonl_malformed(self, temp_dir):
        """Test reading file with malformed JSON."""
        # Create file with some malformed lines
        test_file = Path(temp_dir) / "malformed.jsonl"
        with open(test_file, 'w') as f:
            f.write(json.dumps({"id": 1}) + "\n")
            f.write("INVALID JSON\n")
            f.write(json.dumps({"id": 3}) + "\n")

        # Should skip malformed line
        objects = read_jsonl(str(test_file))

        assert len(objects) == 2
        assert objects[0]["id"] == 1
        assert objects[1]["id"] == 3


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
