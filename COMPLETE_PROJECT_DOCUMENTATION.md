# CoordinationHub

Updated 2026-11-04. This file lists the Rust tree. The Python 0.8.0 inventory is on the `python-legacy` branch.

## Purpose

Live coordination for coding agents: identity, file locks, tasks, messages, and work claims. Standard library only.

## Layout

| Path | Role |
| --- | --- |
| `src/main.rs` | Process entry |
| `src/lib.rs` | Module root |
| `src/cli.rs` | Command line |
| `src/engine.rs` | Tool dispatch |
| `src/mcp.rs` | Newline JSON-RPC |
| `src/http.rs` | Dashboard and POST /call |
| `src/db.rs` | Append-only journal |
| `src/json.rs` | JSON parser and writer |
| `src/validate.rs` | Argument checks |
| `src/act_identity.rs` | Agents |
| `src/act_lock.rs` | Locks, conflicts, notifications |
| `src/act_board.rs` | Tasks, messages, leases, spawns |
| `src/act_view.rs` | Graph, scan, tree, `who` |
| `src/util.rs` | Clock, paths, file lock |
| `assets/tools.json` | The 50 Python tool schemas |
| `assets/dashboard.html` | Dashboard page |
| `parity/harness.py` | Python versus Rust compare |
| `parity/bench.py` | Timings and the speed gate |
| `tests/surface.rs` | One pass over every tool |
| `PARITY.md` | Checklist |
| `MIGRATION.md` | What changed on purpose |
| `BENCHMARKS.md` | Measured numbers |
| `README.md` | Install and MCP snippets |

## Removed on this branch

The `coordinationhub/` Python package, `tests/` pytest tree, and `pyproject.toml` live only on `python-legacy`.
