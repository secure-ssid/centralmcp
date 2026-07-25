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


def test_router_registers_aos8_migration_prompts():
    prompts = {prompt.name: prompt for prompt in router.mcp._prompt_manager.list_prompts()}
    assert {"aos8_migration_readiness", "aos8_staged_migration_plan"} <= set(prompts)


def test_aos8_migration_readiness_prompt_references_dependency_plan_tool():
    prompt = router.mcp._prompt_manager.get_prompt("aos8_migration_readiness")
    assert prompt is not None

    text = prompt.fn("/md/lab")

    assert "aos8_export_all" in text
    assert "aos8_migration_plan" in text
    assert "aos8_migration_dependency_plan" in text
    assert "/md/lab" in text
    assert "requires_secret_input" in text


def test_aos8_staged_migration_plan_prompt_orders_stages_before_preview():
    prompt = router.mcp._prompt_manager.get_prompt("aos8_staged_migration_plan")
    assert prompt is not None

    text = prompt.fn("new_central", "/md")

    assert "aos8_migration_dependency_plan" in text
    assert "aos8_preview_migration_run" in text
    assert "apply_order" in text
    assert "aos8_apply_migration_run" in text
    assert "new_central" in text
