# centralmcp — HPE Networking MCP automation

HPE Aruba Central API tooling for network device migration, SSID config, switch provisioning, and GreenLake Platform management.

## Project layout

```
mcp_servers/
  tool_router.py       Unified low-token router — find_tool + invoke_read_tool/invoke_tool over enabled backends
  prompts.py           Router-level guided workflows (MCP prompts)
  _middleware/         Middleware — null-strip, async rate limit, response envelope, unknown-tool hints, optional MAC normalization
  monitoring.py        Monitoring tools — device health, trends, wireless metrics
  config.py            Config tools — SSIDs, VLANs, profiles, webhooks, firmware
  ops.py               Ops tools — reboots, ping, cable test, PoE bounce, GLP mgmt
  nac.py               NAC tools — MAC reg, MPSK, visitors, auth servers, AAA profiles, AAA test
  glp.py               GreenLake Platform tools — devices, subscriptions, users, audit logs, workspaces, reporting, service catalog, guarded GLP GET
  rag.py               RAG tools — ask_docs/search_docs (LanceDB hybrid) + lookup_api (exact OpenAPI lookup, SQLite)
  clearpass.py         Optional ClearPass starter backend
  mist.py              Optional Juniper Mist starter backend
  apstra.py            Optional Apstra starter backend
  aos8.py              Optional ArubaOS 8 starter backend
  edgeconnect.py       Optional EdgeConnect starter backend
  uxi.py               Optional HPE Aruba UXI starter backend
  shared.py            Shared utilities and helpers
pipeline/
  clients/             CentralClient, GLPClient, MCPClient, TokenManager, EmbedClient (fastembed),
                       LanceClient (embedded store), SpecsIndex (SQLite), OllamaClient/RedisClient (optional server backend)
  stages/              s1_discover → s8_verify (migration pipeline)
  config.py            Credentials loader (config/credentials.yaml or env)
  create_ssid.py       SSID build/delete logic (underlay + overlay)
ingestion/
  ingest_docs.py       Chunk + embed docs → LanceDB + specs SQLite (default) or Redis Stack (--backend redis)
  sources/             Raw scraped docs (git-ignored — regenerable)
scripts/
  doctor.py           Local setup diagnostic; no API calls
  ingest_tools.py      Re-index the find_tool catalog → LanceDB (default) or Redis Stack (--backend redis)
  run_http_router.sh   Start the minimal router over streamable HTTP
  validate_release.py  Local pre-push gate: unit tests, optional RAG/API eval, tool catalog floor
data/                  Embedded indexes (git-ignored): docs.lance, tools.lance, specs.sqlite — rebuild via ingest or download prebuilt
docker-compose.yml     OPTIONAL server backend: Redis Stack + Ollama (set CENTRALMCP_RAG_BACKEND=redis)
config/credentials.yaml  API credentials (never commit)
resources/             Postman API collections (monitoring + config endpoints)
inputs/                CSV files for batch migration
outputs/               Reports and results
state/                 Pipeline state store (idempotent runs)
.cursor/
  mcp.json             Router-only entry (aruba-tool-router) — low token overhead
  mcp.dev.json         6 core Aruba servers directly — full introspection for debugging
.mcp.json.example       Generic stdio MCP client example — router minimal mode
.mcp.http.json.example  Generic streamable HTTP MCP client example
.claude/launch.json    Optional launch profiles — minimal router (`CENTRALMCP_TOOLSETS=central,glp,rag`) plus direct debug servers
.vscode/mcp.json.example  VS Code MCP example — router minimal mode
```

## MCP tools (`mcp_servers/`)

Six core Aruba domain servers — `monitoring.py`, `config.py`, `ops.py`, `nac.py`, `glp.py`, `rag.py` — each registered in `.cursor/mcp.dev.json`. For day-to-day use, `tool_router.py` is the single entrypoint registered in `.cursor/mcp.json`, `.claude/launch.json`, or copied from `.vscode/mcp.json.example`, and proxies to enabled backends.
Tools follow `verb_noun` naming — no prefix. The server name provides context (`aruba-monitoring`, `aruba-config`, `aruba-ops`, `aruba-nac`, `aruba-glp`, `aruba-rag`).

