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
invoke_read_tool("ask_docs", {"query": "WPA3 SAE transition mode", "top_k": 5})
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

## Optional products

Optional product starters are disabled unless you enable them:

```env
CENTRALMCP_PRODUCTS=clearpass,mist,apstra,aos8,edgeconnect
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

## Write or destructive work

For writes, make intent explicit and dry-run first when the selected tool supports it:

```text
Find the tool to build an SSID, show me the dry-run payload only, and do not apply changes yet.
```

Use `invoke_tool` only after the user intentionally asks for a write/destructive action. The router marks it destructive because it can dispatch write-capable backend tools.
