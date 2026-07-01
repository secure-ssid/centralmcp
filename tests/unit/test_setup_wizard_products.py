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


def test_write_env_file_merges_existing_values_without_overwriting_tokens(tmp_path):
    target = tmp_path / ".env"
    target.write_text(
        "\n".join(
            [
                "# local lab tokens",
                "export CENTRALMCP_PRODUCTS=clearpass",
                "export CLEARPASS_API_TOKEN=real-token",
                "",
            ]
        )
    )

    step = setup_wizard._write_env_file(
        target,
        {
            "CENTRALMCP_PRODUCTS": "clearpass,mist",
            "CENTRALMCP_PRODUCT_ACCESS": "read-write",
            "CLEARPASS_API_TOKEN": "YOUR_CLEARPASS_API_TOKEN",
            "MIST_HOST": "https://api.mist.com",
            "MIST_API_TOKEN": "YOUR_MIST_API_TOKEN",
        },
        force=False,
    )

    text = target.read_text()
    assert step.status == "OK"
    assert "export CENTRALMCP_PRODUCTS=clearpass,mist" in text
    assert "export CENTRALMCP_PRODUCT_ACCESS=read-write" in text
    assert "export CLEARPASS_API_TOKEN=real-token" in text
    assert "YOUR_CLEARPASS_API_TOKEN" not in text
    assert "export MIST_API_TOKEN=YOUR_MIST_API_TOKEN" in text


def test_write_env_file_replaces_placeholder_values_on_rerun(tmp_path):
    target = tmp_path / ".env"
    target.write_text(
        "\n".join(
            [
                "export CENTRALMCP_PRODUCTS=mist",
                "export MIST_API_TOKEN=YOUR_MIST_API_TOKEN",
                "export MIST_HOST=https://old.example.com",
                "",
            ]
        )
    )

    step = setup_wizard._write_env_file(
        target,
        {
            "CENTRALMCP_PRODUCTS": "mist",
            "CENTRALMCP_PRODUCT_ACCESS": "read-write",
            "MIST_HOST": "https://api.mist.com",
            "MIST_API_TOKEN": "real-token",
        },
        force=False,
    )

    text = target.read_text()
    assert step.status == "OK"
    assert "export MIST_API_TOKEN=real-token" in text
    assert "YOUR_MIST_API_TOKEN" not in text
    assert "export MIST_HOST=https://old.example.com" in text
    assert "export CENTRALMCP_PRODUCT_ACCESS=read-write" in text


def test_write_env_file_force_replaces_existing_env(tmp_path):
    target = tmp_path / ".env"
    target.write_text("export CENTRALMCP_PRODUCTS=clearpass\n")

    step = setup_wizard._write_env_file(
        target,
        {
            "CENTRALMCP_PRODUCTS": "mist",
            "CENTRALMCP_PRODUCT_ACCESS": "read-only",
        },
        force=True,
    )

    assert step.status == "OK"
    assert "CENTRALMCP_PRODUCTS=mist" in target.read_text()


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


def test_with_products_catalog_uses_all_products_without_tokens(monkeypatch):
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
            "--with-products",
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
    products = ",".join(setup_wizard.PRODUCT_ENV)
    assert command[-2:] == ["--products", products]
    assert env == {
        "CENTRALMCP_PRODUCTS": products,
        "CENTRALMCP_PRODUCT_ACCESS": "read-only",
    }
