# Getting started

This guide gets a local clone running as an MCP server with the low-token router profile.

## 1. Install

```bash
git clone https://github.com/secure-ssid/centralmcp.git
cd centralmcp
python3 scripts/setup_wizard.py
```

Python 3.10+ is required. `uv` is recommended because the lockfile is maintained for this repo.

The guided setup wizard can run `uv sync`, create local git-ignored config
files, replace MCP path placeholders, choose a Central API gateway region, fill
credentials without echoing secrets, enable optional products, build the router
tool catalog, and run the local doctor.

```mermaid
flowchart TD
    start["Run scripts/setup_wizard.py"]
    install{"Install or sync dependencies?"}
    creds{"Configure Central / GLP credentials?"}
    products{"Enable optional products?"}
    access{"Product access mode"}
    transport{"MCP transport"}
    catalog["Build router catalog<br/>scripts/ingest_tools.py"]
    doctor["Run local doctor<br/>scripts/doctor.py"]
    env[".env<br/>CENTRALMCP_PRODUCTS<br/>CENTRALMCP_PRODUCT_ACCESS<br/>product URLs/tokens"]
    yaml["config/credentials.yaml<br/>Central / GLP credentials"]
    stdio[".mcp.json<br/>stdio MCP client config"]
    http[".mcp.http.json<br/>streamable HTTP client config"]
    ready["MCP client connects to<br/>aruba-tool-router"]

    start --> install
    install --> creds
    creds --> yaml
    creds --> products
    products --> env
    products --> access
    access -->|"read-only or read-write"| env
    access --> transport
    transport --> stdio
    transport --> http
    env --> catalog
    yaml --> catalog
    stdio --> catalog
    http --> catalog
    catalog --> doctor
    doctor --> ready
```

If dependencies are already installed, or you want to skip any wizard phase:

```bash
python3 scripts/setup_wizard.py --skip-install
```

### Try without API credentials

You can verify dependencies, build the local router catalog, and start the HTTP
MCP server before adding Central or GLP credentials:

```bash
python3 scripts/setup_wizard.py --yes --skip-credentials
uv run python scripts/doctor.py
MCP_PORT=8010 bash scripts/run_http_router.sh
```

API-backed tools need credentials later, but this confirms the MCP server and
local catalog path first.

## 2. Configure credentials

The wizard creates `config/credentials.yaml` when it is missing and offers common
Central API gateway choices:

| Region / gateway | Base URL |
|---|---|
| US / common API gateway | `https://apigw-prod2.central.arubanetworks.com` |
| EU Central | `https://apigw-eucentral3.central.arubanetworks.com` |
| APAC | `https://apigw-apac.central.arubanetworks.com` |
| Legacy/internal gateway | `https://internal.api.central.arubanetworks.com` |
| Custom | Enter the tenant-specific URL from your Central portal/API docs |

To create the template manually:

```bash
cp config/credentials.yaml.example config/credentials.yaml
```

Fill in the preferred sections:

```yaml
central_account:
  base_url: https://apigw-prod2.central.arubanetworks.com
  client_id: YOUR_CENTRAL_CLIENT_ID
  client_secret: YOUR_CENTRAL_CLIENT_SECRET
  glp_workspace_id: YOUR_GLP_WORKSPACE_ID

glp_account:
  base_url: https://apigw-prod2.central.arubanetworks.com
  client_id: YOUR_GLP_CLIENT_ID
  client_secret: YOUR_GLP_CLIENT_SECRET
  glp_workspace_id: YOUR_GLP_WORKSPACE_ID
```

Environment variables override YAML values. Common overrides:

| Variable | Purpose |
|---|---|
| `SOURCE_BASE_URL`, `SOURCE_CLIENT_ID`, `SOURCE_CLIENT_SECRET` | Central/source account |
| `TARGET_BASE_URL`, `TARGET_CLIENT_ID`, `TARGET_CLIENT_SECRET` | GLP/target account |
| `SOURCE_GLP_WORKSPACE`, `TARGET_GLP_WORKSPACE` | Workspace IDs |
| `GLP_TOKEN_URL`, `GLP_BASE_URL` | GLP endpoint overrides |
| `TOKEN_CACHE_DIR` | Token cache directory |

## 3. Configure your MCP client

```bash
cp .mcp.json.example .mcp.json
```

