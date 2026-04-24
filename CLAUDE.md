# API-Central — Aruba New Central Automation

HPE Aruba Central API tooling for network device migration, SSID config, switch provisioning, and GreenLake Platform management.

## Project layout

```
mcp_servers/
  tool_router.py       Unified router — proxies the 6 domain servers under one MCP entrypoint
  monitoring.py        Monitoring tools — device health, trends, wireless metrics
  config.py            Config tools — SSIDs, VLANs, profiles, webhooks, firmware
  ops.py               Ops tools — reboots, ping, cable test, PoE bounce, GLP mgmt
  nac.py               NAC tools — MAC reg, MPSK, visitors, auth servers, AAA profiles, AAA test
  glp.py               GreenLake Platform tools — devices, subscriptions, users, audit logs
  rag.py               RAG tools — search_docs over ingested Aruba/HPE docs (Qdrant + Ollama)
  shared.py            Shared utilities and helpers
pipeline/
  clients/             CentralClient, GLPClient, MCPClient, TokenManager, OllamaClient, QdrantClient
  stages/              s1_discover → s8_verify (migration pipeline)
  config.py            Credentials loader (config/credentials.yaml or env)
  create_ssid.py       SSID build/delete logic (underlay + overlay)
ingestion/
  ingest_docs.py       Scrape + chunk + embed Aruba/HPE docs into Qdrant
  sources/             Raw scraped docs (git-ignored — regenerable)
scripts/
  ingest_tools.py      Re-index the RAG corpus against the configured Qdrant/Ollama stack
docker-compose.yml     Qdrant + Ollama runtime for the RAG server
config/credentials.yaml  API credentials (never commit)
resources/             Postman API collections (monitoring + config endpoints)
inputs/                CSV files for batch migration
outputs/               Reports and results
state/                 Pipeline state store (idempotent runs)
.cursor/
  mcp.json             Router-only entry (aruba-tool-router) — low token overhead
  mcp.dev.json         All 6 domain servers directly — full introspection for debugging
```

## MCP tools (`mcp_servers/`)

Six domain servers — `monitoring.py`, `config.py`, `ops.py`, `nac.py`, `glp.py`, `rag.py` — each registered in `.cursor/mcp.dev.json`. For day-to-day use, `tool_router.py` is the single entrypoint registered in `.cursor/mcp.json` and proxies to all six.
Tools follow `verb_noun` naming — no prefix. The server name provides context (`aruba-monitoring`, `aruba-config`, `aruba-ops`, `aruba-nac`, `aruba-glp`, `aruba-rag`).

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

1. Pick the right domain server: `monitoring.py`, `config.py`, `ops.py`, `nac.py`, `glp.py`, or `rag.py`. The `tool_router.py` auto-exposes any tool registered on those servers — no router edits needed.
2. Add `@mcp.tool()` with verb_noun naming, no prefix.
3. Use thin wrapper — delegate to `pipeline/clients/` or inline API call.
4. Docstring: what it does, key args, and any gotchas.
5. Update the tool count in the module docstring.

**Before editing:** use `Grep` to find the line, then `Read` only that slice.

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

**Troubleshooting base:** `POST /network-troubleshooting/v1/{device-type}/{serial}/{op}`
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
