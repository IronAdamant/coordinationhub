# CoordinationHub

Updated 2026-11-15 for 1.3.0. This file lists the Rust tree. `AGENTS.md` describes each module and the store; the Python 0.8.0 tree is kept at the tag `pre-rust-python-legacy`.

## Purpose

Live coordination for coding agents: identity, file locks, tasks, messages, handoffs and work claims. Standard library only.

## Layout

| Path | Role |
| --- | --- |
| `src/main.rs` | Process entry |
| `src/lib.rs` | Module root and version |
| `src/cli.rs` | Command line dispatch |
| `src/argparse.rs` | Python 3.12 argparse behaviour: parsing, errors, help |
| `src/cli_catalog.rs` | The 0.8.0 parser's commands, flags and help text |
| `src/cli_cmds.rs` | Command to tool arguments |
| `src/cli_text.rs` | Readable output |
| `src/hooks.rs` | `coordinationhub hook` |
| `src/house.rs` | Background housekeeping and event retention |
| `src/engine.rs` | Tool dispatch |
| `src/groups.rs` | MCP tool groups and their switches |
| `src/mcp.rs` | Stdio JSON-RPC |
| `src/http.rs` | Dashboard, `POST /call` and the event stream |
| `src/waits.rs` | Waiting calls on their own engine |
| `src/db.rs` | Append-only journal, file lock, recovery, compaction |
| `src/json.rs` | Strict JSON parser and writer |
| `src/rand.rs` | Operating-system random bytes |
| `src/validate.rs` | Argument checks |
| `src/act_identity.rs` | Agents |
| `src/act_lock.rs` | Locks, conflicts, notifications |
| `src/act_board.rs` | Tasks, messages, leases, spawns, handoffs |
| `src/act_view.rs` | Status, graph, scan, tree, assessment, `who` |
| `src/util.rs` | Clock, paths, limits, file lock |
| `assets/tools.json` | The 53 tool schemas (the 54th tool, `who`, is added by the engine) |
| `assets/dashboard.html` | Dashboard page |
| `assets/monitor_skill.md` | The skill `init --monitor-skill` installs |
| `examples/parity.rs` | Python 0.8.0 versus Rust compare |
| `examples/bench.rs` | Timings and the speed gate |
| `examples/check_zero_deps.rs`, `examples/check_no_todos.rs` | CI checks |
| `examples/gen_docs.rs`, `examples/propose_stale_phrases.rs`, `examples/update_dispatch_floor.rs` | Ports of the 0.8.0 doc tools (they read a Python tree passed with `--root`) |
| `examples/pyport/` | CPython behaviour those ports need: text I/O, a tokenizer, json, argparse |
| `tests/` | Integration tests; `tests/surface.rs` calls every tool, `tests/argparse_goldens.rs` checks command lines against captured 0.8.0 output, `tests/multi_agent.rs` runs three sessions on one hub |
| `scripts/release.sh` | Release check; prints the tag command |
| `scripts/install-hooks.sh`, `scripts/hooks/pre-commit` | Optional local pre-commit checks |
| `PARITY.md` | Checklist |
| `MIGRATION.md` | What changed on purpose |
| `BENCHMARKS.md` | Measured numbers |
| `RELEASING.md` | How a release is made |
| `README.md` | Install, MCP snippets, tool groups |

## Counts

The live MCP tool list has 54 tools: the 53 schemas in `assets/tools.json` plus `who`. `GROUPS` in `src/groups.rs` puts each of the 54 in one of seven groups, and `tests/tool_groups.rs` checks that every listed tool is in exactly one group and that the list has 54.

## Removed in the Rust tree

The Python package, its pytest tree, pyproject.toml and the files generated from them (the per-module wiki, library.md, the file-health reports, the journal notes) are only at the tag `pre-rust-python-legacy`.
