#!/usr/bin/env python3
"""
Event Bus - Pub/Sub System for Market Data Pipeline

Decouples producers (MarketDataService) from consumers (Engine, Guards, Signals).
Enables deterministic testing and linear telemetry.
"""

from collections import defaultdict
from typing import Callable, Dict, List, Any


class EventBus:
    """
    Simple pub/sub event bus with topic-based routing.

    Producers publish events to topics.
    Consumers subscribe to topics with callbacks.
    Exceptions in callbacks are silently caught to prevent cascade failures.
    """

    def __init__(self) -> None:
        """Initialize event bus with empty subscriber registry."""
        self._subs: Dict[str, List[Callable[[Any], None]]] = defaultdict(list)

    def subscribe(self, topic: str, fn: Callable[[Any], None]) -> None:
        """
        Subscribe a callback to a topic.

        Args:
            topic: Topic name (e.g., "market.snapshots")
            fn: Callback function that receives payload
        """
        self._subs[topic].append(fn)

    def publish(self, topic: str, payload: Any) -> None:
        """
        Publish an event to all subscribers of a topic.

        Args:
            topic: Topic name
            payload: Event data (typically dict or list)
        """
        for fn in list(self._subs.get(topic, [])):
            try:
                fn(payload)
            except Exception:
                # Silent fail to prevent cascade failures
                # Production logger can be added here if needed
                pass

    def unsubscribe(self, topic: str, fn: Callable[[Any], None]) -> None:
        """
        Unsubscribe a callback from a topic.

        Args:
            topic: Topic name
            fn: Callback function to remove
        """
        if topic in self._subs:
            try:
                self._subs[topic].remove(fn)
            except ValueError:
                pass

    def clear(self) -> None:
        """Clear all subscriptions (useful for testing)."""
        self._subs.clear()
