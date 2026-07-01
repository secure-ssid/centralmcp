# Troubleshooting

Use the local doctor first. It does not call Central, GLP, or optional product
APIs.

```bash
uv run python scripts/doctor.py
```

## Setup wizard

| Symptom | Fix |
|---|---|
| `uv` is missing | Install `uv`, or rerun the wizard after installing it. |
| Existing local config was not overwritten | Re-run with `--force` if you want to replace `.mcp.json`, `.mcp.http.json`, `.env`, or `config/credentials.yaml`. |
| You only want a no-credentials trial | Run `python3 scripts/setup_wizard.py --yes --skip-credentials` and skip API-backed tools until credentials are added. |
| You picked the wrong products | Re-run with `--force --products clearpass,mist` or edit local `.env` and rebuild the catalog. |

## Credentials and Central regions

The wizard offers common Central API gateway choices:

| Gateway | Base URL |
|---|---|
| US / common API gateway | `https://apigw-prod2.central.arubanetworks.com` |
| EU Central | `https://apigw-eucentral3.central.arubanetworks.com` |
| APAC | `https://apigw-apac.central.arubanetworks.com` |
| Legacy/internal gateway | `https://internal.api.central.arubanetworks.com` |

If your tenant uses a different host, choose the custom URL option. Environment
variables override YAML values, so check both shell variables and
`config/credentials.yaml` when troubleshooting auth.

## HTTP MCP mode

Start the local HTTP router:

```bash
MCP_PORT=8010 bash scripts/run_http_router.sh
```

Connect the MCP client to:

```text
http://127.0.0.1:8010/mcp
```

| Symptom | Fix |
|---|---|
| Port already in use | The helper prints listener details. Stop the old process with `kill <PID>` or choose another `MCP_PORT`. |
| `curl` returns `406` | Expected for plain curl. Real MCP clients send streaming headers such as `Accept: text/event-stream`. |
| Optional products work in stdio but not HTTP | Confirm local `.env` exists next to the repo root; the HTTP helper safely loads assignments from it before starting. |
| Client URL does not match the server | Update `.mcp.http.json` if you changed `MCP_HOST` or `MCP_PORT`. |

## Router and catalog

Recommended low-token profile:

```env
CENTRALMCP_ROUTER_MODE=minimal
CENTRALMCP_TOOLSETS=central,glp,rag
```

Rebuild the tool catalog:

```bash
uv run python scripts/ingest_tools.py
```

Include selected optional products:

```bash
uv run python scripts/ingest_tools.py --products clearpass,mist
```

If `find_tool` cannot locate expected optional product tools, confirm
`CENTRALMCP_PRODUCTS` and the catalog were built with the same selected
products.

## First useful call flow

```text
find_tool("show active critical alerts")
invoke_read_tool("list_active_alerts", {"severity": "CRITICAL", "limit": 20})
```

Use `invoke_read_tool` for investigations. Use `invoke_tool` only when you
intend to run a write/destructive backend tool.