Optional product starters (`clearpass`, `mist`, `apstra`, `aos8`, `edgeconnect`, `uxi`) are loaded only when enabled with `CENTRALMCP_PRODUCTS` or `CENTRALMCP_TOOLSETS`. Keep new product backends opt-in so default tool-list token cost stays low.
Use router `invoke_read_tool` for read-only dispatch. Keep router `invoke_tool` annotated `DESTRUCTIVE`; it is a generic dispatcher and can reach destructive backend tools.

| Verb | Meaning |
|------|---------|
| `list_*` | Return multiple items |
| `get_*` | Return one item or value |
| `find_*` | Search — returns None if not found |
| `create_*` | Create a resource (idempotent where possible) |
| `set_*` | Update a single attribute |
| `push_*` | Bulk create/upsert |
| `delete_*` | Remove a resource |
| `build_*` | Multi-step create + scope-map |

## Scope and persona translation

Users speak in GUI terms. Translate before calling tools:

**Scope (WHERE config applies):**
- "everywhere" / "org-wide" / "all APs" → call `get_global_scope_id()`
- A site name (e.g. "Home Lab") → call `list_scopes()`, match `scope_name`
- A group name (e.g. "Branch APs") → call `list_scopes()`, match `scope_name`

**Persona (DEVICE TYPE):**
- "Access Points" / "APs" / "wireless" → `CAMPUS_AP`
- "Gateways" / "GW" → `MOBILITY_GW`
- "Access Switch" → `ACCESS_SWITCH`
- "Aggregation Switch" → `AGG_SWITCH`
- "Core Switch" → `CORE_SWITCH`
- Default to `CAMPUS_AP` for SSID/wireless unless told otherwise

## Rules for write operations

**Always use `dry_run=True` first.** Show the user the payload and confirm before executing.

Before calling `build_underlay_ssid` or `create_allow_all_role`, confirm:
1. WHERE: org-wide, site name, or group name?
2. DEVICE TYPE: APs, gateways, or a switch type?
3. SECURITY (SSID only): explicitly ask — options are:
   - MAC auth only, no password → ENHANCED_OPEN
   - MAC auth + PSK (dual factor) → WPA3_SAE or WPA2_PERSONAL + ask for passphrase
   - WPA3 PSK only → WPA3_SAE + ask for passphrase
   - WPA3 + WPA2 compatible → WPA3_SAE + transition mode + ask for passphrase
   - WPA2 PSK only → WPA2_PERSONAL + ask for passphrase
   Never assume or default — always confirm with the user.
4. VLAN IDs (SSID only): which VLAN(s)?

Firmware upgrades via `set_firmware_compliance` — the `/firmware/v1/upgrade` endpoint returns 404 on this instance.

## Credentials

Loaded from `config/credentials.yaml`. Override path with `CREDS_PATH` env var. Two account contexts: `source` (old system) and `target` (New Central). Most MCP tools use `target`.

## Adding new MCP tools

1. Pick the right domain server: `monitoring.py`, `config.py`, `ops.py`, `nac.py`, `glp.py`, or `rag.py`. The `tool_router.py` auto-exposes tools from enabled backends; add router mapping only for a new backend module/toolset.
2. Add `@mcp.tool(annotations=READ_ONLY|DIAGNOSTIC|DESTRUCTIVE|IDEMPOTENT_WRITE)` with verb_noun naming, no prefix. Import the annotation constant from `mcp_servers.shared`.
3. Use thin wrapper — delegate to `pipeline/clients/` or inline API call.
4. Keep output bounded for MCP clients:
   - list tools should expose `limit` / `offset` when the backend supports paging.
   - default `limit` values must stay at or below 200.
   - generic read-only GET tools must use `bound_collection_response()`.
5. Docstring: what it does, key args, pagination behavior, and any gotchas.
6. Update the tool count in the module docstring.
7. Rebuild/check the router catalog with `uv run python scripts/ingest_tools.py --products all` and run `uv run python scripts/validate_release.py`.

