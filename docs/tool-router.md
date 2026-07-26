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
| `plan_tool_workflow` | read-only | Deterministic, catalog-backed dependency/order planner (outside `minimal` mode) |
| `plan_reconciliation_schedule` | read-only | Plan-only recurring reconciliation schedule builder (outside `minimal` mode) |
| `evaluate_compliance_policy` | read-only | Bounded, declarative compliance-policy evaluator over caller-supplied observations (outside `minimal` mode) |

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
| Default router | 15 client-visible tools[^compliance-tool] |
| Complete backend index | 6,699 tools |
| Direct-all router | 6,705 client-visible tools |

[^compliance-tool]: v0.7 added `plan_tool_workflow` and
    `plan_reconciliation_schedule`; the post-v0.7 compliance expansion adds
    `evaluate_compliance_policy`, raising the default-mode count to 15.
    `minimal` mode remains the same three-tool surface.

The complete catalog spans nine platform surfaces plus RAG. Its nine generated
manifests contain 6,143 reproducible operations (6,126 register as active
generated tools; 573 curated tools bring the executable backend total to
6,699). Minimal mode does not expose that schema surface to the MCP client; it
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

## Observability: audit log and metrics

Both are opt-in and disabled by default -- installing them changes no
existing tool behavior, and stdio mode never gains unsolicited output.

**Audit log.** Set `CENTRALMCP_AUDIT_LOG=1` to append one redacted JSONL
record per completed or failed router call to `state/tool-audit.jsonl`
(or set the variable to an explicit path). Set it to `0`/unset to disable.
Each record contains:

- `run_id` -- one random id per server process (`run_<hex>`), so records
  from the same process/deployment can be grouped without ever reusing a
  client-supplied identifier.
- `session_id` -- one random id per connected MCP client session
  (`sess_<hex>`, or `sess_none` outside a session), held in a bounded map
  so a long-lived process cannot accumulate one entry per historical
  connection forever.
- `classification` -- `read` / `write` / `destructive` / `diagnostic` /
  `unknown`, resolved from the dispatched backend tool's own annotations.
- `tool`, `target_tool` (the actual backend tool name for
  `invoke_tool`/`invoke_read_tool` calls), `argument_keys`, a SHA-256
  `argument_digest` of a redacted copy of the arguments, `outcome`
  (`success`/`error`/`blocked`/`cancelled`/`timeout`/`exception`/...),
  `duration_ms`, and `error_type` (never the exception message).

Argument and result *values* are never written -- only key names and a
digest.

**Metrics.** Set `CENTRALMCP_METRICS=1` to enable bounded, in-process
request/latency/outcome counters (no external dependency, no network
call). Counters are bucketed by a capped set of allow-listed labels --
`tool`, `backend`, `capability`, and `outcome` -- and every collection
inside the registry has a hard ceiling (`max_series`, default 512 distinct
`(tool, backend)` pairs; anything beyond that folds into one fixed
overflow bucket instead of growing without bound). Metrics never read
argument values, result values, or exception messages.

