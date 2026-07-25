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

`find_tool` results include normalized routing and safety metadata:

```json
{
  "name": "list_active_alerts",
  "server": "aruba-monitoring",
  "platform": "central",
  "capability": "read",
  "recommended_dispatcher": "invoke_read_tool",
  "requires_write_enablement": false,
  "currently_enabled": true,
  "supports_dry_run": false,
  "supports_confirm": false,
  "requires_confirmation": false,
  "read_only": true,
  "destructive": false,
  "idempotent": true
}
```

Filter discovery with `platform`, exact `server`, or normalized `capability`
(`read`, `diagnostic`, `write`, or `destructive`). Filters apply equally to
keyword and semantic matches:

```text
find_tool("configuration", platform="central", capability="write")
find_tool("health check", server="mist-core", capability="diagnostic")
```

Write/destructive results report the current platform write-gate state.
`supports_dry_run` and `supports_confirm` come from the published input schema;
`requires_confirmation` also reflects destructive annotations. Diagnostic
tools use `invoke_tool` because they are intentionally not annotated read-only.

Write/destructive discovery results also include the same compact
`execution_contract` attached to router-dispatched write responses:

```json
{
  "platform": "central",
  "capability": "write",
  "gate": {
    "env_var": "CENTRALMCP_CENTRAL_WRITES",
    "state": "enabled",
    "source": "platform_default"
  },
  "dry_run": {"supported": true, "state": "default_preview"},
  "confirm": {"supported": true, "required": true},
  "idempotent": true,
  "next_action": "Call invoke_tool with dry_run=true to preview the change."
}
```

At dispatch, `dry_run.state` becomes `preview` or `execution_requested` when
the published schema and call arguments make that state knowable. The router
preserves the backend payload and adds `execution_contract`; blocked writes use
the same shape and identify the exact gate to enable. Invalid gate values fail
closed. Read and diagnostic responses are not decorated with write metadata.

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

| Profile | Client-visible / indexed tools |
|---|---:|
| Minimal router | 3 client-visible tools |
| Default router | 12 client-visible tools |
| Complete backend index | 6,545 tools |
| Direct-all router | 6,548 client-visible tools |

The complete catalog spans nine platform surfaces plus RAG. Its nine generated
manifests contain 6,056 reproducible operations (6,039 register as active
generated tools; 506 curated tools bring the executable backend total to
6,545). Minimal mode does not expose that schema surface to the MCP client; it
searches the catalog on demand.

## Toolsets

| Toolset | Enables |
|---|---|
| `central` | Config, monitoring, NAC, ops |
| `central-generated` | Complete generated Central API surface |
| `config` | Central configuration tools |
| `monitoring` | Health, alerts, events, clients, devices |
| `nac` | MAC registration, MPSK, visitors, auth policy tools |
| `ops` | Troubleshooting and operational tools |
| `glp` | GreenLake Platform devices and documented attribute grouping, subscriptions, users, Audit Logs v2beta1, workspaces, reporting, service catalog, and guarded writes |
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
one optional backend needs write access without enabling all optional writes,
for example `CENTRALMCP_AXIS_WRITES=1` for Axis Atmos Cloud alone.

Set `CENTRALMCP_TOKENIZE_SECRETS=1` to install the optional session-scoped
secret-tokenization middleware. Plaintext values remain in bounded TTL vaults
instead of being repeated through model-visible tool arguments and results.

## Why `invoke_tool` is destructive

The backend catalog contains both read-only tools and tools that can change state. Since `invoke_tool` can dispatch any enabled backend tool, it is conservatively annotated as destructive. Use `invoke_read_tool` for normal investigations.
