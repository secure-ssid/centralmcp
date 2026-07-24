# centralmcp — HPE Networking MCP toolkit

[![License](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)
[![Python](https://img.shields.io/badge/python-3.10+-blue.svg)](https://www.python.org/)
[![MCP](https://img.shields.io/badge/MCP-FastMCP-brightgreen)](https://modelcontextprotocol.io/)
[![CI](https://github.com/secure-ssid/centralmcp/actions/workflows/ci.yml/badge.svg?branch=main)](https://github.com/secure-ssid/centralmcp/actions/workflows/ci.yml)
[![Docs](https://img.shields.io/badge/docs-GitHub%20Pages-0969da)](https://secure-ssid.github.io/centralmcp/)
[![Release](https://img.shields.io/github/v/release/secure-ssid/centralmcp?display_name=tag)](https://github.com/secure-ssid/centralmcp/releases)

![centralmcp 0.3.0 - low-token HPE Networking MCP toolkit](docs/assets/centralmcp-hero.svg)

**Low-token Model Context Protocol (MCP) server for HPE Networking automation: Aruba Central, HPE GreenLake Platform, ClearPass, Juniper Mist, Apstra, ArubaOS 8, EdgeConnect, and HPE Aruba UXI.**

centralmcp gives MCP-capable AI clients a low-token way to search Aruba/HPE docs, look up exact OpenAPI details, inspect Central health, run troubleshooting workflows, manage configuration, and use guarded GreenLake Platform operations.

It is built around direct REST calls with `httpx`.

## Version 0.3.0 highlights

- **1,498-tool guarded catalog:** 270 core tools, 916 tools in the all-product
  read-only catalog, or 1,498 when guarded optional writes are intentionally
  enabled. The optional Mist backend contributes 1,050 generated OpenAPI tools
  from its committed manifest (see `mcp_servers/openapi_gen`).
- **Migration-ready AOS8:** UIDARUBA/X-CSRF sessions, structured exports and
  parsing, Classic/New Central candidates, compatibility warnings, diffs, and
  post-migration verification plans.
- **Broader platform parity:** new Central routing, checkpoint policy,
  automatic rollback status, telemetry,
  GLP v2beta1, Mist NAC/Marvis/Wired/WAN, Apstra connectivity, ClearPass
  Insight/OnGuard, and UXI lifecycle workflows.
- **Current API sources:** 25 Aruba ReadMe registries plus the pinned official
  Mist OpenAPI 2606.1.1 snapshot, with weekly drift checks.
- **Hardened transport and writes:** per-platform gates, dry-run confirmation,
  health probes, host/origin controls, streamable HTTP bearer protection, and
  protocol-level MCP tests.

See the [complete 0.3.0 release notes](docs/release-notes-0.3.0.md).

```mermaid
flowchart LR
    client["MCP clients<br/>Cursor, VS Code, Claude, local agents<br/>or any MCP-capable model"]
    router["aruba-tool-router<br/>find_tool<br/>invoke_read_tool<br/>invoke_tool"]
    rag["Embedded RAG<br/>LanceDB docs<br/>SQLite OpenAPI lookup"]
    core["Core Aruba backends<br/>Central monitoring/config/NAC/ops<br/>GreenLake Platform"]
    optional["Optional starters<br/>ClearPass, Mist, Apstra<br/>AOS8, EdgeConnect, UXI"]

    client -->|"stdio or streamable HTTP"| router
    router -->|"search_docs / ask_docs / lookup_api"| rag
    router -->|"async httpx REST"| core
    router -->|"opt-in only"| optional
```

## Search keywords

HPE Networking MCP server, HPE Aruba Networking MCP server, HPE Aruba Central
MCP server, Aruba Central AI tools, HPE GreenLake Platform MCP, GreenLake
Platform MCP, GreenLake service catalog MCP, GreenLake reporting status MCP,
FastMCP network automation, Model Context Protocol networking, network
configuration MCP, Aruba API RAG, Aruba Central OpenAPI lookup, ClearPass MCP,
Juniper Mist MCP, Apstra MCP, ArubaOS 8 MCP, AOS8 automation, HPE Aruba
EdgeConnect MCP, EdgeConnect SD-WAN MCP, HPE Aruba UXI MCP, UXI sensor status MCP,
guarded read/write lab automation, EdgeConnect zones, EdgeConnect interface
labels, zone-based firewall MCP, Python `httpx` network automation,
EdgeConnect ACL object groups, EdgeConnect services, EdgeConnect bypass mode,
EdgeConnect link integrity diagnostics.

## Who this is for

| You want to... | Use centralmcp to... |
|---|---|
| Connect an MCP-capable AI client to Aruba Central | Run the low-token `aruba-tool-router` over stdio or streamable HTTP |
| Ask questions about Aruba/HPE docs and APIs | Use embedded LanceDB + SQLite RAG/OpenAPI lookup without Docker |
| Inspect Central health, devices, clients, alerts, events, or sites | Discover tools with `find_tool` and call read-only tools through `invoke_read_tool` |
| Automate migrations or SSID workflows | Use the 8-stage migration pipeline and SSID helpers |
| Experiment with ClearPass, Mist, Apstra, AOS8, EdgeConnect, or UXI | Enable optional starter backends only when needed |

## Quick links

| Need | Start here |
|---|---|
| Documentation site | [centralmcp GitHub Pages](https://secure-ssid.github.io/centralmcp/) |
| Guided setup | [`scripts/setup_wizard.py`](scripts/setup_wizard.py) |
| Try without API credentials | [Try it locally without credentials](#try-it-locally-without-credentials) |
| Try it quickly | [Quick start](#quick-start) |
| Check your local setup | [`scripts/doctor.py`](scripts/doctor.py) |
| Install and connect an MCP client | [docs/getting-started.md](docs/getting-started.md) |
| Copy/paste MCP client setup | [docs/mcp-client-recipes.md](docs/mcp-client-recipes.md) |
| Enable optional products | [docs/optional-products.md](docs/optional-products.md) |
| See typed product workflow roadmap | [docs/product-workflows.md](docs/product-workflows.md) |
| Download prebuilt RAG/OpenAPI indexes | [docs/release-indexes.md](docs/release-indexes.md) |
| Fix setup or HTTP issues | [docs/troubleshooting.md](docs/troubleshooting.md) |
| Try useful prompts | [docs/example-prompts.md](docs/example-prompts.md) |
| Understand the low-token router | [docs/tool-router.md](docs/tool-router.md) |
| Browse tool counts and backend coverage | [docs/tool-catalog.md](docs/tool-catalog.md) |
| Run with any MCP-capable AI client/model | [Streamable HTTP mode](#streamable-http-mode) |
| See the architecture diagrams | [docs/architecture/system-overview.md](docs/architecture/system-overview.md) |
| Browse the documentation map | [docs/README.md](docs/README.md) |
| Review the RAG design | [docs/architecture/RAG-ARCHITECTURE.md](docs/architecture/RAG-ARCHITECTURE.md) |
| Review everything added in 0.3.0 | [docs/release-notes-0.3.0.md](docs/release-notes-0.3.0.md) |
| Run validation before pushing | [`scripts/validate_release.py`](scripts/validate_release.py) |
| Get support or report issues | [SUPPORT.md](SUPPORT.md) |
| Contribute safely | [CONTRIBUTING.md](CONTRIBUTING.md), [CODE_OF_CONDUCT.md](CODE_OF_CONDUCT.md), and [SECURITY.md](SECURITY.md) |
| Agent/developer conventions | [CLAUDE.md](CLAUDE.md) |

## What is included

| Area | Current coverage |
|---|---|
| MCP tools | 270 core tools / 916 read-only optional starters / 1498 read-write optional starters indexed |
| Core servers | Central monitoring, configuration, operations, NAC, GLP, and RAG |
| Router | `find_tool`, `invoke_read_tool`, `invoke_tool`, optional convenience wrappers, and MCP prompts |
| RAG | Embedded LanceDB docs index + SQLite OpenAPI lookup; no Docker required |
| GLP | v1/v2beta1 devices and device groups, subscriptions, users, audit logs, workspaces, reporting, service catalog, guarded GLP GET, and feature-gated writes |
| Optional products | ClearPass Insight/OnGuard, Mist NAC/Marvis/Wired/WAN, Apstra session auth/connectivity templates, AOS8 migration planning, EdgeConnect compatibility diagnostics, and UXI guarded writes |
| Pipeline | 8-stage migration flow, AOS8 Classic/New Central migration planning, and SSID build/delete helpers |

![centralmcp platform and tool coverage](docs/assets/platform-coverage.svg)

### Tool catalog by backend

| Backend | Read-only catalog | Read-write catalog |
|---|---:|---:|
| Central configuration | 75 | 75 |
| Central monitoring | 77 | 77 |
| Central NAC | 34 | 34 |
| Central operations | 40 | 40 |
| GreenLake Platform | 41 | 41 |
| RAG/OpenAPI | 3 | 3 |
| ClearPass | 9 | 15 |
| Juniper Mist | 19 | 26 |
| Apstra | 15 | 20 |
| ArubaOS 8 | 34 | 43 |
| EdgeConnect | 32 | 49 |
| HPE Aruba UXI | 13 | 25 |
| **Total** | **392** | **448** |

## Why the router matters

Point your MCP client at **one** server: `mcp_servers/tool_router.py`.

The recommended `minimal` router profile keeps the MCP tool list small while still giving access to the larger backend catalog:

1. Use `find_tool` to discover the right backend tool.
2. Use `invoke_read_tool` for read-only calls.
3. Use `invoke_tool` only for intentional write/destructive calls.

`invoke_tool` is deliberately marked destructive because it can dispatch destructive backend tools. This gives MCP clients a safer warning boundary without loading hundreds of direct tools into context.

## Try it locally without credentials

You can verify the install, build the router catalog, and start the MCP HTTP
server before adding Aruba Central or GreenLake credentials. API-backed tools
will need credentials later, but the local setup path is safe to test first.

```bash
git clone https://github.com/secure-ssid/centralmcp.git
cd centralmcp
python3 scripts/setup_wizard.py --yes --skip-credentials
uv run python scripts/doctor.py
MCP_PORT=8010 bash scripts/run_http_router.sh
```

Then connect an MCP-capable AI client to:

```text
http://127.0.0.1:8010/mcp
```

## Quick start

```bash
git clone https://github.com/secure-ssid/centralmcp.git
cd centralmcp

python3 scripts/setup_wizard.py
```

The wizard can run `uv sync`, write local MCP client configs, pick a Central API
gateway region, fill credentials without echoing secrets, enable optional
products, build the router catalog, and run the local doctor.

Review:

- `config/credentials.yaml` with your Central / GLP OAuth credentials.
- `.env` if you enabled ClearPass, Mist, Apstra, AOS8, EdgeConnect, or UXI.
- `.mcp.json` if you want to tune the generated stdio MCP client config.
- `.mcp.http.json` if your MCP client connects to an already-running
  streamable HTTP server instead of launching stdio.
- `.claude/launch.json` if you use those launch profiles; choose the minimal
  `aruba-tool-router` profile for daily use.

Build the lightweight router tool index:

```bash
uv run python scripts/ingest_tools.py
```

For optional product starters too:

```bash
uv run python scripts/ingest_tools.py --products all
```

That safe default indexes the 392 read-only catalog. To include all 448
guarded write tools, set `CENTRALMCP_PRODUCT_ACCESS=read-write` while rebuilding.

For full RAG docs/API search, download the prebuilt release index:

```bash
uv run python scripts/download_indexes.py
```

To rebuild locally, populate the git-ignored `ingestion/sources/` tree with
scraped docs/API source files first. Aruba's July 2026 developer-portal
migration is handled through the page `oasPublicUrl` pointer and ReadMe API
registry rather than the retired internal-UI JSON URLs:

```bash
uv run python ingestion/scrape_openapi.py
uv run python ingestion/scrape_cnac_spec.py
uv run python ingestion/fetch_mist_openapi.py
uv run python ingestion/ingest_docs.py
```

After a refresh, use `uv run python scripts/check_openapi_drift.py` to compare
the generated registry manifest with the current Aruba specifications, and
`uv run python scripts/check_mist_openapi_drift.py` to check the pinned
official `mistsys/mist_openapi` snapshot. Both checks run weekly in CI.

RAG source targets are tracked in
[`ingestion/source_manifest.json`](ingestion/source_manifest.json), including
DevHub, New Central techdocs, and the Switching Feature Navigator seeds.

Check the local setup without making API calls:

```bash
uv run python scripts/doctor.py
```

The doctor reports missing local stdio/HTTP MCP config copies, index files,
RAG source-manifest drift, placeholder stdio paths, low-token router profile
drift, HTTP config URL or transport mismatches, optional product env, and HTTP
listener status without calling Central or GLP APIs.

See [docs/getting-started.md](docs/getting-started.md) for the full setup path.

## Default MCP client profile

The committed client examples are intentionally lean:

```env
CENTRALMCP_ROUTER_MODE=minimal
CENTRALMCP_TOOLSETS=central,glp,rag
```

Enable optional products only when needed:

```env
CENTRALMCP_PRODUCTS=clearpass,mist,apstra,aos8,edgeconnect,uxi
CENTRALMCP_PRODUCT_ACCESS=read-only
```

The setup wizard can enable a subset for you, write the matching local `.env`,
and add only the product selector to local stdio MCP configs so tokens stay in
one local file:

```bash
python3 scripts/setup_wizard.py --products clearpass,mist
```

The optional product tools are lab-friendly. Every optional backend now has
guarded writes that default to `dry_run=True` with `confirm=True` required for
execution. Optional product access defaults to `read-only`, which hides and
blocks optional product write tools. Set `CENTRALMCP_PRODUCT_ACCESS=read-write`
or a narrower `CENTRALMCP_<PLATFORM>_WRITES=1` override only for trusted lab
workflows.

## Streamable HTTP mode

The MCP server is model-agnostic: any AI client/model that supports MCP
streamable HTTP can connect to the same router endpoint.

Start the low-token HTTP router in the foreground. The helper defaults to port
`8010`, matching the HTTP client example:

```bash
MCP_PORT=8010 bash scripts/run_http_router.sh
```

Then point your MCP client at:

```text
http://127.0.0.1:8010/mcp
```

Use [`.mcp.http.json.example`](.mcp.http.json.example) as a generic HTTP client
snippet. Plain `curl` is only useful for checking that the server is listening;
real MCP clients use streaming headers such as `Accept: text/event-stream`.
The helper loads local `.env` assignments first, so optional product settings created by
the wizard are available to HTTP mode too.
If the port is already in use, the helper exits before starting another router
and prints the listener details plus the `kill <PID>` stop command.

HTTP mode also exposes local `/livez`, `/readyz`, and `/healthz` probes without
calling external platforms. Non-loopback binds require explicit host/origin
allow-lists; set `MCP_HTTP_BEARER_TOKEN` to require a shared bearer token.

## Common environment variables

| Variable | Purpose | Default |
|---|---|---|
| `CREDS_PATH` | Credentials YAML path | `config/credentials.yaml` |
| `TOKEN_CACHE_DIR` | OAuth token cache directory | `~/.cache/centralmcp/` |
| `CENTRALMCP_ROUTER_MODE` | Router mode: `minimal` or `default`; examples use `minimal` for low-token clients | `default` |
| `CENTRALMCP_TOOLSETS` | Loaded backend profiles; examples use `central,glp,rag` | all core Aruba backends |
| `CENTRALMCP_PRODUCTS` | Optional product backends | empty |
| `CENTRALMCP_PRODUCT_ACCESS` | Optional product write-tool visibility: `read-write` or `read-only` | `read-only` |
| `CENTRALMCP_<PLATFORM>_WRITES` | Per-platform write override for Central, AOS8, EdgeConnect, Apstra, Mist, ClearPass, or UXI | platform default |
| `CENTRALMCP_GLP_V2BETA1_WRITES` | Enable guarded GLP write tools | off |
| `CENTRALMCP_TROUBLESHOOTING_API_VERSION` | Pin troubleshooting API to `v1` or legacy `v1alpha1` | `v1` with fallback |
| `CENTRALMCP_TOKENIZE_SECRETS` | Enable bounded session-scoped secret tokenization middleware | off |
| `CENTRALMCP_NORMALIZE_MACS` | Normalize outbound MAC strings in router responses | off |
| `GLP_TOKEN_URL` | Override GLP SSO token URL | HPE default |
| `GLP_BASE_URL` | Override GLP API base URL | HPE default |
| `MCP_TRANSPORT` | `stdio` or `streamable-http` | `stdio` |
| `MCP_HOST` | HTTP bind address for streamable HTTP mode | `127.0.0.1` |
| `MCP_PORT` | HTTP port for streamable HTTP mode; `scripts/run_http_router.sh` defaults to `8010` | `8010` |
| `MCP_ALLOWED_HOSTS` / `MCP_ALLOWED_ORIGINS` | Required explicit allow-lists for safe non-loopback HTTP binding | loopback-only |
| `MCP_HTTP_BEARER_TOKEN` | Optional bearer token protecting all streamable HTTP routes | unset |

Product starter backends also use product-specific URL/token variables. See [docs/getting-started.md](docs/getting-started.md).

## Project layout

```text
.claude/                 Optional launch profiles and repo agent notes
.cursor/                 Cursor MCP profiles: router default and direct-server dev mode
.vscode/                 VS Code MCP example config
config/                  Credentials template; real credentials stay git-ignored
docker-compose.yml       Optional localhost-only Redis/Ollama server backend for power users

mcp_servers/
  tool_router.py        Low-token MCP entrypoint
  prompts.py            Guided MCP prompt templates
  monitoring.py         Central health, alerts, events, clients, devices
  config.py             SSIDs, VLANs, profiles, webhooks, firmware
  ops.py                Ping, traceroute, show, reboot, PoE, cable test
  nac.py                MAC reg, MPSK, visitors, AAA, auth policies
  glp.py                GreenLake Platform tools
  rag.py                ask_docs, search_docs, lookup_api
  clearpass.py          Optional ClearPass starter backend
  mist.py               Optional Mist starter backend
  apstra.py             Optional Apstra starter backend
  aos8.py               Optional ArubaOS 8 starter backend
  edgeconnect.py        Optional EdgeConnect starter backend
  uxi.py                Optional HPE Aruba UXI starter backend

ingestion/
  ingest_docs.py        Build docs/API indexes into LanceDB + SQLite

pipeline/
  clients/              httpx clients, token manager, LanceDB, SQLite specs
  stages/               8-stage migration pipeline

docs/
  getting-started.md    Setup and MCP connection guide
  tool-router.md        Router modes and low-token usage
  architecture/         System overview and RAG design notes

inputs/                  Example CSV inputs for migration workflows
resources/               Postman/API reference material and resource notes
ingestion/source_manifest.json  RAG source seed URLs and source folders
scripts/                 Tool catalog ingest, local doctor, HTTP router helper, release validation
tests/                   Unit, integration, and RAG eval tests

.mcp.json.example        Generic stdio MCP client example using the minimal router
.mcp.http.json.example   Generic streamable HTTP MCP client example
run_pipeline.py          Migration pipeline CLI
run_ssid.py              SSID helper CLI
```

## RAG and API lookup

The default RAG stack is embedded:

| Index | File | Tool | Purpose |
|---|---|---|---|
| Docs | `data/docs.lance` | `search_docs`, `ask_docs` | Hybrid retrieval over Aruba/HPE docs |
| API specs | `data/specs.sqlite` | `lookup_api` | Exact endpoint/schema/enum lookup |
| Tools | `data/tools.lance` | `find_tool` | Semantic router tool discovery |

Current rebuilt index snapshot:

| Content | Count |
|---|---:|
| Prose chunks | 47,633 |
| OpenAPI specs | 239 |
| Exact endpoints | 3,465 |
| Schemas | 10,297 |
| Fields | 57,131 |

Measured on the bundled eval set:

| Metric | Result |
|---|---:|
| `api_exact` | 1.00 |
| `howto_recall@5` | 0.90 |
| `mrr` | 0.90 |

## Safety model

- Credentials stay in `config/credentials.yaml` or environment variables; do not commit real credentials.
- Token caches live under `~/.cache/centralmcp/` by default with `0600` permissions.
- GLP v2beta1 writes fail closed unless `CENTRALMCP_GLP_V2BETA1_WRITES=1`.
- Optional product writes fail closed unless `CENTRALMCP_PRODUCT_ACCESS=read-write`.
- Destructive Central operations use MCP elicitation/confirmation where supported.
- The router's `invoke_read_tool` blocks non-read-only backend tools.
- The generic router `invoke_tool` is marked destructive because it can reach write/destructive tools.
- `find_tool` omits full JSON schemas by default; request `include_schema=true` only when needed.
- Generic GLP and optional product GET tools bound list responses with `limit` / `offset`.
- MCP tool list defaults are capped at 200 items to protect client context windows.
- Report vulnerabilities and accidental credential exposure through [SECURITY.md](SECURITY.md); do not publish real tokens, tenant IDs, or customer data in issues or PRs.

## Validation

Run unit tests:

```bash
uv run pytest tests/unit -q
```

Run the local release gate:

```bash
uv run python scripts/validate_release.py --catalog-products all --strict-rag --strict-tool-index --min-tools 448
```

The release helper runs unit tests, optional RAG/API eval when indexes exist, tool catalog floor checks, and local tool-index freshness checks. Unit tests also include static guards for the active MCP/pipeline code, committed low-token MCP config examples, local-only config files, router product/toolset docs, bounded generic read-only GET tools, MCP list default bounds, RAG/search top_k bounds, public tool-count claims, tool-count docstrings, tracked Markdown local links and images, Pages sitemap and robots metadata, documented router example arguments, product workflow tool-name tables, and wizard optional-product env tables.

## Related projects

With appreciation to these projects and maintainers for official APIs, MCP
patterns, and community references that helped shape centralmcp's low-token,
lab-friendly direction:

- [HewlettPackard/gl-mcp](https://github.com/HewlettPackard/gl-mcp) - official GreenLake Platform MCP server
- [modelcontextprotocol/python-sdk](https://github.com/modelcontextprotocol/python-sdk) - MCP Python SDK
- [mistsys/mist_openapi](https://github.com/mistsys/mist_openapi) - official Mist OpenAPI source
- [KarthikSKumar98/central-mcp-server](https://github.com/KarthikSKumar98/central-mcp-server) - community Aruba Central MCP server
- [nowireless4u/hpe-networking-mcp](https://github.com/nowireless4u/hpe-networking-mcp) - unified HPE networking MCP reference

## Disclaimer

This is an independent community project. It is not an official HPE or HPE Aruba Networking product and is not endorsed by or supported by HPE.

## License

MIT - see [LICENSE](LICENSE).

Generated API metadata and upstream implementation references are documented in
[THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md).
