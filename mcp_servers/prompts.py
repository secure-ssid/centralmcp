"""Reusable MCP prompts for common Central/GLP workflows.

Prompts are guidance templates, not tools: they add no API calls and do not
increase the router tool list. They steer clients toward the low-token router
pattern (`find_tool` -> `invoke_read_tool` for reads, `invoke_tool` for writes)
and the compact RAG tools.
"""

from __future__ import annotations

from mcp.server.fastmcp import FastMCP


def register_router_prompts(mcp: FastMCP) -> None:
    """Register guided workflows on the unified tool router."""

    @mcp.prompt(
        name="network_health_overview",
        description="Summarize tenant/site health, active alerts, and worst affected scopes.",
    )
    def network_health_overview() -> str:
        return """Create a concise Aruba Central health overview.

Use `find_tool` and `invoke_read_tool` for read-only checks rather than guessing direct tool names.
Workflow:
1. Find and call tools for tenant health, sites/client health, and active alerts.
2. Prioritize scopes with health below 80, critical alerts, or many offline devices.
3. Drill into the top 3 worst scopes with scope/device helpers if available.
4. Return a short table: scope/site, health, critical alerts, likely cause, next action.
Keep raw payloads out of the answer unless a detail is needed for troubleshooting."""

    @mcp.prompt(
        name="troubleshoot_site",
        description="Investigate health, alerts, devices, and likely causes for one site.",
    )
    def troubleshoot_site(site_name: str) -> str:
        return f"""Troubleshoot Aruba Central site `{site_name}`.

Use `find_tool` and `invoke_read_tool` for read-only checks.
Workflow:
1. Find the site/scope by name and capture its scope ID.
2. Pull site/client health, active alerts, and devices for that scope.
3. Separate AP, switch, gateway, and client symptoms.
4. If config-health tools are available, check affected devices for config issues.
5. Summarize probable root cause, impacted devices/clients, and safe next actions.
Do not run destructive tools unless the user explicitly asks and confirms."""

    @mcp.prompt(
        name="client_connectivity_check",
        description="Investigate one client by MAC/name/IP and correlate AP/site symptoms.",
    )
    def client_connectivity_check(client_query: str) -> str:
        return f"""Investigate client connectivity for `{client_query}`.

Use `find_tool` and `invoke_read_tool` for read-only checks.
Workflow:
1. Find the client by MAC, IP, hostname, or username.
2. Identify connected/last-connected AP, site/scope, status, RSSI/SNR, VLAN/SSID, and last seen time.
3. Check active alerts and site health for the same scope.
4. Check the AP/device health and radios if available.
5. If event tools are available, inspect events around last seen time +/- 2 hours.
Return: current state, likely failure domain, evidence, and next safe troubleshooting step."""

    @mcp.prompt(
        name="investigate_device_events",
        description="Investigate one device's recent events and related health indicators.",
    )
    def investigate_device_events(serial_number: str, time_range: str = "last 2 hours") -> str:
        return f"""Investigate recent events for device `{serial_number}` over `{time_range}`.

Use `find_tool` and `invoke_read_tool` for read-only checks.
Workflow:
1. Find the device and note site/scope, device type, firmware, and status.
2. Pull device health/details and recent alerts for its scope.
3. Find event or audit tools and query the requested window.
4. Group events by severity/category and identify repeated or correlated failures.
5. Recommend next read-only checks first; avoid destructive actions unless explicitly requested."""

    @mcp.prompt(
        name="compare_site_health",
        description="Compare multiple sites and rank them by health/risk.",
    )
    def compare_site_health(site_names: str) -> str:
        return f"""Compare Aruba Central health for these comma-separated sites: `{site_names}`.

Use `find_tool` and `invoke_read_tool` for read-only checks.
Workflow:
1. Resolve each site/scope name.
2. Pull site/client health, active alert counts, and device counts for each.
3. Normalize results into one table sorted worst-first.
4. Highlight outliers: high client failures, critical alerts, offline infrastructure, config drift.
5. End with the top 3 recommended follow-up investigations."""

    @mcp.prompt(
        name="critical_alerts_review",
        description="Review active critical/high alerts and group them by category and scope.",
    )
    def critical_alerts_review() -> str:
        return """Review active critical/high Aruba Central alerts.

Use `find_tool` and `invoke_read_tool` for read-only checks.
Workflow:
1. Pull active alerts, filtering to critical/high severity or priority when possible.
2. Group alerts by site/scope, category, and impacted device type.
3. For the top groups, pull scope/device context to avoid listing isolated noise.
4. Return a compact action board: alert group, impacted scope, count, first seen, likely owner, next action.
Do not clear/defer/reactivate alerts unless the user explicitly asks and confirms."""

    @mcp.prompt(
        name="failed_clients_investigation",
        description="Investigate failed clients at a site and correlate to infrastructure.",
    )
    def failed_clients_investigation(site_name: str) -> str:
        return f"""Investigate failed clients at site `{site_name}`.

Use `find_tool` and `invoke_read_tool` for read-only checks.
Workflow:
1. Resolve the site/scope.
2. Pull client/site health and identify failed or unhealthy clients.
3. Group failed clients by SSID, VLAN, band, AP, and failure reason when available.
4. Check the top 5 implicated APs/devices for health, radio, and alert signals.
5. Return probable pattern, supporting evidence, and the safest next checks."""
