"""
Memory Management Service
Manages memory usage, deque limits, and automated cleanup across the application
"""

import gc
import logging
import threading
import time
from collections import deque
from dataclasses import dataclass, field
from threading import RLock
from typing import Any, Callable, Dict, List, Optional

import psutil

logger = logging.getLogger(__name__)


@dataclass
class DequeConfig:
    """Configuration for managed deque"""
    max_size: int
    cleanup_threshold: float = 0.8  # Cleanup when 80% full
    cleanup_percentage: float = 0.3  # Remove 30% of oldest items
    max_age_seconds: Optional[float] = None  # Max age for items


@dataclass
class MemoryStats:
    """Memory statistics snapshot"""
    timestamp: float
    total_memory_mb: float
    used_memory_mb: float
    available_memory_mb: float
    memory_percent: float
    process_memory_mb: float
    managed_deques_count: int
    managed_deques_total_items: int
    gc_collections: Dict[int, int] = field(default_factory=dict)


class ManagedDeque:
    """
    Memory-managed deque with automatic cleanup and size limits
    """

    def __init__(self, config: DequeConfig, name: str = "unnamed"):
        self.config = config
        self.name = name
        self._deque = deque(maxlen=config.max_size)
        self._timestamps = deque(maxlen=config.max_size)  # Track item timestamps
        self._lock = RLock()
        self._stats = {
            'items_added': 0,
            'items_removed': 0,
            'cleanup_runs': 0,
            'auto_evictions': 0
        }

    def append(self, item: Any) -> None:
        """Add item to deque with timestamp tracking"""
        with self._lock:
            current_time = time.time()

            # Check if we need cleanup before adding
            if self._should_cleanup():
                self._cleanup()

            # Add item with timestamp
            self._deque.append(item)
            self._timestamps.append(current_time)
            self._stats['items_added'] += 1

    def appendleft(self, item: Any) -> None:
        """Add item to left of deque"""
        with self._lock:
            current_time = time.time()

            if self._should_cleanup():
                self._cleanup()

            self._deque.appendleft(item)
            self._timestamps.appendleft(current_time)
            self._stats['items_added'] += 1

    def pop(self) -> Any:
        """Remove and return rightmost item"""
        with self._lock:
            if not self._deque:
                raise IndexError("pop from empty deque")

            self._timestamps.pop()
            self._stats['items_removed'] += 1
            return self._deque.pop()

    def popleft(self) -> Any:
        """Remove and return leftmost item"""
        with self._lock:
            if not self._deque:
                raise IndexError("pop from empty deque")

            self._timestamps.popleft()
            self._stats['items_removed'] += 1
            return self._deque.popleft()

    def _should_cleanup(self) -> bool:
        """Check if cleanup is needed"""
        if not self.config.max_age_seconds:
            return False

        current_size = len(self._deque)
        threshold_size = int(self.config.max_size * self.config.cleanup_threshold)

        # Cleanup if we're near size limit or have old items
        if current_size >= threshold_size:
            return True

        # Check for old items
        if self._timestamps and self.config.max_age_seconds:
            oldest_time = self._timestamps[0]
            age = time.time() - oldest_time
            return age > self.config.max_age_seconds

        return False

    def _cleanup(self) -> None:
        """Remove old items and free space"""
        if not self._deque:
            return

        items_to_remove = 0
        current_time = time.time()

        # Remove items based on age
        if self.config.max_age_seconds:
            while (self._timestamps and
                   current_time - self._timestamps[0] > self.config.max_age_seconds):
                self._deque.popleft()
                self._timestamps.popleft()
                items_to_remove += 1

        # Remove items based on size percentage
        current_size = len(self._deque)
        if current_size > 0:
            percentage_to_remove = int(current_size * self.config.cleanup_percentage)
            items_to_remove = max(items_to_remove, percentage_to_remove)

            while items_to_remove > 0 and self._deque:
                self._deque.popleft()
                self._timestamps.popleft()
                items_to_remove -= 1

        self._stats['cleanup_runs'] += 1
        logger.debug(f"Cleaned up deque {self.name}: {items_to_remove} items removed")

    def force_cleanup(self, max_items: Optional[int] = None) -> int:
        """Force cleanup, optionally limiting to max_items"""
        with self._lock:
            initial_size = len(self._deque)

            if max_items:
                target_size = max(0, len(self._deque) - max_items)
                while len(self._deque) > target_size:
                    self._deque.popleft()
                    self._timestamps.popleft()
            else:
                self._cleanup()

            removed_count = initial_size - len(self._deque)
            self._stats['items_removed'] += removed_count
            return removed_count

    def get_stats(self) -> Dict[str, Any]:
        """Get deque statistics"""
        with self._lock:
            current_time = time.time()
            ages = []

            if self._timestamps:
                ages = [current_time - ts for ts in self._timestamps]

            return {
                'name': self.name,
                'size': len(self._deque),
                'max_size': self.config.max_size,
                'utilization_percent': (len(self._deque) / self.config.max_size) * 100,
                'oldest_item_age_seconds': max(ages) if ages else 0,
                'newest_item_age_seconds': min(ages) if ages else 0,
                'average_age_seconds': sum(ages) / len(ages) if ages else 0,
                **self._stats
            }

    def __len__(self) -> int:
        return len(self._deque)

    def __iter__(self):
        with self._lock:
            return iter(list(self._deque))

    def __getitem__(self, index):
        with self._lock:
            return self._deque[index]


