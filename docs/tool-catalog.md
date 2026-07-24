# Tool catalog

centralmcp registers **6,133 backend tools** when every generated surface and
guarded write is enabled. Direct-all router mode adds the three router tools for
**6,136 total**. The recommended minimal router exposes only `find_tool`,
`invoke_read_tool`, and `invoke_tool`, then searches the larger index on demand.

The nine committed generated manifests contain **5,703 operations**.

## Counts by backend

| Server | Read-only annotated | Registered total | Main coverage |
|---|---:|---:|---|
| `aruba-central-generated` | 448 | 1,347 | New Central configuration APIs from merged official specifications |
| `aruba-config` | 26 | 75 | SSIDs, VLANs, profiles, firmware, BGP, OSPF, VRF, HA, telemetry, application experience, checkpoint policy |
| `aruba-monitoring` | 68 | 77 | Health, inventory, topology, applications, onboarding, AP tunnels, config health, notification rules |
| `aruba-nac` | 13 | 34 | MAC registration, named MPSK, visitors, auth servers, AAA profiles and diagnostics |
| `aruba-ops` | 2 | 40 | Troubleshooting, reboot, PoE/port bounce, cable tests, gateway iperf and ping sweep |
| `aruba-glp` | 520 | 944 | Current devices, grouping, subscriptions, users, Audit Logs v2beta1, workspaces, reporting, service catalog |
| `aruba-rag` | 3 | 3 | `ask_docs`, `search_docs`, `lookup_api` |
| `clearpass-core` | 272 | 829 | CPPM 6.12.7 APIs, Insight endpoint data, OnGuard activity, guarded writes |
| `mist-core` | 543 | 1,076 | 1,050 official OpenAPI operations plus curated NAC, Marvis, inventory, Wired/WAN workflows |
| `apstra-core` | 46 | 68 | Official 6.1 SDK-derived blueprints, tasks, endpoint policies, object-policy workflows |
| `aos8-core` | 125 | 301 | UIDARUBA/X-CSRF sessions, 258 generated config operations, migration exports and plans |
| `edgeconnect-core` | 684 | 1,265 | 1,216 generated operations plus compatibility diagnostics and curated SD-WAN workflows |
| `uxi-core` | 24 | 49 | Current 25-operation UXI API plus curated OAuth, inventory, groups, and assignments |
| `axis-core` | 12 | 25 | Reviewed Atmos applications, connectors, tunnels, locations, policies, status, and commits |
| **Backend total** | **2,786** | **6,133** | |

“Read-only annotated” excludes diagnostic operations that remain visible in
optional read-only mode. Registered totals include guarded writes; write gates,
dry-run defaults, and confirmation still apply.

## Generated manifest counts

| Platform | Operations |
|---|---:|
| Aruba Central | 1,347 |
| GreenLake Platform | 918 |
| Juniper Mist | 1,050 |
| ClearPass | 816 |
| ArubaOS 8 | 258 |
| EdgeConnect | 1,216 |
| HPE Aruba UXI | 25 |
| Juniper Apstra | 48 |
| Axis Atmos Cloud | 25 |
| **Total** | **5,703** |

GLP registers 904 generated operations because 14 sunset device/subscription
operations remain in the provenance manifest but are intentionally suppressed
at runtime. ClearPass registers 815 generated operations because `/oauth`
returns credentials and is excluded from model-visible tools. Apstra excludes
its two login operations because session credentials are injected internally.

## Router modes

| Mode | Client-visible tools | Use |
|---|---:|---|
| `minimal` | 3 | Recommended low-token discovery and dispatch |
| `default` | 12 | Router convenience wrappers |
| `direct` + all toolsets/products | 6,136 | Full schema introspection and debugging |

```env
CENTRALMCP_ROUTER_MODE=minimal
CENTRALMCP_TOOLSETS=central,glp,rag
```

For full direct introspection:

```env
CENTRALMCP_ROUTER_MODE=direct
CENTRALMCP_TOOLSETS=all
CENTRALMCP_PRODUCTS=all
CENTRALMCP_PRODUCT_ACCESS=read-write
```

## Build and validate the catalog

```bash
uv run python scripts/ingest_tools.py
CENTRALMCP_PRODUCT_ACCESS=read-write uv run python scripts/ingest_tools.py --products all
uv run python scripts/check_generated_tool_manifests.py
```

Optional product writes are hidden and blocked in read-only mode. Generated
writes are platform-gated and preview-first. `invoke_tool` remains annotated
destructive because it can dispatch any enabled write-capable backend.
