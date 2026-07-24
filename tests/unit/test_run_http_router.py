from __future__ import annotations

import ast
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


def test_http_router_banner_shows_health_endpoints_and_bearer_status():
    text = _script_text()

    assert "/livez, /readyz, /healthz" in text
    assert "bearer_status" in text
    assert "MCP_HTTP_BEARER_TOKEN" in text


def test_http_router_warns_about_public_binding_allowlist_requirement():
    text = _script_text()

    assert "MCP_ALLOWED_HOSTS" in text
    assert "UnsafeHttpBindingError" in text
