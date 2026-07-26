# Tool catalog

centralmcp registers **6,699 backend tools** when every generated surface and
guarded write is enabled. Direct-all router mode adds seven router-native tools for
**6,706 total**. The recommended minimal router exposes only `find_tool`,
`invoke_read_tool`, and `invoke_tool`, then searches the larger index on demand.

The nine committed generated manifests contain **6,143 operations** (6,126
register as active generated tools; 17 are intentionally excluded — see below).
Adding 573 curated tools yields the 6,699 executable backend total. Capability
totals across the full catalog are 3,147 read, 165 diagnostic, 2,544 write, and
843 destructive. See [`docs/capability-gap-matrix.md`](capability-gap-matrix.md)
for the full, reproducible breakdown and the pinned benchmark comparison.

## Counts by backend

| Server | Read-only annotated | Registered total | Main coverage |
|---|---:|---:|---|
| `aruba-central-generated` | 680 | 1,678 | New Central configuration APIs from merged official specifications |
| `aruba-config` | 26 | 80 | SSIDs, VLANs, profiles, firmware, BGP, OSPF, VRF, HA, telemetry, application experience, checkpoint policy |
| `aruba-monitoring` | 72 | 87 | Health, inventory, topology, applications, onboarding, AP tunnels, config health, notification rules, guarded Central GET, and reports |
| `aruba-nac` | 15 | 38 | MAC registration, named MPSK, visitors, auth servers, auth server groups, AAA profiles and diagnostics |
| `aruba-ops` | 2 | 41 | Troubleshooting, reboot, PoE/port bounce, cable tests, gateway iperf and ping sweep |
| `aruba-glp` | 570 | 1,009 | Current devices, grouping, subscriptions, auto-subscription settings, users, Audit Logs v2beta1, workspaces, reporting, service catalog, RBAC role-assignment/scope-group lifecycle, identity user lifecycle, events/webhooks/deliveries, locations/tags, SCIM users/groups/membership, region-aware Compute Ops Management/Storage Fleet/Block Storage/Virtualization/Backup & Recovery/Data Services reads plus guarded VM power and run-protection-job-now writes, and read-only cross-resource reconciliation (105 curated + 904 active generated; `CENTRALMCP_GLP_GENERATED_TOOLS=1` to expand) |
| `aruba-rag` | 9 | 9 | Docs, exact API, advisory, and lifecycle lookup |
| `clearpass-core` | 285 | 845 | CPPM 6.12.7 APIs, Insight endpoint data, OnGuard activity, guarded writes |
| `mist-core` | 547 | 1,080 | 1,050 official OpenAPI operations plus curated NAC, Marvis, inventory, Wired/WAN workflows, assurance snapshots, and bounded authenticated regional WebSocket diagnostic collection |
| `apstra-core` | 86 | 155 | Official 6.1 SDK-derived blueprints, tasks, endpoint policies, object-policy workflows |
| `aos8-core` | 132 | 311 | UIDARUBA/X-CSRF sessions, 258 generated config operations, normalized migration model and dependency planning, and six resumable migration-run tools |
| `edgeconnect-core` | 687 | 1,270 | 1,216 generated operations plus fail-closed Swagger compatibility diagnostics and curated SD-WAN workflows |
| `uxi-core` | 24 | 49 | Current 25-operation UXI API plus curated OAuth, inventory, groups, and assignments |
| `axis-core` | 12 | 47 | Reviewed split create/update/delete Atmos operations from the deterministic SHA-pinned manifest generator |
| **Backend total** | **3,147** | **6,699** | |

“Read-only annotated” excludes diagnostic operations that remain visible in
optional read-only mode. Registered totals include guarded writes; write gates,
dry-run defaults, and confirmation still apply.

## Generated manifest counts

| Platform | Operations |
|---|---:|
| Aruba Central | 1,678 |
| GreenLake Platform | 918 |
| Juniper Mist | 1,050 |
| ClearPass | 816 |
| ArubaOS 8 | 258 |
| EdgeConnect | 1,216 |
| HPE Aruba UXI | 25 |
| Juniper Apstra | 135 |
| Axis Atmos Cloud | 47 |
| **Total** | **6,143** |

GLP registers 904 generated operations because 14 sunset device/subscription
operations remain in the provenance manifest but are intentionally suppressed
at runtime. ClearPass registers 815 generated operations because `/oauth`
returns credentials and is excluded from model-visible tools. Apstra excludes
its two login operations because session credentials are injected internally.
17 operations total are excluded this way, so 6,126 of the 6,143 manifest
operations register as active generated tools.

## Router modes

| Mode | Client-visible tools | Use |
|---|---:|---|
| `minimal` | 3 | Recommended low-token discovery and dispatch |
| `default` | 16 | Router convenience wrappers |
| `direct` + all toolsets/products | 6,706 | Full schema introspection and debugging |

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
CENTRALMCP_GLP_GENERATED_TOOLS=1
CENTRALMCP_GLP_V2BETA1_WRITES=1
```

## Build and validate the catalog

```bash
uv run python scripts/ingest_tools.py
CENTRALMCP_PRODUCT_ACCESS=read-write CENTRALMCP_GLP_GENERATED_TOOLS=1 uv run python scripts/ingest_tools.py --products all
uv run python scripts/check_generated_tool_manifests.py
uv run python scripts/report_capability_gaps.py --check
```

Optional product writes are hidden and blocked in read-only mode. Generated
writes are platform-gated and preview-first. `invoke_tool` remains annotated
destructive because it can dispatch any enabled write-capable backend.
