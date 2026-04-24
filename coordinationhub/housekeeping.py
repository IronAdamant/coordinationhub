"""HousekeepingScheduler — background periodic pruners for long-running hubs.

Runs a set of named tasks on independent intervals inside a single daemon
thread. Each task is a zero-arg callable; failures are logged and swallowed
so one bad pruner cannot kill the thread. Shutdown is cooperative via a
``threading.Event``.

Resolves audit items:

* T4.7 — ``coordination_events`` unbounded: the bus journal grew without an
  automatic pruner. ``_publish_event`` writes every event; over weeks this
  table dominates DB size.
* T7.32 — ``assessment_results.details_json`` retention: each metric stores
  the full trace JSON, so an hourly assessment run balloons storage.
* T1.17 tail — stale agent DB rows: ``reap_stale_agents`` transitions stale
  rows to ``status='stopped'`` but never deletes them. The longer the hub
  runs, the more tombstones accumulate.

Opt-in via ``CoordinationEngine(housekeeping=True)`` or the
``COORDINATIONHUB_HOUSEKEEPING=1`` env var. Disabled by default so single-
shot CLI invocations don't spin up an orphan thread.
"""

from __future__ import annotations

import logging
import os
import threading
import time
from typing import Any, Callable

_log = logging.getLogger(__name__)


# Default cadence and retention values. All overridable via env vars so
# operators can tune per-deployment without code changes.
_DEFAULT_EVENTS_INTERVAL_S = float(
    os.environ.get("COORDINATIONHUB_HOUSEKEEPING_EVENTS_INTERVAL_S", 600.0),
)
_DEFAULT_EVENTS_MAX_AGE_S = float(
    os.environ.get("COORDINATIONHUB_EVENTS_MAX_AGE_S", 7 * 24 * 3600.0),
)
_DEFAULT_AGENTS_INTERVAL_S = float(
    os.environ.get("COORDINATIONHUB_HOUSEKEEPING_AGENTS_INTERVAL_S", 3600.0),
)
_DEFAULT_AGENTS_STALE_TIMEOUT_S = float(
    os.environ.get("COORDINATIONHUB_AGENTS_STALE_TIMEOUT_S", 600.0),
)
_DEFAULT_AGENTS_RETENTION_S = float(
    os.environ.get("COORDINATIONHUB_AGENTS_RETENTION_S", 7 * 24 * 3600.0),
)
_DEFAULT_ASSESSMENT_INTERVAL_S = float(
    os.environ.get("COORDINATIONHUB_HOUSEKEEPING_ASSESSMENT_INTERVAL_S", 3600.0),
)
_DEFAULT_ASSESSMENT_MAX_AGE_S = float(
    os.environ.get("COORDINATIONHUB_ASSESSMENT_MAX_AGE_S", 30 * 24 * 3600.0),
)
_DEFAULT_WORK_INTENT_INTERVAL_S = float(
    os.environ.get("COORDINATIONHUB_HOUSEKEEPING_WORK_INTENT_INTERVAL_S", 300.0),
)

# Minimum tick the scheduler wakes on. Bounded below by 1 second so tests
# can drive it without the thread burning CPU.
_TICK_FLOOR_S = 1.0


class _Task:
    __slots__ = ("name", "interval_s", "fn", "next_run_at", "last_result", "last_error")

    def __init__(self, name: str, interval_s: float, fn: Callable[[], Any]) -> None:
        self.name = name
        self.interval_s = max(interval_s, _TICK_FLOOR_S)
        self.fn = fn
        # Fire immediately on first tick so startup housekeeping doesn't
        # wait a full interval to catch up on accumulated backlog.
        self.next_run_at = 0.0
        self.last_result: Any = None
        self.last_error: Exception | None = None


