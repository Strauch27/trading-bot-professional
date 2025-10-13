#!/usr/bin/env python3
"""
Ringbuffer for Efficient Orderbook Storage

Implements a circular buffer (ring buffer) for storing orderbook snapshots
from WebSocket streams with minimal memory allocations and no fragmentation.

Features:
- Fixed-size FIFO buffer (overwrites oldest when full)
- Thread-safe operations
- Zero-copy reads where possible
- Efficient iteration and slicing
- Memory-efficient for high-frequency updates

Use Cases:
- Storing L2 orderbook snapshots from WebSocket streams
- Keeping recent ticker history
- Any fixed-window time-series data

Example:
    # Create ringbuffer for orderbook snapshots
    buffer = RingBuffer(capacity=1000, item_type=dict)

    # Add orderbook snapshots
    for snapshot in ws_stream:
        buffer.append(snapshot)

    # Get latest snapshot
    latest = buffer.get_latest()

    # Get last N snapshots
    recent_100 = buffer.get_last_n(100)

    # Iterate over all items
    for snapshot in buffer:
        process(snapshot)
"""

from __future__ import annotations
import threading
from typing import TypeVar, Generic, Optional, List, Iterator, Any
from dataclasses import dataclass
import logging

logger = logging.getLogger(__name__)

T = TypeVar('T')


@dataclass
class RingBufferStats:
    """Statistics for ring buffer."""
    capacity: int = 0           # Maximum capacity
    size: int = 0               # Current size
    total_writes: int = 0       # Total items written
    overwrites: int = 0         # Number of overwrites
    is_full: bool = False       # Whether buffer is full

    @property
    def fill_rate(self) -> float:
        """Percentage of capacity filled."""
        if self.capacity > 0:
            return self.size / self.capacity
        return 0.0

    @property
    def overwrite_rate(self) -> float:
        """Percentage of writes that were overwrites."""
        if self.total_writes > 0:
            return self.overwrites / self.total_writes
        return 0.0


class RingBuffer(Generic[T]):
    """
    Thread-safe circular buffer with fixed capacity.

    When the buffer is full, new items overwrite the oldest items (FIFO).
    This prevents memory growth and fragmentation for streaming data.

    Attributes:
        capacity: Maximum number of items
        size: Current number of items stored
        _data: Internal storage (pre-allocated list)
        _head: Index where next item will be written
        _tail: Index of oldest item (if full)
    """

    def __init__(self, capacity: int, item_type: type = object):
        """
        Initialize ring buffer.

        Args:
            capacity: Maximum number of items to store
            item_type: Type hint for stored items (for type checking)

        Raises:
            ValueError: If capacity <= 0
        """
        if capacity <= 0:
            raise ValueError(f"Capacity must be positive, got {capacity}")

        self.capacity = capacity
        self.item_type = item_type
        self._data: List[Optional[T]] = [None] * capacity
        self._head = 0  # Where next write goes
        self._size = 0  # Current number of items
        self._lock = threading.RLock()
        self._stats = RingBufferStats(capacity=capacity)

    def append(self, item: T) -> None:
        """
        Add item to buffer.

        If buffer is full, overwrites oldest item.

        Args:
            item: Item to add

        Thread Safety:
            Safe to call from multiple threads
        """
        with self._lock:
            # Store item at head position
            self._data[self._head] = item

            # Update statistics
            self._stats.total_writes += 1
            if self._size == self.capacity:
                self._stats.overwrites += 1
                self._stats.is_full = True

            # Advance head
            self._head = (self._head + 1) % self.capacity

            # Update size (max is capacity)
            if self._size < self.capacity:
                self._size += 1
            else:
                self._stats.size = self._size

    def get_latest(self) -> Optional[T]:
        """
        Get most recently added item.

        Returns:
            Latest item or None if buffer is empty

        Thread Safety:
            Safe to call from multiple threads
        """
        with self._lock:
            if self._size == 0:
                return None

            # Latest item is before head (with wraparound)
            latest_idx = (self._head - 1) % self.capacity
            return self._data[latest_idx]

    def get_last_n(self, n: int) -> List[T]:
        """
        Get last N items in chronological order (oldest to newest).

        Args:
            n: Number of items to retrieve

        Returns:
            List of last N items (or all items if N > size)

        Thread Safety:
            Safe to call from multiple threads
        """
        with self._lock:
            if self._size == 0:
                return []

            # Clamp N to actual size
            n = min(n, self._size)

            result = []
            # Start from (head - n) and go to head
            start_idx = (self._head - n) % self.capacity

            for i in range(n):
                idx = (start_idx + i) % self.capacity
                item = self._data[idx]
                if item is not None:
                    result.append(item)

            return result

    def get_all(self) -> List[T]:
        """
        Get all items in chronological order (oldest to newest).

        Returns:
            List of all items

        Thread Safety:
            Safe to call from multiple threads
        """
        return self.get_last_n(self._size)

    def __iter__(self) -> Iterator[T]:
        """
        Iterate over items in chronological order.

        Usage:
            for item in buffer:
                process(item)

        Returns:
            Iterator over items

        Thread Safety:
            Creates snapshot, safe to iterate while buffer is modified
        """
        # Get snapshot to avoid holding lock during iteration
        snapshot = self.get_all()
        return iter(snapshot)

    def __len__(self) -> int:
        """
        Get current number of items in buffer.

        Returns:
            Number of items
        """
        return self._size

    def __getitem__(self, index: int) -> T:
        """
        Get item at index (0 = oldest, -1 = newest).

        Args:
            index: Index (supports negative indexing)

        Returns:
            Item at index

        Raises:
            IndexError: If index out of range
        """
        with self._lock:
            if self._size == 0:
                raise IndexError("Buffer is empty")

            # Handle negative indexing
            if index < 0:
                index = self._size + index

            if index < 0 or index >= self._size:
                raise IndexError(f"Index {index} out of range [0, {self._size})")

            # Calculate actual index in circular buffer
            if self._size == self.capacity:
                # Buffer is full, oldest item is at head
                actual_idx = (self._head + index) % self.capacity
            else:
                # Buffer not full, oldest item is at 0
                actual_idx = index

            item = self._data[actual_idx]
            if item is None:
                raise RuntimeError("Internal error: None item at valid index")

            return item

    def clear(self) -> None:
        """
        Clear all items from buffer.

        Thread Safety:
            Safe to call from multiple threads
        """
        with self._lock:
            self._data = [None] * self.capacity
            self._head = 0
            self._size = 0
            # Reset write stats but keep capacity
            capacity = self._stats.capacity
            self._stats = RingBufferStats(capacity=capacity)

    def is_empty(self) -> bool:
        """Check if buffer is empty."""
        return self._size == 0

    def is_full(self) -> bool:
        """Check if buffer is full."""
        return self._size == self.capacity

    def get_statistics(self) -> dict:
        """
        Get buffer statistics.

        Returns:
            Dict with statistics:
                - capacity: Maximum capacity
                - size: Current size
                - total_writes: Total items written
                - overwrites: Number of overwrites
                - fill_rate: Percentage full
                - overwrite_rate: Percentage of writes that were overwrites
                - is_full: Whether buffer is full
        """
        with self._lock:
            self._stats.size = self._size
            self._stats.is_full = self.is_full()

            return {
                "capacity": self._stats.capacity,
                "size": self._stats.size,
                "total_writes": self._stats.total_writes,
                "overwrites": self._stats.overwrites,
                "fill_rate": self._stats.fill_rate,
                "overwrite_rate": self._stats.overwrite_rate,
                "is_full": self._stats.is_full,
            }


