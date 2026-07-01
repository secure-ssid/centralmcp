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
| Controller inventory | `aos8_list_controllers` | Bounded root-scope `show switches` read |
| Software version | `aos8_get_version` | Bounded root-scope `show version` read |
| License inventory | `aos8_list_licenses` | Bounded root-scope `show license` read |
| AP inventory | `aos8_list_aps` | Bounded `show ap database` read scoped by `config_path` |
| Active APs | `aos8_list_active_aps` | Bounded `show ap active` read scoped by `config_path` |
| Client visibility | `aos8_list_clients` | Bounded `show user-table` read scoped by `config_path` |
| Client lookup | `aos8_find_client` | Bounded `show user-table` lookup by exactly one MAC, IP, or username |
| Client detail | `aos8_get_client_detail` | Bounded verbose `show user-table verbose mac` read scoped by `config_path` |
| Client association history | `aos8_get_client_history` | Bounded root-scope `show ap association history client-mac` read |
| Active alarms | `aos8_get_alarms` | Bounded `show alarms` read scoped by `config_path` |
| Audit trail | `aos8_get_audit_trail` | Bounded root-scope `show audit-trail` read |
| Events | `aos8_get_events` | Bounded `show events` read scoped by `config_path` |
| MD hierarchy | `aos8_get_md_hierarchy` | Bounded root-scope `show configuration node-hierarchy` read |
| RF neighbors | `aos8_get_rf_neighbors` | Bounded `show ap arm-neighbors ap-name` read scoped by AP name and `config_path` |
| Cluster state | `aos8_get_cluster_state` | Bounded root-scope `show lc-cluster group-membership` read |
| AP wired ports | `aos8_get_ap_wired_ports` | Bounded root-scope `show ap port status ap-name` read for one AP |
| IPsec tunnel state | `aos8_get_ipsec_tunnels` | Bounded root-scope `show crypto ipsec sa` read |
| System logs | `aos8_get_system_logs` | Bounded root-scope `show log system` diagnostic read with capped count |
| ARM history | `aos8_get_ap_arm_history` | Bounded `show ap arm history` RF diagnostic read scoped by `config_path` |
| AP monitor stats | `aos8_get_ap_monitor_stats` | Bounded `show ap monitor stats` RF diagnostic read scoped by `config_path` |
| BSS table | `aos8_list_bss` | Bounded `show ap bss-table` read scoped by `config_path` |
| Radio summary | `aos8_get_radio_summary` | Bounded `show ap radio-summary` read scoped by `config_path` |
| AP-group inventory | `aos8_list_ap_groups` | Configuration-object read scoped by `config_path` |
| SSID profile summary | `aos8_list_ssid_profiles` | Configuration-object read scoped by `config_path` |
| Virtual AP profiles | `aos8_list_virtual_aps` | Configuration-object read scoped by `config_path` |
| User roles | `aos8_list_user_roles` | Configuration-object read scoped by `config_path` |
| Generic lab write | `aos8_write` | Guarded POST/PUT/PATCH/DELETE to `/v1/*`; dry-run default |
| SSID profile lab write | `aos8_manage_ssid_profile` | Create/update/delete `ssid_prof` objects; dry-run default; returns write-memory hint |
| Virtual AP lab write | `aos8_manage_virtual_ap` | Create/update/delete `virtual_ap` objects; dry-run default; returns write-memory hint |
| AP group lab write | `aos8_manage_ap_group` | Create/update/delete `ap_group` objects; dry-run default; returns write-memory hint |
| User role lab write | `aos8_manage_user_role` | Create/update/delete `role` objects with `rolename`; dry-run default; returns write-memory hint |
| VLAN lab write | `aos8_manage_vlan` | Create/update/delete `vlan_id` objects; dry-run default; returns write-memory hint |
| Persist staged AOS8 config | `aos8_write_memory` | POST write-memory for an affected `config_path`; dry-run default |

## EdgeConnect implemented starters

