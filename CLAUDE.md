# API-Central — Aruba New Central Automation

HPE Aruba Central API tooling for network device migration, SSID config, switch provisioning, and GreenLake Platform management.

## Project layout

```
mcp_server.py          MCP tool definitions (thin wrappers — 60+ tools)
pipeline/
  clients/             CentralClient, GLPClient, MCPClient, TokenManager
  stages/              s1_discover → s8_verify (migration pipeline)
  config.py            Credentials loader (config/credentials.yaml or env)
  ssid_underlay.py     SSID build/delete logic
config/credentials.yaml  API credentials (never commit)
inputs/                CSV files for batch migration
outputs/               Reports and results
state/                 Pipeline state store (idempotent runs)
```

## MCP tools (mcp_server.py)

Tools follow `verb_noun` naming — no prefix. The server name `aruba-central` provides context.

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
3. SECURITY (SSID only): open, WPA3, WPA3+WPA2, or WPA2-PSK? (if PSK: passphrase?)
4. VLAN IDs (SSID only): which VLAN(s)?

Firmware upgrades via `set_firmware_compliance` — the `/firmware/v1/upgrade` endpoint returns 404 on this instance.

## Credentials

Loaded from `config/credentials.yaml`. Override path with `CREDS_PATH` env var. Two account contexts: `source` (old system) and `target` (New Central). Most MCP tools use `target`.

## Adding new MCP tools

1. Add `@mcp.tool()` function in `mcp_server.py`
2. Use thin wrapper pattern — delegate to `pipeline/clients/` or inline API call
3. Follow verb_noun naming, no prefix
4. Include docstring: what it does, args, returns, and any gotchas
5. Update the tool list in the module docstring at the top of `mcp_server.py`

**Before editing mcp_server.py:** use `Grep` to find the exact line number, then `Read` only the relevant slice. Never page through the whole file.

## API reference

Postman collections are in `resourse/` (note spelling):
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

- Use `Grep` + targeted `Read` with offset/limit instead of reading whole files
- Filter Postman collection data with a tight Python script — don't dump raw output
- Start a fresh conversation after long build sessions to reset context
