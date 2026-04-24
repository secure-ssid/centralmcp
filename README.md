# centralmcp — HPE Aruba Central & GreenLake MCP Server

[![License](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)
[![Python](https://img.shields.io/badge/python-3.10+-blue.svg)](https://www.python.org/)
[![MCP](https://img.shields.io/badge/MCP-Model%20Context%20Protocol-brightgreen)](https://modelcontextprotocol.io/)
[![Tested against live lab](https://img.shields.io/badge/tested-live%20lab-success)](#running-tests)

> **Model Context Protocol (MCP) server** for **HPE Aruba Networking Central
> (New Central)** and **HPE GreenLake Platform (GLP)**. Drive real network
> operations from Claude, Claude Code, Claude Desktop, or any MCP-capable
> client — **conversationally**.

Python tooling for Aruba Central New-Central / NBAPI: monitoring, configuration,
troubleshooting, NAC, GLP device lifecycle, doc-grounded RAG, and an 8-stage
cross-account migration pipeline. Ships as **6 FastMCP domain servers plus a
unified tool-router** for AI agents, and as **standalone CLI tools** for batch
workflows.

**Keywords** (for GitHub search):
`aruba-central` · `new-central` · `nbapi` · `greenlake` · `hpe-greenlake` ·
`hpe-aruba` · `mcp-server` · `model-context-protocol` · `claude-code` ·
`claude-desktop` · `fastmcp` · `network-automation` · `network-config` ·
`switch-automation` · `wifi-automation` · `pycentral` · `aruba-api` ·
`aruba-networking` · `llm-tools` · `ai-for-networking`

---

## What it gives you

| Surface | Count | What |
|---|---|---|
| **MCP tool servers** | 6 + router | `aruba-monitoring`, `aruba-config`, `aruba-ops`, `aruba-nac`, `aruba-glp`, `aruba-rag` — optionally fronted by `aruba-tool-router` |
| **MCP tools** | ~140 | Read + write across Central and GLP, plus doc-grounded search |
| **Migration pipeline stages** | 8 | Discover → verify → transfer → configure → attest |
| **Supported device types** | AP / CX / AOS-S / Gateway | Full troubleshoot + provisioning surface |
| **GLP operations** | Devices / Subscriptions / Users / Audit logs | v2beta1 PATCH writes behind a feature flag |
| **RAG corpus** | Aruba/HPE docs | Dev docs, tech docs, NAC/VSG guides, OpenAPI specs indexed in Qdrant |

### Feature highlights

- 🧰 **~140 MCP tools** across 6 FastMCP servers (`aruba-monitoring`,
  `aruba-config`, `aruba-ops`, `aruba-nac`, `aruba-glp`, `aruba-rag`)
- 🔀 **`aruba-tool-router`** — a single MCP entrypoint that proxies to the 6
  domain servers, reducing client tool-listing tokens from ~8k to a routed
  interface. Use the router for day-to-day; fall back to `.cursor/mcp.dev.json`
  for per-server introspection when debugging.
- 📚 **Doc-grounded RAG** — `search_docs` over ingested Aruba/HPE developer
  docs, tech docs, NAC/VSG guides, and OpenAPI specs (Qdrant + Ollama stack,
  `docker-compose.yml` included; re-index via `scripts/ingest_tools.py`)
- 🚀 **8-stage migration pipeline** — Discover → verify → push to New Central
- 📶 **SSID build/delete** with scope-map targeting (org-wide, site,
  device-group)
- 🔌 **Switch provisioning** — VLANs, port profiles, SVIs, PoE management
- 🪪 **GreenLake Platform** — device lifecycle (archive, unarchive,
  subscription assign/unassign via v2beta1 PATCH)
- 🔧 **Async troubleshooting** — ping, traceroute, `show` commands, cable
  test, PoE bounce, reboot, LED-locate
- 🪝 **NAC tooling** — MAC registrations, Named MPSK, visitors, AAA
  profiles, authorization policies
- 🧱 **Built-in reliability** — `Retry-After` aware 429 handling, 5xx
  backoff with jitter, configurable token-bucket rate limit
  (default 8 req/s under Central's 10 req/s account cap)
- 🔒 **Token cache hardening** — 0600 perms, `~/.cache/centralmcp/`
  by default, per-client expiry buffer
- 🎯 **Feature flags for writes** — every destructive path is
  opt-in (`CENTRALMCP_GLP_V2BETA1_WRITES=1`)

---

## Why this exists

Aruba publishes [`pycentral`](https://github.com/aruba/pycentral) for classic
Central, and HPE publishes [`gl-mcp`](https://github.com/HewlettPackard/gl-mcp)
for the GreenLake Platform. There's no single MCP server that:

1. Covers both **New Central** and **GLP** in one place,
2. Wraps ~140 concrete tools with proper FastMCP surfacing,
3. Ships with production-grade reliability middleware (rate-limit,
   retry, null-strip), **and**
4. Includes a real cross-account migration pipeline.

centralmcp fills that gap.

---

## Prerequisites

- Python ≥ 3.10
- [`uv`](https://docs.astral.sh/uv/) (recommended) or `pip`
- HPE Aruba Central account with API credentials (OAuth2 client ID + secret)
- (Optional) HPE GreenLake Platform client credentials for GLP tools

---

## Setup

```bash
# 1. Clone and install dependencies
git clone https://github.com/secure-ssid/centralmcp.git
cd centralmcp
uv sync

# 2. Configure credentials (new naming; legacy source_account/target_account also accepted)
cp config/credentials.yaml.example config/credentials.yaml
#    Edit — fill in central_account.{client_id,client_secret,base_url}
#           and glp_account.{client_id,client_secret,glp_workspace_id}

# 3. Configure MCP servers (for Claude Code integration)
cp .mcp.json.example .mcp.json
#    Edit .mcp.json — replace /path/to/centralmcp with your clone path
```

> **Security:** `config/credentials.yaml` and `.mcp.json` are git-ignored.
> Never commit them. Token caches live in `~/.cache/centralmcp/`
> (`0600` perms) by default.

### Environment variables

| Var | Purpose | Default |
|---|---|---|
| `CREDS_PATH` | Override credentials YAML location | `config/credentials.yaml` |
| `TOKEN_CACHE_DIR` | Override OAuth token cache directory | `~/.cache/centralmcp/` |
| `CENTRALMCP_GLP_V2BETA1_WRITES` | Enable `PATCH /devices/v2beta1/devices` GLP writes | off |
| `CENTRALMCP_BOUND_LISTS` | Wrap list tool responses as `{items, _pagination}` | off |
| `GLP_TOKEN_URL` | Override SSO token endpoint | `https://sso.common.cloud.hpe.com/as/token.oauth2` |
| `GLP_BASE_URL` | Override GLP API base URL | `https://global.api.greenlake.hpe.com` |

---

## Usage

### Claude Code / Claude Desktop MCP integration

With `.mcp.json` configured, start Claude Code from this directory — the 5
MCP servers (`aruba-monitoring`, `aruba-config`, `aruba-ops`, `aruba-nac`,
`aruba-glp`) load automatically.

Example prompts:

- *"List all devices at the Home Lab site."*
- *"Build a WPA3 SSID called `Corp-WiFi` on VLAN 100 for all APs."*
- *"Ping 8.8.8.8 from switch SN123456."*
- *"Show me active alerts at sites in Frankfurt."*
- *"Assign subscription `sub-uuid-123` to device `SG30LMR164`."* (requires the GLP writes flag)

### CLI — Migration pipeline

```bash
python run_pipeline.py --input inputs/devices.csv
```

Runs the full 8-stage migration (discover → assign → configure → verify).
Idempotent — safe to re-run.

### CLI — SSID builder

```bash
python run_ssid.py
```

Interactive SSID build/delete workflow.

---

## Project layout

```
mcp_servers/
  _middleware/          NullStripMiddleware, RateLimitMiddleware, installer
  _cache_hygiene.py     Stable-sort tool listing for prompt-cache stability
  monitoring.py         Monitoring tools (health, trends, wireless metrics)
  config.py             Config tools (SSIDs, VLANs, profiles, webhooks, firmware)
  ops.py                Ops tools (reboots, ping, cable test, PoE bounce)
  nac.py                NAC tools (MAC reg, MPSK, visitors, auth servers, AAA)
  glp.py                GreenLake Platform tools (aruba-glp server)
  shared.py             Shared clients, helpers, pagination, feature flags
pipeline/
  clients/              CentralClient (429+5xx retry), GLPClient (v2beta1 PATCH), TokenManager
  stages/               s1_discover → s8_verify
  config.py             Credential loader (source_account + central_account aliases)
  create_ssid.py        SSID build/delete logic (underlay + overlay)
config/
  credentials.yaml.example   Template — copy to credentials.yaml and fill in
docs/                   Reference documents (including HPE support ticket drafts)
resources/              Postman download script (collections git-ignored — see resources/README.md)
inputs/                 CSV templates for batch migration
tests/                  Unit + integration tests
```

---

## Reliability guarantees

- **429 retry** — parses `Retry-After` (seconds or HTTP-date); falls back to
  60s→300s legacy backoff.
- **5xx retry** — 502/503/504 retried for GET/HEAD with exponential backoff
  + ±20% jitter; POST/PATCH opt in via `retry_5xx=True`.
- **Rate limiting** — token bucket at 8 req/s (below Central's 10 req/s
  account-wide cap).
- **Null-strip middleware** — drops top-level `None` args before
  validation so clients that send `null` for optional params don't fail
  Pydantic.
- **Stable tool ordering** — every server sorts tools alphabetically so
  prompt-cache prefixes stay stable across restarts.

---

## Running tests

```bash
pytest tests/
```

All tests use mocked HTTP — no real API calls. See
[`tests/unit/test_mcp_middleware.py`](tests/unit/test_mcp_middleware.py),
[`tests/unit/test_central_client_retry.py`](tests/unit/test_central_client_retry.py),
[`tests/unit/test_glp_v2beta1_writes.py`](tests/unit/test_glp_v2beta1_writes.py),
and [`tests/unit/test_bound_collection_response.py`](tests/unit/test_bound_collection_response.py)
for coverage of retry, middleware, GLP writes, and pagination wrapping.

---

## Documentation

See [CLAUDE.md](CLAUDE.md) for:

- Full MCP tool reference and verb/noun naming conventions
- Scope and device-type translation rules
- API endpoint patterns (New Central, GLP)
- Token cost and cache optimization tips
- Known broken endpoints and workarounds

See [`docs/`](docs/) for:

- HPE support ticket drafts (e.g. `hpe-support-events-endpoint.md`)
- Endpoint diffs against live lab probes

---

## Contributing

Issues and PRs welcome — please open an issue first for anything non-trivial
so we can sync on scope.

### Safety

- Never commit credentials. `config/credentials.yaml` is git-ignored.
- Never enable `CENTRALMCP_GLP_V2BETA1_WRITES=1` against a production
  workspace until you've sandbox-validated the payload + rollback.
- Token cache files (`.token_cache_*.json`) are git-ignored and stored
  with `0600` perms.

---

## Related projects

- [`HewlettPackard/gl-mcp`](https://github.com/HewlettPackard/gl-mcp) — HPE's official
  GreenLake Platform MCP server (GLP-only; complementary to centralmcp)
- [`aruba/pycentral`](https://github.com/aruba/pycentral) — Aruba's Python
  SDK for classic + new Central
- [`modelcontextprotocol/python-sdk`](https://github.com/modelcontextprotocol/python-sdk) —
  the MCP Python SDK (FastMCP shim) centralmcp builds on
- [`KarthikSKumar98/central-mcp-server`](https://github.com/KarthikSKumar98/central-mcp-server) —
  community Aruba Central MCP server (narrower tool surface)
- [`nowireless4u/hpe-networking-mcp`](https://github.com/nowireless4u/hpe-networking-mcp) —
  unified HPE networking MCP covering Mist + Central + GreenLake

---

## Disclaimer

This project was made with love for the Aruba community. It is an
**independent, community-built tool** and is **not an official HPE or HPE
Aruba Networking product**. It is not endorsed by, affiliated with, or
supported by HPE. Use at your own risk.

---

## License

MIT — see [LICENSE](LICENSE). A few specific files carry additional
attribution headers where code has been ported from MIT-licensed peers
(e.g. `nowireless4u/hpe-networking-mcp`); those attributions are
preserved inline.

---

## Security

- Credentials load at runtime from `config/credentials.yaml` (git-ignored)
- No secrets are hardcoded in source files
- Token cache files are git-ignored and written with `0600` perms
- MCP server config (`.mcp.json`) is git-ignored — it contains local paths

Report security issues via [GitHub Issues](../../issues) — **do not include
credentials in bug reports**.
