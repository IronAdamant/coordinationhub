# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

CoordinationHub is a shared coordination layer for coding agents on one project: agent identity and lineage, document locks (including line regions), a task board with dependencies, work intents, messages, broadcasts, handoffs, a coordinator lease, and a live dashboard. This branch is a std-only Rust rewrite of Python 0.8.0 (kept unchanged on `python-legacy`). **Zero crates, no build.rs, nothing needed at run time.** Python appears only as the parity reference in `examples/parity.rs` and `examples/bench.rs`.

## Commands

```bash
cargo build --release
cargo test --release                                   # all tests, no Python needed
cargo run --release --example parity -- target/release/coordinationhub --python python3.12
cargo run --release --example bench -- target/release/coordinationhub --python python3.12
cargo run --release --example check_zero_deps          # Cargo.toml must have no dependencies
cargo run --release --example check_no_todos           # no unfinished markers in src, tests
cargo run --release --example gen_docs -- --root <python tree> [--check]   # ports of the 0.8.0 doc tools;
cargo run --release --example propose_stale_phrases    # they read a coordinationhub/ Python package,
cargo run --release --example update_dispatch_floor -- --root <python tree>  # e.g. python-legacy
```

`parity` and `bench` need `git fetch origin python-legacy` and `pip install "mcp==1.30.0"` for that Python.

## Layout

- `src/engine.rs`: tool dispatch (`Engine::call` for MCP with schema checks, `call_cli` for the CLI) over the journal.
- `src/db.rs`: the append-only `coordination.jsonl` journal and its file lock; every call locks, reads new lines, applies, appends.
- `src/act_*.rs`: tool behaviour (identity, locks, board: tasks/messages/handoffs/leases/spawns, views: status, scan, assessment).
- `src/mcp.rs` (stdio JSON-RPC), `src/http.rs` (dashboard, `/call`, SSE), `src/hooks.rs` (`coordinationhub hook`).
- `src/cli.rs`, `src/cli_cmds.rs`, `src/cli_text.rs`: CLI dispatch, argument mapping, readable output.
- `src/argparse.rs` + `src/cli_catalog.rs`: a port of CPython 3.12 argparse over tables introspected from the 0.8.0 parser (prefixes, choices, int/float errors, help wrapped to `COLUMNS`). Regenerate the catalog from the 0.8.0 parser rather than hand-editing it; the only hand-written rows are the 1.1.0 additions (`-V/--version`, `assign-task --force`), marked in the file.
- `src/validate.rs`: tool argument checks against `assets/tools.json`.

## Rules

- Match 0.8.0's function: every tool, command, flag and reply shape. Where 0.8.0 had a bug, return the working result and list it in `MIGRATION.md`. `MIGRATION.md` lists every deliberate difference and nothing else; `examples/parity.rs` allows only those (its `ALLOWED` list).
- Errors must say what is wrong, which field, what is expected and what to do next.
- Write the code first, then a test for it (`tests/round5.rs`, `tests/agent_improvements.rs` style); goldens in `tests/argparse_goldens.rs` come from unmodified 0.8.0 on Python 3.12, with the 1.1.0 flags declared on that parser by argparse's own `version` and `store_true` actions.
- MCP tool replies are compact JSON (`json::dumps_tight`); the parity harness compares them with 0.8.0 by value.
- Rust must stay faster than Python on every benchmark row (`benchmark-gate` in CI).
- Keep Rust 1.83 compatibility (`rust-toolchain.toml`).
- Add an entry at the top of `LLM_Development.md` for significant changes.
