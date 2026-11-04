# Benchmarks

Measured by `python3 parity/bench.py target/release/coordinationhub` on this Linux VM (4 CPUs, Intel Xeon). Python is 0.8.0 from `python-legacy`. Rust is the release binary. Each in-process row is the median of 3 runs. Startup is the median of 7 process launches.

`COORDINATIONHUB_BENCH_LOOPS` was 200. `many_agent_scale` always registers 1000 agents and then reads status. `contended_locks` is 4 workers, 40 acquires each, on one path (160 acquires). Startup launches a status query in a fresh directory.

Rust was faster on every row. The gate command exits 1 if that stops being true.

| Operation | Python seconds | Rust seconds |
| --- | ---: | ---: |
| startup | 0.05628775900004257 | 0.0013276580000365357 |
| register (200) | 0.14173429400011628 | 0.018491435 |
| lock acquire and release (200) | 0.07890297299991289 | 0.023828376 |
| contended locks (160) | 0.025611879000052795 | 0.003831096 |
| notification fan-out (200) | 0.031210841999836703 | 0.021618638 |
| status / dashboard query (200) | 0.0035686819999227737 | 0.000167425 |
| many-agent scale (1000 registers + status) | 2.426644532999944 | 0.844073829 |

The status row is the same call the dashboard data is built from: an in-memory read of agents, locks, and notifications. It is not a browser paint time.

Do not copy these numbers onto another machine and call them current. Re-run the script.
