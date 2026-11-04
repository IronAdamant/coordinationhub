# CoordinationHub (Rust)

Agents are the users. Call tools, do not scrape the dashboard for decisions.

- Binary: `coordinationhub` for the CLI and HTTP server. `coordinationhub-mcp` for stdio JSON-RPC.
- State file: `.coordinationhub/state.json` under the project root.
- Before editing a shared file: `acquire_lock`. Before a wider area: `claim_work`.
- One round trip for the swarm picture: `compact_status`.
- Unread notes: `inbox`.
- Relatum holds long handoff notes. stele-context holds the code map. This repo holds live ownership.

Python 0.8 is on branch `python-legacy`. Parity: `python3 parity/harness.py`.
