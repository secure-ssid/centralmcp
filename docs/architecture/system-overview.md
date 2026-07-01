# centralmcp system overview

This page shows how the repo fits together for MCP users, contributors, and people evaluating the project from GitHub.

## Runtime architecture

```mermaid
flowchart LR
    client["MCP clients<br/>Cursor, VS Code, Claude, local agents<br/>any MCP-capable model"]
    router["mcp_servers/tool_router.py<br/>aruba-tool-router"]
    catalog["data/tools.lance<br/>semantic tool catalog"]
    rag["mcp_servers/rag.py<br/>search_docs, ask_docs, lookup_api"]
    docs["data/docs.lance<br/>hybrid docs index"]
    specs["data/specs.sqlite<br/>exact OpenAPI lookup"]
    core["Core Aruba servers<br/>monitoring, config, ops, nac, glp"]
    optional["Optional product starters<br/>clearpass, mist, apstra,<br/>aos8, edgeconnect, uxi"]
    apis["External APIs<br/>Aruba Central, GreenLake,<br/>optional products"]

    client -->|"stdio or streamable HTTP"| router
    router -->|"find_tool"| catalog
    router -->|"invoke_read_tool / invoke_tool"| core
    router -->|"opt-in"| optional
    router -->|"RAG toolset"| rag
    rag --> docs
    rag --> specs
    core -->|"async httpx REST"| apis
    optional -->|"async httpx REST"| apis
```

The default MCP client profile should stay small:

```env
CENTRALMCP_ROUTER_MODE=minimal
CENTRALMCP_TOOLSETS=central,glp,rag
```

Optional products are disabled until explicitly enabled:

```env
CENTRALMCP_PRODUCTS=clearpass,mist,apstra,aos8,edgeconnect,uxi
```

## Tool discovery and dispatch

```mermaid
sequenceDiagram
    participant User
    participant Client as MCP client
    participant Router as aruba-tool-router
    participant Catalog as Tool catalog
    participant Backend as Backend MCP server
    participant API as External API

    User->>Client: "show critical Central alerts"
    Client->>Router: find_tool("critical alerts")
    Router->>Catalog: semantic or keyword lookup
    Catalog-->>Router: list_active_alerts, read_only=true
    Router-->>Client: compact tool result with params
    Client->>Router: invoke_read_tool("list_active_alerts", args)
    Router->>Backend: dispatch read-only backend tool
    Backend->>API: async httpx GET
    API-->>Backend: JSON
    Backend-->>Router: bounded response
    Router-->>Client: result
```

Use `invoke_read_tool` for normal investigations. Use `invoke_tool` only when the user intentionally asks for a write or destructive action; it is marked destructive because it can dispatch any enabled backend tool.

## Local setup flow

```mermaid
flowchart TD
    clone["git clone"]
    wizard["scripts/setup_wizard.py<br/>install, region, credentials"]
    products{"Enable optional<br/>product starters?"}
    selected["Select products<br/>clearpass, mist, apstra,<br/>aos8, edgeconnect, uxi, or all"]
    access{"Product access mode"}
    ro["read-only default<br/>hide/block optional writes"]
    rw["read-write lab mode<br/>writes visible, dry-run default,<br/>dry_run=False + confirm=True required"]
    catalog["uv run python scripts/ingest_tools.py"]
    doctor["uv run python scripts/doctor.py"]
    creds["config/credentials.yaml<br/>.env or environment variables"]
    stdio["stdio client<br/>.mcp.json"]
    http["HTTP client<br/>.mcp.http.json + scripts/run_http_router.sh"]
    ready["MCP client connected to aruba-tool-router"]

    clone --> wizard
    wizard --> products
    products -->|"yes / --products / --with-products"| selected
    products -->|"no"| catalog
    selected --> access
    access -->|"default"| ro
    access -->|"--product-access read-write"| rw
    ro --> catalog
    rw --> catalog
    catalog --> doctor
    doctor --> creds
    creds --> stdio
    creds --> http
    stdio --> ready
    http --> ready
```

`scripts/setup_wizard.py` can run install, offer common Central API gateway
choices, fill credentials without echoing secrets, and enable only the optional
products you choose. `scripts/doctor.py` is intentionally non-mutating and does
not call Central, GLP, or optional product APIs. It checks local dependencies,
credentials/config paths, indexes, RAG source-manifest drift, router profile
drift, HTTP URL/transport mismatches, optional product env, and listener status.

## Tracked file structure

```text
.claude/                 Optional launch profiles and repo agent notes
.cursor/                 Cursor MCP profiles
.vscode/                 VS Code MCP example config
config/                  Credentials template
docs/                    User, architecture, setup, router, and product docs
ingestion/               Docs/API ingestion into LanceDB and SQLite
inputs/                  Example migration input templates
mcp_servers/             FastMCP servers and low-token router
pipeline/                Clients, migration stages, SSID helpers
resources/               API/Postman reference notes and resources
scripts/                 Local doctor, HTTP router helper, catalog ingest, release validation
tests/                   Unit, integration, and eval coverage

.mcp.json.example        Generic stdio MCP client example
.mcp.http.json.example   Generic streamable HTTP MCP client example
docker-compose.yml       Optional localhost-only Redis/Ollama server backend
run_pipeline.py          Migration pipeline CLI
run_ssid.py              SSID helper CLI
```

Generated local artifacts are intentionally git-ignored:

```text
config/credentials.yaml
.env
.mcp.json
.mcp.http.json
data/
state/
outputs/
ingestion/sources/
ingestion/markdown*/
```

The optional Redis/Ollama Docker helper uses Docker named volumes for service
state, so it does not create repo-local `redis_data/` or `ollama_data/`
directories on new setups.
