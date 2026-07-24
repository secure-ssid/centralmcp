# Example prompts

These examples are written for the default low-token router profile:

```env
CENTRALMCP_ROUTER_MODE=minimal
CENTRALMCP_TOOLSETS=central,glp,rag
```

In this profile, ask your MCP client to use `find_tool` first, then call `invoke_read_tool` for read-only work. That keeps the tool list small while still reaching the backend catalog.

## First smoke test

Natural-language prompt:

```text
Use centralmcp to find the tool for listing Aruba Central sites, then call it with limit 10.
```

Router flow:

```text
find_tool("list Aruba Central sites")
invoke_read_tool("list_sites", {"limit": 10, "offset": 0})
```

## Check active alerts

Natural-language prompt:

```text
Show me the active critical alerts in Aruba Central. Keep the result short.
```

Router flow:

```text
find_tool("active critical alerts")
invoke_read_tool("list_active_alerts", {"severity": "CRITICAL", "limit": 20, "offset": 0})
```

## Search clients without flooding context

Natural-language prompt:

```text
Find connected clients whose hostname contains "printer". Return only the first 25.
```

Router flow:

```text
find_tool("connected clients hostname contains")
invoke_read_tool("list_clients", {"hostname_contains": "printer", "limit": 25, "offset": 0})
```

## Ask documentation questions

Natural-language prompt:

```text
Use the Aruba docs index to explain how WPA3 SAE transition mode is represented. Include citations.
```

Router flow:

```text
find_tool("ask Aruba docs with citations")
invoke_read_tool("ask_docs", {"question": "WPA3 SAE transition mode", "top_k": 5})
```

## Look up exact API details

Natural-language prompt:

```text
Look up the exact OpenAPI endpoint or schema for Central client alerts. Do not guess from prose.
```

Router flow:

```text
find_tool("exact OpenAPI lookup")
invoke_read_tool("lookup_api", {"query": "Central client alerts", "top_k": 10})
```

## Inspect device inventory

Natural-language prompt:

```text
List the first 25 access points at a site, then tell me which tool can get device health for one serial number.
```

Router flow:

```text
find_tool("list devices by site")
invoke_read_tool("list_devices", {"device_type": "AP", "site_id": "SITE_ID", "limit": 25, "offset": 0})
find_tool("device health by serial number")
```

## Review configuration checkpoint behavior

New Central exposes checkpoint policy and automatic rollback status guidance,
not an API for selecting and restoring an arbitrary historical checkpoint.

```text
Explain Central checkpoint and rollback behavior, then preview a checkpoint
policy for my gateway scope without applying it.
```

```text
find_tool("configuration rollback status")
invoke_read_tool("get_config_rollback_status", {})
find_tool("build configuration checkpoint policy")
invoke_tool("build_config_checkpoint_policy", {"name": "gateway-checkpoints", "scope_id": "SCOPE_ID", "device_function": "GATEWAY", "dry_run": true})
```

## Plan an AOS8 migration

```text
Export the AOS8 configuration at /md, normalize the migration objects, and
build separate Classic Central and New Central plans. Show warnings and diffs;
do not write to either target.
```

```text
find_tool("AOS8 Classic New Central migration plan")
invoke_read_tool("aos8_migration_plan", {"config_path": "/md", "limit": 200})
```

The plan covers WLANs, roles, VLANs, AP groups, controllers, and policies and
preserves export or malformed-section warnings.

## Group GLP devices by model

```text
Group GreenLake Platform devices by model using the v2beta1 API and keep the
output to the first 25 values.
```

```text
find_tool("GLP group devices by model")
invoke_read_tool("group_glp_devices", {"group_by": "model", "limit": 25, "offset": 0})
```

## Optional products

Optional product starters are disabled unless you enable them:

```env
CENTRALMCP_PRODUCTS=clearpass,mist,apstra,aos8,edgeconnect,uxi,axis
CENTRALMCP_PRODUCT_ACCESS=read-only
```

Example prompt:

```text
Check whether the Mist optional backend is configured, then find the guarded read-only Mist GET tool.
```

Router flow:

```text
find_tool("Mist backend status")
invoke_read_tool("mist_status", {})
find_tool("Mist read-only GET")
```

