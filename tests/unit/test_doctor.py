from __future__ import annotations

from scripts import doctor


def test_doctor_recognizes_uxi_optional_product(monkeypatch):
    monkeypatch.setenv("CENTRALMCP_PRODUCTS", "uxi")
    monkeypatch.delenv("CENTRALMCP_TOOLSETS", raising=False)
    monkeypatch.delenv("UXI_CLIENT_ID", raising=False)
    monkeypatch.delenv("UXI_CLIENT_SECRET", raising=False)

    checks = {check.name: check for check in doctor._runtime_checks()}

    assert checks["Optional product names"].status == "OK"
    assert checks["uxi required env"].detail == "missing or placeholder: UXI_CLIENT_ID, UXI_CLIENT_SECRET"


def test_doctor_warns_on_uxi_placeholder_credentials(monkeypatch):
    monkeypatch.setenv("CENTRALMCP_PRODUCTS", "uxi")
    monkeypatch.delenv("CENTRALMCP_TOOLSETS", raising=False)
    monkeypatch.setenv("UXI_CLIENT_ID", "YOUR_UXI_CLIENT_ID")
    monkeypatch.setenv("UXI_CLIENT_SECRET", "YOUR_UXI_CLIENT_SECRET")

    checks = {check.name: check for check in doctor._runtime_checks()}

    assert checks["uxi required env"].status == "WARN"
    assert checks["uxi required env"].detail == "missing or placeholder: UXI_CLIENT_ID, UXI_CLIENT_SECRET"
