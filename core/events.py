#!/usr/bin/env python3
"""
Simple Event Bus for Drop Snapshot Distribution

Minimal pub-sub implementation for distributing drop snapshots
from MarketDataService to Engine and Dashboard.
"""

import logging
from typing import Callable, Dict, List, Any
from threading import RLock

logger = logging.getLogger(__name__)


class EventBus:
    """
    Simple thread-safe event bus for pub/sub messaging.

    Allows components to subscribe to topics and publish events.
    """

    def __init__(self):
        """Initialize event bus."""
        self.subscribers: Dict[str, List[Callable]] = {}
        self._lock = RLock()
        logger.debug("EventBus initialized")

    def subscribe(self, topic: str, callback: Callable[[Any], None]):
        """
        Subscribe to a topic with a callback.

        Args:
            topic: Topic name (e.g., "drop.snapshots")
            callback: Function to call when events are published (receives payload)
        """
        with self._lock:
            if topic not in self.subscribers:
                self.subscribers[topic] = []

            self.subscribers[topic].append(callback)
            logger.debug(f"Subscribed to topic '{topic}': {callback.__name__}")

    def unsubscribe(self, topic: str, callback: Callable[[Any], None]):
        """
        Unsubscribe a callback from a topic.

        Args:
            topic: Topic name
            callback: Callback to remove
        """
        with self._lock:
            if topic in self.subscribers:
                try:
                    self.subscribers[topic].remove(callback)
                    logger.debug(f"Unsubscribed from topic '{topic}': {callback.__name__}")
                except ValueError:
                    pass  # Callback not found

    def publish(self, topic: str, payload: Any):
        """
        Publish an event to a topic.

        All subscribed callbacks will be called with the payload.
        Errors in callbacks are caught and logged to prevent disruption.

        Args:
            topic: Topic name
            payload: Event data to send to subscribers
        """
        with self._lock:
            callbacks = self.subscribers.get(topic, [])

        # Call callbacks outside lock to prevent deadlocks
        for callback in callbacks:
            try:
                callback(payload)
            except Exception as e:
                logger.error(
                    f"Error in event bus callback for topic '{topic}': {e}",
                    exc_info=True
                )

    def get_subscriber_count(self, topic: str) -> int:
        """Get number of subscribers for a topic."""
        with self._lock:
            return len(self.subscribers.get(topic, []))

    def clear_topic(self, topic: str):
        """Remove all subscribers from a topic."""
        with self._lock:
            if topic in self.subscribers:
                del self.subscribers[topic]
                logger.debug(f"Cleared all subscribers from topic '{topic}'")

    def clear_all(self):
        """Remove all subscribers from all topics."""
        with self._lock:
            self.subscribers.clear()
            logger.debug("Cleared all event bus subscribers")


# Global event bus instance
_global_event_bus: EventBus = None

# Topic counters for debugging (only active when DEBUG_DROPS=True)
topic_counters = {"published": {}, "received": {}}


def get_event_bus() -> EventBus:
    """
    Get the global event bus instance.

    Returns:
        Global EventBus instance (creates if needed)
    """
    global _global_event_bus
    if _global_event_bus is None:
        _global_event_bus = EventBus()

        # Wrap publish with debug counters if DEBUG_DROPS is enabled
        try:
            import config
            if getattr(config, "DEBUG_DROPS", False):
                orig_publish = _global_event_bus.publish

                def dbg_publish(topic, payload):
                    topic_counters["published"][topic] = topic_counters["published"].get(topic, 0) + 1
                    return orig_publish(topic, payload)

                _global_event_bus.publish = dbg_publish
                logger.debug("EventBus debug counters enabled (DEBUG_DROPS=True)")
        except Exception as e:
            logger.debug(f"Could not enable EventBus debug counters: {e}")

    return _global_event_bus