Typed optional read prompt:

```text
List the first 10 Apstra blueprints and show only their IDs, labels, and status.
```

Router flow:

```text
find_tool("Apstra list blueprints")
invoke_read_tool("apstra_list_blueprints", {"limit": 10})
```

UXI read prompt:

```text
List the first 10 UXI sensors, then get online/testing status for one sensor ID.
```

Router flow:

```text
find_tool("UXI list sensors")
invoke_read_tool("uxi_list_sensors", {"page_size": 10})
find_tool("UXI sensor status")
invoke_read_tool("uxi_get_sensor_status", {"sensor_id": "SENSOR_ID"})
```

EdgeConnect compatibility prompt:

```text
Run the EdgeConnect API compatibility doctor. Do not enable legacy endpoints
or run operational calls.
```

```text
find_tool("EdgeConnect Swagger compatibility doctor")
invoke_read_tool("edgeconnect_doctor", {})
```

Lab write dry-run prompt:

```env
CENTRALMCP_PRODUCT_ACCESS=read-write
```

```text
Find the Mist alarm acknowledgement tool and show the dry-run payload for alarm ALARM_ID at site SITE_ID. Do not execute it.
```

Router flow:

```text
find_tool("Mist acknowledge alarm")
invoke_tool("mist_ack_alarm", {"site_id": "SITE_ID", "alarm_id": "ALARM_ID", "note": "lab verified", "dry_run": true})
```

## Preview a resumable AOS8 migration run

```text
Preview a resumable AOS8 migration run to New Central for the Branch APs
scope using the migration plan I already exported. Do not create or apply
it yet.
```

Router flow:

```text
find_tool("AOS8 preview migration run")
invoke_read_tool("aos8_preview_migration_run", {"target_type": "new_central", "migration_plan": {"wlans": []}, "scope_name": "Branch APs", "persona": "CAMPUS_AP", "limit": 50, "offset": 0})
```

Once the preview looks right, create the run and apply it dry-run first:

```text
find_tool("AOS8 create migration run")
invoke_tool("aos8_create_migration_run", {"target_type": "new_central", "migration_plan": {"wlans": []}, "scope_name": "Branch APs", "persona": "CAMPUS_AP", "conflict_policy": "fail"})
find_tool("AOS8 apply migration run")
invoke_tool("aos8_apply_migration_run", {"run_id": "RUN_ID", "dry_run": true})
```

Only pass `dry_run=false` with `confirm=true` and any required target secrets
after reviewing the dry-run output. New Central rollback guidance is limited
to its post-change checkpoint policy and automatic device rollback.

## Collect Mist device diagnostic results

```text
Collect the results for a diagnostic session I already started on a Mist
device, bounded to 30 seconds and 50 events.
```

Router flow:

```text
find_tool("Mist collect diagnostic results")
invoke_read_tool("mist_collect_diagnostic_results", {"site_id": "SITE_ID", "device_id": "DEVICE_ID", "session_id": "SESSION_ID", "timeout_seconds": 30, "max_events": 50})
```

This requires `MIST_API_TOKEN` (or `MIST_SESSION_COOKIE` + `MIST_CSRF_TOKEN`)
and the `websockets` dependency; it only connects to the documented regional
`WS /api-ws/v1/stream` endpoint derived from `MIST_HOST`.

## Look up GreenLake Platform RBAC and SCIM details

```text
List the first 10 GreenLake Platform RBAC role assignments, then look up SCIM
group membership for one group ID.
```

Router flow:

```text
find_tool("GLP RBAC role assignments")
invoke_read_tool("list_glp_role_assignments", {"limit": 10, "offset": 0})
find_tool("GLP SCIM group users")
invoke_read_tool("list_glp_scim_group_users", {"group_id": "GROUP_ID"})
```

## Write or destructive work

For writes, make intent explicit and dry-run first when the selected tool supports it:

```text
Find the tool to build an SSID, show me the dry-run payload only, and do not apply changes yet.
```

Use `invoke_tool` only after the user intentionally asks for a write/destructive action. The router marks it destructive because it can dispatch write-capable backend tools.
