# centralmcp — HPE Aruba Central & GreenLake MCP Server

[![License](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)
[![Python](https://img.shields.io/badge/python-3.10+-blue.svg)](https://www.python.org/)
[![MCP](https://img.shields.io/badge/MCP-Model%20Context%20Protocol-brightgreen)](https://modelcontextprotocol.io/)
[![Tested against live lab](https://img.shields.io/badge/tested-live%20lab-success)](#running-tests)

> **Model Context Protocol (MCP) server** for **HPE Aruba Networking Central (New Central)** and **HPE GreenLake Platform (GLP)**. Drive real network operations from any MCP-capable AI client — conversationally.

Python tooling for Aruba Central New-Central / NBAPI: monitoring, configuration, troubleshooting, NAC, GLP device lifecycle, doc-grounded RAG, and an 8-stage cross-account migration pipeline. Ships as **6 FastMCP domain servers plus a unified tool-router** for AI agents, and as **standalone CLI tools** for batch workflows.

**Keywords** (for GitHub search):
`aruba-central` · `new-central` · `nbapi` · `greenlake` · `hpe-greenlake` · `hpe-aruba` · `mcp-server` · `model-context-protocol` · `fastmcp` · `network-automation` · `network-config` · `switch-automation` · `wifi-automation` · `pycentral` · `aruba-api` · `aruba-networking` · `llm-tools` · `ai-for-networking`

---

## What it gives you

| Surface | Count | What |
|---|---|---|
| **MCP tool servers** | 6 + router | `aruba-monitoring`, `aruba-config`, `aruba-ops`, `aruba-nac`, `aruba-glp`, `aruba-rag` — optionally fronted by `aruba-tool-router` |
| **MCP tools** | 150 | Read + write across Central and GLP, plus doc-grounded search and exact API lookup |
| **Migration pipeline stages** | 8 | Discover → verify → transfer → configure → attest |
| **Supported device types** | AP / CX / AOS-S / Gateway | Full troubleshoot + provisioning surface |
| **GLP operations** | Devices / Subscriptions / Users / Audit logs | v2beta1 PATCH writes behind a feature flag |
| **RAG corpus** | 53k chunks + 213 API specs | Dev docs, tech docs, NAC/VSG guides, OpenAPI specs — **fully embedded** (LanceDB + SQLite + fastembed), no servers |
| **Local knowledge vault** | Obsidian MCP | Aruba docs + personal runbooks, locally accessible to any MCP client |

### Feature highlights

- **150 MCP tools** across 6 FastMCP servers
- **`aruba-tool-router`** — single MCP entrypoint that proxies all 6 domain servers, reducing tool-listing overhead. Use the router day-to-day; switch to `.cursor/mcp.dev.json` for per-server debugging.
- **Embedded doc-grounded RAG — no Docker, no servers** — `search_docs` runs hybrid (vector + BM25) retrieval over 53k chunks of Aruba/HPE developer docs, tech docs, NAC/VSG guides, and OpenAPI specs via LanceDB + in-process fastembed embeddings. Measured: `recall@5` 0.90, `mrr` 0.90 on the bundled eval set.
- **`lookup_api` — exact API answers** — endpoint/schema/enum questions answered losslessly from a SQLite index over 213 parsed OpenAPI specs (1,071 endpoints, 29k fields). Measured: 10/10 exact on the bundled eval set. Vector search guesses; this doesn't.
- **Obsidian vault MCP** — local filesystem MCP server over a personal Obsidian vault for runbooks and reference docs
- **MCP ToolAnnotations** — all tools tagged `READ_ONLY`, `DIAGNOSTIC`, or `DESTRUCTIVE` so clients can display safety hints
- **Elicitation for destructive ops** — `reboot_device`, `poe_bounce`, `port_bounce`, `disconnect_client` prompt for confirmation before executing
- **8-stage migration pipeline** — Discover → verify → push to New Central
- **SSID build/delete** with scope-map targeting (org-wide, site, device-group)
- **Switch provisioning** — VLANs, port profiles, SVIs, PoE management
- **GreenLake Platform** — device lifecycle (archive, unarchive, subscription assign/unassign via v2beta1 PATCH)
- **Async troubleshooting** — ping, traceroute, `show` commands, cable test, PoE bounce, reboot
- **NAC tooling** — MAC registrations, Named MPSK, visitors, AAA profiles, authorization policies
- **Built-in reliability** — `Retry-After` aware 429 handling, 5xx backoff with jitter
- **Token cache hardening** — 0600 perms, `~/.cache/centralmcp/` by default, per-client expiry buffer
- **Feature flags for writes** — every destructive path is opt-in (`CENTRALMCP_GLP_V2BETA1_WRITES=1`)

---

## Why this exists

Aruba publishes [`pycentral`](https://github.com/aruba/pycentral) for classic Central, and HPE publishes [`gl-mcp`](https://github.com/HewlettPackard/gl-mcp) for the GreenLake Platform. There's no single MCP server that:

1. Covers both **New Central** and **GLP** in one place,
2. Wraps 149 concrete tools with proper FastMCP surfacing,
3. Ships with production-grade reliability middleware (rate-limit, retry, null-strip), **and**
4. Includes a real cross-account migration pipeline.

centralmcp fills that gap.

---

## Prerequisites

- Python >= 3.10
- [`uv`](https://docs.astral.sh/uv/) (recommended) or `pip`
- HPE Aruba Central account with API credentials (OAuth2 client ID + secret)
- (Optional) HPE GreenLake Platform client credentials for GLP tools

No Docker, no database servers — the RAG stack is fully embedded (LanceDB + SQLite + in-process fastembed embeddings).

---

## Setup

```bash
# 1. Clone and install dependencies
git clone https://github.com/secure-ssid/centralmcp.git
cd centralmcp
uv sync

# 2. Configure credentials
cp config/credentials.yaml.example config/credentials.yaml
#    Edit — fill in central_account.{client_id,client_secret,base_url}
#           and glp_account.{client_id,client_secret,glp_workspace_id}

# 3. Configure MCP client
cp .mcp.json.example .mcp.json
#    Edit .mcp.json — replace /path/to/centralmcp with your clone path

# 4. Get the RAG indexes (-> data/)
#    Option A — download prebuilt (fast):
curl -L -o /tmp/rag-index.tar.gz \
  https://github.com/secure-ssid/centralmcp/releases/download/v0.2.0/centralmcp-rag-index-v0.2.0.tar.gz
tar -xzf /tmp/rag-index.tar.gz   # extracts data/ into the repo root

#    Option B — rebuild locally (re-scrapes nothing; embeds 53k chunks
#      in-process — several hours on CPU):
uv run python scripts/ingest_tools.py     # find_tool catalog (~1 min)
uv run python ingestion/ingest_docs.py    # docs + API specs (slow)
```

The embedding model (`nomic-embed-text-v1.5`, ~250 MB ONNX) downloads to the
Hugging Face cache on first use.

> **Security:** `config/credentials.yaml` and `.mcp.json` are git-ignored. Never commit them. Token caches live in `~/.cache/centralmcp/` (`0600` perms) by default.

### Environment variables

| Var | Purpose | Default |
|---|---|---|
| `CREDS_PATH` | Override credentials YAML location | `config/credentials.yaml` |
| `TOKEN_CACHE_DIR` | Override OAuth token cache directory | `~/.cache/centralmcp/` |
| `CENTRALMCP_GLP_V2BETA1_WRITES` | Enable `PATCH /devices/v2beta1/devices` GLP writes | off |
| `CENTRALMCP_BOUND_LISTS` | Wrap list tool responses as `{items, _pagination}` | off |
| `MCP_TRANSPORT` | Server transport: `stdio` (default) or `streamable-http` | `stdio` |
| `MCP_HOST` | Bind address for HTTP transport | `127.0.0.1` |
| `MCP_PORT` | Port for HTTP transport | `8000` |
| `GLP_TOKEN_URL` | Override SSO token endpoint | `https://sso.common.cloud.hpe.com/as/token.oauth2` |
| `GLP_BASE_URL` | Override GLP API base URL | `https://global.api.greenlake.hpe.com` |
| `CENTRALMCP_RAG_BACKEND` | RAG backend: `lancedb` (embedded) or `redis` (server) | `lancedb` |
| `CENTRALMCP_EMBED_PROVIDERS` | ONNX execution providers for embedding (e.g. `cuda`) | CPU |
| `REDIS_URL` | Redis Stack connection (only with `CENTRALMCP_RAG_BACKEND=redis`) | `redis://localhost:6379` |

---

## Usage

### MCP client integration

With `.mcp.json` configured, start your MCP client from this directory — the servers load automatically.

Example prompts:

- *"List all devices at the Home Lab site."*
- *"Build a WPA3 SSID called `Corp-WiFi` on VLAN 100 for all APs."*
- *"Ping 8.8.8.8 from switch SN123456."*
- *"Show me active alerts at sites in Frankfurt."*
- *"What enum values does the SSID `opmode` field accept?"* (exact answer via `lookup_api`)
- *"Assign subscription `sub-uuid-123` to device `SG30LMR164`."* (requires the GLP writes flag)

### CLI — Migration pipeline

```bash
python run_pipeline.py --input inputs/devices.csv
```

Runs the full 8-stage migration (discover → assign → configure → verify). Idempotent — safe to re-run.

### CLI — SSID builder

```bash
python run_ssid.py
```

Interactive SSID build/delete workflow.

---

## Project layout

```
mcp_servers/
  monitoring.py         Monitoring tools (health, trends, wireless metrics)
  config.py             Config tools (SSIDs, VLANs, profiles, webhooks, firmware)
  ops.py                Ops tools (reboots, ping, cable test, PoE bounce)
  nac.py                NAC tools (MAC reg, MPSK, visitors, auth servers, AAA)
  glp.py                GreenLake Platform tools
  rag.py                RAG tools — search_docs (hybrid) + lookup_api (exact)
  tool_router.py        Unified router — proxies the 6 domain servers
  shared.py             Shared clients, helpers, pagination, feature flags
pipeline/
  clients/              CentralClient, GLPClient, TokenManager, EmbedClient,
                        LanceClient, SpecsIndex (+ optional Ollama/Redis clients)
  stages/               s1_discover → s8_verify
  config.py             Credential loader
  create_ssid.py        SSID build/delete logic (underlay + overlay)
ingestion/
  ingest_docs.py        Chunk + embed docs → LanceDB + specs SQLite (default)
  sources/              Scraped docs (git-ignored — regenerate with scrapers)
data/                   RAG indexes (git-ignored): docs.lance, tools.lance,
                        specs.sqlite — rebuild via ingest or download prebuilt
config/
  credentials.yaml.example   Template — copy to credentials.yaml and fill in
docker-compose.yml      OPTIONAL server RAG backend (Redis Stack + Ollama)
docs/                   Reference documents (audit, RAG architecture, eval)
resources/              Postman download script (collections git-ignored)
inputs/                 CSV templates for batch migration
tests/                  Unit tests + RAG eval harness (tests/eval/)
```

---

## RAG stack

The default RAG backend is **fully embedded** — nothing to install or run:

| Index | File | Backs | What |
|---|---|---|---|
| Docs | `data/docs.lance` (190 MB) | `search_docs` | 53,052 chunks across 7 sources, hybrid vector + BM25 search (RRF-fused), nomic task prefixes |
| API specs | `data/specs.sqlite` (18 MB) | `lookup_api` | 213 OpenAPI specs parsed to 1,071 endpoints / 4,958 schemas / 29k fields with FTS5 — exact, lossless answers for "what enum values / which endpoint / what fields" questions |
| Tools | `data/tools.lance` (0.6 MB) | `find_tool` | the 150-tool catalog for the router's semantic tool search |

Measured on the bundled eval set (`tests/eval/`) vs the previous Redis vector-only stack: `api_exact` 0.50 → **1.00**, `howto_recall@5` 0.80 → **0.90**, `mrr` 0.34 → **0.90**.

### Optional server backend (Redis Stack + Ollama)

The pre-migration server stack remains supported for deployments that want a
shared index: `docker-compose up -d`, ingest with
`python ingestion/ingest_docs.py --backend redis` and
`python scripts/ingest_tools.py --backend redis`, then run the MCP servers
with `CENTRALMCP_RAG_BACKEND=redis`.

---

## Reliability guarantees

- **429 retry** — parses `Retry-After` (seconds or HTTP-date); falls back to 60s→300s legacy backoff.
- **5xx retry** — 502/503/504 retried for GET/HEAD with exponential backoff + ±20% jitter; POST/PATCH opt in via `retry_5xx=True`.
- **Null-strip middleware** — drops top-level `None` args before validation so clients that send `null` for optional params don't fail Pydantic.
- **Stable tool ordering** — every server sorts tools alphabetically so prompt-cache prefixes stay stable across restarts.

---

## Running tests

```bash
pytest tests/
```

All tests use mocked HTTP — no real API calls.

---

## Documentation

See [CLAUDE.md](CLAUDE.md) for:

- Full MCP tool reference and verb/noun naming conventions
- Scope and device-type translation rules
- API endpoint patterns (New Central, GLP)
- Token cost and cache optimization tips
- Known broken endpoints and workarounds

---

## Contributing

Issues and PRs welcome — please open an issue first for anything non-trivial so we can sync on scope.

### Safety

- Never commit credentials. `config/credentials.yaml` is git-ignored.
- Never enable `CENTRALMCP_GLP_V2BETA1_WRITES=1` against a production workspace until you've sandbox-validated the payload + rollback.
- Token cache files (`.token_cache_*.json`) are git-ignored and stored with `0600` perms.

---

## Related projects

- [`HewlettPackard/gl-mcp`](https://github.com/HewlettPackard/gl-mcp) — HPE's official GreenLake Platform MCP server
- [`aruba/pycentral`](https://github.com/aruba/pycentral) — Aruba's Python SDK for classic + new Central
- [`modelcontextprotocol/python-sdk`](https://github.com/modelcontextprotocol/python-sdk) — the MCP Python SDK centralmcp builds on
- [`KarthikSKumar98/central-mcp-server`](https://github.com/KarthikSKumar98/central-mcp-server) — community Aruba Central MCP server
- [`nowireless4u/hpe-networking-mcp`](https://github.com/nowireless4u/hpe-networking-mcp) — unified HPE networking MCP covering Mist + Central + GreenLake

---

## Disclaimer

This is an **independent, community-built tool** and is **not an official HPE or HPE Aruba Networking product**. It is not endorsed by, affiliated with, or supported by HPE. Use at your own risk.

---

## License

MIT — see [LICENSE](LICENSE).

---

## Security

- Credentials load at runtime from `config/credentials.yaml` (git-ignored)
- No secrets are hardcoded in source files
- Token cache files are git-ignored and written with `0600` perms
- MCP server config (`.mcp.json`) is git-ignored — it contains local paths

Report security issues via [GitHub Issues](../../issues) — do not include credentials in bug reports.
