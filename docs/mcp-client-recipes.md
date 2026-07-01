# MCP client recipes

Use the low-token router profile for day-to-day clients:

```env
CENTRALMCP_ROUTER_MODE=minimal
CENTRALMCP_TOOLSETS=central,glp,rag
```

This exposes only `find_tool`, `invoke_read_tool`, and `invoke_tool` in minimal mode while still letting the router reach the backend catalog on demand.

## Pick a connection style

| Style | Use when | Config |
|---|---|---|
| stdio | Your client launches the MCP server process | `.mcp.json.example`, `.cursor/mcp.json`, `.vscode/mcp.json.example`, `.claude/launch.json` |
| streamable HTTP | Your client connects to an already-running local MCP server | `.mcp.http.json.example` + `scripts/run_http_router.sh` |

## Generic stdio client

```bash
cp .mcp.json.example .mcp.json
```

Edit `.mcp.json` and replace `/path/to/centralmcp` with your local clone path. Keep:

```json
{
  "CENTRALMCP_ROUTER_MODE": "minimal",
  "CENTRALMCP_TOOLSETS": "central,glp,rag"
}
```

## Cursor

The committed `.cursor/mcp.json` is already the default low-token router profile.

Use `.cursor/mcp.dev.json` only when debugging direct backend servers. It exposes the six core Aruba servers directly, so it costs more tool-list context than the router profile.

## VS Code

```bash
cp .vscode/mcp.json.example .vscode/mcp.json
```

Then keep the `aruba-tool-router` server entry enabled for normal use.

## Claude launch profiles

Use `.claude/launch.json`. The first configuration is:

```text
aruba-tool-router MCP server (minimal)
```

The remaining direct-server profiles are for debugging individual backends.

## Streamable HTTP

Start the local HTTP router:

```bash
MCP_PORT=8010 bash scripts/run_http_router.sh
```

Copy the generic HTTP client snippet:

```bash
cp .mcp.http.json.example .mcp.http.json
```

Point your MCP client to:

```text
http://127.0.0.1:8010/mcp
```

If you change `MCP_HOST` or `MCP_PORT`, update `.mcp.http.json` to match.

## Verify local setup

Run the local doctor before opening the client:

```bash
uv run python scripts/doctor.py
```

It does not call Central, GLP, or optional product APIs. It checks copied local configs, placeholder paths, HTTP URL/transport mismatch, low-token router profile drift, optional product env, local indexes, and listener status.

## First useful MCP call flow

```text
find_tool("show critical alerts")
invoke_read_tool("list_active_alerts", {"severity": "CRITICAL", "limit": 20})
```

Use `invoke_read_tool` for investigations. Use `invoke_tool` only after intentional write/destructive user intent.
