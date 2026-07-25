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
| Juniper Mist | 544 / 1,077 | Official 1,050-operation OpenAPI plus NAC, Marvis, inventory, Wired and WAN Assurance, and bounded authenticated regional WebSocket diagnostic-result collection | `MIST_HOST`, `MIST_API_TOKEN`; optional session cookie/CSRF | Writes dry-run by default; diagnostics are distinct from config writes |
| Apstra | 46 / 68 | Official 6.1 SDK-derived blueprints, tasks, endpoint policies, object policies, topology, and protocols | `APSTRA_BASE_URL`, preferred `APSTRA_USERNAME`/`APSTRA_PASSWORD`, optional `APSTRA_API_TOKEN` | Current `/api/aaa/login` with older `/api/user/login` fallback |
| ArubaOS 8 | 129 / 307 | UIDARUBA/X-CSRF/SESSION auth, 258 generated config operations, exhaustive exports, and resumable Classic/New Central migration runs | `AOS8_BASE_URL`, preferred `AOS8_USERNAME`/`AOS8_PASSWORD`, optional legacy `AOS8_API_TOKEN`, optional `AOS8_CLIENT_IP`, optional `AOS8_SESSION_TTL_SECONDS` | Writes dry-run by default and require write-memory to persist |
| EdgeConnect | 684 / 1,265 | 1,216 generated operations, multipart uploads, fail-closed Swagger compatibility diagnostics, and curated SD-WAN workflows | `EDGECONNECT_BASE_URL`, `EDGECONNECT_API_TOKEN`, optional `EDGECONNECT_AUTH_HEADER` and session overrides | Source artifact is reproducible but must be checked against live Orchestrator Swagger |
| HPE Aruba UXI | 24 / 49 | Current 25-operation API plus OAuth, sensor/agent/group/network/test inventories and documented writes | `UXI_CLIENT_ID`, `UXI_CLIENT_SECRET`, optional `UXI_BASE_URL`, optional `UXI_TOKEN_URL` | Generic writes accept only documented method/path pairs; 5 requests/second |
| Axis Atmos Cloud | 12 / 25 | Reviewed application, connector, tunnel, location, policy, status, and commit workflows from the deterministic SHA-pinned manifest generator | `AXIS_BASE_URL`, `AXIS_API_TOKEN` | Writes dry-run by default |
| **Optional subtotal** | **1,711 / 3,620** | Seven opt-in product backends | Product-specific | Hidden and blocked unless enabled |

Combined with the Central/GLP/RAG surfaces, the backend catalog contains 2,813
read-only-annotated tools and 6,162 registered tools. Diagnostic tools are
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
`CENTRALMCP_MIST_WRITES=1`, `CENTRALMCP_UXI_WRITES=1`, and
`CENTRALMCP_AXIS_WRITES=1` can enable one product without opening every
optional backend. A platform-specific setting takes precedence over
`CENTRALMCP_PRODUCT_ACCESS`; unrecognized values fail closed.

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

For resumable execution, use `aos8_preview_migration_run`, then
`aos8_create_migration_run`. Run `aos8_apply_migration_run` with its default
`dry_run=True` before calling it with `dry_run=False`, `confirm=True`, and any
required target secrets. Secrets are accepted only for that attempt and are
never written to `state/aos8_migrations/`. Use `aos8_get_migration_run`,
`aos8_list_migration_runs`, and `aos8_verify_migration_run` for bounded status
and read-only source-intent/target-result comparisons. New Central guidance is
limited to post-change checkpoint policy plus automatic device rollback; there
is no manual checkpoint listing or restore workflow. Classic Central guidance
remains export-before-apply. Override the state directory only when needed with
`CENTRALMCP_AOS8_MIGRATION_STATE_DIR`.

### ArubaOS 8 migration prerequisites

Migration-verified mappings are gated by the authoritative
[AOS8 migration contract matrix](aos8-migration-contract-matrix.md); a
read-only [live/dry-run evaluation](aos8-live-dryrun-evaluation.md) records
exactly what was and was not exercised live in one prior evaluation
environment. To reproduce or extend that evaluation against your own
ArubaOS 8 estate:

| Requirement | Variable(s) | Notes |
|---|---|---|
| AOS8 source access | `AOS8_BASE_URL`, `AOS8_USERNAME`/`AOS8_PASSWORD` (or legacy `AOS8_API_TOKEN`) | Required for any live export, login, or Classic/New Central migration plan against a real Mobility Conductor/controller |
| Login client context (optional) | `AOS8_CLIENT_IP` | Optional `client_ip` query parameter sent at login; leave unset unless your controller requires it |
| Session lifetime (optional) | `AOS8_SESSION_TTL_SECONDS` | Cached session lifetime in seconds; default 600, max 3600 |
| New Central target access | `central_account` in `config/credentials.yaml` | Required for any live New Central preflight read or preview/apply against a real tenant |
| Classic Central target access | An explicit Classic group name, GUID, or device serial | Required before any live Classic Central preview or apply; **never inferred from a New Central scope** even when one is configured |

Without AOS8 credentials configured, AOS8 source parsing and Classic Central
target behavior can still be exercised against the fixture-backed unit test
suite (`tests/unit/test_aos8_parsers.py`, `test_aos8_migration.py`,
`test_aos8_session.py`, `test_aos8_target_adapters.py`), but not against a
live controller. New Central preflight reads and stateless `preview()` calls
can be exercised live with only `central_account` configured, independent of
AOS8 access.

EdgeConnect API generations differ materially. Run
`edgeconnect_doctor` against the target Orchestrator before using operational
tools. The pinned artifact is named for 9.7 but declares API version 7.2.0
internally, so production compatibility must be confirmed against that
Orchestrator's instance-hosted Swagger specification.

Export the target Orchestrator's Swagger/OpenAPI document to a local file, then
run the fail-closed compatibility check:

```bash
uv run python scripts/generate_edgeconnect_tools.py \
  --source inputs/target-orchestrator-openapi.json \
  --expect-sha256 <sha256> \
  --report-output outputs/edgeconnect-compatibility.json
```

JSON and YAML Swagger 2.0, OpenAPI 3.0, and OpenAPI 3.1 documents are accepted.
The report compares operations, methods, paths, auth declarations, API version,
base-path assumptions, and source/manifest digests with the committed
1,216-operation baseline. Malformed input, unsupported versions, stale
baselines, digest mismatch, endpoint drift, unsupported auth, and non-root
server base paths all fail closed. The local document and credentials are never
uploaded; authenticated download is intentionally left to approved local
operator tooling.

The command is read-only unless `--generate` is explicitly supplied. Even then,
it replaces the generated manifest only after validation succeeds and updates
`mcp_servers/openapi_gen/provenance/edgeconnect.json`.

The 25-operation Axis manifest is a reviewed derivation from the MIT-licensed
upstream registry, not a redistributed proprietary specification. Verify the
committed pin offline with
`uv run python scripts/generate_axis_manifest.py --check`. Regenerate from a
pinned local checkout with `--source-dir PATH`, or use the explicit
digest-validated network path with `--fetch`.

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
