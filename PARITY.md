# Parity with Python 0.8.0

```bash
git fetch origin python-legacy
python -m pip install "mcp==1.30.0"          # the version 0.8.0's stdio server runs on
cargo build --release
cargo run --release --example parity -- target/release/coordinationhub [--python python3.12]
```

`examples/parity.rs` extracts the unmodified 0.8.0 tree from `python-legacy` (byte-identical to the PyPI package) and runs it beside the Rust binary over real MCP stdio and the real CLI. Neither side is patched; both use the real clock. Time fields, pids, temporary paths, epoch and ISO timestamps are masked, CRLF is folded to LF, and the Python child runs with `PYTHONUTF8=1` so Windows pipes carry UTF-8. Everything else is compared. Rust MCP replies are compact JSON since 1.1.0; each one is parsed and written again in 0.8.0's indented layout before the comparison, so replies are compared by value, and a separate check fails the run if the Rust text is not compact.

Every difference goes into a ledger. A difference whose title is in `ALLOWED` is one `MIGRATION.md` documents; for those the harness also checks the Rust reply against the documented one, so a regression there still fails. Anything else fails the run with exit 1. CI runs it on Linux with Python 3.12.

## What it compares

- **The original harness checks**: 15 scripted MCP calls (registration with a non-ASCII id, locks and a refused lock, notifications with `limit: -1`, tasks, status, the agent tree), the caller `worktree_root`, the tool list, every tool called with empty or safe arguments, every CLI command present, `heartbeat` and `cancel-spawn` text and exit codes, `acquire-lock --help`, an unknown flag, and `cancel-spawn --json`.
- **MCP scenarios**: contention hotspot ties and `limit` -1 / 0 / 1, conflicts with `limit: -1`, a duplicate task id, a string and an integer-valued float priority, lease ttl as integer, float and default, lease release and claim, a handoff message's payload (`handoff_type`), message `limit: -1`, `get_notifications` with `timeout_s: -1`, a negative lock ttl, an unknown tool, a caller mismatch, deregistration; `acknowledge_handoff`, `complete_handoff` and `cancel_handoff` against 0.8.0's `wait_for_handoff` modes (the replies must match), a second agent's `assign_task` on a held task, and `manage_leases` `get` without `agent_id`; a lease holder deregistering (the lease must be released, as in 0.8.0), then a `ttl: 0` lease read with `get` and refreshed by its holder.
- **A 70-command CLI sequence**, once with `--json` and once readable: registration, locks with negative ttl and region, prefixes such as `--agent` and `--exclude` and `--unread`, negative `--limit` and `--timeout`, `--priority=-4`, a duplicate task, task trees and failure history, dependencies, broadcasts and handoffs, leases refused and claimed, spawns, scan, status.
- **argparse**: 18 error cases (unrecognized arguments with their values, invalid choices, invalid int and float values, `--message -loud`, a dash-led positional, `-j` before the command, an ambiguous prefix, `--json=yes`, a missing value) and `--help` for the top level and all 75 commands. With Python 3.12 the bytes must match once the 1.1.0 flags (`-V/--version` in the top-level usage and help, `assign-task --force`) are taken out of the Rust text; each case where they were is recorded in the ledger. With another Python, whose argparse wraps usage lines differently, the help only has to list every option Python lists, and an error only has to give the same exit code and the same error sentence (3.13 also quotes the choices in `invalid choice`).
- **prune-notifications** counts after ten event-writing commands, for `--max-entries 2` and `-1` and `--max-age-seconds 0` and `3600`.

## Ledger on this branch

With Python 3.12 the run records only documented differences: `status.tools`, compact MCP replies, the explicit handoff tools, the `register_agent` reply's `worktree_root`, the duplicate task id (MCP and CLI), the integer-valued float, the unknown tool, the validation wording, the `assign_task` refusal, `manage_leases` `get` without `agent_id`, an expired lease read as no leader and its refresh refused, satisfying an unknown dependency, the version flag and `assign-task --force` in usage and help, and the readable-output fixes for the commands listed in `MIGRATION.md`.

## Tests

`cargo test --release` runs without Python:

- `tests/argparse_goldens.rs`: 31 command lines whose stdout, stderr and exit code were captured from 0.8.0 on Python 3.12 (errors, prefixes, help at 80, 60 and 120 columns), with the 1.1.0 `-V/--version` and `assign-task --force` declared on that parser by argparse's own actions.
- `tests/agent_improvements.rs`: one test per 1.1.0 change, including 16 processes claiming one task at once (exactly one wins).
- `tests/round5.rs`: one test per fifth-review fix.
- `tests/surface.rs`, `tests/behavior.rs`, `tests/fresh.rs`, `tests/round3.rs`, `tests/round4.rs`: the tool surface, lock and lease rules, waits, the hook, the dashboard data, zero crate dependencies.
