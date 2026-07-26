from __future__ import annotations

import ast
import re
from pathlib import Path

from scripts import setup_wizard

REPO_ROOT = Path(__file__).resolve().parents[2]


def _script_text() -> str:
    return (REPO_ROOT / "scripts" / "run_http_router.sh").read_text()


def _http_helper_allowed_keys() -> set[str]:
    text = _script_text()
    prefix = "allowed_keys = "
    start = text.index(prefix) + len(prefix)
    end = text.index("}\nfor raw_line", start) + 1
    return set(ast.literal_eval(text[start:end]))


def test_http_router_loads_wizard_optional_product_env_keys():
    allowed_keys = _http_helper_allowed_keys()
    wizard_product_keys = {
        key
        for meta in setup_wizard.PRODUCT_ENV.values()
        for key in meta["vars"]
    }

    assert wizard_product_keys <= allowed_keys
    assert "CENTRALMCP_PRODUCTS" in allowed_keys
    assert "CENTRALMCP_PRODUCT_ACCESS" in allowed_keys


def test_http_router_loads_lab_safety_flags():
    allowed_keys = _http_helper_allowed_keys()

    assert "CENTRALMCP_ALLOW_LOCAL_PRODUCT_URLS" in allowed_keys
    assert "CENTRALMCP_GLP_V2BETA1_WRITES" in allowed_keys


def test_http_router_banner_shows_product_access_mode():
    text = _script_text()

    assert 'export CENTRALMCP_PRODUCT_ACCESS="${CENTRALMCP_PRODUCT_ACCESS:-read-only}"' in text
    assert "access:   ${CENTRALMCP_PRODUCT_ACCESS}" in text


def test_http_router_loads_http_hardening_env_keys():
    allowed_keys = _http_helper_allowed_keys()

    assert "MCP_ALLOWED_HOSTS" in allowed_keys
    assert "MCP_ALLOWED_ORIGINS" in allowed_keys
    assert "MCP_DNS_REBINDING_PROTECTION" in allowed_keys
    assert "MCP_HTTP_BEARER_TOKEN" in allowed_keys
    assert "CENTRALMCP_ALLOW_WILDCARD_HTTP_ALLOWLIST" in allowed_keys
    assert "CENTRALMCP_ALLOW_INSECURE_HTTP_BINDING" in allowed_keys


def test_http_router_loads_platform_write_gate_env_keys():
    allowed_keys = _http_helper_allowed_keys()

    for platform in (
        "CENTRAL",
        "AOS8",
        "EDGECONNECT",
        "APSTRA",
        "MIST",
        "CLEARPASS",
        "UXI",
        "AXIS",
    ):
        assert f"CENTRALMCP_{platform}_WRITES" in allowed_keys


def test_http_router_loads_troubleshooting_version_and_tokenize_keys():
    allowed_keys = _http_helper_allowed_keys()

    assert "CENTRALMCP_TROUBLESHOOTING_API_VERSION" in allowed_keys
    assert "CENTRALMCP_TOKENIZE_SECRETS" in allowed_keys


def test_http_router_loads_observability_env_keys():
    allowed_keys = _http_helper_allowed_keys()

    assert "CENTRALMCP_AUDIT_LOG" in allowed_keys
    assert "CENTRALMCP_METRICS" in allowed_keys
    assert "CENTRALMCP_METRICS_HTTP" in allowed_keys


def test_http_router_banner_shows_metrics_and_audit_status():
    text = _script_text()

    assert "metrics:" in text
    assert "CENTRALMCP_METRICS" in text
    assert "CENTRALMCP_METRICS_HTTP" in text
    assert "audit:" in text
    assert "CENTRALMCP_AUDIT_LOG" in text


def test_http_router_banner_shows_health_endpoints_and_bearer_status():
    text = _script_text()

    assert "/livez, /readyz, /healthz" in text
    assert "bearer_status" in text
    assert "MCP_HTTP_BEARER_TOKEN" in text


