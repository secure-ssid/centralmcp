# Optional product starters

centralmcp keeps optional products disabled by default so normal MCP sessions
stay low-token. Enable only the starters you want for the current setup.

```bash
python3 scripts/setup_wizard.py --products clearpass,mist
```

Use every starter only when you intentionally want the broader catalog:

```bash
python3 scripts/setup_wizard.py --with-products
```

## Product matrix

| Product | Read-only annotated / total | Enables | Required settings | Safety surface |
|---|---:|---|---|---|
| ClearPass | 272 / 829 | CPPM 6.12.7 APIs plus verified Insight endpoint and OnGuard activity workflows | `CLEARPASS_BASE_URL`, `CLEARPASS_API_TOKEN` | `/oauth` is excluded; writes dry-run by default |
| Juniper Mist | 543 / 1,076 | Official 1,050-operation OpenAPI plus NAC, Marvis, inventory, Wired and WAN Assurance | `MIST_HOST`, `MIST_API_TOKEN`; optional session cookie/CSRF | Writes dry-run by default; diagnostics are distinct from config writes |
| Apstra | 46 / 68 | Official 6.1 SDK-derived blueprints, tasks, endpoint policies, object policies, topology, and protocols | `APSTRA_BASE_URL`, preferred `APSTRA_USERNAME`/`APSTRA_PASSWORD`, optional `APSTRA_API_TOKEN` | Current `/api/aaa/login` with older `/api/user/login` fallback |
| ArubaOS 8 | 125 / 301 | UIDARUBA/X-CSRF/SESSION auth, 258 generated config operations, exhaustive exports, and migration plans | `AOS8_BASE_URL`, preferred `AOS8_USERNAME`/`AOS8_PASSWORD`, optional legacy `AOS8_API_TOKEN` | Writes dry-run by default and require write-memory to persist |
| EdgeConnect | 684 / 1,265 | 1,216 generated operations, multipart uploads, Swagger diagnostics, and curated SD-WAN workflows | `EDGECONNECT_BASE_URL`, `EDGECONNECT_API_TOKEN`, optional `EDGECONNECT_AUTH_HEADER` and session overrides | Source artifact is reproducible but must be checked against live Orchestrator Swagger |
| HPE Aruba UXI | 24 / 49 | Current 25-operation API plus OAuth, sensor/agent/group/network/test inventories and documented writes | `UXI_CLIENT_ID`, `UXI_CLIENT_SECRET`, optional `UXI_BASE_URL`, optional `UXI_TOKEN_URL` | Generic writes accept only documented method/path pairs; 5 requests/second |
| Axis Atmos Cloud | 12 / 25 | Reviewed application, connector, tunnel, location, policy, status, and commit workflows | `AXIS_BASE_URL`, `AXIS_API_TOKEN` | Writes dry-run by default |
| **Optional subtotal** | **1,706 / 3,613** | Seven opt-in product backends | Product-specific | Hidden and blocked unless enabled |

Combined with the Central/GLP/RAG surfaces, the backend catalog contains 2,786
read-only-annotated tools and 6,133 registered tools. Diagnostic tools are
available in optional read-only mode but are not included in the read-only
annotation count.

The generic GET tools reject absolute URLs and stay bounded to the configured
product host. List-like responses are paged with `limit` and `offset` when
possible so broad API calls do not flood the MCP context.

Write-capable optional product tools are intended for lab and controlled
operations. They are annotated as write/destructive, default to `dry_run=True`,
and require `dry_run=False` plus `confirm=True` before sending API changes.
Optional product access defaults to `read-only`, which hides optional write
tools from router discovery and blocks direct write-tool execution. Set
`CENTRALMCP_PRODUCT_ACCESS=read-write` or run the setup wizard with
`--product-access read-write` only for trusted lab workflows where confirmed
writes are expected. Per-platform overrides such as
`CENTRALMCP_MIST_WRITES=1` and `CENTRALMCP_UXI_WRITES=1` can enable one product
without opening every optional backend. Unrecognized values fail closed.

For ArubaOS 8 typed configuration-object writes, the manage tools return
`requires_write_memory_for` with each affected `config_path`. Run
`aos8_write_memory` for those hierarchy nodes only after reviewing the pending
changes and confirming the staged config should be persisted.

Use `aos8_export_all` and `aos8_migration_plan` before migration work. Export
now exhausts local pages and includes WLANs, roles, VLANs, AP groups,
controllers, policies, AAA profiles/servers, IPv4/IPv6 routes, and VRRP. The plan
normalizes the supported migration objects into
separate Classic Central and New Central candidates, reports lossy mappings,
produces deterministic diffs, and returns read-only post-migration checks.

