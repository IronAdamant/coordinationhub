"""CoordinationHub load test — not collected by default pytest.

Run manually:
    python tests/load_test.py

Simulates 100 agents contending for 50 files. Measures lock acquisition
latency with the in-memory lock cache and event bus enabled.
"""

from __future__ import annotations

import statistics
import threading
import time
from pathlib import Path

import sys
sys.path.insert(0, str(Path(__file__).parent.parent))

from coordinationhub.core import CoordinationEngine


NUM_AGENTS = 100
NUM_FILES = 50
DURATION_S = 60.0
WARMUP_S = 5.0


def main() -> None:
    engine = CoordinationEngine()
    engine.start()

    # Register agents
    agents: list[str] = []
    parent = engine.generate_agent_id()
    engine.register_agent(parent)
    for _ in range(NUM_AGENTS):
        aid = engine.generate_agent_id(parent)
        engine.register_agent(aid, parent_id=parent)
        agents.append(aid)

    files = [f"/src/module_{i}.py" for i in range(NUM_FILES)]
    latencies: list[float] = []
    errors = 0
    stop_event = threading.Event()
    lock = threading.Lock()

    def worker(agent_id: str) -> None:
        nonlocal errors
        i = 0
        while not stop_event.is_set():
            path = files[i % len(files)]
            start = time.perf_counter()
            result = engine.acquire_lock(path, agent_id, ttl=10.0)
            elapsed = time.perf_counter() - start
            if result.get("acquired"):
                with lock:
                    latencies.append(elapsed)
                engine.release_lock(path, agent_id)
            else:
                with lock:
                    errors += 1
            i += 1

    threads = [threading.Thread(target=worker, args=(aid,), daemon=True) for aid in agents]

    # Warmup
    for t in threads:
        t.start()
    time.sleep(WARMUP_S)

    # Measure
    with lock:
        latencies.clear()
        errors = 0
    start_measure = time.perf_counter()
    time.sleep(DURATION_S)
    stop_event.set()
    for t in threads:
        t.join(timeout=5.0)
    actual_duration = time.perf_counter() - start_measure

    with lock:
        ops = len(latencies)
        if latencies:
            p50 = statistics.median(latencies)
            p99 = sorted(latencies)[int(len(latencies) * 0.99)]
            mean = statistics.mean(latencies)
        else:
            p50 = p99 = mean = 0.0
        err_count = errors

    print(f"Agents: {NUM_AGENTS}")
    print(f"Files:  {NUM_FILES}")
    print(f"Duration: {actual_duration:.1f}s")
    print(f"Total ops: {ops}")
    print(f"Errors: {err_count}")
    print(f"Throughput: {ops / actual_duration:.1f} ops/s")
    print(f"Latency p50: {p50 * 1000:.3f}ms")
    print(f"Latency p99: {p99 * 1000:.3f}ms")
    print(f"Latency mean: {mean * 1000:.3f}ms")

    engine.close()


if __name__ == "__main__":
    main()
