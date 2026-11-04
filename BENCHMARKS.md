# Benchmarks

Measured by `python3 parity/bench.py target/release/coordinationhub` on this Linux machine. Python is unmodified 0.8.0 from `python-legacy`, speaking MCP stdio through `mcp` 1.30.0. Rust is the release binary, speaking the same stdio protocol. Each number is the median of 3 runs. `COORDINATIONHUB_BENCH_LOOPS` was 50.

An earlier table compared Python in-process calls with a Rust `bench` subcommand. That comparison is withdrawn. These rows use the same interface on both sides.

Rust was faster on every row. The gate command exits 1 if that stops being true.

| Operation | Python seconds | Rust seconds |
| --- | ---: | ---: |
| startup and status | 0.3450 | 0.0010 |
| register x50 | 0.4958 | 0.0060 |
| lock x50 | 0.5350 | 0.0048 |
| get_agent_tree x20 after 50 agents | 0.5461 | 0.0108 |

Do not copy these numbers onto another machine and call them current. Re-run the script.
