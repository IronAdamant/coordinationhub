# CoordinationHub

Shared coordination for several AI coding agents working in one repo. It tracks who is alive, who holds which file, who owns which area of work, and the short messages they leave for each other.

Version 1.0.0 is a Rust rewrite of the Python 0.8 line. The standard library is the only dependency. There are no Cargo crates.

The Python 0.8 source stays on the `python-legacy` branch so it can be run beside this one.

## What it is for

CoordinationHub is the live layer: identity, file locks, work claims, inboxes, and a dashboard. It does not replace the other Iron Adamant tools:

- [Relatum](https://github.com/IronAdamant/Relatum) keeps pass-by-reference notes and orchestrator-to-subagent handoff documents.
- [stele-context](https://github.com/IronAdamant/stele-context) is the local code map.
- [Trammel](https://github.com/IronAdamant/Trammel) plans and checks work.
- [Chisel](https://github.com/IronAdamant/Chisel) maps tests to code.
- [Probatio](https://github.com/IronAdamant/Probatio) runs mutation checks.

Use CoordinationHub when two agents might touch the same file or the same area at the same time, and when a parent needs a compact answer to "who is doing what".

## Install

Build from this repo (Rust 1.83 or newer):

```bash
cargo build --release
cp target/release/coordinationhub target/release/coordinationhub-mcp ~/.local/bin/
```

Or run the binaries from a GitHub Release when one is published. Each archive name includes the target triple. `SHA256SUMS` sits beside the binaries.

```bash
coordinationhub init
coordinationhub doctor
coordinationhub serve-sse --no-auth
```

The dashboard listens on `http://127.0.0.1:9898`.

## MCP clients

Stdio server: `coordinationhub-mcp`. It speaks JSON-RPC with `Content-Length` framing. No extra package is required.

Claude Code (`.mcp.json`):

```json
{
  "mcpServers": {
    "coordinationhub": {
      "command": "coordinationhub-mcp",
      "env": {
        "COORDINATIONHUB_PROJECT_ROOT": "."
      }
    }
  }
}
```

Cursor (`~/.cursor/mcp.json`):

```json
{
  "mcpServers": {
    "coordinationhub": {
      "command": "coordinationhub-mcp"
    }
  }
}
```

Grok Build (same shape, command on `PATH`):

```json
{
  "mcpServers": {
    "coordinationhub": {
      "command": "coordinationhub-mcp"
    }
  }
}
```

`coordinationhub init` writes `~/.coordinationhub/hooks.json` so an IDE that runs hook commands can register a session, lock a file before Write/Edit, and release it afterwards.

## Everyday commands

```bash
coordinationhub register agent-a -j
coordinationhub acquire-lock src/main.rs agent-a -j
coordinationhub claim-work agent-a src/ -j
coordinationhub compact-status -j --agent-id agent-a
coordinationhub inbox agent-a -j
coordinationhub status -j
```

`-j` prints one JSON object. Storage defaults to `<project>/.coordinationhub/state.json`.

## Environment

| Variable | Role |
| --- | --- |
| `COORDINATIONHUB_STORAGE_DIR` | Directory for `state.json` |
| `COORDINATIONHUB_PROJECT_ROOT` | Root used to normalise paths |
| `COORDINATIONHUB_NAMESPACE` | Agent id prefix (default `hub`) |
| `COORDINATIONHUB_COORDINATION_URL` | URL placed in the register bundle |
| `COORDINATIONHUB_MAX_DESCRIPTION` and the other `COORDINATIONHUB_MAX_*` caps | Field length caps |
| `COORDINATIONHUB_SPAWN_TTL_SECONDS` | How long a pending spawn stays pending |
| `COORDINATIONHUB_MAX_PENDING_SPAWNS` | Cap on pending spawns |

## Further reading

- [MIGRATION.md](MIGRATION.md) — Python 0.8 to Rust 1.0
- [PARITY.md](PARITY.md) — checklist and how the harness compares the two
- [BENCHMARKS.md](BENCHMARKS.md) — measured Python versus Rust times
- [CHANGELOG.md](CHANGELOG.md)