| Workflow | Tool | Notes |
|---|---|---|
| Appliance inventory | `edgeconnect_list_appliances` | IDs/names/site/status only |
| Appliance system info | `edgeconnect_get_system_info` | Model/version/status/alarm summary from `/rest/json/systemInfo` |
| Appliance alarms | `edgeconnect_list_alarms` | Outstanding alarms from `/rest/json/alarm`, bounded by `limit` / `offset` |
| Appliance interface state | `edgeconnect_get_interface_state` | Compact interface admin/oper/IP/speed view from `/gms/rest/interfaceState`, scoped by appliance `nePk` |
| Interface labels | `edgeconnect_list_interface_labels` / `edgeconnect_set_interface_labels` / `edgeconnect_apply_interface_labels` | Compact WAN/LAN interface-label read, guarded complete-label-map lab write, and guarded push-to-appliance action |
| Appliance disk report | `edgeconnect_get_disk_report` | Compact disk/storage health view from `/gms/rest/configReportDisk`, scoped by appliance `nePk` |
| Appliance reachability | `edgeconnect_get_appliance_reachability` | Compact reachability from `/gms/rest/reachability/{appliance,gms,gms2}`, scoped by appliance `nePk` |
| Fleet reachability | `edgeconnect_list_appliance_reachability` | Compact all-appliance reachability from `/gms/rest/reachability/gms2/appliancesReachability` |
| Overlay configuration | `edgeconnect_list_overlays` | Compact overlay configs from `/gms/rest/gms/overlays/config`, with optional overlay ID filter |
| Overlay priority | `edgeconnect_get_overlay_priority` | Compact overlay priority order from `/gms/rest/gms/overlays/priority` |
| Topology link status | `edgeconnect_get_topology_link_info` | Sparse topology link status from `/gms/rest/gms/topologyConfig/linkInfo/v2`, scoped by overlay ID |
| Route maps | `edgeconnect_get_route_maps` | Compact route policy settings from `/gms/rest/routeMaps`, scoped by appliance `nePk` |
| Route labels | `edgeconnect_list_route_labels` / `edgeconnect_set_route_labels` | Compact route-label read plus guarded lab write to `/gms/rest/routeLabels` |
| Firewall zones | `edgeconnect_list_zones` / `edgeconnect_set_zones` | Compact firewall-zone read plus guarded complete-map lab write to `/gms/rest/zones` |
| Zone-based firewall | `edgeconnect_get_zone_firewall_status` / `edgeconnect_set_zone_firewall_status` | Read and guarded lab write for End-to-End Zone-Based Firewall status at `/gms/rest/zones/eeEnable` |
| Zone ID allocation | `edgeconnect_get_next_zone_id` / `edgeconnect_set_next_zone_id` | Read and guarded lab write for next firewall-zone ID at `/gms/rest/zones/nextId` |
| VRF zone maps | `edgeconnect_list_vrf_segment_zones` / `edgeconnect_list_vrf_zone_map` | Compact VRF-to-zone mappings from `/gms/rest/zones/vrfSegmentZonesMap` and `/gms/rest/zones/vrfZonesMap` |
| Tunnel health | `edgeconnect_list_tunnels` | Physical tunnel status from `/gms/rest/tunnels2/physical`, with optional filters |
| Tunnel metadata | `edgeconnect_get_tunnel_metadata` | Compact tunnel count metadata from `/gms/rest/tunnels2?metaData=true` |
| VRF/routing segments | `edgeconnect_list_vrf_segments` | Compact routing-segment inventory from `/gms/rest/vrf/config/segments`, with optional segment ID filter |
| Network role and site | `edgeconnect_get_appliance_network_role_site` / `edgeconnect_set_appliance_network_role_site` | Compact appliance network-role/site read plus guarded lab write to `/gms/rest/appliance/networkRoleAndSite` |
| Maintenance mode | `edgeconnect_get_maintenance_mode` / `edgeconnect_set_maintenance_mode` | Compact maintenance-mode read plus guarded lab write to `/gms/rest/maintenanceMode` |
| Persist appliance changes | `edgeconnect_save_changes` | Guarded lab write to `/gms/rest/appliance/saveChanges`, dry-run default and `confirm=True` required |
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