class HousekeepingScheduler:
    """Daemon thread that runs registered tasks on independent intervals.

    Lifecycle:
        start()   — spawn the worker; idempotent.
        stop(timeout=5.0) — signal and join; idempotent.
        run_once() — fire every registered task synchronously. Used by tests
                     and by operators invoking housekeeping on demand.

    Tasks are added with ``add_task(name, interval_s, fn)``. Each ``fn`` is
    called with no arguments; it may return any value (stored on the task
    for observability). Exceptions are logged and suppressed.
    """

    def __init__(self, name: str = "coordhub-housekeeping") -> None:
        self._name = name
        self._tasks: list[_Task] = []
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None
        self._lock = threading.Lock()

    def add_task(
        self, name: str, interval_s: float, fn: Callable[[], Any],
    ) -> None:
        """Register ``fn`` to run every ``interval_s`` seconds.

        Must be called before ``start()``; adding tasks to a running
        scheduler is not supported (keeps the worker loop simple).
        """
        with self._lock:
            if self._thread is not None and self._thread.is_alive():
                raise RuntimeError(
                    "cannot add housekeeping tasks after scheduler has started",
                )
            self._tasks.append(_Task(name, interval_s, fn))

    def start(self) -> None:
        with self._lock:
            if self._thread is not None and self._thread.is_alive():
                return
            self._stop_event.clear()
            self._thread = threading.Thread(
                target=self._run, name=self._name, daemon=True,
            )
            self._thread.start()

    def stop(self, timeout: float = 5.0) -> None:
        """Signal the worker to exit and join. Idempotent."""
        with self._lock:
            thread = self._thread
        self._stop_event.set()
        if thread is not None and thread.is_alive():
            thread.join(timeout=timeout)
            if thread.is_alive():
                _log.warning(
                    "housekeeping scheduler did not stop within %.1fs", timeout,
                )
        with self._lock:
            self._thread = None

    def run_once(self) -> dict[str, Any]:
        """Fire every registered task once, synchronously.

        Returns a dict keyed by task name with each task's return value (or
        the exception that was raised). Safe to call while the background
        thread is running, though it duplicates work; tests typically call
        this without starting the thread.
        """
        results: dict[str, Any] = {}
        for task in self._tasks:
            try:
                results[task.name] = {"ok": True, "result": task.fn()}
            except Exception as exc:
                _log.exception(
                    "housekeeping task %r failed during run_once", task.name,
                )
                results[task.name] = {"ok": False, "error": str(exc)}
        return results

    def _run(self) -> None:
        """Worker loop: sleep until the next due task, fire it, repeat."""
        while not self._stop_event.is_set():
            now = time.time()
            next_due = None
            for task in self._tasks:
                if task.next_run_at <= now:
                    try:
                        task.last_result = task.fn()
                        task.last_error = None
                    except Exception as exc:
                        task.last_error = exc
                        _log.exception(
                            "housekeeping task %r failed; continuing", task.name,
                        )
                    # Schedule the next run relative to the completion time
                    # so a slow run doesn't pile up back-to-back retries.
                    task.next_run_at = time.time() + task.interval_s
                if next_due is None or task.next_run_at < next_due:
                    next_due = task.next_run_at

            if next_due is None:
                # No tasks registered — sleep for the tick floor and let
                # stop() wake us.
                wait_s = _TICK_FLOOR_S
            else:
                wait_s = max(next_due - time.time(), _TICK_FLOOR_S)
            # Event.wait returns True if set, so stop() cuts the sleep
            # short. timeout=None would block forever on an empty task list.
            self._stop_event.wait(timeout=wait_s)


def is_enabled_by_env() -> bool:
    """Return True if ``COORDINATIONHUB_HOUSEKEEPING`` is set truthily."""
    val = os.environ.get("COORDINATIONHUB_HOUSEKEEPING", "")
    return val.strip().lower() in {"1", "true", "yes", "on"}


def build_default_scheduler(engine: Any) -> HousekeepingScheduler:
    """Build a scheduler wired up with the standard prune tasks.

    Each task captures the engine by reference. The scheduler is returned
    in the stopped state; the caller invokes ``start()``.
    """
    sched = HousekeepingScheduler()

    def _prune_events() -> dict[str, Any]:
        return engine.prune_notifications(max_age_seconds=_DEFAULT_EVENTS_MAX_AGE_S)

    def _reap_and_prune_agents() -> dict[str, Any]:
        reaped = engine.reap_stale_agents(timeout=_DEFAULT_AGENTS_STALE_TIMEOUT_S)
        pruned = engine.prune_stopped_agents(
            retention_seconds=_DEFAULT_AGENTS_RETENTION_S,
        )
        return {"reap": reaped, "prune": pruned}

    def _prune_assessments() -> dict[str, Any]:
        return engine.prune_assessment_results(
            max_age_seconds=_DEFAULT_ASSESSMENT_MAX_AGE_S,
        )

    def _prune_work_intents() -> dict[str, Any]:
        return engine.prune_work_intents()

    sched.add_task("coordination_events", _DEFAULT_EVENTS_INTERVAL_S, _prune_events)
    sched.add_task(
        "stale_agents", _DEFAULT_AGENTS_INTERVAL_S, _reap_and_prune_agents,
    )
    sched.add_task(
        "assessment_results",
        _DEFAULT_ASSESSMENT_INTERVAL_S,
        _prune_assessments,
    )
    sched.add_task(
        "work_intents", _DEFAULT_WORK_INTENT_INTERVAL_S, _prune_work_intents,
    )
    return sched
