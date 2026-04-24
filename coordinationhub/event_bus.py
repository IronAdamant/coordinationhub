"""Lightweight thread-safe in-memory pub-sub event bus for CoordinationHub.

Zero external dependencies. Events are ephemeral (not persisted).
Persistence remains in SQLite; the bus is purely for low-latency
notification between coordination primitives.

T3.25: per-subscriber queues are now bounded so a slow subscriber can't
drive the publisher into OOM. When a queue is full the oldest event is
dropped and a ``bus.overflow`` counter is incremented on the subscriber
so consumers can detect the loss.
"""

from __future__ import annotations

import logging
import queue
import threading
from typing import Any, Callable

_log = logging.getLogger(__name__)

_SubFilter = Callable[[dict[str, Any]], bool] | None

# T3.25: queue cap per subscriber. Tuned for hub-scale workloads — a
# subscriber that can't keep up with 10k queued events is definitely
# broken; dropping oldest is better than OOM'ing the whole process.
_DEFAULT_QUEUE_MAX = 10_000


class _Sub:
    __slots__ = (
        "sub_id", "topics", "filter_fn", "_queue", "dropped", "all_topics",
    )

    def __init__(
        self,
        sub_id: int,
        topics: list[str],
        filter_fn: _SubFilter = None,
        maxsize: int = _DEFAULT_QUEUE_MAX,
        all_topics: bool = False,
    ) -> None:
        self.sub_id = sub_id
        self.topics = topics
        self.filter_fn = filter_fn
        self._queue: queue.Queue[dict[str, Any]] = queue.Queue(maxsize=maxsize)
        self.dropped = 0
        # T3.8: SSE streams want every event regardless of topic. Rather
        # than enumerating 24+ topic strings, subscribe-all carries a
        # flag that short-circuits the topic check in publish().
        self.all_topics = all_topics

    def put(self, event: dict[str, Any]) -> None:
        """Enqueue *event*, dropping oldest if the queue is full.

        T3.25: full queue → pop one (oldest) and put the new one so the
        subscriber always has the freshest events. Increments ``dropped``
        so callers can spot the overflow.
        """
        try:
            self._queue.put_nowait(event)
        except queue.Full:
            try:
                # Discard the oldest event, make room for the new one.
                self._queue.get_nowait()
            except queue.Empty:
                pass
            self.dropped += 1
            try:
                self._queue.put_nowait(event)
            except queue.Full:
                # Extremely unlikely: another thread refilled the queue
                # between our get and put. Drop the event rather than
                # block.
                self.dropped += 1
                _log.warning(
                    "event bus subscriber %s queue full after drain; "
                    "event dropped", self.sub_id,
                )

    def get(self, timeout: float | None = None) -> dict[str, Any]:
        """Return the next event, or raise queue.Empty on timeout."""
        return self._queue.get(timeout=timeout)


class EventBus:
    """Thread-safe pub-sub bus for coordination events."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._subs: dict[int, _Sub] = {}
        self._next_id = 0

    def subscribe(
        self,
        topics: list[str],
        filter_fn: _SubFilter = None,
    ) -> tuple[int, _Sub]:
        """Register a subscription and return (sub_id, sub)."""
        with self._lock:
            self._next_id += 1
            sub_id = self._next_id
            sub = _Sub(sub_id, topics, filter_fn)
            self._subs[sub_id] = sub
        return sub_id, sub

    def subscribe_all(
        self, filter_fn: _SubFilter = None,
    ) -> tuple[int, _Sub]:
        """Register a subscription that matches every published topic.

        T3.8: SSE dashboards want live notifications on any state change.
        Rather than keeping an enumerated topic list in sync with every
        new ``_publish_event`` site, subscribe-all receives the delivered
        event as ``{"topic": <name>, ...payload}`` so the consumer can
        discriminate by topic if it wants.
        """
        with self._lock:
            self._next_id += 1
            sub_id = self._next_id
            sub = _Sub(sub_id, topics=[], filter_fn=filter_fn, all_topics=True)
            self._subs[sub_id] = sub
        return sub_id, sub

    def unsubscribe(self, sub_id: int) -> None:
        """Remove a subscription. Safe to call multiple times."""
        with self._lock:
            self._subs.pop(sub_id, None)

    def publish(self, topic: str, payload: dict[str, Any]) -> None:
        """Deliver an event to all matching subscribers."""
        with self._lock:
            subs = list(self._subs.values())
        for sub in subs:
            matches = sub.all_topics or topic in sub.topics
            if not matches:
                continue
            # T3.8: subscribe_all consumers receive the topic inline so
            # a single receiver can route by event type.
            delivered = (
                {"topic": topic, **payload} if sub.all_topics else payload
            )
            if sub.filter_fn is None or sub.filter_fn(delivered):
                sub.put(delivered)

    def wait_for_event(
        self,
        topics: list[str],
        filter_fn: _SubFilter = None,
        timeout: float | None = None,
    ) -> dict[str, Any] | None:
        """Block until a matching event arrives or timeout expires.

        Returns the event dict, or None on timeout.
        """
        sub_id, sub = self.subscribe(topics, filter_fn)
        try:
            return sub.get(timeout=timeout)
        except queue.Empty:
            return None
        finally:
            self.unsubscribe(sub_id)