The wizard does this and replaces `/path/to/centralmcp` with your local clone
path. If configuring manually, edit `.mcp.json` yourself.
For VS Code, copy `.vscode/mcp.json.example` to `.vscode/mcp.json`.
For included `.claude` launch profiles, use `.claude/launch.json`; the first profile is the
same minimal `aruba-tool-router` setup and the remaining profiles are direct
debug servers.
For clients that connect to an already-running HTTP MCP server, copy
`.mcp.http.json.example` to `.mcp.http.json` and edit the URL if you use a
different host or port. The copied file is local-only and git-ignored.
For client-specific examples, see [mcp-client-recipes.md](mcp-client-recipes.md).

Recommended default:

```env
CENTRALMCP_ROUTER_MODE=minimal
CENTRALMCP_TOOLSETS=central,glp,rag
```

This exposes only the router discovery/dispatch surface and keeps tool-list token cost low.
The router can search 6,166 backend tools when all platforms and guarded writes
are indexed, while minimal mode exposes only three client-visible tools.

### Streamable HTTP instead of stdio

Any MCP-capable AI client/model can connect over streamable HTTP if the client
supports remote MCP servers.

Start the minimal router. The helper defaults to port `8010`, matching
`.mcp.http.json.example`:

```bash
MCP_PORT=8010 bash scripts/run_http_router.sh
```

Connect your client to:

```text
http://127.0.0.1:8010/mcp
```

The HTTP example in `.mcp.http.json.example` points at that local endpoint.
The helper safely loads expected local `.env` assignments first, so optional
product settings created by the wizard are available in HTTP mode.
If the port is already in use, `scripts/run_http_router.sh` exits before
starting another router and prints the listener details. Stop the foreground
server with `Ctrl-C`. If you launched it in the background, find the listener
and stop that PID:

```bash
lsof -nP -iTCP:8010 -sTCP:LISTEN
kill <PID>
```

Plain `curl` requests are expected to fail unless they send MCP streaming
headers such as `Accept: text/event-stream`; use an MCP client for actual tool
calls.

For a listener outside loopback, configure explicit `MCP_ALLOWED_HOSTS` and
`MCP_ALLOWED_ORIGINS`. Set `MCP_HTTP_BEARER_TOKEN` only with
`MCP_TRANSPORT=streamable-http`; clients must send
`Authorization: Bearer <token>`. Bearer configuration with SSE fails closed.

## 4. Build the tool catalog

```bash
uv run python scripts/ingest_tools.py
```

Include optional product starters:

```bash
uv run python scripts/ingest_tools.py --products all
```

The safe default hides optional write tools. Build all 6,166 backend tools only
for an intentional lab read/write profile:

```bash
CENTRALMCP_PRODUCT_ACCESS=read-write CENTRALMCP_GLP_GENERATED_TOOLS=1 uv run python scripts/ingest_tools.py --products all
```

Or let the wizard enable only the products you want:

```bash
python3 scripts/setup_wizard.py --products clearpass,mist --product-access read-write
```

Optional products default to read-only. Explicit read/write mode is lab-friendly:
write tools are exposed, but they dry-run by default and require `confirm=True`
to execute.

## 5. Optional: build the docs/API RAG indexes

The router tool catalog is quick. The full docs/API index is larger. Fresh clones need either a prebuilt release index or locally populated
`ingestion/sources/` input files before rebuilding docs/API search. Structured
OpenAPI data is written only to SQLite exact lookup; it is not embedded into the
LanceDB prose corpus.

```bash
uv run python ingestion/scrape_openapi.py
uv run python ingestion/scrape_cnac_spec.py
uv run python ingestion/fetch_mist_openapi.py
uv run python ingestion/scrape_security_lifecycle.py
uv run python scripts/check_openapi_drift.py
uv run python scripts/check_mist_openapi_drift.py
uv run python ingestion/ingest_docs.py
```

Built indexes live under `data/` and are git-ignored.

The current rebuilt snapshot contains 51,737 prose chunks and an exact API
index with 239 specs, 3,465 endpoints, 10,297 schemas, and 57,131 fields.

## 6. Validate

```bash
python3 scripts/setup_wizard.py --yes --skip-credentials --skip-catalog
uv run python scripts/doctor.py
uv run pytest tests/unit -q
uv run python scripts/validate_release.py --catalog-products all --strict-rag --strict-tool-index --min-tools 6162
```

