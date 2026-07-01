from __future__ import annotations

import argparse
import json
import sys

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


def test_product_env_includes_uxi_credentials():
    env = setup_wizard._product_env(
        ["uxi"],
        assume_yes=True,
        product_access="read-only",
    )

    assert env["CENTRALMCP_PRODUCTS"] == "uxi"
    assert env["UXI_CLIENT_ID"] == "YOUR_UXI_CLIENT_ID"
    assert env["UXI_CLIENT_SECRET"] == "YOUR_UXI_CLIENT_SECRET"


def test_uxi_client_secret_uses_secret_prompt():
    assert setup_wizard._is_secret_env_var("UXI_CLIENT_SECRET") is True


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


def test_catalog_build_receives_product_access_env(monkeypatch):
    calls: list[tuple[list[str], str, dict[str, str] | None]] = []

    monkeypatch.setattr(
        sys,
        "argv",
        [
            "setup_wizard.py",
            "--yes",
            "--skip-install",
            "--skip-credentials",
            "--skip-stdio",
            "--skip-http",
            "--skip-doctor",
            "--products",
            "clearpass",
            "--product-access",
            "read-only",
        ],
    )
    monkeypatch.setattr(
        setup_wizard,
        "_write_env_file",
        lambda *args, **kwargs: setup_wizard.Step(".env", "OK", "captured"),
    )

    def fake_run(
        command: list[str],
        label: str,
        *,
        env: dict[str, str] | None = None,
    ) -> setup_wizard.Step:
        calls.append((command, label, env))
        return setup_wizard.Step(label, "OK", "captured")

    monkeypatch.setattr(setup_wizard, "_run", fake_run)

    assert setup_wizard.main() == 0

    catalog_calls = [call for call in calls if call[1] == "tool catalog"]
    assert len(catalog_calls) == 1
    command, _, env = catalog_calls[0]
    assert command[-2:] == ["--products", "clearpass"]
    assert env is not None
    assert set(env) == {"CENTRALMCP_PRODUCTS", "CENTRALMCP_PRODUCT_ACCESS"}
    assert env["CENTRALMCP_PRODUCTS"] == "clearpass"
    assert env["CENTRALMCP_PRODUCT_ACCESS"] == "read-only"
