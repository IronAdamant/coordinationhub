# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

**Read `AGENTS.md` first.** It is the guide for this repository: the module map, how the store works, the hard rules, the checks a pull request must pass, and the tool groups. This file adds only the command list and the points most often missed.

## What this is

CoordinationHub is a shared coordination layer for coding agents on one project, as one std-only Rust binary: **zero crates, no build script, nothing needed at run time.** It is a rewrite of Python 0.8.0, which is kept unchanged at the tag `pre-rust-python-legacy` (formerly the `python-legacy` branch). Python appears only as the parity reference in `examples/parity.rs` and `examples/bench.rs`.

## Commands

```bash
cargo build --release
cargo test --release                                   # all tests, no Python needed
cargo clippy --all-targets -- -D warnings              # CI fails on any warning
cargo run --release --example check_zero_deps          # Cargo.toml must have no dependencies
cargo run --release --example check_no_todos           # no unfinished markers in src, tests
cargo run --release --example parity -- target/release/coordinationhub --python python3.12
cargo run --release --example bench -- target/release/coordinationhub --python python3.12
cargo run --release --example gen_docs -- --root <python tree> [--check]   # ports of the 0.8.0 doc tools;
cargo run --release --example propose_stale_phrases    # they read a Python package tree,
cargo run --release --example update_dispatch_floor -- --root <python tree>  # e.g. the pre-rust-python-legacy tag
```

`parity` and `bench` need `git fetch origin tag pre-rust-python-legacy` and `pip install "mcp==1.30.0"` for that Python. On Windows pass `target/release/coordinationhub.exe`.

## Points most often missed

- Match 0.8.0's function: every tool, command, flag and reply shape. A deliberate difference goes in `MIGRATION.md` and in the `ALLOWED` list of `examples/parity.rs`; nothing else may differ.
- Read JSON that comes back from the store with `json::parse_journal` or `json::parse_stored`, never `json::parse` (strict: depth 128, finite numbers, no duplicate keys).
- Regenerate `src/cli_catalog.rs` from the 0.8.0 parser rather than hand-editing it; the few hand-written rows are marked in the file.
- Goldens in `tests/argparse_goldens.rs` come from unmodified 0.8.0 on Python 3.12. Do not edit them to make a failure pass.
- MCP tool replies are compact JSON (`json::dumps_tight`); errors say what is wrong, which field, what is expected and what to do next.
- Rust must stay faster than Python on every benchmark row, and build with Rust 1.83 (`rust-toolchain.toml`).
- Add an entry at the top of `LLM_Development.md` for significant changes.
