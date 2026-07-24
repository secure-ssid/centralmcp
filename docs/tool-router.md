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

See [example-prompts.md](example-prompts.md) for more copy/paste prompt and
router-call examples.

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

To keep discovery responses small, `find_tool` omits full JSON schemas by
default and returns only parameter names in `params`. Set
`include_schema=true` only when you need the full schema for a selected tool.

If the semantic tool index is unavailable and no keyword fallback matches,
`find_tool` returns a compact error with a rebuild hint instead of an empty
success-shaped result.

## Recommended client profile

```env
CENTRALMCP_ROUTER_MODE=minimal
CENTRALMCP_TOOLSETS=central,glp,rag
```

This keeps the tool list small while still covering the common Central, GLP, and RAG workflows.

If `CENTRALMCP_ROUTER_MODE` is omitted, the router uses `default` mode and includes convenience wrappers. Keep `minimal` in MCP client configs when token surface matters.

## Catalog size

| Profile | Tools indexed |
|---|---:|
| Core Aruba/GLP/RAG | 270 |
| All products with read-only optional access | 392 |
| All products with guarded optional writes | 448 |

The complete read/write catalog contains 75 configuration, 77 monitoring, 34
NAC, 40 operations, 41 GLP, 3 RAG, 15 ClearPass, 26 Mist, 20 Apstra, 43 AOS8,
49 EdgeConnect, and 25 UXI tools. Minimal mode does not expose that full schema
surface to the MCP client; it searches the catalog on demand.

## Toolsets

| Toolset | Enables |
|---|---|
| `central` | Config, monitoring, NAC, ops |
| `config` | Central configuration tools |
| `monitoring` | Health, alerts, events, clients, devices |
| `nac` | MAC registration, MPSK, visitors, auth policy tools |
| `ops` | Troubleshooting and operational tools |
| `glp` | GreenLake Platform v1/v2beta1 devices and groups, subscriptions, users, audit logs, workspaces, reporting, service catalog, API-family discovery, and guarded writes |
| `rag` | `ask_docs`, `search_docs`, `lookup_api` |
| `clearpass`, `mist`, `apstra`, `aos8`, `edgeconnect`, `uxi`, `axis` | Optional product backends |
| `all` | All core and optional backends |

## Optional products

Optional products can be enabled either by `CENTRALMCP_TOOLSETS` or by `CENTRALMCP_PRODUCTS`.

```env
CENTRALMCP_PRODUCTS=clearpass,mist,apstra,aos8,edgeconnect,uxi,axis
CENTRALMCP_PRODUCT_ACCESS=read-only
```

The optional product backends expose an opt-in, lab-friendly surface:

- `<product>_status`
- guarded `<product>_get`
- guarded `<product>_write` for lab POST/PUT/PATCH/DELETE calls on write-capable starters
- typed ClearPass troubleshooting, Insight, and OnGuard workflows
- typed Mist wireless, NAC, Marvis, inventory/claims, Wired, and WAN workflows
- session-authenticated Apstra blueprint/connectivity-template workflows
- AOS8 operational/config export and Classic/New Central migration planning
- EdgeConnect appliance, route, tunnel, VRF, interface-label, ACL object-group,
  service, bypass, link-integrity, firewall-zone, and API compatibility workflows
- typed UXI sensor, agent, group, network, service-test, assignment, and guarded write workflows
- reviewed Axis Atmos application, connector, tunnel, location, policy, and commit workflows

Generic GET responses are paginated with `limit` and `offset` when the response
contains a list. This keeps token cost low while leaving room to add
product-specific tools later.

Optional product access defaults to `read-only`, which hides optional product
write tools from `find_tool` and blocks direct dispatch through `invoke_tool`;
the product write tools also return a blocked response if run directly with
that mode. Use `CENTRALMCP_PRODUCT_ACCESS=read-write` for lab workflows that
need guarded writes. Those write tools still default to `dry_run=True`; execute
only after reviewing the preview with `dry_run=False` plus `confirm=True`.
Unrecognized manual access-mode values fail closed as read-only.

Use `CENTRALMCP_<PLATFORM>_WRITES=1` for a narrower per-platform override when
one optional backend needs write access without enabling all optional writes.

Set `CENTRALMCP_TOKENIZE_SECRETS=1` to install the optional session-scoped
secret-tokenization middleware. Plaintext values remain in bounded TTL vaults
instead of being repeated through model-visible tool arguments and results.

## Why `invoke_tool` is destructive

The backend catalog contains both read-only tools and tools that can change state. Since `invoke_tool` can dispatch any enabled backend tool, it is conservatively annotated as destructive. Use `invoke_read_tool` for normal investigations.