def test_http_router_warns_about_public_binding_allowlist_requirement():
    text = _script_text()

    assert "MCP_ALLOWED_HOSTS" in text
    assert "UnsafeHttpBindingError" in text


# ---------------------------------------------------------------------------
# Allowlist completeness (regression)
# ---------------------------------------------------------------------------
#
# The .env allowlist is a hand-maintained set, so it silently drifted behind
# the CENTRALMCP_* knobs the router actually reads: an operator setting, say,
# CENTRALMCP_ROUTER_RESPONSE_MAX_BYTES in .env saw it quietly ignored. These
# tests recompute the knob set from the source tree instead of restating it.

_RUNTIME_ENV_RE = re.compile(r"CENTRALMCP_[A-Z0-9_]+")

#: Knobs deliberately excluded from the .env allowlist.
#: - CENTRALMCP_LIVE_TEST_*: opt-in live-test switches, never a server knob.
#: - CENTRALMCP_TOOL_IDS__ / CENTRALMCP_LIVE_TEST_: dynamic name prefixes, not
#:   complete variable names.
_EXCLUDED_ENV_KEYS = {"CENTRALMCP_TOOL_IDS__", "CENTRALMCP_LIVE_TEST_"}
_EXCLUDED_ENV_PREFIXES = ("CENTRALMCP_LIVE_TEST_",)


def _runtime_centralmcp_keys() -> set[str]:
    keys: set[str] = set()
    for directory in ("mcp_servers", "pipeline", "ingestion"):
        for path in (REPO_ROOT / directory).rglob("*.py"):
            if "__pycache__" in path.parts:
                continue
            keys.update(_RUNTIME_ENV_RE.findall(path.read_text(encoding="utf-8")))
    return {
        key
        for key in keys
        if key not in _EXCLUDED_ENV_KEYS
        and not key.startswith(_EXCLUDED_ENV_PREFIXES)
    }


def test_http_router_allowlist_covers_every_runtime_centralmcp_key():
    allowed_keys = _http_helper_allowed_keys()

    missing = sorted(_runtime_centralmcp_keys() - allowed_keys)

    assert not missing, f"missing from run_http_router.sh allowed_keys: {missing}"


def test_http_router_allowlist_includes_router_response_budget_keys():
    allowed_keys = _http_helper_allowed_keys()

    assert "CENTRALMCP_ROUTER_RESPONSE_MAX_ITEMS" in allowed_keys
    assert "CENTRALMCP_ROUTER_RESPONSE_MAX_BYTES" in allowed_keys
    assert "CENTRALMCP_ROUTER_BATCH_RESPONSE_MAX_BYTES" in allowed_keys
    assert "CENTRALMCP_ROUTER_CURSOR_TTL_SECONDS" in allowed_keys


def test_http_router_allowlist_includes_generated_tool_opt_ins():
    allowed_keys = _http_helper_allowed_keys()

    for platform in ("CENTRAL", "GLP", "AOS8", "APSTRA", "CLEARPASS", "MIST", "UXI"):
        assert f"CENTRALMCP_{platform}_GENERATED_TOOLS" in allowed_keys


def test_http_router_allowlist_includes_rag_and_embedding_knobs():
    allowed_keys = _http_helper_allowed_keys()

    assert "CENTRALMCP_RAG_BACKEND" in allowed_keys
    assert "CENTRALMCP_EMBED_PROVIDERS" in allowed_keys
    assert "CENTRALMCP_NOMIC_PREFIXES" in allowed_keys
    assert "CENTRALMCP_BOUND_LISTS" in allowed_keys
    assert "CENTRALMCP_NORMALIZE_MACS" in allowed_keys


def test_http_router_allowlist_includes_glp_region():
    """Every curated GLP compute/storage/virtualization tool needs it."""
    assert "GLP_GENERATED_REGION" in _http_helper_allowed_keys()


def test_http_router_allowlist_excludes_live_test_switches():
    allowed_keys = _http_helper_allowed_keys()

    assert not [key for key in allowed_keys if key.startswith("CENTRALMCP_LIVE_TEST_")]
