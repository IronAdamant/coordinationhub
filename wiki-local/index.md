# CoordinationHub wiki

**Version:** 0.3.1
**Last updated:** 2026-04-07

## Pages

| Page | Description |
|------|-------------|
| [spec-project.md](spec-project.md) | Architecture, constraints, SQLite schema, MCP tools |
| [glossary.md](glossary.md) | Named concepts (agent lineage, lock types, conflict resolution, etc.) |

## Root docs

| Path | Role |
|------|------|
| [../README.md](../README.md) | Quickstart, CLI, MCP setup, architecture |
| [../COMPLETE_PROJECT_DOCUMENTATION.md](../COMPLETE_PROJECT_DOCUMENTATION.md) | File inventory and data flow |
| [../LLM_Development.md](../LLM_Development.md) | Chronological change log |
| [../CLAUDE.md](../CLAUDE.md) | Agent guidance for working in this project |

## Integration

CoordinationHub is self-contained and works standalone. When co-installed with Stele, Chisel, or Trammel, it cooperates through each LLM's MCP tool layer.

| Tool | Role |
|------|------|
| **Stele** | Persistent context retrieval and semantic indexing |
| **Chisel** | Code analysis, churn, coupling, risk mapping |
| **Trammel** | Planning discipline, verification, failure learning, recipe memory |
| **CoordinationHub** | Multi-agent identity, lineage, locking, conflict prevention |

## Test Suite

187 tests across 12 test files. Run with:
```bash
python -m pytest tests/ -v
```
