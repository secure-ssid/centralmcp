# centralmcp documentation

Use this directory as the organized documentation hub for setup, architecture, operations, audits, and planning notes.

## Start here

| Doc | Use it for |
|---|---|
| [getting-started.md](getting-started.md) | Install, configure credentials, connect an MCP client, and build indexes |
| [tool-router.md](tool-router.md) | Low-token router modes, toolsets, optional products, and safe dispatch |
| [architecture/RAG-ARCHITECTURE.md](architecture/RAG-ARCHITECTURE.md) | Embedded RAG design, eval results, and migration rationale |
| [operations/hpe-support-events-endpoint.md](operations/hpe-support-events-endpoint.md) | HPE support events endpoint notes |
| [audits/AUDIT-2026-06-03.md](audits/AUDIT-2026-06-03.md) | Historical deep audit and remediation notes |
| [plans/agentic-rag-plan.md](plans/agentic-rag-plan.md) | Historical original Qdrant/Ollama RAG plan; current design is the architecture doc above |

## Documentation sections

| Section | Contents |
|---|---|
| [architecture/](architecture/) | System design, RAG architecture, data stores, eval rationale |
| [audits/](audits/) | Time-bound reviews, findings, and remediation history |
| [operations/](operations/) | Endpoint notes and operator-facing runbook material |
| [plans/](plans/) | Historical plans and larger implementation tracks |

## Repo map

| Path | Purpose |
|---|---|
| `mcp_servers/` | FastMCP servers, low-token router, prompts, middleware, optional product starters |
| `pipeline/` | Migration pipeline, typed clients, credentials loading, state store, SSID helpers |
| `ingestion/` | Docs/API ingestion into LanceDB and SQLite |
| `scripts/` | Tool-catalog ingestion, release validation, local sync helpers |
| `tests/unit/` | Mocked unit coverage for tools, clients, middleware, routing, RAG, release gates |
| `tests/eval/` | RAG/API eval data and runner |
| `data/` | Local built indexes, git-ignored |

## Common commands

```bash
# Install dependencies
uv sync

# Build the router tool catalog
uv run python scripts/ingest_tools.py

# Include optional product starters in the tool catalog
uv run python scripts/ingest_tools.py --products all

# Run unit tests
uv run pytest tests/unit -q

# Run the full local release gate
uv run python scripts/validate_release.py
```

The release helper enforces the documented tool catalog floor and checks local LanceDB tool-index freshness when `data/tools.lance` exists. The unit suite also carries static regression guards for async-safe MCP tools, shared `httpx` client boundaries, project metadata (`centralmcp` package name with no direct `pycentral`/`requests` runtime dependencies), committed MCP config examples, tool-count docstrings, and tracked Markdown local links.
