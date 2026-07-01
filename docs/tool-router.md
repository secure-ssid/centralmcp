# Low-token tool router

`mcp_servers/tool_router.py` is the recommended MCP entrypoint.

Instead of exposing every backend tool to the client up front, the router exposes a small discovery/dispatch surface and loads backend tools on demand.

## Daily workflow

1. Ask `find_tool` for the action you need.
2. If the selected tool is read-only, call `invoke_read_tool`.
3. If the selected tool writes or can be destructive, call `invoke_tool` only after explicit user intent.

Example:

```text
find_tool("show active critical alerts")
invoke_read_tool("list_active_alerts", {"severity": "CRITICAL"})
```

## Router tools

| Tool | Safety | Use |
|---|---|---|
| `find_tool` | read-only | Search the enabled backend catalog |
| `invoke_read_tool` | read-only | Dispatch only backend tools annotated read-only |
| `invoke_tool` | destructive | Generic dispatcher for write/destructive tools |
| Convenience wrappers | mixed | Available only outside `minimal` mode |

`find_tool` results include safety flags:

```json
{
  "name": "list_active_alerts",
  "server": "aruba-monitoring",
  "read_only": true,
  "destructive": false,
  "idempotent": true
}
```

## Recommended client profile

```env
CENTRALMCP_ROUTER_MODE=minimal
CENTRALMCP_TOOLSETS=central,glp,rag
```

This keeps the tool list small while still covering the common Central, GLP, and RAG workflows.

If `CENTRALMCP_ROUTER_MODE` is omitted, the router uses `default` mode and includes convenience wrappers. Keep `minimal` in MCP client configs when token surface matters.

## Toolsets

| Toolset | Enables |
|---|---|
| `central` | Config, monitoring, NAC, ops |
| `config` | Central configuration tools |
| `monitoring` | Health, alerts, events, clients, devices |
| `nac` | MAC registration, MPSK, visitors, auth policy tools |
| `ops` | Troubleshooting and operational tools |
| `glp` | GreenLake Platform tools |
| `rag` | `ask_docs`, `search_docs`, `lookup_api` |
| `clearpass`, `mist`, `apstra`, `aos8`, `edgeconnect` | Optional starter backends |
| `all` | All core and optional backends |

## Optional products

Optional products can be enabled either by `CENTRALMCP_TOOLSETS` or by `CENTRALMCP_PRODUCTS`.

```env
CENTRALMCP_PRODUCTS=clearpass,mist,apstra,aos8,edgeconnect
```

The optional product starters intentionally expose a small read-only surface:

- `<product>_status`
- guarded `<product>_get`

This keeps token cost low while leaving room to add product-specific tools later.

## Why `invoke_tool` is destructive

The backend catalog contains both read-only tools and tools that can change state. Since `invoke_tool` can dispatch any enabled backend tool, it is conservatively annotated as destructive. Use `invoke_read_tool` for normal investigations.
