# centralmcp

[![License](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)
[![Python](https://img.shields.io/badge/python-3.10+-blue.svg)](https://www.python.org/)
[![MCP](https://img.shields.io/badge/MCP-FastMCP-brightgreen)](https://modelcontextprotocol.io/)

**HPE Aruba Central + GreenLake Platform MCP server and automation toolkit.**

centralmcp gives MCP-capable AI clients a low-token way to search Aruba/HPE docs, look up exact OpenAPI details, inspect Central health, run troubleshooting workflows, manage configuration, and use guarded GreenLake Platform operations.

It is built around direct REST calls with `httpx`. `pycentral` is not a runtime dependency.

## Quick links

| Need | Start here |
|---|---|
| Install and connect an MCP client | [docs/getting-started.md](docs/getting-started.md) |
| Understand the low-token router | [docs/tool-router.md](docs/tool-router.md) |
| Browse the documentation map | [docs/README.md](docs/README.md) |
| Review the RAG design | [docs/architecture/RAG-ARCHITECTURE.md](docs/architecture/RAG-ARCHITECTURE.md) |
| Run validation before pushing | [`scripts/validate_release.py`](scripts/validate_release.py) |
| Agent/developer conventions | [CLAUDE.md](CLAUDE.md) |

## What is included

| Area | Current coverage |
|---|---|
| MCP tools | 194 core tools, or 204 with optional product starters indexed |
| Core servers | Central monitoring, configuration, operations, NAC, GLP, and RAG |
| Router | `find_tool`, `invoke_read_tool`, `invoke_tool`, optional convenience wrappers, and MCP prompts |
| RAG | Embedded LanceDB docs index + SQLite OpenAPI lookup; no Docker required |
| GLP | Devices, subscriptions, users, audit logs, guarded read-only GLP GET, and feature-gated writes |
| Optional products | ClearPass, Mist, Apstra, AOS8, and EdgeConnect starter backends |
| Pipeline | 8-stage migration flow plus SSID build/delete helpers |

## Why the router matters

Point your MCP client at **one** server: `mcp_servers/tool_router.py`.

The recommended `minimal` router profile keeps the MCP tool list small while still giving access to the larger backend catalog:

1. Use `find_tool` to discover the right backend tool.
2. Use `invoke_read_tool` for read-only calls.
3. Use `invoke_tool` only for intentional write/destructive calls.

`invoke_tool` is deliberately marked destructive because it can dispatch destructive backend tools. This gives MCP clients a safer warning boundary without loading hundreds of direct tools into context.

## Quick start

```bash
git clone https://github.com/secure-ssid/centralmcp.git
cd centralmcp
uv sync

cp config/credentials.yaml.example config/credentials.yaml
cp .mcp.json.example .mcp.json
```

Edit:

- `config/credentials.yaml` with your Central / GLP OAuth credentials.
- `.mcp.json` and replace `/path/to/centralmcp` with your local clone path.
- `.claude/launch.json` if you use Claude launch profiles; choose the minimal
  `aruba-tool-router` profile for daily use.

Build the lightweight router tool index:

```bash
uv run python scripts/ingest_tools.py
```

For optional product starters too:

```bash
uv run python scripts/ingest_tools.py --products all
```

For full RAG docs/API search, download the prebuilt release index if available or rebuild locally:

```bash
uv run python ingestion/ingest_docs.py
```

See [docs/getting-started.md](docs/getting-started.md) for the full setup path.

## Default MCP client profile

The committed client examples are intentionally lean:

```env
CENTRALMCP_ROUTER_MODE=minimal
CENTRALMCP_TOOLSETS=central,glp,rag
```

Enable optional products only when needed:

```env
CENTRALMCP_PRODUCTS=clearpass,mist,apstra,aos8,edgeconnect
```

The optional product starter GET tools are read-only and page list responses with
`limit` / `offset` so broad API calls do not flood the MCP context.

## Common environment variables

| Variable | Purpose | Default |
|---|---|---|
| `CREDS_PATH` | Credentials YAML path | `config/credentials.yaml` |
| `TOKEN_CACHE_DIR` | OAuth token cache directory | `~/.cache/centralmcp/` |
| `CENTRALMCP_ROUTER_MODE` | Router mode: `minimal` or `default`; examples use `minimal` for low-token clients | `default` |
| `CENTRALMCP_TOOLSETS` | Loaded backend profiles; examples use `central,glp,rag` | all core Aruba backends |
| `CENTRALMCP_PRODUCTS` | Optional product backends | empty |
| `CENTRALMCP_GLP_V2BETA1_WRITES` | Enable guarded GLP write tools | off |
| `CENTRALMCP_NORMALIZE_MACS` | Normalize outbound MAC strings in router responses | off |
| `GLP_TOKEN_URL` | Override GLP SSO token URL | HPE default |
| `GLP_BASE_URL` | Override GLP API base URL | HPE default |
| `MCP_TRANSPORT` | `stdio` or `streamable-http` | `stdio` |

Product starter backends also use product-specific URL/token variables. See [docs/getting-started.md](docs/getting-started.md).

## Project layout

```text
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

pipeline/
  clients/              httpx clients, token manager, LanceDB, SQLite specs
  stages/               8-stage migration pipeline

docs/
  getting-started.md    Setup and MCP connection guide
  tool-router.md        Router modes and low-token usage
  architecture/         RAG and architecture notes
  audits/               Historical audits and remediation notes
  operations/           Endpoint/runbook notes
  plans/                Planning documents

.vscode/
  mcp.json.example      VS Code MCP example using the minimal router profile
```

## RAG and API lookup

The default RAG stack is embedded:

| Index | File | Tool | Purpose |
|---|---|---|---|
| Docs | `data/docs.lance` | `search_docs`, `ask_docs` | Hybrid retrieval over Aruba/HPE docs |
| API specs | `data/specs.sqlite` | `lookup_api` | Exact endpoint/schema/enum lookup |
| Tools | `data/tools.lance` | `find_tool` | Semantic router tool discovery |

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
- Destructive Central operations use MCP elicitation/confirmation where supported.
- The router's `invoke_read_tool` blocks non-read-only backend tools.
- The generic router `invoke_tool` is marked destructive because it can reach write/destructive tools.
- `find_tool` omits full JSON schemas by default; request `include_schema=true` only when needed.
- Generic GLP and optional product GET tools bound list responses with `limit` / `offset`.
- MCP tool list defaults are capped at 200 items to protect client context windows.

## Validation

Run unit tests:

```bash
uv run pytest tests/unit -q
```

Run the local release gate:

```bash
uv run python scripts/validate_release.py
```

The release helper runs unit tests, optional RAG/API eval when indexes exist, tool catalog floor checks, and local tool-index freshness checks. Unit tests also include static guards for the active MCP/pipeline code, committed low-token MCP config examples, local-only config files, router product/toolset docs, bounded generic read-only GET tools, MCP list default bounds, public tool-count claims, tool-count docstrings, and tracked Markdown local links.

## Related projects

- [HewlettPackard/gl-mcp](https://github.com/HewlettPackard/gl-mcp) - official GreenLake Platform MCP server
- [modelcontextprotocol/python-sdk](https://github.com/modelcontextprotocol/python-sdk) - MCP Python SDK
- [KarthikSKumar98/central-mcp-server](https://github.com/KarthikSKumar98/central-mcp-server) - community Aruba Central MCP server
- [nowireless4u/hpe-networking-mcp](https://github.com/nowireless4u/hpe-networking-mcp) - unified HPE networking MCP reference

## Disclaimer

This is an independent community project. It is not an official HPE or HPE Aruba Networking product and is not endorsed by or supported by HPE.

## License

MIT - see [LICENSE](LICENSE).