`scripts/doctor.py` is a non-mutating local setup diagnostic. It checks Python
modules, credentials/config paths, local stdio/HTTP MCP config copies, local
stdio placeholder paths, local low-token router profile drift, local HTTP URL
or transport mismatches, indexes, RAG source-manifest drift, low-token router
env, optional product names and required product env vars, and the HTTP router
port without calling Central or GLP APIs.

The unit suite includes static guards that keep async MCP tools off sync HTTP calls, prevent direct `CentralClient.session` bypasses, keep direct runtime dependencies on `httpx` instead of sync SDKs or `requests`, and protect the committed low-token MCP config examples.

## Optional product starters

Optional product backends are disabled by default.

```env
CENTRALMCP_PRODUCTS=clearpass,mist,apstra,aos8,edgeconnect,uxi,axis
CENTRALMCP_PRODUCT_ACCESS=read-only
```

The wizard can prompt for the selected product URL/token settings, merge them
into local git-ignored `.env` while preserving existing non-placeholder token
values, and add the product selector plus access mode to local MCP configs. Use
a subset when you only want ClearPass, Mist, or another specific starter:

```bash
python3 scripts/setup_wizard.py --products clearpass
```

| Product | Variables |
|---|---|
| ClearPass | `CLEARPASS_BASE_URL`, `CLEARPASS_API_TOKEN` |
| Juniper Mist | `MIST_HOST`, `MIST_API_TOKEN` |
| Apstra | `APSTRA_BASE_URL`, preferred `APSTRA_USERNAME`/`APSTRA_PASSWORD`, optional pre-issued `APSTRA_API_TOKEN` |
| ArubaOS 8 | `AOS8_BASE_URL`, preferred `AOS8_USERNAME`/`AOS8_PASSWORD`, optional legacy `AOS8_API_TOKEN`, optional `AOS8_CLIENT_IP`, optional `AOS8_SESSION_TTL_SECONDS` |
| EdgeConnect | `EDGECONNECT_BASE_URL`, `EDGECONNECT_API_TOKEN`, optional `EDGECONNECT_AUTH_HEADER`, legacy-only `EDGECONNECT_ALLOW_LEGACY_API=1`, endpoint-specific `EDGECONNECT_AI_SESSION_AUTHORIZATION` |
| HPE Aruba UXI | `UXI_CLIENT_ID`, `UXI_CLIENT_SECRET`, optional `UXI_BASE_URL`, optional `UXI_TOKEN_URL` |
| Axis Atmos Cloud | `AXIS_BASE_URL`, `AXIS_API_TOKEN` |

Set `CENTRALMCP_PRODUCT_ACCESS=read-write` only for trusted lab writes, or
enable a single platform with `CENTRALMCP_<PLATFORM>_WRITES=1`.

Mist device diagnostic result collection (`mist_collect_diagnostic_results`)
requires the `websockets>=14.0` dependency installed by `uv sync` and connects
only to the documented regional `WS /api-ws/v1/stream` endpoint derived from
`MIST_HOST`.

Run `edgeconnect_doctor` before any EdgeConnect operational workflow. The
bundled pre-9.3 endpoint map is blocked by default; production 9.3+ remapping
requires the target Orchestrator's current instance-hosted Swagger document.

Before relying on any AOS8 migration mapping in your own environment, review
the [AOS8 migration contract matrix](aos8-migration-contract-matrix.md) and
prerequisites in [optional-products.md](optional-products.md#arubaos-8-migration-prerequisites);
a prior read-only [live/dry-run evaluation](aos8-live-dryrun-evaluation.md)
records exactly which surfaces were confirmed live versus fixture-backed only.

## Safety defaults

- GLP writes are disabled unless `CENTRALMCP_GLP_V2BETA1_WRITES=1`.
- Central and optional writes can be independently disabled/enabled with the
  per-platform `CENTRALMCP_<PLATFORM>_WRITES` variables.
- Token caches are stored in `~/.cache/centralmcp/` by default with `0600` permissions.
- Non-loopback HTTP binds require explicit `MCP_ALLOWED_HOSTS` and
  `MCP_ALLOWED_ORIGINS`; set `MCP_HTTP_BEARER_TOKEN` to protect HTTP routes.
- `/livez`, `/readyz`, and `/healthz` report local server health without
  contacting Central, GreenLake, or optional products.
- Use `invoke_read_tool` for read-only router dispatch.
- Use `invoke_tool` only for intentional writes/destructive actions.
