# Typed product workflow roadmap

The optional product starters are intentionally small and read-only. They give
users a safe way to connect ClearPass, Mist, Apstra, ArubaOS 8, and EdgeConnect
without loading every possible product workflow into the MCP tool list.

Use this page as the implementation roadmap for typed tools that should graduate
from generic GET exploration into named MCP workflows.

## Promotion rule

Promote a generic GET pattern to a typed tool when it is:

| Signal | Why it matters |
|---|---|
| Repeated in real troubleshooting | Saves prompt tokens and user time |
| Easy to type safely | Clear parameters and bounded output |
| Useful across tenants | More than a one-off lab endpoint |
| Write/destructive | Needs explicit MCP annotations and confirmation |

## ClearPass candidates

| Workflow | Proposed tool | Notes |
|---|---|---|
| Check endpoint by MAC | `clearpass_get_endpoint_by_mac` | Normalize MAC input and return compact endpoint/profile/status fields |
| List recent auth failures | `clearpass_list_auth_failures` | Bound by `limit` / `offset`; include username, MAC, NAD, reason |
| Show NAD status | `clearpass_get_network_device` | Useful for RADIUS/TACACS troubleshooting |
| Find guest by email/name | `clearpass_find_guest` | Read-only lookup only |

## Mist candidates

| Workflow | Proposed tool | Notes |
|---|---|---|
| List org sites | `mist_list_sites` | Return site IDs/names/timezone only by default |
| Client lookup by MAC | `mist_get_client` | Compact client health, AP, WLAN, RSSI/SNR |
| Site WLAN summary | `mist_list_wlans` | Bound output for model context |
| Recent site alarms | `mist_list_alarms` | Severity/time bounded |

## Apstra candidates

| Workflow | Proposed tool | Notes |
|---|---|---|
| List blueprints | `apstra_list_blueprints` | IDs/names/state only |
| Blueprint anomalies | `apstra_list_anomalies` | Read-only fabric health |
| Device details | `apstra_get_device` | Compact system/fabric role/status |

## ArubaOS 8 candidates

| Workflow | Proposed tool | Notes |
|---|---|---|
| Controller status | `aos8_get_controller_status` | Read-only summary |
| AP inventory | `aos8_list_aps` | Bound by `limit` / `offset` |
| WLAN summary | `aos8_list_wlans` | Compact profile/status output |

## EdgeConnect candidates

| Workflow | Proposed tool | Notes |
|---|---|---|
| Appliance inventory | `edgeconnect_list_appliances` | IDs/names/site/status only |
| Tunnel health | `edgeconnect_list_tunnels` | Bound output and status filters |
| Alarm summary | `edgeconnect_list_alarms` | Severity/time bounded |

## Design constraints

1. Keep optional products opt-in via `CENTRALMCP_PRODUCTS`.
2. Prefer read-only typed tools first.
3. Keep outputs compact and paginated.
4. Require explicit destructive annotations and confirmation for writes.
5. Keep product tokens in `.env`; do not duplicate them into MCP client configs.