**Before editing:** use `Grep` to find the line, then `Read` only that slice.

## RAG-first rule

**Always consult the RAG tools before answering any question about Aruba/HPE config, APIs, or features.**

- **Exact API questions** (what enum values does field X accept, which endpoint configures Y, what fields does schema Z have) → call `lookup_api` first. It reads the parsed OpenAPI specs — lossless and authoritative. If it returns `[]`, fall back to `ask_docs` or `search_docs`.
- **Everything else** (how-to, concepts, design guidance) → call `ask_docs` for compact cited answers, or `search_docs` for raw retrieval. The corpus (53k+ chunks of developer docs, tech docs, NAC/VSG guides, and OpenAPI specs) is authoritative and must be consulted first.

Skip both only when:
- The question is purely about live device/client state (use monitoring tools directly).
- You already retrieved matching RAG results earlier in the same conversation turn.

When the tools return relevant hits, cite the `file_path` and use the content to inform your answer before calling any config/ops tool.

## API reference

Postman collections are in `resources/` (git-ignored — download with `python resources/download_collections.py` or import directly from the [HPE Aruba Networking Postman workspace](https://www.postman.com/hpe-aruba-networking/new-hpe-aruba-networking-central/collection/32717089-1d8b9f9e-2137-4a7d-b735-1b3c06f87e70)):
- `MRT APIs.postman_collection.json` — monitoring + troubleshooting endpoints
- `Configuration APIs.postman_collection.json` — config/provisioning endpoints

## Confirmed endpoint patterns

**Monitoring base:** `GET /network-monitoring/v1/...`

| Resource | Endpoint |
|---|---|
| AP detail | `/aps/{serial}` |
| AP radios | `/aps/{serial}/radios` |
| AP ports | `/aps/{serial}/ports` |
| AP cpu/memory trends | `/aps/{serial}/{metric}-utilization-trends?filter=timestamp gt {iso} and timestamp lt {iso}&site-id={id}` |
| AP throughput trends | `/aps/{serial}/throughput-trends?interface-type=WIRELESS` |
| Switch detail | `/switches/{serial}` |
| Switch interfaces | `/switches/{serial}/interfaces` |
| Switch VLANs | `/switches/{serial}/vlans` |
| Switch PoE | `/switches/{serial}/interface-poe` |
| Switch hw trends (cpu/mem) | `/switches/{serial}/hardware-trends?filter=...&site-id={id}` |
| Switch iface trends | `/switches/{serial}/interface-trends?filter=...&interface-id={id}` |
| Devices list | `/devices` |
| Clients list | `/clients` |
| Config health | `/network-config/v1alpha1/config-health/devices` |

**Troubleshooting base:** `POST /network-troubleshooting/v1alpha1/{device-type}/{serial}/{op}`
- Device types: `cx`, `aos-s`, `gateways`, `aps`
- Ops: `ping`, `traceroute`, `showCommands`, `reboot`, `poeBounce`, `portBounce`, `cableTest`
- All async: POST returns 202 + Location header → poll `{endpoint}/async-operations/{task_id}`
- CX port format: `"1/1/1"` — AOS-S: `"1"` — Gateway: `"GE 0/0/0"`

**Port profiles (confirmed two-step):**
1. `POST /network-config/v1/sw-port-profiles/{name}` with `{"description": "..."}` (shell)
2. `PUT /network-config/v1/sw-port-profiles/{name}` with full nested body (switchport/stp/poe/lldp)

## Token cost tips

- Use `Grep` + targeted `Read` with offset/limit instead of reading whole files.
- Filter Postman collection data with a tight Python script — don't dump raw output.
- Start a fresh conversation after long build sessions to reset context.
- Large tool results — grep before using: `list_devices`, `get_device_health`, `get_wireless_metrics`, `list_switch_ports`, `list_audit_logs`, `cx_show`.
- Use `site_id` / `serial_number` / `filter` params to limit API results before they hit context.
- Use `CENTRALMCP_ROUTER_MODE=minimal` and narrow `CENTRALMCP_TOOLSETS` for daily client configs; expose optional product backends only when needed.
