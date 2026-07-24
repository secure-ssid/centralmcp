# centralmcp 0.3.0 - platform parity, migrations, and safety

Version 0.3.0 is the largest centralmcp expansion so far. It updates the core
Aruba Central and GreenLake Platform surfaces, turns every optional product
starter into a guarded read/write lab backend, repairs AOS8 and Apstra
authentication, and refreshes the exact API index from current Aruba and Mist
sources.

![centralmcp platform coverage](assets/platform-coverage.svg)

## Catalog snapshot

| Catalog | Tools | Intended use |
|---|---:|---|
| Core Aruba and GLP | 270 | Central configuration, monitoring, NAC, operations, GLP, and RAG |
| All products, read-only | 392 | Safe discovery and diagnostics across every enabled backend |
| All products, read-write | 448 | Guarded lab writes with dry-run and confirmation controls |

The read/write catalog includes 75 configuration, 77 monitoring, 34 NAC, 40
operations, 41 GLP, 3 RAG, 15 ClearPass, 26 Mist, 20 Apstra, 43 AOS8, 49
EdgeConnect, and 25 UXI tools.

## Major additions

- **Aruba Central:** configuration checkpoint policy and automatic rollback
  status guidance, BGP, OSPF, VRF, high availability, telemetry, application experience, configuration health,
  topology, notification rules, device notes, onboarding, AP tunnels, named
  MPSK, visitors, and expanded gateway/AP diagnostics.
- **GreenLake Platform:** v2beta1 devices and device groups, Audit Logs
  v2beta1, subscriptions, workspaces, reporting, and guarded API-family writes.
- **ArubaOS 8:** UIDARUBA/X-CSRF sessions, reliable exports, normalized WLAN,
  role, VLAN, AP-group, controller, and policy parsing, plus deterministic
  Classic Central and New Central migration candidates, warnings, diffs, and
  verification plans.
- **Mist, Apstra, ClearPass, and UXI:** Mist NAC/Marvis/Wired/WAN, Apstra
  AuthToken sessions and connectivity templates, ClearPass Insight/OnGuard,
  and UXI guarded lifecycle and assignment workflows.
- **EdgeConnect:** live Swagger/API diagnostics and a fail-closed gate for the
  incompatible pre-9.3 endpoint map. Production 9.3+ remapping still requires
  the target Orchestrator's current instance-hosted Swagger document.

## Framework and transport safety

- Per-platform write gates with read-only defaults.
- Dry-run previews and explicit confirmation for optional product writes.
- Streamable HTTP `/livez`, `/readyz`, and `/healthz` endpoints.
- Host/origin validation and optional bearer protection for streamable HTTP.
- Protocol-level MCP tests, rate-limit metadata, deprecation/sunset handling,
  concurrent token refresh protection, and optional session-scoped secret
  tokenization.

## RAG and API source refresh

The current local release index separates prose retrieval from exact API lookup:

| Index | Current content |
|---|---:|
| LanceDB prose corpus | 47,633 chunks |
| OpenAPI specifications | 239 |
| Exact endpoints | 3,465 |
| Schemas | 10,297 |
| Fields | 57,131 |

Aruba specifications now resolve through the July 2026 ReadMe API registry
format. The official `mistsys/mist_openapi` 2606.1.1 snapshot is pinned and
verified. Weekly GitHub Actions checks report Aruba registry or Mist upstream
drift.

## Upgrade notes

1. Run `uv sync`.
2. Rebuild the router catalog. Use the safe read-only default, or set
   `CENTRALMCP_PRODUCT_ACCESS=read-write` to index all 448 guarded tools.
3. Download the latest prebuilt indexes or refresh and rebuild local sources.
4. Run `uv run python scripts/doctor.py`.
5. Review [optional product safety](optional-products.md) before enabling
   platform writes.