class MemoryManager:
    """
    Central memory management service
    """

    def __init__(self, memory_limit_percent: float = 80.0,
                 cleanup_interval: float = 60.0):
        self.memory_limit_percent = memory_limit_percent
        self.cleanup_interval = cleanup_interval

        # Managed resources
        self._managed_deques: Dict[str, ManagedDeque] = {}
        self._cleanup_callbacks: List[Callable[[], int]] = []
        self._memory_history: deque = deque(maxlen=100)

        # Threading
        self._lock = RLock()
        self._monitor_thread: Optional[threading.Thread] = None
        self._shutdown_event = threading.Event()

        # Statistics
        self._stats = {
            'memory_cleanups_triggered': 0,
            'total_items_cleaned': 0,
            'gc_collections_forced': 0,
            'deques_created': 0,
            'deques_destroyed': 0
        }

    def start_monitoring(self) -> None:
        """Start memory monitoring"""
        if self._monitor_thread and self._monitor_thread.is_alive():
            return

        def memory_monitor():
            logger.info(f"Memory manager started (limit: {self.memory_limit_percent}%, "
                       f"interval: {self.cleanup_interval}s)")

            while not self._shutdown_event.is_set():
                try:
                    self._shutdown_event.wait(self.cleanup_interval)

                    if self._shutdown_event.is_set():
                        break

                    # Collect memory statistics
                    memory_stats = self._collect_memory_stats()
                    self._memory_history.append(memory_stats)

                    # Check if cleanup is needed
                    if memory_stats.memory_percent > self.memory_limit_percent:
                        logger.warning(f"Memory usage high: {memory_stats.memory_percent:.1f}% "
                                     f"(limit: {self.memory_limit_percent}%)")
                        self._trigger_memory_cleanup()

                    # Regular maintenance cleanup
                    self._perform_maintenance()

                except Exception as e:
                    logger.error(f"Memory monitor error: {e}")

            logger.info("Memory manager stopped")

        self._monitor_thread = threading.Thread(
            target=memory_monitor,
            name="MemoryManager",
            daemon=True
        )
        self._monitor_thread.start()

    def stop_monitoring(self) -> None:
        """Stop memory monitoring"""
        self._shutdown_event.set()
        if self._monitor_thread and self._monitor_thread.is_alive():
            self._monitor_thread.join(timeout=5.0)

    def create_managed_deque(self, name: str, config: DequeConfig) -> ManagedDeque:
        """Create a managed deque"""
        with self._lock:
            if name in self._managed_deques:
                logger.warning(f"Managed deque {name} already exists")
                return self._managed_deques[name]

            managed_deque = ManagedDeque(config, name)
            self._managed_deques[name] = managed_deque
            self._stats['deques_created'] += 1

            logger.debug(f"Created managed deque: {name} (max_size: {config.max_size})")
            return managed_deque

    def remove_managed_deque(self, name: str) -> bool:
        """Remove a managed deque"""
        with self._lock:
            if name in self._managed_deques:
                del self._managed_deques[name]
                self._stats['deques_destroyed'] += 1
                logger.debug(f"Removed managed deque: {name}")
                return True
            return False

    def get_managed_deque(self, name: str) -> Optional[ManagedDeque]:
        """Get a managed deque by name"""
        return self._managed_deques.get(name)

    def add_cleanup_callback(self, callback: Callable[[], int]) -> None:
        """Add a cleanup callback that returns number of items cleaned"""
        self._cleanup_callbacks.append(callback)

    def _collect_memory_stats(self) -> MemoryStats:
        """Collect current memory statistics"""
        # System memory
        memory = psutil.virtual_memory()

        # Process memory
        process = psutil.Process()
        process_memory = process.memory_info().rss / (1024 * 1024)  # MB

        # Managed deques
        total_deque_items = sum(len(deque) for deque in self._managed_deques.values())

        # GC stats
        gc_stats = {}
        for i in range(3):
            gc_stats[i] = gc.get_count()[i]

        return MemoryStats(
            timestamp=time.time(),
            total_memory_mb=memory.total / (1024 * 1024),
            used_memory_mb=memory.used / (1024 * 1024),
            available_memory_mb=memory.available / (1024 * 1024),
            memory_percent=memory.percent,
            process_memory_mb=process_memory,
            managed_deques_count=len(self._managed_deques),
            managed_deques_total_items=total_deque_items,
            gc_collections=gc_stats
        )

    def _trigger_memory_cleanup(self) -> None:
        """Trigger aggressive memory cleanup"""
        logger.info("Triggering memory cleanup")
        self._stats['memory_cleanups_triggered'] += 1

        items_cleaned = 0

        # Cleanup managed deques (remove 50% of items)
        for managed_deque in self._managed_deques.values():
            current_size = len(managed_deque)
            target_cleanup = max(1, current_size // 2)
            cleaned = managed_deque.force_cleanup(target_cleanup)
            items_cleaned += cleaned

        # Run cleanup callbacks
        for callback in self._cleanup_callbacks:
            try:
                cleaned = callback()
                items_cleaned += cleaned
            except Exception as e:
                logger.error(f"Cleanup callback failed: {e}")

        # Force garbage collection
        collected = gc.collect()
        self._stats['gc_collections_forced'] += 1

        self._stats['total_items_cleaned'] += items_cleaned

        logger.info(f"Memory cleanup completed: {items_cleaned} items cleaned, "
                   f"{collected} objects collected by GC")

    def _perform_maintenance(self) -> None:
        """Perform regular maintenance tasks"""
        # Light cleanup of managed deques
        for managed_deque in self._managed_deques.values():
            if managed_deque._should_cleanup():
                managed_deque._cleanup()

        # Regular garbage collection
        if self._stats['memory_cleanups_triggered'] % 10 == 0:  # Every 10th cleanup cycle
            gc.collect(generation=0)  # Only young generation

    def get_memory_status(self) -> Dict[str, Any]:
        """Get current memory status"""
        current_stats = self._collect_memory_stats()

        # Calculate trends
        recent_stats = list(self._memory_history)[-10:]  # Last 10 readings
        memory_trend = 0.0
        if len(recent_stats) >= 2:
            old_memory = recent_stats[0].memory_percent
            new_memory = recent_stats[-1].memory_percent
            memory_trend = new_memory - old_memory

        # Deque statistics
        deque_stats = {}
        total_deque_utilization = 0.0
        for name, managed_deque in self._managed_deques.items():
            stats = managed_deque.get_stats()
            deque_stats[name] = stats
            total_deque_utilization += stats['utilization_percent']

        avg_deque_utilization = (total_deque_utilization / len(self._managed_deques)
                                if self._managed_deques else 0.0)

        return {
            'current_memory': {
                'percent': current_stats.memory_percent,
                'process_mb': current_stats.process_memory_mb,
                'available_mb': current_stats.available_memory_mb
            },
            'memory_trend_percent': memory_trend,
            'memory_limit_percent': self.memory_limit_percent,
            'memory_status': self._get_memory_status_text(current_stats.memory_percent),
            'managed_deques': {
                'count': len(self._managed_deques),
                'total_items': current_stats.managed_deques_total_items,
                'average_utilization_percent': avg_deque_utilization,
                'details': deque_stats
            },
            'statistics': self._stats.copy()
        }

    def _get_memory_status_text(self, memory_percent: float) -> str:
        """Get text description of memory status"""
        if memory_percent < 50:
            return "excellent"
        elif memory_percent < 70:
            return "good"
        elif memory_percent < self.memory_limit_percent:
            return "moderate"
        elif memory_percent < 90:
            return "high"
        else:
            return "critical"

    def force_cleanup(self) -> Dict[str, int]:
        """Force immediate cleanup of all managed resources"""
        logger.info("Forcing immediate memory cleanup")

        results = {}

        # Cleanup managed deques
        for name, managed_deque in self._managed_deques.items():
            cleaned = managed_deque.force_cleanup()
            results[f"deque_{name}"] = cleaned

        # Run cleanup callbacks
        for i, callback in enumerate(self._cleanup_callbacks):
            try:
                cleaned = callback()
                results[f"callback_{i}"] = cleaned
            except Exception as e:
                logger.error(f"Cleanup callback {i} failed: {e}")
                results[f"callback_{i}"] = 0

        # Force full garbage collection
        collected = gc.collect()
        results['gc_collected'] = collected

        total_cleaned = sum(results.values())
        logger.info(f"Forced cleanup completed: {total_cleaned} total items/objects cleaned")

        return results

    def get_deque_stats(self) -> Dict[str, Dict[str, Any]]:
        """Get statistics for all managed deques"""
        return {name: deque.get_stats() for name, deque in self._managed_deques.items()}


# Global memory manager instance
_global_memory_manager: Optional[MemoryManager] = None


def get_memory_manager() -> MemoryManager:
    """Get or create global memory manager"""
    global _global_memory_manager
    if _global_memory_manager is None:
        _global_memory_manager = MemoryManager(
            memory_limit_percent=80.0,
            cleanup_interval=60.0
        )
        _global_memory_manager.start_monitoring()
    return _global_memory_manager


def shutdown_memory_manager():
    """Shutdown global memory manager"""
    global _global_memory_manager
    if _global_memory_manager:
        _global_memory_manager.stop_monitoring()
        _global_memory_manager = None


def create_managed_deque(name: str, max_size: int,
                        max_age_seconds: Optional[float] = None) -> ManagedDeque:
    """Convenience function to create a managed deque"""
    config = DequeConfig(
        max_size=max_size,
        max_age_seconds=max_age_seconds
    )
    return get_memory_manager().create_managed_deque(name, config)
