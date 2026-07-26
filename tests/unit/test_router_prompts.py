from __future__ import annotations

from mcp.server.fastmcp import FastMCP

import mcp_servers.tool_router as router
from mcp_servers.prompts import AOS8_BACKEND_SERVER, register_router_prompts


def _prompts_with_backends(enabled_backends):
    """Register the router prompt set on a throwaway server."""
    server = FastMCP("prompt-test")
    register_router_prompts(server, enabled_backends=enabled_backends)
    return {prompt.name: prompt for prompt in server._prompt_manager.list_prompts()}, server


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


def test_router_registers_aos8_migration_prompts_when_backend_enabled():
    prompts, _ = _prompts_with_backends({"aruba-config", AOS8_BACKEND_SERVER})

    assert {"aos8_migration_readiness", "aos8_staged_migration_plan"} <= set(prompts)


def test_aos8_prompts_are_omitted_when_backend_disabled():
    """They instruct the model to call aos8_* tools by name; without the
    backend enabled those tools are not in the tool list at all."""
    prompts, _ = _prompts_with_backends({"aruba-config", "aruba-glp"})

    assert "aos8_migration_readiness" not in prompts
    assert "aos8_staged_migration_plan" not in prompts
    # The backend-independent prompts are still registered.
    assert "network_health_overview" in prompts
    assert "troubleshoot_site" in prompts


def test_prompts_default_to_registering_everything():
    """A caller that does not declare its backend set keeps every prompt."""
    prompts, _ = _prompts_with_backends(None)

    assert {"aos8_migration_readiness", "aos8_staged_migration_plan"} <= set(prompts)


def test_live_router_gates_aos8_prompts_on_the_enabled_backend_set():
    prompts = {prompt.name: prompt for prompt in router.mcp._prompt_manager.list_prompts()}
    expected = AOS8_BACKEND_SERVER in router._BACKENDS

    assert ("aos8_migration_readiness" in prompts) is expected
    assert ("aos8_staged_migration_plan" in prompts) is expected


def test_aos8_migration_readiness_prompt_references_dependency_plan_tool():
    _prompts, server = _prompts_with_backends({AOS8_BACKEND_SERVER})
    prompt = server._prompt_manager.get_prompt("aos8_migration_readiness")
    assert prompt is not None

    text = prompt.fn("/md/lab")

    assert "aos8_export_all" in text
    assert "aos8_migration_plan" in text
    assert "aos8_migration_dependency_plan" in text
    assert "/md/lab" in text
    assert "requires_secret_input" in text


def test_aos8_staged_migration_plan_prompt_orders_stages_before_preview():
    _prompts, server = _prompts_with_backends({AOS8_BACKEND_SERVER})
    prompt = server._prompt_manager.get_prompt("aos8_staged_migration_plan")
    assert prompt is not None

    text = prompt.fn("new_central", "/md")

    assert "aos8_migration_dependency_plan" in text
    assert "aos8_preview_migration_run" in text
    assert "apply_order" in text
    assert "aos8_apply_migration_run" in text
    assert "new_central" in text
