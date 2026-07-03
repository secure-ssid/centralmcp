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


def test_doctor_warns_on_invalid_product_access(monkeypatch):
    monkeypatch.setenv("CENTRALMCP_PRODUCT_ACCESS", "read-wrtie")
    monkeypatch.delenv("CENTRALMCP_PRODUCTS", raising=False)
    monkeypatch.delenv("CENTRALMCP_TOOLSETS", raising=False)

    checks = {check.name: check for check in doctor._runtime_checks()}

    assert checks["Optional product access"].status == "WARN"
    assert "optional writes fail closed" in checks["Optional product access"].detail


def test_doctor_reports_unset_product_access_as_read_only(monkeypatch):
    monkeypatch.delenv("CENTRALMCP_PRODUCT_ACCESS", raising=False)
    monkeypatch.delenv("CENTRALMCP_PRODUCTS", raising=False)
    monkeypatch.delenv("CENTRALMCP_TOOLSETS", raising=False)

    checks = {check.name: check for check in doctor._runtime_checks()}

    assert checks["Optional product access"].status == "OK"
    assert checks["Optional product access"].detail == (
        "unset; optional product writes default to read-only"
    )


def test_doctor_source_manifest_matches_ingest_sources():
    checks = {check.name: check for check in doctor._source_manifest_checks()}

    assert checks["RAG source manifest"].status == "OK"
    assert "sources match ingestion SOURCE_META" in checks["RAG source manifest"].detail


def test_stdio_config_checks_survive_non_utf8_config(tmp_path):
    """Regression: a non-UTF8 .mcp.json crashed the whole doctor run with
    UnicodeDecodeError instead of reporting a FAIL check."""
    from scripts import doctor

    bad = tmp_path / ".mcp.json"
    bad.write_bytes(b"\xff\xfe\x00broken")

    checks = doctor._stdio_config_checks(bad)

    assert any(c.status == "FAIL" for c in checks)


def test_has_placeholders_survives_unreadable_file(tmp_path, monkeypatch):
    from scripts import doctor
    from pathlib import Path

    target = tmp_path / "credentials.yaml"
    target.write_text("ok")

    def deny_read(self, *args, **kwargs):
        raise PermissionError("denied")

    monkeypatch.setattr(Path, "read_text", deny_read)

    assert doctor._has_placeholders(target) is False


def test_load_local_env_survives_utf16_env_file(tmp_path, monkeypatch):
    """Regression: a UTF-16 .env (e.g. a PowerShell redirect) decoded with
    embedded NULs that crashed os.environ assignment with
    'ValueError: embedded null byte'."""
    from scripts import doctor

    env_file = tmp_path / ".env"
    # The BOM garbles the first line's key with replacement chars; later
    # lines must still parse. The contract under test is "never crash".
    env_file.write_bytes("# comment\nDOCTOR_UTF16_TEST=value\n".encode("utf-16"))
    monkeypatch.delenv("DOCTOR_UTF16_TEST", raising=False)

    doctor._load_local_env(env_file)  # must not raise

    import os as _os
    assert _os.environ.pop("DOCTOR_UTF16_TEST", None) == "value"
