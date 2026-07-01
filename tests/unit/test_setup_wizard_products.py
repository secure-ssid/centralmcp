from __future__ import annotations

import argparse
import json

from scripts import setup_wizard


def test_product_env_includes_access_mode():
    env = setup_wizard._product_env(
        ["clearpass", "mist"],
        assume_yes=True,
        product_access="read-write",
    )

    assert env["CENTRALMCP_PRODUCTS"] == "clearpass,mist"
    assert env["CENTRALMCP_PRODUCT_ACCESS"] == "read-write"
    assert env["CLEARPASS_BASE_URL"] == "https://clearpass.example.com"
    assert env["MIST_HOST"] == "https://api.mist.com"


def test_product_access_defaults_to_read_write_for_products():
    args = argparse.Namespace(product_access="read-write", yes=False)

    assert setup_wizard._product_access(args, ["clearpass"]) == "read-write"


def test_merge_json_env_adds_product_access(tmp_path):
    target = tmp_path / ".mcp.json"
    target.write_text(
        json.dumps(
            {
                "mcpServers": {
                    "aruba-tool-router": {
                        "command": "uv",
                        "env": {"CENTRALMCP_ROUTER_MODE": "minimal"},
                    }
                }
            }
        )
    )

    step = setup_wizard._merge_json_env(
        target,
        "aruba-tool-router",
        {
            "CENTRALMCP_PRODUCTS": "clearpass,mist",
            "CENTRALMCP_PRODUCT_ACCESS": "read-write",
        },
    )

    data = json.loads(target.read_text())
    env = data["mcpServers"]["aruba-tool-router"]["env"]
    assert step.status == "OK"
    assert env["CENTRALMCP_ROUTER_MODE"] == "minimal"
    assert env["CENTRALMCP_PRODUCTS"] == "clearpass,mist"
    assert env["CENTRALMCP_PRODUCT_ACCESS"] == "read-write"
