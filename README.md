# API-Central — Aruba New Central Automation

Python tooling for HPE Aruba Central (New Central) — network device migration, SSID configuration, switch provisioning, and GreenLake Platform management.

Designed to work as a **Claude Code MCP server** so you can drive network operations conversationally, and as standalone **CLI tools** for batch migration workflows.

---

## Features

- **88 MCP tools** across three domain servers (monitoring, config, ops)
- **8-stage migration pipeline** — discover devices → verify config → push to New Central
- SSID build/delete with scope-map targeting (org-wide, site, or group)
- Switch provisioning: VLANs, port profiles, SVIs
- GreenLake Platform (GLP) subscription and device management
- Async troubleshooting: ping, traceroute, cable test, PoE bounce, reboot

---

## Prerequisites

- Python ≥ 3.10
- [`uv`](https://docs.astral.sh/uv/) (recommended) or `pip`
- HPE Aruba Central account with API credentials (OAuth2 client ID + secret)

---

## Setup

```bash
# 1. Clone and install dependencies
git clone https://github.com/your-org/API-Central.git
cd API-Central
uv sync

# 2. Configure credentials
cp config/credentials.yaml.example config/credentials.yaml
# Edit config/credentials.yaml — fill in client_id, client_secret, base_url

# 3. Configure MCP servers (for Claude Code integration)
cp .mcp.json.example .mcp.json
# Edit .mcp.json — replace /path/to/API-Central with your actual clone path
```

> **Security:** `config/credentials.yaml` and `.mcp.json` are git-ignored. Never commit them.

---

## Usage

### Claude Code MCP integration

Once `.mcp.json` is configured, start Claude Code from this directory — the three MCP servers (`aruba-monitoring`, `aruba-config`, `aruba-ops`) will load automatically.

Example prompts:
- *"List all devices at the Home Lab site"*
- *"Build a WPA3 SSID called Corp-WiFi on VLAN 100 for all APs"*
- *"Ping 8.8.8.8 from switch SN123456"*

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
  monitoring.py       Monitoring tools (health, trends, wireless metrics)
  config.py           Config tools (SSIDs, VLANs, profiles, webhooks, firmware)
  ops.py              Ops tools (reboots, ping, cable test, PoE bounce, GLP)
  shared.py           Shared utilities
pipeline/
  clients/            CentralClient, GLPClient, MCPClient, TokenManager
  stages/             s1_discover → s8_verify
  config.py           Credential loader
  ssid_underlay.py    SSID build/delete logic
config/
  credentials.yaml.example   Template — copy to credentials.yaml and fill in
resources/            Postman download script (collections git-ignored — see resources/README.md)
inputs/               CSV templates for batch migration
tests/                Unit + integration tests
```

---

## Running tests

```bash
pytest tests/
```

All tests use mock credentials — no real API calls.

---

## Documentation

See [CLAUDE.md](CLAUDE.md) for:
- Full MCP tool reference and verb/noun naming conventions
- Scope and device-type translation rules
- API endpoint patterns
- Token cost optimization tips

---

## Security

- Credentials load at runtime from `config/credentials.yaml` (git-ignored)
- No secrets are hardcoded in source files
- Token cache files (`.token_cache_*.json`) are git-ignored
- MCP server config (`.mcp.json`) is git-ignored — it contains local paths

Report security issues via [GitHub Issues](../../issues) — do not include credentials in bug reports.