Set `CENTRALMCP_METRICS_HTTP=1` (in addition to `CENTRALMCP_METRICS=1`,
and only on the streamable-HTTP transport -- see
[Streamable HTTP instead of stdio](getting-started.md#streamable-http-instead-of-stdio))
to also expose a compact JSON snapshot at `GET /metrics`. That route is a
`custom_route` on the same app as `/livez`/`/readyz`/`/healthz`, so it
automatically inherits the same loopback/allow-list protections and the
same `MCP_HTTP_BEARER_TOKEN` gate as every other HTTP path here -- there is
no separate auth mechanism to keep in sync. With only
`CENTRALMCP_METRICS_HTTP=1` set (collection itself still off), the route
responds `{"enabled": false}` instead of an empty-looking snapshot.

## Response budgets and continuation metadata

Every result dispatched through `invoke_tool`/`invoke_read_tool` passes
through a deterministic, configurable bounding step before it reaches the
client. Most curated tools already bound their own output (`limit`/`offset`,
`bound_collection_response`); this is a hard, backend-agnostic ceiling for
everything else, including generated/optional-product/direct-mode tools.

A response already inside budget is returned byte-for-byte unchanged -- no
new keys are added -- so this is invisible until a response actually needs
clipping. Error/blocked dicts (an `error` key present) and plain scalars are
never touched, regardless of size.

When clipping is required, the response gains the existing `_pagination`
shape (from `bound_collection_response`) plus a `_response_bounds` marker:

```json
{
  "items": ["...bounded..."],
  "_pagination": {"limit": 25, "offset": 0, "truncated": true, "total": 400},
  "_response_bounds": {
    "truncated": true,
    "reason": "item_budget",
    "item_limit": 25,
    "byte_limit": 200000
  }
}
```

`reason` is `item_budget`, `byte_budget`, or `item_budget+byte_budget`. If a
result has nothing sliceable (e.g. one oversized scalar/nested field) and
still exceeds the byte budget, the response falls back to a bounded text
`preview` instead of an over-budget payload.

Configure the two budgets with environment variables (both optional; invalid
or missing values fall back to the defaults below rather than raising):

| Variable | Default | Notes |
|---|---:|---|
| `CENTRALMCP_ROUTER_RESPONSE_MAX_ITEMS` | 200 | Range 1-200 |
| `CENTRALMCP_ROUTER_RESPONSE_MAX_BYTES` | 200,000 | Minimum 1024 |

### Continuation cursors (`invoke_read_tool` only)

When a clipped response has more data remaining, it also gains an opaque
`next_cursor` string plus a `resumable: true` flag inside
`_response_bounds`. Pass that value back as the optional `cursor` argument
on a **repeated call to the same tool with the same arguments** to fetch
the next page:

```json
{
  "items": ["...page 1..."],
  "_pagination": {"limit": 40, "offset": 0, "truncated": true, "total": 100},
  "_response_bounds": {"truncated": true, "reason": "item_budget", "item_limit": 40, "byte_limit": 200000, "resumable": true},
  "next_cursor": "eyJ2IjoxLCJl...",
  "cursor_expires_in_seconds": 900
}
```

```
invoke_read_tool("list_devices", {"site_id": "hq"}, cursor="eyJ2IjoxLCJl...")
```

Cursor semantics:

- **`invoke_read_tool` only** -- the generic, destructive-annotated
  `invoke_tool` has no `cursor` parameter and never emits or accepts one,
  even when dispatching a capability-`read` tool. Only capability `read`
  tools can produce or consume a cursor; `invoke_read_tool` already refuses
  write/destructive tools outright before any cursor logic runs.
- **Opaque and integrity-protected** -- the token is HMAC-signed with a
  random key generated once per server process. It carries only a version,
  an expiry, the next offset, and short digests binding it to the exact
  tool name and canonical (null-stripped) arguments -- never raw arguments,
  identifiers, credentials, or result data.
- **Process-local** -- a server restart generates a new key, so every
  outstanding cursor from before the restart is rejected. A cursor is also
  rejected if it is malformed, tampered, expired (`CENTRALMCP_ROUTER_CURSOR_TTL_SECONDS`,
  default 900s, clamped to 30-3600s), reused against a different tool, or
  reused against different arguments. Any rejection returns
  `{"error": ..., "tool": ..., "status": "invalid_cursor"}` **without**
  calling the backend.
- **No endless loops** -- if a single item can never fit the byte budget
  (e.g. one huge blob), the response is marked `"resumable": false` with a
  `resumable_reason` instead of emitting a cursor that would just re-fetch
  the same oversized item forever.

## Router automation planning

Two additional read-only, plan-only tools (outside `minimal` mode) support
NOC dependency planning and recurring reconciliation without ever executing
a tool themselves.

### `plan_tool_workflow`

Builds a deterministic dependency/order plan across the enabled backend
catalog. Steps reference an exact `tool` name (resolved only via an exact
catalog match -- never guessed) or a free-text `hint` resolved through the
same bounded keyword search `find_tool` uses (no semantic/embedding
guessing). Unresolved or ambiguous references are reported explicitly, never
silently dropped or inferred.

```text
plan_tool_workflow([
  {"id": "discover", "hint": "list devices"},
  {"id": "inspect", "hint": "find a specific device", "depends_on": ["discover"]},
])
```

Returns resolved step metadata, a topological `order` (only when every step
resolved cleanly and the dependency graph is acyclic -- `null` otherwise,
alongside explicit `cycles`/`unresolved_step_ids`/`unresolved_dependencies`),
and an `artifact` payload (`router_dependency_plan`, see
[artifact-contracts.md](artifact-contracts.md)) ready for
`pipeline.artifact_contracts.write_artifact` -- this tool never writes to
disk itself. `plan_tool_workflow` never calls `invoke_tool`/
`invoke_read_tool`; it only discovers and orders.

### `plan_reconciliation_schedule`

Builds a bounded, read-only recurring-check specification: a validated
cadence (`"hourly"`/`"daily"`/`"weekly"`, an `interval_minutes` object, or a
structurally-validated 5-field `cron` object) plus a bounded set of
currently enabled read/diagnostic tools. Write and destructive tools are
always excluded from the schedulable `entries` list (reported in `excluded`
with a reason), regardless of whether they were explicitly requested.

```text
plan_reconciliation_schedule("daily", platforms=["central"], max_entries=25)
```

`dry_run` is always `true` in both the response and the resulting
`router_reconciliation_plan` artifact. This tool never creates an OS timer,
cron job, or GitHub Actions schedule, and never dispatches a tool -- it only
produces a plan specification.

## Compliance-policy evaluation

`evaluate_compliance_policy` (outside `minimal` mode) is a bounded,
read-only, declarative compliance-policy evaluator. Its architecture is
inspired by NAPALM's `compliance_report` (a fixed comparison-operator
dispatch table evaluated over structured device state) and by Nornir-style
aggregate run counts, but it is implemented independently in
`pipeline/compliance.py` -- no `eval`/`exec`, no arbitrary expressions, no
dynamic imports, and no third-party dependency.

It never fetches data and never calls `invoke_tool`/`invoke_read_tool`
itself: fetch device/config/inventory state first (e.g. one or more
`invoke_read_tool` calls), then pass the already-retrieved results as
`observations` alongside a declarative `policy`.

```text
evaluate_compliance_policy(
  observations=[{"hostname": "sw1", "firmware": {"version": "8.10.0"}}],
  policy=[
    {"field": "firmware.version", "operator": "version_gte", "expected": "8.9.0"},
  ],
)
```

Each rule has a `field` (a restricted dotted/indexed path such as
`interfaces[0].status` or `firmware.version` -- `Mapping` key lookup and
`Sequence` integer indexing only), an `operator` (one of `eq`, `ne`, `lt`,
`le`, `gt`, `ge`, `contains`, `in`, `regex_fullmatch`, `version_gte`,
`version_range`, `exists`, `not_exists`), and an `expected` value (required
for every operator except `exists`/`not_exists`). An optional `optional`
flag (default `false`) reports a missing field as `"skipped"` instead of
`"error"`; an optional `severity` (`critical`/`error`/`warning`/`info`,
default `"error"`) is informational only.

A structurally invalid policy -- an unknown operator, a malformed field
path, an `expected` shape that does not match its operator, an unparsable
regex/version value, an oversized policy/observations list, or a
`policy_id`/rule `id` longer than 200/100 characters -- is rejected with
`"ok": false` before any observation is evaluated. Every per-rule result is
exactly one of `"pass"`, `"fail"`, `"error"`, or `"skipped"` -- never
silently success-shaped.

`regex_fullmatch` patterns are restricted to a fail-closed *safe regex
subset*: literals, character classes, anchors, alternation, and grouping
are permitted, but **at most one quantifier opcode (`*`, `+`, `?`, `{m,n}`,
or a lazy/possessive form of one) is permitted anywhere in the entire
pattern** -- a conservative, auditable ceiling that rejects not only a
nested quantified group (e.g. `(a+)+`, `(a*)+`, `([ab]+)*` -- the shape
that causes catastrophic backtracking) but also plain sibling/sequential
quantifiers that never nest at all (e.g. `a*a*a*a*a*a*a*a*a*b`,
`.*.*=.*`, `[a-z]*[a-z]*!`, `^\d+\.\d+\.\d+$`), and even an
otherwise-ordinary two-quantifier pattern such as `^[\w.-]+@[\w.-]+$`.
Common single-quantifier patterns such as `^sw[0-9]+$` and `^[A-Z]{2}$`
remain accepted, as does unquantified alternation. A backreference, a
lookaround assertion, or any other unrecognized construct is also rejected
outright, even though Python's stdlib `re.compile` would otherwise accept
it. Finite repeat counts cannot exceed the 500-character subject bound,
and a quantified body must consume at least one character, so huge repeats
of empty or zero-width groups are rejected before matching. This is a
static, parse-tree-based check (no thread/subprocess/signal
timeout, no new dependency) -- a risky pattern is rejected before it is
ever matched against any observation. This intentionally rejects some
otherwise-safe complex regexes in exchange for a small, fully auditable
rule with no combinatorial edge cases.

Every per-rule result's `"actual"` value is bounded (depth, collection
size, string length, and final serialized-byte size) and recursively
redacted: a field path containing any credential/secret-shaped segment
(e.g. `credentials.password`, `auth.value`) or tenant/workspace/account-
shaped segment (e.g. `account.tenant_id.v`) is replaced outright, and any
dict/list `"actual"` value is walked recursively so a nested secret/tenant
key is redacted too -- never only the top-level field. A value that still
cannot fit the bound after redaction falls back to a deterministic
`"**TRUNCATED-ACTUAL**"` marker rather than ever exceeding the
`compliance_report` artifact contract's own ceiling, so a legitimately
large observation (e.g. a 50-interface list) always still produces a
valid `"artifact"`.

The response includes `"compliant"` (only `true` when every rule for every
observation passed or was explicitly skipped), aggregate `"counts"`,
per-observation `"observations"` summaries, bounded per-rule `"results"`
detail (`"results_truncated"`/`"results_total"` report the true total even
when the detail list itself is capped), and an `"artifact"`
(`compliance_report`-shaped, see [artifact-contracts.md](artifact-contracts.md))
ready for `pipeline.artifact_contracts.write_artifact` -- this tool never
writes to disk itself.

### Generating versioned report artifacts

`scripts/generate_router_automation_report.py` runs a small fixed example
through both planners against the currently enabled catalog and writes their
artifact payloads to `outputs/router-automation-dependency-plan.json` and
`outputs/router-automation-reconciliation-plan.json` via
`pipeline.artifact_contracts.write_artifact` (redacted, bounded, atomically
written, SHA-256-manifested). It never calls a live backend API.

## Why `invoke_tool` is destructive

The backend catalog contains both read-only tools and tools that can change state. Since `invoke_tool` can dispatch any enabled backend tool, it is conservatively annotated as destructive. Use `invoke_read_tool` for normal investigations.
