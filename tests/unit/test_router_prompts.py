from __future__ import annotations

import mcp_servers.tool_router as router


def test_router_registers_guided_prompts():
    prompts = {prompt.name: prompt for prompt in router.mcp._prompt_manager.list_prompts()}

    assert {
        "network_health_overview",
        "troubleshoot_site",
        "client_connectivity_check",
        "investigate_device_events",
        "compare_site_health",
        "critical_alerts_review",
        "failed_clients_investigation",
    } <= set(prompts)


def test_prompt_guides_router_tool_usage():
    prompt = router.mcp._prompt_manager.get_prompt("troubleshoot_site")
    assert prompt is not None

    text = prompt.fn("Branch Office")

    assert "find_tool" in text
    assert "invoke_read_tool" in text
    assert "Branch Office" in text
