# CoordinationHub

Updated 2026-11-05. This file lists the Rust tree. The Python 0.8.0 inventory is on the `python-legacy` branch.

## Purpose

Live coordination for coding agents: identity, file locks, tasks, messages, and work claims. Standard library only.

## Layout

| Path | Role |
| --- | --- |
| `src/main.rs` | Process entry |
| `src/lib.rs` | Module root |
| `src/cli.rs` | Command line dispatch |
| `src/argparse.rs` | Python 3.12 argparse behaviour: parsing, errors, help |
| `src/cli_catalog.rs` | The 0.8.0 parser's commands, flags and help text |
| `src/cli_cmds.rs` | Command to tool arguments |
| `src/cli_text.rs` | Readable output |
| `src/hooks.rs` | `coordinationhub hook` |
| `src/house.rs` | Background housekeeping |
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
| `examples/parity.rs` | Python 0.8.0 versus Rust compare |
| `examples/bench.rs` | Timings and the speed gate |
| `examples/check_zero_deps.rs`, `examples/check_no_todos.rs` | CI checks |
| `examples/gen_docs.rs`, `examples/propose_stale_phrases.rs`, `examples/update_dispatch_floor.rs` | Ports of the 0.8.0 doc tools (they read a `coordinationhub/` Python tree) |
| `examples/pyport/` | CPython behaviour those ports need: text I/O, a tokenizer, json, argparse |
| `tests/surface.rs` | One pass over every tool |
| `tests/argparse_goldens.rs` | Command lines checked against captured 0.8.0 output |
| `tests/multi_agent.rs` | Three sessions sharing one hub end to end |
| `PARITY.md` | Checklist |
| `MIGRATION.md` | What changed on purpose |
| `BENCHMARKS.md` | Measured numbers |
| `README.md` | Install and MCP snippets |

## Removed on this branch

The `coordinationhub/` Python package, `tests/` pytest tree, and `pyproject.toml` live only on `python-legacy`.
