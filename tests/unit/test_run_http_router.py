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

    assert 'export CENTRALMCP_PRODUCT_ACCESS="${CENTRALMCP_PRODUCT_ACCESS:-read-write}"' in text
    assert "access:   ${CENTRALMCP_PRODUCT_ACCESS}" in text
