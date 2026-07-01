# Typed product workflow roadmap

The optional product starters are intentionally small and lab-friendly. They
give users a safe way to connect ClearPass, Mist, Apstra, ArubaOS 8, and
EdgeConnect with read workflows plus guarded write tools, without loading every
possible product workflow into the MCP tool list.

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

## ClearPass implemented starters

| Workflow | Tool | Notes |
|---|---|---|
| Check endpoint by MAC | `clearpass_get_endpoint_by_mac` | Normalize MAC input and return compact endpoint/profile/status fields |
| List recent auth failures | `clearpass_list_auth_failures` | Bound by `limit` / `offset`; include username, MAC, NAD, reason |
| Show NAD status | `clearpass_get_network_device` | Useful for RADIUS/TACACS troubleshooting |
| Find guest by email/name | `clearpass_find_guest` | Read-only lookup only |

## Mist implemented starters

| Workflow | Tool | Notes |
|---|---|---|
| List org sites | `mist_list_sites` | Return site IDs/names/timezone only by default |
| Client lookup by MAC | `mist_get_client` | Compact client health, AP, WLAN, RSSI/SNR |
| Site WLAN summary | `mist_list_wlans` | Bound output for model context |
| Recent site alarms | `mist_list_alarms` | Severity/time bounded |

## ClearPass implemented lab writes

| Workflow | Tool | Notes |
|---|---|---|
| Generic lab write | `clearpass_write` | Guarded POST/PUT/PATCH/DELETE to `/api/*`; dry-run default |
| Endpoint attributes | `clearpass_update_endpoint_attributes` | Patch endpoint attributes by MAC; optional CoA query flag |
| Delete endpoint | `clearpass_delete_endpoint` | Destructive endpoint delete by MAC |
| Enable/disable guest | `clearpass_set_guest_enabled` | Patch guest enabled state by username or ID |
| Delete guest | `clearpass_delete_guest` | Destructive guest delete by username or ID |

## Mist implemented lab writes

| Workflow | Tool | Notes |
|---|---|---|
| Generic lab write | `mist_write` | Guarded POST/PUT/PATCH/DELETE to `/api/v1/*`; dry-run default |
| Ack site alarm | `mist_ack_alarm` | POST site alarm acknowledgement |
| Unack site alarm | `mist_unack_alarm` | POST site alarm unacknowledgement |
| Delete WLAN | `mist_delete_wlan` | Destructive site WLAN delete |

## Apstra implemented starters

| Workflow | Tool | Notes |
|---|---|---|
| List blueprints | `apstra_list_blueprints` | IDs/names/state only |
| List design templates | `apstra_list_templates` | Compact template inventory from `/api/design/templates` |
| Blueprint anomalies | `apstra_list_anomalies` | Read-only fabric health |
| Blueprint racks | `apstra_list_racks` | Compact rack topology from `/api/blueprints/{id}/racks` |
| Blueprint routing zones | `apstra_list_routing_zones` | Compact security-zone/VRF view from `/api/blueprints/{id}/security-zones` |
| Blueprint virtual networks | `apstra_list_virtual_networks` | Compact VN/subnet/binding view from `/api/blueprints/{id}/virtual-networks` |
| Blueprint remote gateways | `apstra_list_remote_gateways` | Compact remote EVPN gateway view from `/api/blueprints/{id}/remote_gateways` |
| Blueprint connectivity templates | `apstra_list_connectivity_templates` | Compact assignable policy view from `/api/blueprints/{id}/obj-policy-export` |
| Blueprint application endpoints | `apstra_list_application_endpoints` | Compact CT attachment-point view from `/api/blueprints/{id}/obj-policy-application-points` |
| Blueprint diff status | `apstra_get_diff_status` | Compact staging-vs-active status from `/api/blueprints/{id}/diff-status` |
| Blueprint protocol sessions | `apstra_list_protocol_sessions` | Compact protocol/BGP session status from `/api/blueprints/{id}/protocol-sessions` |
| Blueprint system info | `apstra_get_system_info` | Compact systems/devices from `/api/blueprints/{id}/experience/web/system-info` |
| Generic lab write | `apstra_write` | Guarded POST/PUT/PATCH/DELETE to `/api/*`; dry-run default |

## ArubaOS 8 implemented starters

| Workflow | Tool | Notes |
|---|---|---|
| Show command | `aos8_show_command` | Only permits `show ...` commands via `/v1/configuration/showcommand` |
| AP inventory | `aos8_list_aps` | Bounded `show ap database` read scoped by `config_path` |
| AP-group inventory | `aos8_list_ap_groups` | Configuration-object read scoped by `config_path` |
| SSID profile summary | `aos8_list_ssid_profiles` | Configuration-object read scoped by `config_path` |
| Generic lab write | `aos8_write` | Guarded POST/PUT/PATCH/DELETE to `/v1/*`; dry-run default |

## EdgeConnect implemented starters

| Workflow | Tool | Notes |
|---|---|---|
| Appliance inventory | `edgeconnect_list_appliances` | IDs/names/site/status only |
| Appliance system info | `edgeconnect_get_system_info` | Model/version/status/alarm summary from `/rest/json/systemInfo` |
| Appliance alarms | `edgeconnect_list_alarms` | Outstanding alarms from `/rest/json/alarm`, bounded by `limit` / `offset` |
| Tunnel health | `edgeconnect_list_tunnels` | Physical tunnel status from `/gms/rest/tunnels2/physical`, with optional filters |
| Tunnel metadata | `edgeconnect_get_tunnel_metadata` | Compact tunnel count metadata from `/gms/rest/tunnels2?metaData=true` |
| Generic lab write | `edgeconnect_write` | Guarded POST/PUT/PATCH/DELETE to Orchestrator REST paths; dry-run default |

## Remaining optional typed candidates

No verified optional typed candidates are queued. Continue promoting new reads
or lab writes only after confirming stable endpoint patterns from upstream or
public references.

## Design constraints

1. Keep optional products opt-in via `CENTRALMCP_PRODUCTS`.
2. Include both read and guarded write options for lab workflows.
3. Keep outputs compact and paginated.
4. Require explicit write/destructive annotations and confirmation for writes.
5. Keep product tokens in `.env`; do not duplicate them into MCP client configs.
6. Honor `CENTRALMCP_PRODUCT_ACCESS=read-only` by hiding/blocking optional
   product write tools.