class OrderbookRingBuffer:
    """
    Specialized ring buffer for orderbook snapshots.

    Stores L2 orderbook snapshots with timestamps for efficient access
    and retrieval of recent market data.

    Each snapshot contains:
        - timestamp: Unix timestamp (ms)
        - bids: List of [price, quantity] tuples
        - asks: List of [price, quantity] tuples
        - symbol: Trading pair

    Example:
        buffer = OrderbookRingBuffer(symbol="BTC/USDT", capacity=1000)

        # Add snapshot from WebSocket
        buffer.add_snapshot(
            timestamp=1234567890000,
            bids=[[50000.0, 1.5], [49999.0, 2.0]],
            asks=[[50001.0, 1.0], [50002.0, 1.5]]
        )

        # Get latest orderbook
        latest = buffer.get_latest_snapshot()
        print(f"Best bid: {latest['bids'][0]}")
        print(f"Best ask: {latest['asks'][0]}")
    """

    def __init__(self, symbol: str, capacity: int = 1000):
        """
        Initialize orderbook ring buffer.

        Args:
            symbol: Trading pair symbol
            capacity: Maximum number of snapshots to store
        """
        self.symbol = symbol
        self._buffer = RingBuffer[dict](capacity=capacity, item_type=dict)

    def add_snapshot(
        self,
        timestamp: int,
        bids: List[List[float]],
        asks: List[List[float]],
        meta: Optional[dict] = None
    ) -> None:
        """
        Add orderbook snapshot.

        Args:
            timestamp: Unix timestamp in milliseconds
            bids: List of [price, quantity] bid levels
            asks: List of [price, quantity] ask levels
            meta: Optional metadata (e.g., sequence number, latency)
        """
        snapshot = {
            "symbol": self.symbol,
            "timestamp": timestamp,
            "bids": bids,
            "asks": asks,
            "meta": meta or {}
        }
        self._buffer.append(snapshot)

    def get_latest_snapshot(self) -> Optional[dict]:
        """
        Get most recent orderbook snapshot.

        Returns:
            Latest snapshot or None if buffer is empty
        """
        return self._buffer.get_latest()

    def get_best_bid_ask(self) -> Optional[tuple[float, float]]:
        """
        Get current best bid and ask prices.

        Returns:
            Tuple of (best_bid, best_ask) or None if buffer is empty
        """
        latest = self.get_latest_snapshot()
        if not latest:
            return None

        bids = latest.get("bids", [])
        asks = latest.get("asks", [])

        if not bids or not asks:
            return None

        best_bid = bids[0][0] if bids else 0.0
        best_ask = asks[0][0] if asks else 0.0

        return best_bid, best_ask

    def get_recent_snapshots(self, n: int = 10) -> List[dict]:
        """
        Get last N orderbook snapshots.

        Args:
            n: Number of snapshots to retrieve

        Returns:
            List of recent snapshots (chronological order)
        """
        return self._buffer.get_last_n(n)

    def get_statistics(self) -> dict:
        """Get buffer statistics."""
        return self._buffer.get_statistics()

    def clear(self) -> None:
        """Clear all snapshots."""
        self._buffer.clear()


# ============================================================================
# Convenience Functions
# ============================================================================

def create_orderbook_buffer_pool(symbols: List[str], capacity: int = 1000) -> dict[str, OrderbookRingBuffer]:
    """
    Create a pool of orderbook ring buffers for multiple symbols.

    Args:
        symbols: List of trading pair symbols
        capacity: Capacity for each buffer

    Returns:
        Dict mapping symbol -> OrderbookRingBuffer

    Example:
        pool = create_orderbook_buffer_pool(["BTC/USDT", "ETH/USDT"], capacity=1000)
        pool["BTC/USDT"].add_snapshot(...)
    """
    return {symbol: OrderbookRingBuffer(symbol=symbol, capacity=capacity) for symbol in symbols}
