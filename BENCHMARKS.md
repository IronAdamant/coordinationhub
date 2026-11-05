# Benchmarks

Remeasured on 5 November 2026 on a Windows 11 desktop (x86-64), after the fifth-review changes. Python is unmodified 0.8.0 from `python-legacy` on CPython 3.12.10, speaking MCP stdio through `mcp` 1.30.0. Rust is the release binary, speaking the same stdio protocol. Each number is the median of 3 runs, in seconds. Another build job was running on the machine at the time, so treat the absolute numbers as noisy; the comparison is what the gate checks.

An earlier table compared Python in-process calls with a Rust `bench` subcommand. That comparison is withdrawn. These rows use the same interface on both sides.

## Gate rows

`cargo run --release --example bench -- target/release/coordinationhub --python python` with `COORDINATIONHUB_BENCH_LOOPS=100`. CI runs the same command on Linux with 200 loops and fails if Rust is not faster on every row. `status_450` registers 450 agents under one parent before the clock starts and times 450 `status` calls.

| Operation | Python seconds | Rust seconds |
| --- | ---: | ---: |
| startup and status | 1.7481 | 0.0102 |
| status x450 after 450 agents | 1.0599 | 0.2301 |
| register x100 | 2.2121 | 0.0945 |
| lock x100 | 1.8191 | 0.1246 |
| get_agent_tree x20 after 100 agents | 3.2875 | 0.2107 |

## Review workload

The workload the fourth and fifth reviews used: one server, 450 agents, then each row in turn.

| Operation | Python seconds | Rust seconds |
| --- | ---: | ---: |
| startup and first call | 2.8033 | 0.0690 |
| register x450 | 13.6019 | 1.1126 |
| lock acquire and release x450 | 13.0083 | 3.8100 |
| contended lock x450 | 8.1265 | 3.0034 |
| notify_change x450 | 5.0373 | 3.4760 |
| get_notifications x150 | 1.4648 | 0.3940 |
| send_message x450 | 4.6299 | 3.3844 |
| create_task x450 | 4.7562 | 3.4792 |
| query_tasks x150 | 4.3110 | 1.1900 |
| status x450 | 2.0919 | 0.6314 |
| get_agent_tree x150 | 9.1026 | 1.6958 |

Rust was faster on every row of both tables. On Linux the fifth review measured the same workload with Rust ahead on every row as well (writes about 1 ms each); Windows file operations make every write slower on both sides.

Do not copy these numbers onto another machine and call them current. Re-run the commands.
