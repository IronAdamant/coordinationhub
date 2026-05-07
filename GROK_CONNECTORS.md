# Grok Connectors / BYO MCP Support

This project is ready for Grok Connectors (Bring Your Own MCP) and Grok Build.

The MCP servers (stdio for local IDE integration and HTTP admin/SSE for visibility) are designed to work with any MCP-compatible client, including future Grok Build local agents and remote MCP connections in Grok.

## For Grok Connectors (BYO MCP)

When Grok supports adding custom MCP servers:

1. Start the server locally (example for this project):
   ```bash
   python -m coordinationhub.mcp_server --port 9877
   ```
   Or use the stdio mode for local agents:
   ```bash
   python -m coordinationhub.mcp_stdio
   ```

2. For remote access from Grok, expose the HTTP endpoint over HTTPS using a secure tunnel (e.g., Cloudflare Tunnel, ngrok, or Tailscale).

3. Add to Grok Connectors / remote MCP config:
   - **server_url**: `https://your-https-tunnel/mcp` (or the specific MCP endpoint)
   - **server_label**: `coordinationhub` (or a descriptive name)
   - **server_description**: "Multi-agent coordination, file locking, dashboard, and safety for LLM coding agents"

The server implements MCP tool schemas for agent registration, locking, notifications, tasks, etc. Grok will discover the tools automatically.

## For Grok Build (Local)

Once Grok Build is available:
- The stdio MCP adapter (`mcp_stdio.py`) allows direct integration similar to Claude Code hooks.
- Run the coordination engine alongside Grok Build agents for automatic file locking, conflict prevention, and multi-agent orchestration.
- Use the dashboard (http://127.0.0.1:9898) to monitor live agent activity during Grok Build sessions.

No changes to your existing workflows are required. These projects were built to be backend-agnostic and work with any LLM/IDE/CLI via MCP.

See README.md for full setup and the hooks/ directory for IDE-specific adapters (extendable to Grok Build events).

For questions or to contribute Grok-specific adapters, open an issue.