EdgeConnect API generations differ materially. Run
`edgeconnect_doctor` against the target Orchestrator before using operational
tools. The pinned artifact is named for 9.7 but declares API version 7.2.0
internally, so production compatibility must be confirmed against that
Orchestrator's instance-hosted Swagger specification.

Generated EdgeConnect multipart upload tools accept file fields as
`{"filename": "...", "content_base64": "...", "content_type": "..."}` and
enforce a 20 MiB decoded-file limit.

Product base URLs must use HTTPS and public hostnames by default. For local lab
testing against localhost or private IPs, set
`CENTRALMCP_ALLOW_LOCAL_PRODUCT_URLS=1` only in that trusted lab environment.

## What the wizard writes

When you select products, the setup wizard:

```mermaid
flowchart TD
    start["scripts/setup_wizard.py"]
    choose{"Choose optional products"}
    subset["--products clearpass,mist"]
    all["--with-products"]
    access{"Product access mode"}
    ro["read-only default<br/>write tools hidden and blocked"]
    rw["read-write lab mode<br/>writes visible<br/>dry_run=False + confirm=True required"]
    env[".env<br/>CENTRALMCP_PRODUCTS<br/>CENTRALMCP_PRODUCT_ACCESS<br/>product URLs/tokens"]
    config["Local MCP configs<br/>.mcp.json / .mcp.http.json<br/>product selector only, no tokens"]
    catalog["Router catalog<br/>scripts/ingest_tools.py"]
    doctor["Local doctor<br/>scripts/doctor.py"]

    start --> choose
    choose --> subset
    choose --> all
    subset --> access
    all --> access
    access -->|"default"| ro
    access -->|"--product-access read-write"| rw
    rw --> env
    ro --> env
    env --> config
    env --> catalog
    config --> catalog
    catalog --> doctor
```

1. Adds or merges `CENTRALMCP_PRODUCTS`, `CENTRALMCP_PRODUCT_ACCESS`, and
   product URL/token settings into local `.env`; existing non-placeholder token
   values are preserved unless you pass `--force`.
2. Adds only `CENTRALMCP_PRODUCTS` and `CENTRALMCP_PRODUCT_ACCESS` to local MCP
   config files, leaving product tokens in `.env`.
3. Builds the router tool catalog with the selected product starters (or every
   starter with `--with-products`) and access mode; product tokens are not
   passed to the catalog-build subprocess.
4. Lets `scripts/doctor.py` confirm required product variables are present.

Real `.env`, `.mcp.json`, and `.vscode/mcp.json` files are git-ignored.

## Manual setup

The wizard defaults optional products to read-only and records the access mode
in local `.env` / MCP config files. Use explicit read/write lab mode when you
want write tools visible and still guarded by `dry_run=False` plus
`confirm=True`:

```bash
python3 scripts/setup_wizard.py --products clearpass,mist --product-access read-write
```

Omit `--product-access read-write` if you want generated local configs to keep
the safer read-only operating posture.

For manual shell setup:

```bash
export CENTRALMCP_PRODUCTS=clearpass,mist
export CENTRALMCP_PRODUCT_ACCESS=read-only
export CLEARPASS_BASE_URL=https://clearpass.example.com
export CLEARPASS_API_TOKEN=...
export MIST_HOST=https://api.mist.com
export MIST_API_TOKEN=...
uv run python scripts/ingest_tools.py --products clearpass,mist
```

Set `CENTRALMCP_PRODUCT_ACCESS=read-write` in the same shell only when you want
lab write tools indexed and visible.

For streamable HTTP, `scripts/run_http_router.sh` safely loads expected local
`.env` assignments before starting the router, including the product selector,
access mode, supported product URL/token variables, and UXI OAuth settings:

```bash
MCP_PORT=8010 bash scripts/run_http_router.sh
```

## When to add product-specific tools

Keep expanding typed product tools when a workflow is common enough to deserve
a named function instead of a generic GET call, for example:

| Workflow type | Better as a typed tool? |
|---|---|
| "Show ClearPass endpoint status for this MAC" | Yes |
| "List Mist sites with client counts" | Yes |
| "Fetch this one documented endpoint while exploring" | Generic GET is fine |
| "Perform a write/remediation action" | Yes, with explicit destructive annotations and confirmation |

See [Typed product workflow roadmap](product-workflows.md) for implemented
ClearPass, Mist, Apstra, ArubaOS 8, EdgeConnect, and UXI workflows plus
candidates.
