# Grok connectors

CoordinationHub speaks MCP over stdio. Point a Grok Build or Grok connector entry at the binary:

```bash
coordinationhub serve-mcp
```

Example config:

```json
{
  "mcpServers": {
    "coordinationhub": {
      "command": "coordinationhub",
      "args": ["serve-mcp"]
    }
  }
}
```

The dashboard listens on `coordinationhub serve` (port 9877) or `coordinationhub serve-sse` (port 9898). Both stay on the loopback interface unless you pass `--host`.

See `README.md` for Claude Code and Cursor as well.
