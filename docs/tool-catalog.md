# Tool catalog

centralmcp 0.3.0 registers 448 tools when all products and guarded writes are
enabled. The recommended minimal router exposes only three discovery/dispatch
tools to the MCP client and searches this larger catalog on demand.

## Counts by backend

| Server | Read-only mode | Read-write mode | Main coverage |
|---|---:|---:|---|
| `aruba-config` | 75 | 75 | SSIDs, VLANs, profiles, webhooks, firmware, checkpoint policy, BGP, OSPF, VRF, HA, telemetry, application experience |
| `aruba-monitoring` | 77 | 77 | Health, inventory, clients, alerts, events, topology, applications, reports, onboarding, AP tunnels, config health |
| `aruba-nac` | 34 | 34 | MAC registration, named MPSK, visitors, auth servers, AAA profiles and diagnostics |
| `aruba-ops` | 40 | 40 | Ping, traceroute, show commands, reboot, PoE/port bounce, cable tests, iperf, ping sweep |
| `aruba-glp` | 41 | 41 | Devices/groups v2beta1, subscriptions, users, audit logs, workspaces, reporting, service catalog |
| `aruba-rag` | 3 | 3 | `ask_docs`, `search_docs`, `lookup_api` |
| `clearpass-core` | 9 | 15 | Endpoints, auth failures, NADs, guests, Insight, OnGuard, guarded writes |
| `mist-core` | 19 | 26 | WLANs, clients, NAC, Marvis, inventory/claims, Wired/WAN Assurance |
| `apstra-core` | 15 | 20 | Blueprints, topology, anomalies, protocols, AuthToken sessions, connectivity templates |
| `aos8-core` | 34 | 43 | Controller operations, config export, typed writes, Classic/New Central migration plans |
| `edgeconnect-core` | 32 | 49 | Swagger diagnostics and gated legacy appliance, route, tunnel, VRF, ACL, service, and zone workflows |
| `uxi-core` | 13 | 25 | Sensors, agents, groups, networks, tests, assignments, guarded lifecycle writes |
| **Total** | **392** | **448** | |

The six core Aruba/GLP/RAG servers contribute 270 tools. Optional products add
122 read-only tools or 178 tools when guarded writes are included.

## Router surface

Minimal mode exposes:

| Router tool | Annotation | Purpose |
|---|---|---|
| `find_tool` | read-only | Search enabled backends and return compact safety/parameter metadata |
| `invoke_read_tool` | read-only | Dispatch only tools annotated read-only |
| `invoke_tool` | destructive | Dispatch intentional writes/destructive calls |

Use:

```env
CENTRALMCP_ROUTER_MODE=minimal
CENTRALMCP_TOOLSETS=central,glp,rag
```

Enable optional products only when needed:

```env
CENTRALMCP_PRODUCTS=clearpass,mist,apstra,aos8,edgeconnect,uxi,axis
CENTRALMCP_PRODUCT_ACCESS=read-only
```

## Build the catalog

Core catalog:

```bash
uv run python scripts/ingest_tools.py
```

All products, read-only:

```bash
uv run python scripts/ingest_tools.py --products all
```

All products, guarded read/write:

```bash
CENTRALMCP_PRODUCT_ACCESS=read-write uv run python scripts/ingest_tools.py --products all
```

## Safety notes

- Optional product writes are hidden and blocked in read-only mode.
- Write tools default to dry-run when supported and require explicit
  confirmation before execution.
- Central writes can be blocked globally with `CENTRALMCP_CENTRAL_WRITES=0`.
- Per-platform `CENTRALMCP_<PLATFORM>_WRITES` overrides can enable or disable a
  single backend.
- EdgeConnect operational workflows remain blocked unless the instance is
  validated for the bundled legacy endpoint map; use `edgeconnect_doctor`
  first.
- `invoke_tool` remains marked destructive because it can reach any enabled
  write-capable backend.

See [typed product workflows](product-workflows.md) for named optional product
tools and [tool router](tool-router.md) for discovery and dispatch behavior.
