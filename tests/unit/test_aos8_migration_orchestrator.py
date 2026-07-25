"""Resumability, persistence, verification, and MCP coverage for AOS8 runs."""

from __future__ import annotations

import json
import time
from concurrent.futures import ThreadPoolExecutor

import pytest

import mcp_servers.aos8 as aos8
from pipeline.aos8_migration_orchestrator import (
    AOS8MigrationOrchestrator,
    MalformedMigrationStateError,
    MigrationRunError,
    MigrationRunStore,
    _target_context,
)
from pipeline.aos8_target_adapters import (
    ClassicCentralAdapter,
    NewCentralAdapter,
    TargetType,
    WriteGateError,
)


def candidate(
    object_type: str,
    identifier: str,
    *,
    payload: dict | None = None,
    dependencies: list[str] | None = None,
    apply_order: int = 10,
    unsupported_fields: dict | None = None,
    requires_secret_input: bool = False,
    secret_fields: list[str] | None = None,
) -> dict:
    return {
        "object_type": object_type,
        "identifier": identifier,
        "payload": payload or {},
        "dependencies": dependencies or [],
        "apply_order": apply_order,
        "unsupported_fields": unsupported_fields or {},
        "requires_secret_input": requires_secret_input,
        "secret_fields": secret_fields or [],
        "warnings": [],
    }


class FakeBackend:
    def __init__(self):
        self.read_values: dict[str, object] = {}
        self.read_failures: dict[str, Exception] = {}
        self.write_calls = []
        self.read_calls = []
        self.fail_real: dict[str, int] = {}
        self.echo_secret = False

    def read(self, operation):
        self.read_calls.append(operation)
        if operation.name in self.read_failures:
            raise self.read_failures[operation.name]
        return self.read_values.get(operation.name)

    def write(self, operation, *, confirmation):
        self.write_calls.append((operation, confirmation))
        dry_run = bool(operation.arguments.get("dry_run"))
        remaining = self.fail_real.get(operation.name, 0)
        if not dry_run and remaining:
            self.fail_real[operation.name] = remaining - 1
            raise RuntimeError(f"{operation.name} rejected")
        result = {"ok": True, "name": operation.name, "dry_run": dry_run}
        if self.echo_secret:
            result["echo"] = operation.arguments.get("shared_secret")
        return result


def target(target_type: str = "new_central", policy: str = "fail") -> dict:
    return {
        "type": target_type,
        "scope_id": "100",
        "scope_name": "Branch",
        "persona": "CAMPUS_AP",
        "conflict_policy": policy,
        "cluster_name": None,
        "cluster_scope_id": None,
        "gateway_name": None,
        "gateway_scope_id": None,
    }


def orchestrator(tmp_path, backend: FakeBackend, *, writes: bool = True):
    def factory(context):
        adapter = (
            NewCentralAdapter
            if context.target_type is TargetType.NEW_CENTRAL
            else ClassicCentralAdapter
        )
        return adapter(
            context,
            scope_resolver=lambda ctx: (
                str(ctx.scope_id or "100"),
                str(ctx.scope_name or "Branch"),
            ),
            persona_validator=lambda ctx: str(ctx.persona or "CAMPUS_AP"),
            read_invoker=backend.read,
            write_invoker=backend.write,
            writes_enabled=lambda _target: writes,
        )

    store = MigrationRunStore(tmp_path / "state")
    return AOS8MigrationOrchestrator(store, factory), store


def test_preview_and_create_are_dependency_ordered_and_bounded(tmp_path):
    backend = FakeBackend()
    service, _ = orchestrator(tmp_path, backend)
    vlan = candidate("vlan", "20", payload={"description": "Corp"})
    role = candidate(
        "role",
        "employee",
        payload={"policies": ["allowall"]},
        dependencies=["vlan:20"],
        apply_order=30,
    )

    preview = service.preview([role, vlan], target(), limit=1)
    assert preview["candidate_count"] == 2
    assert preview["operations"][0]["candidate"] == "vlan:20"
    assert preview["pagination"]["truncated"] is True

    created = service.create_run([role, vlan], target(), run_id="ordered")
    assert [item["candidate"] for item in created["candidates"]] == [
        "vlan:20",
        "role:employee",
    ]
    assert created["secrets_persisted"] is False


def test_confirmed_apply_requires_prior_dry_run_and_does_not_reapply(tmp_path):
    backend = FakeBackend()
    service, _ = orchestrator(tmp_path, backend)
    service.create_run([candidate("vlan", "20")], target(), run_id="resume")

    with pytest.raises(WriteGateError, match="dry_run=True"):
        service.apply("resume", dry_run=False, confirmation=True)

    dry_run = service.apply("resume", dry_run=True, confirmation=False)
    assert dry_run["candidates"][0]["dry_run_ok"] is True
    applied = service.apply("resume", dry_run=False, confirmation=True)
    assert applied["candidates"][0]["status"] == "applied"
    calls_after_apply = len(backend.write_calls)

    resumed = service.apply("resume", dry_run=False, confirmation=True)
    assert resumed["attempted_candidates"] == []
    assert len(backend.write_calls) == calls_after_apply


def test_concurrent_resume_serializes_and_does_not_duplicate_writes(tmp_path):
    class SlowBackend(FakeBackend):
        def write(self, operation, *, confirmation):
            if not operation.arguments.get("dry_run"):
                time.sleep(0.05)
            return super().write(operation, confirmation=confirmation)

    backend = SlowBackend()
    service, _ = orchestrator(tmp_path, backend)
    service.create_run([candidate("vlan", "20")], target(), run_id="concurrent")
    service.apply("concurrent", dry_run=True, confirmation=False)
    backend.write_calls.clear()

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(
            executor.map(
                lambda _: service.apply(
                    "concurrent",
                    dry_run=False,
                    confirmation=True,
                ),
                range(2),
            )
        )

    assert len(backend.write_calls) == 1
    assert sorted(len(result["attempted_candidates"]) for result in results) == [0, 1]
    assert all(result["candidates"][0]["status"] == "applied" for result in results)


def test_failed_candidate_requires_explicit_retry_and_unblocks_dependency(tmp_path):
    backend = FakeBackend()
    backend.fail_real["create_vlan"] = 1
    service, _ = orchestrator(tmp_path, backend)
    candidates = [
        candidate("vlan", "20"),
        candidate(
            "role",
            "employee",
            payload={"policies": ["allowall"]},
            dependencies=["vlan:20"],
            apply_order=30,
        ),
    ]
    service.create_run(candidates, target(), run_id="retry")
    service.apply("retry", dry_run=True, confirmation=False)
    first = service.apply("retry", dry_run=False, confirmation=True)
    assert first["candidates"][0]["status"] == "failed"
    assert first["candidates"][1]["status"] == "blocked"

    no_retry = service.apply("retry", dry_run=False, confirmation=True)
    assert no_retry["attempted_candidates"] == ["role:employee"]
    assert no_retry["candidates"][0]["status"] == "failed"

    retried = service.apply(
        "retry",
        dry_run=False,
        confirmation=True,
        retry_failed=True,
    )
    assert [item["status"] for item in retried["candidates"]] == [
        "applied",
        "applied",
    ]


def test_partial_multi_operation_result_is_preserved(tmp_path):
    backend = FakeBackend()
    backend.fail_real["central_api_request"] = 1
    service, _ = orchestrator(tmp_path, backend)
    role = candidate("role", "employee", payload={"policies": ["allowall"]})
    service.create_run([role], target(), run_id="partial")
    service.apply("partial", dry_run=True, confirmation=False)

    result = service.apply("partial", dry_run=False, confirmation=True)
    entry = result["candidates"][0]
    assert entry["status"] == "failed"
    operation_results = entry["last_result"]["results"]
    assert len(operation_results) == 1
    assert operation_results[0]["operation"]["tool_or_endpoint"] == "create_role"


def test_secret_is_required_each_attempt_and_never_persisted_or_returned(tmp_path):
    backend = FakeBackend()
    backend.echo_secret = True
    service, store = orchestrator(tmp_path, backend)
    auth = candidate(
        "auth_server",
        "radius:rad1",
        payload={"name": "rad1", "server_type": "radius", "host": "10.0.0.10"},
        unsupported_fields={"rad_key": "raw-source-secret"},
        requires_secret_input=True,
        secret_fields=["unsupported_fields.rad_key"],
    )
    created = service.create_run([auth], target(), run_id="secrets")
    assert created["candidates"][0]["required_secret_names"] == ["shared_secret"]
    assert "raw-source-secret" not in store.path_for("secrets").read_text()

    supplied = {"auth_server:radius:rad1": {"shared_secret": "target-super-secret"}}
    dry_run = service.apply(
        "secrets",
        dry_run=True,
        confirmation=False,
        target_secrets=supplied,
    )
    assert "target-super-secret" not in json.dumps(dry_run)
    assert "target-super-secret" not in store.path_for("secrets").read_text()

    missing_again = service.apply(
        "secrets",
        dry_run=False,
        confirmation=True,
    )
    assert missing_again["candidates"][0]["status"] == "blocked"
    assert "target-super-secret" not in json.dumps(missing_again)


def test_malformed_state_and_path_traversal_fail_cleanly(tmp_path):
    backend = FakeBackend()
    service, store = orchestrator(tmp_path, backend)
    with pytest.raises(MigrationRunError, match="path traversal"):
        store.path_for("../outside")

    malformed = store.state_dir / "broken.json"
    malformed.write_text("{not-json", encoding="utf-8")
    with pytest.raises(MalformedMigrationStateError, match="malformed"):
        service.get_run("broken")
    listed = service.list_runs()
    assert listed["malformed_state_count"] == 1
    assert listed["runs"] == []


def _ap_group_candidate(identifier="ap-group-hq"):
    return candidate("ap_group", identifier, payload={"name": identifier})


def test_operator_context_maps_are_rejected_by_every_persistent_workflow(tmp_path):
    """Fail-closed contract regression: `external_object_references`/
    `ap_group_target_map`/`ap_group_device_serials` are accepted only by
    the stateless `preview()` (which may echo them back in that one live
    response, since nothing it returns is persisted). Every persistent
    workflow -- `create_run`, and by construction `apply`/`get_run`/
    `list_runs`, which only ever operate on an already-persisted run --
    must reject a non-empty value for any of them with a clear error,
    and must never write the raw values, a hash, a count, or any other
    resupply metadata to disk."""
    import base64

    backend = FakeBackend()
    service, store = orchestrator(tmp_path, backend)

    opaque_password = "hunter2"
    opaque_api_key = "sk-proj-abcdefghijklmnopqrstuvwxyz0123456789ABCDEFGH"
    opaque_aws_key = "AKIAIOSFODNN7EXAMPLE"
    opaque_base64 = base64.b64encode(b"super-secret-payload-material").decode()
    opaque_pem = (
        "-----BEGIN PRIVATE KEY-----\n"
        "MIIBVQIBADANBgkqhkiG9w0BAQEFAASCAT8wggE7AgEAAkEA\n"
        "-----END PRIVATE KEY-----"
    )
    opaque_values = [
        opaque_password,
        opaque_api_key,
        opaque_aws_key,
        opaque_base64,
        opaque_pem,
    ]

    wlan = _wpa3_enterprise_candidate("Enterprise-Opaque")
    ap_group = _ap_group_candidate("ap-group-opaque")
    wpa3_target = {
        **target("classic_central"),
        "external_object_references": {
            "wlan:Enterprise-Opaque": {
                "auth_server1": opaque_password,
                "cert_ref": opaque_pem,
            }
        },
        "ap_group_target_map": {"ap-group-opaque": opaque_api_key},
        "ap_group_device_serials": {
            "ap-group-opaque": [opaque_aws_key, opaque_base64]
        },
    }

    # `preview()` is stateless -- nothing it returns is persisted -- so it
    # may echo the caller's own just-supplied context back in this one
    # live response.
    preview = service.preview([wlan, ap_group], wpa3_target)
    assert opaque_password in json.dumps(preview)

    # `create_run()` must reject the same target outright rather than
    # silently dropping, hashing, or fingerprinting the context.
    with pytest.raises(MigrationRunError, match="external_object_references"):
        service.create_run([wlan, ap_group], wpa3_target, run_id="opaque-ctx")
    # No run file was ever written by the rejected call.
    assert not store.path_for("opaque-ctx").exists()

    # A target with only `ap_group_target_map`/`ap_group_device_serials`
    # populated is rejected too (each field is checked independently).
    ap_group_only_target = {
        **target("classic_central"),
        "ap_group_target_map": {"ap-group-opaque": opaque_api_key},
        "ap_group_device_serials": {
            "ap-group-opaque": [opaque_aws_key, opaque_base64]
        },
    }
    with pytest.raises(MigrationRunError, match="ap_group_target_map"):
        service.create_run([ap_group], ap_group_only_target, run_id="opaque-ctx-2")
    assert not store.path_for("opaque-ctx-2").exists()

    # A normal create_run/apply/get_run/list_runs cycle with no operator
    # context at all still works, and (trivially) never contains any of
    # the opaque values above.
    created = service.create_run([wlan, ap_group], target("classic_central"), run_id="no-ctx")
    dry_run = service.apply("no-ctx", dry_run=True, confirmation=False)
    blocked = service.apply("no-ctx", dry_run=False, confirmation=True)
    fetched = service.get_run("no-ctx", include_details=True)
    listed = service.list_runs()
    raw_state = store.path_for("no-ctx").read_text()
    for value in opaque_values:
        assert value not in raw_state
        assert value not in json.dumps(created)
        assert value not in json.dumps(dry_run)
        assert value not in json.dumps(blocked)
        assert value not in json.dumps(fetched)
        assert value not in json.dumps(listed)
    assert "external_object_references" not in created["target"]
    assert "ap_group_target_map" not in created["target"]
    assert "ap_group_device_serials" not in created["target"]


def test_verification_records_mismatch_and_unsupported_unverifiable(tmp_path):
    backend = FakeBackend()
    service, _ = orchestrator(tmp_path, backend)
    vlan = candidate("vlan", "20", payload={"description": "Corp"})
    route = candidate("route", "ipv4:default")
    service.create_run([vlan, route], target(), run_id="verify")
    service.apply("verify", dry_run=True, confirmation=False)
    service.apply("verify", dry_run=False, confirmation=True)
    backend.read_values["central_api_read"] = {"id": "20", "name": "Wrong"}

    verified = service.verify("verify")
    comparisons = {
        item["candidate"]: item for item in verified["comparisons"]
    }
    assert comparisons["vlan:20"]["verification_status"] == "mismatch"
    assert comparisons["route:ipv4:default"]["verification_status"] == "unverifiable"
    assert "unsupported and remains unapplied" in comparisons[
        "route:ipv4:default"
    ]["reason"]
    assert "does not claim full semantic equivalence" in verified["verification_scope"]


def test_verification_read_failure_is_unverifiable(tmp_path):
    backend = FakeBackend()
    service, _ = orchestrator(tmp_path, backend)
    service.create_run([candidate("vlan", "20")], target(), run_id="read-fail")
    service.apply("read-fail", dry_run=True, confirmation=False)
    service.apply("read-fail", dry_run=False, confirmation=True)
    backend.read_failures["central_api_read"] = RuntimeError("read unavailable")

    result = service.verify("read-fail")
    assert result["comparisons"][0]["verification_status"] == "unverifiable"
    assert "read failed" in result["comparisons"][0]["reason"]


def test_verification_records_identity_and_comparable_field_success(tmp_path):
    backend = FakeBackend()
    service, _ = orchestrator(tmp_path, backend)
    service.create_run(
        [candidate("vlan", "20", payload={"description": "Corp"})],
        target(),
        run_id="verified",
    )
    service.apply("verified", dry_run=True, confirmation=False)
    service.apply("verified", dry_run=False, confirmation=True)
    # Target read must confirm every non-secret expected field
    # (identifier, vlan_id, vlan_name) for status to legitimately reach
    # "verified" under finding #3's stricter partially_verified gate --
    # if any expected non-secret field were absent/unverifiable here the
    # status must drop to "partially_verified" instead (covered by
    # test_verify_partially_verified_when_no_nonsecret_field_can_be_confirmed
    # and the reordered/incomplete server-group tests below).
    backend.read_values["central_api_read"] = {
        "id": "20",
        "name": "Corp",
        "vlan-id": "20",
    }

    result = service.verify("verified")
    comparison = result["comparisons"][0]
    assert comparison["verification_status"] == "verified"
    assert any(
        field["field"] == "vlan_name" and field["status"] == "match"
        for field in comparison["field_comparison"]
    )
    assert any(
        field["field"] == "vlan_id" and field["status"] == "match"
        for field in comparison["field_comparison"]
    )


def test_list_runs_is_bounded(tmp_path):
    backend = FakeBackend()
    service, _ = orchestrator(tmp_path, backend)
    for index in range(3):
        service.create_run(
            [candidate("vlan", str(index + 10))],
            target(),
            run_id=f"run-{index}",
        )
    result = service.list_runs(limit=2)
    assert len(result["runs"]) == 2
    assert result["pagination"] == {
        "offset": 0,
        "limit": 2,
        "total": 3,
        "truncated": True,
    }


def test_exact_new_and_classic_rollback_claims_are_preserved(tmp_path):
    backend = FakeBackend()
    service, _ = orchestrator(tmp_path, backend)
    new = service.create_run(
        [candidate("vlan", "20")],
        target(),
        run_id="new-rollback",
    )
    assert new["checkpoint_and_rollback"]["automatic_rollback_supported"] is True
    assert (
        new["checkpoint_and_rollback"]["manual_checkpoint_restore_supported"]
        is False
    )
    assert "no manual checkpoint listing or restore" in new[
        "checkpoint_and_rollback"
    ]["guidance"]

    wlan = candidate(
        "wlan",
        "Guest",
        payload={"name": "Guest", "essid": "Guest", "vlan": 20},
        unsupported_fields={
            "ssid_profile.opmode": "open",
            "virtual_ap.forward_mode": "bridge",
        },
    )
    classic = service.create_run(
        [wlan],
        target("classic_central"),
        run_id="classic-rollback",
    )
    guidance = classic["checkpoint_and_rollback"]
    assert guidance["automatic_rollback_supported"] is False
    assert guidance["manual_checkpoint_restore_supported"] is False
    assert "Export the current Classic Central group configuration" in guidance[
        "guidance"
    ]


def test_safe_candidate_redaction_preserves_presence_booleans_and_redacts_secrets(
    tmp_path,
):
    """Regression: `_safe_candidate`/`_sanitize` redaction must keep
    presence-only booleans (`passphrase_present`, `psk_hexkey_present`) as
    real JSON booleans -- not mask them to the literal string "******" --
    while still redacting any actual secret string reachable from the same
    candidate, in both the returned preview/get_run views and the
    on-disk persisted state file."""
    backend = FakeBackend()
    service, store = orchestrator(tmp_path, backend)
    wlan = candidate(
        "vlan",
        "20",
        payload={
            "description": "Corp",
            "security": {
                "mode": "wpa2_personal",
                "passphrase_present": True,
                "psk_hexkey_present": False,
                # A real secret value nested alongside the presence flags;
                # it must still be redacted even though the flags beside it
                # are preserved.
                "shared_secret": "actual-secret-value",
            },
        },
    )

    created = service.create_run([wlan], target(), run_id="presence-bools")
    detail = service.get_run("presence-bools", include_details=True)
    security = detail["candidates"][0]["source_candidate"]["payload"]["security"]
    assert security["passphrase_present"] is True
    assert security["psk_hexkey_present"] is False
    assert security["shared_secret"] == "******"

    preview = service.preview([wlan], target())
    assert "secrets_persisted" in preview  # sanity: preview still redacts via _sanitize

    # The persisted JSON state file on disk must also carry real booleans,
    # not the string "******", and must never contain the actual secret.
    state_path = store.path_for("presence-bools")
    raw_state = json.loads(state_path.read_text())
    persisted_security = raw_state["candidates"][0]["candidate"]["payload"]["security"]
    assert persisted_security["passphrase_present"] is True
    assert persisted_security["psk_hexkey_present"] is False
    assert persisted_security["shared_secret"] == "******"
    assert "actual-secret-value" not in state_path.read_text()
    assert created["candidates"][0]["status"] in {"pending", "blocked", "unsupported"}


def test_safe_candidate_redaction_only_bypasses_boolean_presence_values(tmp_path):
    """Regression: the presence-only allowlist must be a narrow key+type
    check, not a broad suffix exception -- a field sharing one of the
    allowlisted names but holding a non-bool (e.g. an actual secret string)
    must still be redacted."""
    backend = FakeBackend()
    service, store = orchestrator(tmp_path, backend)
    wlan = candidate(
        "vlan",
        "21",
        payload={
            "description": "Corp2",
            "security": {
                # Same key name as the allowlisted presence flag, but a
                # non-bool value -- must NOT bypass redaction.
                "passphrase_present": "yes-it-is-present-and-also-a-leaked-secret",
            },
        },
    )
    service.create_run([wlan], target(), run_id="presence-bools-narrow")
    detail = service.get_run("presence-bools-narrow", include_details=True)
    security = detail["candidates"][0]["source_candidate"]["payload"]["security"]
    assert security["passphrase_present"] == "******"


def test_mcp_run_wrappers_use_injected_orchestrator_without_credentials(
    tmp_path, monkeypatch
):
    backend = FakeBackend()
    service, _ = orchestrator(tmp_path, backend)
    monkeypatch.setattr(aos8, "_aos8_migration_orchestrator", lambda: service)
    candidates = [candidate("vlan", "20")]

    created = aos8.aos8_create_migration_run(
        "new_central",
        candidates=candidates,
        scope_id="100",
        scope_name="Branch",
        run_id="mcp",
    )
    assert created["run_id"] == "mcp"
    dry_run = aos8.aos8_apply_migration_run("mcp")
    assert dry_run["dry_run"] is True
    status = aos8.aos8_get_migration_run("mcp")
    assert status["candidates"][0]["dry_run_ok"] is True


# ---------------------------------------------------------------------------
# Review-fix regression tests: finding #6 (payload field-mismatch
# verification, qualified identifiers, secret-omitted-GET) and finding #7
# (rollback honesty).
# ---------------------------------------------------------------------------


# NOTE: `auth_server` (and every other unverified-assignment SHARED profile
# type -- see the "finding #1" tests below) is permanently "blocked" and
# never reaches "applied" any more, so it can no longer serve as a test
# vehicle for these Finding #6 verification-mechanics tests. `wlan` (a
# `wpa2_personal` SSID, "ready" on the default CAMPUS_AP persona) is used
# instead: it has a genuine payload field distinct from its identity
# (`vlan_ids`), a `match_identifier` distinct from its qualified candidate
# key ("wlan:Guest" vs the bare SSID name "Guest"), and a real transient
# secret (`wpa_passphrase`, surfaced as the sensitive `passphrase` argument).


def _wpa2_wlan_candidate(name: str = "Guest", *, vlan: int = 20) -> dict:
    return candidate(
        "wlan",
        name,
        payload={
            "essid": name,
            "security": {"mode": "wpa2_personal", "ambiguous": False},
            "vlan": vlan,
        },
        requires_secret_input=True,
        secret_fields=["wpa_passphrase"],
    )


def test_verify_catches_payload_field_mismatch_via_operation_payload(tmp_path):
    """Finding #6: expected fields must come from Operation.payload/arguments
    (the exact request the SSID build tool receives), and a genuine field
    mismatch there must be reported -- not silently passed on identity
    alone."""
    backend = FakeBackend()
    service, _ = orchestrator(tmp_path, backend)
    wlan = _wpa2_wlan_candidate()
    service.create_run([wlan], target(), run_id="wlan-mismatch")
    supplied = {"wlan:Guest": {"wpa_passphrase": "Sup3rSecret!"}}
    service.apply("wlan-mismatch", dry_run=True, confirmation=False, target_secrets=supplied)
    service.apply("wlan-mismatch", dry_run=False, confirmation=True, target_secrets=supplied)

    # Target read: identity (qualified short SSID `name`, not the full
    # candidate key "wlan:Guest") matches and `opmode` matches, but the
    # actual VLAN differs from what was sent -- a genuine payload field
    # mismatch.
    backend.read_values["get_ssid"] = {
        "name": "Guest",
        "opmode": "WPA2_PERSONAL",
        "vlan_ids": [99],
    }

    result = service.verify("wlan-mismatch")
    comparison = result["comparisons"][0]
    assert comparison["verification_status"] == "mismatch"
    fields = {item["field"]: item for item in comparison["field_comparison"]}
    assert fields["vlan_ids[0]"]["status"] == "mismatch"
    assert fields["vlan_ids[0]"]["expected"] == 20
    assert fields["vlan_ids[0]"]["actual"] == 99
    # A correctly-matching non-identifier field is still confirmed.
    assert fields["opmode"]["status"] == "match"


def test_verify_uses_qualified_match_identifier_not_full_candidate_key(tmp_path):
    """Finding #6: identity/field comparison must use `match_identifier`
    (the short SSID `name` New Central actually returns), not the fully
    qualified candidate key ("wlan:Guest") which would never appear in a
    real target read response."""
    backend = FakeBackend()
    service, _ = orchestrator(tmp_path, backend)
    wlan = _wpa2_wlan_candidate()
    service.create_run([wlan], target(), run_id="wlan-qualified")
    supplied = {"wlan:Guest": {"wpa_passphrase": "Sup3rSecret!"}}
    service.apply("wlan-qualified", dry_run=True, confirmation=False, target_secrets=supplied)
    service.apply("wlan-qualified", dry_run=False, confirmation=True, target_secrets=supplied)

    # The target only ever returns the short SSID `name`; a naive
    # comparison against the full candidate key "wlan:Guest" would
    # incorrectly report the identity as missing. Every other non-secret
    # expected field is also returned so status legitimately reaches
    # "verified" (Finding #3's stricter gate).
    backend.read_values["get_ssid"] = {
        "name": "Guest",
        "opmode": "WPA2_PERSONAL",
        "vlan_ids": [20],
        "wpa3_transition": False,
    }
    result = service.verify("wlan-qualified")
    comparison = result["comparisons"][0]
    assert comparison["verification_status"] == "verified"


def test_verify_reports_secret_fields_as_unverifiable_never_mismatch(tmp_path):
    """Finding #6: GET responses never return secret material -- these
    fields must be reported "unverifiable", never compared/"mismatch"."""
    backend = FakeBackend()
    service, _ = orchestrator(tmp_path, backend)
    wlan = _wpa2_wlan_candidate()
    service.create_run([wlan], target(), run_id="wlan-secret")
    supplied = {"wlan:Guest": {"wpa_passphrase": "Sup3rSecret!"}}
    service.apply("wlan-secret", dry_run=True, confirmation=False, target_secrets=supplied)
    service.apply("wlan-secret", dry_run=False, confirmation=True, target_secrets=supplied)

    # Target read omits wpa_passphrase entirely, as any real GET would.
    backend.read_values["get_ssid"] = {
        "name": "Guest",
        "opmode": "WPA2_PERSONAL",
        "vlan_ids": [20],
        "wpa3_transition": False,
    }
    result = service.verify("wlan-secret")
    comparison = result["comparisons"][0]
    secret_entries = [
        item for item in comparison["field_comparison"] if item["field"] == "passphrase"
    ]
    assert len(secret_entries) == 1
    assert secret_entries[0]["status"] == "unverifiable"
    assert secret_entries[0]["expected"] == "***"
    assert comparison["verification_status"] == "verified"


def test_verify_partially_verified_when_no_nonsecret_field_can_be_confirmed(tmp_path):
    """Finding #6: identity alone must not be reported as full success when
    the target read omits every non-secret expected field."""
    backend = FakeBackend()
    service, _ = orchestrator(tmp_path, backend)
    vlan = candidate("vlan", "20", payload={"description": "Corp"})
    service.create_run([vlan], target(), run_id="vlan-identity-only")
    service.apply("vlan-identity-only", dry_run=True, confirmation=False)
    service.apply("vlan-identity-only", dry_run=False, confirmation=True)
    # Target read confirms identity (vlan id "20") but returns no other
    # comparable field at all.
    backend.read_values["central_api_read"] = {"id": "20"}
    result = service.verify("vlan-identity-only")
    comparison = result["comparisons"][0]
    assert comparison["verification_status"] == "partially_verified"


# ---------------------------------------------------------------------------
# Finding #4 (final-pass safety decision): rollback is fully retracted for
# 0.5, not left as a half-safe/unsafe feature. These tests confirm the
# adapter/orchestrator rollback-execution path introduced in `9c9a7b0` is
# completely removed and unreachable -- not merely gated -- and that no
# preview/API surface still advertises rollback as verified/executable.
# ---------------------------------------------------------------------------


def test_orchestrator_has_no_rollback_method(tmp_path):
    backend = FakeBackend()
    service, _ = orchestrator(tmp_path, backend)
    assert not hasattr(service, "rollback")
    assert not hasattr(service, "_rollback_locked")


def test_adapters_have_no_rollback_method():
    for adapter_cls in (NewCentralAdapter, ClassicCentralAdapter):
        assert not hasattr(adapter_cls, "rollback")


def test_preview_reports_rollback_supported_false_never_verified_available(tmp_path):
    """Finding #4: preview output must honestly report rollback as
    unsupported -- never the retracted `verified_rollback_available: True`
    claim, and delete/read-back operation metadata (if present at all) must
    not be presented as executable rollback."""
    backend = FakeBackend()
    service, _ = orchestrator(tmp_path, backend)
    vlan = candidate("vlan", "20", payload={"description": "Corp"})
    preview = service.preview([vlan], target())
    operation = preview["operations"][0]
    assert operation["rollback_supported"] is False
    assert "verified_rollback_available" not in operation

    role = candidate(
        "role",
        "employee",
        payload={"policies": ["allowall"]},
    )
    role_preview = service.preview([role], target())
    role_operation = role_preview["operations"][0]
    assert role_operation["rollback_supported"] is False
    assert "verified_rollback_available" not in role_operation
    # Role's assignment write is still verified/ready -- rollback dishonesty
    # is not conflated with the (separate) finding #1 assignment gating.
    assert role_operation["status"] == "ready"


def test_run_summary_and_entry_summary_have_no_rollback_fields(tmp_path):
    """Finding #4: no rollback timestamps/results survive in run or entry
    summaries -- the feature is retracted, not merely hidden behind a flag."""
    backend = FakeBackend()
    service, _ = orchestrator(tmp_path, backend)
    vlan = candidate("vlan", "20", payload={"description": "Corp"})
    service.create_run([vlan], target(), run_id="rollback-retracted-summary")
    service.apply("rollback-retracted-summary", dry_run=True, confirmation=False)
    applied = service.apply("rollback-retracted-summary", dry_run=False, confirmation=True)

    forbidden_run_keys = {
        "rollback_dry_run_attempted_at",
        "last_rollback_at",
        "rolled_back",
    }
    assert not forbidden_run_keys & set(applied.keys())
    forbidden_entry_keys = {
        "rollback_dry_run_ok",
        "last_rollback_result",
        "conflict",
    }
    for entry in applied["candidates"]:
        assert not forbidden_entry_keys & set(entry.keys())
        assert entry["status"] != "rolled_back"

    status = service.get_run("rollback-retracted-summary")
    assert not forbidden_run_keys & set(status.keys())
    for entry in status["candidates"]:
        assert not forbidden_entry_keys & set(entry.keys())


def test_no_mcp_rollback_tool_is_registered():
    """Finding #4: rollback must not be exposed as an MCP tool."""
    tool_names = {name for name in dir(aos8) if name.startswith("aos8_")}
    assert not any("rollback" in name for name in tool_names)


# ---------------------------------------------------------------------------
# Finding #3 (final-pass safety decision): indexed array flattening must
# catch a reordered or truncated `servers` array in a server-group payload,
# never silently "match" on the first element alone. Server-group
# candidates are permanently "blocked" (finding #1 -- unverified
# `server-groups` assignment profile-type) and can no longer reach
# "applied" through the normal apply() path, so `_verify_entry` is invoked
# directly with a synthetic "applied" entry -- a legitimate, supported way
# to exercise this general verification mechanism against a real
# `_map_server_group` payload shape.
# ---------------------------------------------------------------------------


def _server_group_candidate() -> dict:
    return candidate(
        "server_group",
        "corp-sg",
        payload={
            "auth_server_entries": [
                {"name": "rad1", "position": 1},
                {"name": "rad2", "position": 2},
            ]
        },
        dependencies=["auth_server:radius:rad1", "auth_server:radius:rad2"],
    )


def _verify_server_group_directly(tmp_path, backend, target_read):
    service, _ = orchestrator(tmp_path, backend)
    sg_candidate = _server_group_candidate()
    adapter = service._adapter(target(), [sg_candidate], placeholders=True)
    entry = {
        "key": "server_group:corp-sg",
        "status": "applied",
        "candidate": sg_candidate,
        "last_result": {"ok": True},
    }
    backend.read_values["central_api_read"] = target_read
    return service._verify_entry(adapter, entry)


def test_verify_detects_reordered_server_group_servers_array(tmp_path):
    backend = FakeBackend()
    reordered = {
        "name": "corp-sg",
        "type": "RADIUS",
        "servers": [
            {"server-name": "rad2", "position": 2},
            {"server-name": "rad1", "position": 1},
        ],
    }
    comparison = _verify_server_group_directly(tmp_path, backend, reordered)
    assert comparison["verification_status"] == "mismatch"
    fields = {item["field"]: item for item in comparison["field_comparison"]}
    assert fields["servers[0].server_name"]["status"] == "mismatch"
    assert fields["servers[0].server_name"]["expected"] == "rad1"
    assert fields["servers[0].server_name"]["actual"] == "rad2"
    assert fields["servers[1].server_name"]["status"] == "mismatch"
    assert fields["servers[1].server_name"]["expected"] == "rad2"
    assert fields["servers[1].server_name"]["actual"] == "rad1"


def test_verify_detects_incomplete_server_group_servers_array(tmp_path):
    """A target read missing the second `servers` entry must not be
    reported "verified" merely because the first (bare-key) entry matches
    -- the missing indexed entry must be explicitly unverifiable, forcing
    "partially_verified"."""
    backend = FakeBackend()
    truncated = {
        "name": "corp-sg",
        "type": "RADIUS",
        "servers": [{"server-name": "rad1", "position": 1}],
    }
    comparison = _verify_server_group_directly(tmp_path, backend, truncated)
    fields = {item["field"]: item for item in comparison["field_comparison"]}
    # The first (bare-key) entry legitimately matches.
    assert fields["servers[0].server_name"]["status"] == "match"
    # The second, missing entry is explicitly unverifiable, not silently
    # dropped and not a false "match".
    assert fields["servers[1].server_name"]["status"] == "unverifiable"
    assert fields["servers[1].position"]["status"] == "unverifiable"
    assert comparison["verification_status"] == "partially_verified"


# --------------------------------------------------------------------------
# Finding #3 regression: numeric Classic group names are accepted end-to-end
# (no spelling-based `.isdigit()` rejection anywhere in the orchestration
# path).
# --------------------------------------------------------------------------


def test_classic_numeric_group_name_accepted_end_to_end_through_orchestrator(tmp_path):
    backend = FakeBackend()
    service, _ = orchestrator(tmp_path, backend)
    numeric_target = target("classic_central")
    numeric_target["scope_name"] = "12345"
    wlan = candidate(
        "wlan",
        "Guest",
        payload={
            "name": "Guest",
            "essid": "Guest",
            "vlan": 20,
            "aaa_profile": None,
            "security": {
                "mode": "open",
                "opmode": "open",
                "ambiguous": False,
                "aaa_profile": None,
                "dot1x_auth_profile": None,
                "mac_auth_profile": None,
                "passphrase_present": False,
                "psk_hexkey_present": False,
                "wpa3_transition": False,
                "evidence": [],
            },
        },
        unsupported_fields={
            "ssid_profile.opmode": "open",
            "virtual_ap.forward_mode": "bridge",
        },
    )
    preview = service.preview([wlan], numeric_target)
    assert preview["target"]["scope_name"] == "12345"
    assert preview["operations"][0]["status"] == "ready"


# --------------------------------------------------------------------------
# Finding #5 regression: operator-context bounding/validation and backward
# compatibility with persisted 0.4-era target dictionaries.
# --------------------------------------------------------------------------


def test_target_context_backward_compatible_with_pre_operator_context_state():
    # A 0.4-era persisted `run["target"]` dict predates
    # `external_object_references`/`ap_group_target_map`/
    # `ap_group_device_serials` entirely -- it must load without error and
    # fall back to empty collections.
    legacy_target = {
        "type": "classic_central",
        "scope_id": "classic-id",
        "scope_name": "Branch Group",
        "persona": "CAMPUS_AP",
        "conflict_policy": "fail",
        "cluster_name": None,
        "cluster_scope_id": None,
        "gateway_name": None,
        "gateway_scope_id": None,
    }
    context = _target_context(legacy_target)
    assert context.external_object_references == {}
    assert context.ap_group_target_map == {}
    assert context.ap_group_device_serials == {}


def test_run_store_loads_legacy_0_4_state_without_operator_context_metadata(tmp_path):
    # A 0.4-era run file has neither the three operator-context keys on
    # `target` nor an `operator_context_metadata` key at all. Loading,
    # summarizing, and listing it must not crash, and must report no
    # legacy-sanitization marker (there is nothing to sanitize).
    backend = FakeBackend()
    service, store = orchestrator(tmp_path, backend)
    legacy_run = {
        "schema_version": 1,
        "run_id": "legacy-04",
        "fingerprint": "deadbeef",
        "status": "pending",
        "created_at": "2024-01-01T00:00:00+00:00",
        "updated_at": "2024-01-01T00:00:00+00:00",
        "target": {
            "type": "classic_central",
            "scope_id": "1",
            "scope_name": "Branch",
            "persona": "CAMPUS_AP",
            "conflict_policy": "fail",
            "cluster_name": None,
            "cluster_scope_id": None,
            "gateway_name": None,
            "gateway_scope_id": None,
        },
        "checkpoint_and_rollback": None,
        "dry_run_attempted_at": None,
        "last_apply_at": None,
        "last_verification_at": None,
        "candidates": [],
    }
    store.save(legacy_run)

    fetched = service.get_run("legacy-04")
    assert fetched["legacy_operator_context_sanitized"] is None
    assert "external_object_references" not in fetched["target"]

    listed = service.list_runs()
    assert listed["runs"][0]["run_id"] == "legacy-04"
    assert listed["runs"][0]["legacy_operator_context_sanitized"] is None

    # A 0.4 run with no operator context at all is fully resumable: apply
    # is not blocked by any legacy marker.
    applied = service.apply("legacy-04", dry_run=True, confirmation=False)
    assert applied["dry_run"] is True


def _write_raw_state_file(store: MigrationRunStore, run_id: str, run: dict) -> None:
    """Write a hand-crafted state file directly to disk, bypassing
    `MigrationRunStore.save()` entirely -- this simulates a genuinely
    stale file written before the fail-closed contract existed (or a
    hand-edited one), which is exactly the scenario `load()` must heal.
    `save()` itself now refuses to persist a non-empty operator-context
    map (see its hard backstop), so it cannot be used to construct this
    fixture.
    """
    path = store.path_for(run_id)
    path.write_text(json.dumps(run), encoding="utf-8")


def test_run_store_sanitizes_stale_raw_operator_context_on_disk_atomically(
    tmp_path,
):
    # Simulate a state file written before the fail-closed contract
    # existed (or hand-edited) that still carries raw operator-context
    # values directly on `target`. A read must heal it -- never crash,
    # never continue serving the raw value back out through
    # get_run/list_runs/apply, and (per the atomic-migration requirement)
    # must rewrite the *actual file on disk*, not just an in-memory copy.
    backend = FakeBackend()
    service, store = orchestrator(tmp_path, backend)
    stale_secret_lookalike = "sk-proj-stale-leaked-value-0123456789"
    stale_run = {
        "schema_version": 1,
        "run_id": "stale-05",
        "fingerprint": "deadbeef",
        "status": "pending",
        "created_at": "2025-01-01T00:00:00+00:00",
        "updated_at": "2025-01-01T00:00:00+00:00",
        "target": {
            "type": "classic_central",
            "scope_id": "1",
            "scope_name": "Branch",
            "persona": "CAMPUS_AP",
            "conflict_policy": "fail",
            "cluster_name": None,
            "cluster_scope_id": None,
            "gateway_name": None,
            "gateway_scope_id": None,
            "external_object_references": {
                "wlan:Stale": {"auth_server1": stale_secret_lookalike}
            },
            "ap_group_target_map": {"ap-group-stale": "Stale-Group"},
            "ap_group_device_serials": {"ap-group-stale": ["CN0001"]},
        },
        "checkpoint_and_rollback": None,
        "dry_run_attempted_at": None,
        "last_apply_at": None,
        "last_verification_at": None,
        "candidates": [],
    }
    path = store.path_for("stale-05")
    _write_raw_state_file(store, "stale-05", stale_run)
    # Sanity: the hand-crafted fixture really does carry the raw value on
    # disk before anything reads it.
    assert stale_secret_lookalike in path.read_text()

    fetched = service.get_run("stale-05")
    assert "external_object_references" not in fetched["target"]
    assert "ap_group_target_map" not in fetched["target"]
    assert "ap_group_device_serials" not in fetched["target"]
    assert stale_secret_lookalike not in json.dumps(fetched)
    assert fetched["legacy_operator_context_sanitized"] is not None

    # The actual on-disk file was rewritten (atomically, by
    # `MigrationRunStore`'s existing temp-file + os.replace mechanism) --
    # this is not just an in-memory mutation.
    raw_state_after_load = path.read_text()
    assert stale_secret_lookalike not in raw_state_after_load
    assert "Stale-Group" not in raw_state_after_load
    assert "CN0001" not in raw_state_after_load
    assert "external_object_references" not in json.loads(raw_state_after_load)["target"]
    assert "legacy_operator_context_sanitized" in json.loads(raw_state_after_load)

    listed = service.list_runs()
    assert stale_secret_lookalike not in json.dumps(listed)
    assert listed["runs"][0]["legacy_operator_context_sanitized"] is not None

    # A run healed from unsafe legacy operator context can never be
    # applied -- it must be recreated instead, since the context that may
    # have affected its candidates' mapping is gone and cannot be trusted
    # to be resupplied.
    with pytest.raises(MigrationRunError, match="recreate"):
        service.apply("stale-05", dry_run=True, confirmation=False)


def test_run_store_sanitizes_stale_fingerprint_metadata_on_disk_atomically(
    tmp_path,
):
    # A state file written by the intermediate (now-removed)
    # fingerprint/resupply design persisted a non-reversible
    # `operator_context_metadata` (count + SHA-256 hash) instead of the
    # raw values. That, too, must be healed on load and removed from disk
    # -- the fail-closed contract stores no hash at all, not even a safe
    # one, since there is no verifier for these free-form identifiers and
    # any stored hash is itself an unwanted offline-guessing surface.
    backend = FakeBackend()
    service, store = orchestrator(tmp_path, backend)
    stale_run = {
        "schema_version": 1,
        "run_id": "stale-fp-06",
        "fingerprint": "deadbeef",
        "status": "pending",
        "created_at": "2025-01-02T00:00:00+00:00",
        "updated_at": "2025-01-02T00:00:00+00:00",
        "target": {
            "type": "classic_central",
            "scope_id": "1",
            "scope_name": "Branch",
            "persona": "CAMPUS_AP",
            "conflict_policy": "fail",
            "cluster_name": None,
            "cluster_scope_id": None,
            "gateway_name": None,
            "gateway_scope_id": None,
        },
        "operator_context_metadata": {
            "external_object_references": {
                "count": 1,
                "fingerprint": "a" * 64,
            },
            "ap_group_target_map": {"count": 0, "fingerprint": None},
            "ap_group_device_serials": {"count": 0, "fingerprint": None},
        },
        "checkpoint_and_rollback": None,
        "dry_run_attempted_at": None,
        "last_apply_at": None,
        "last_verification_at": None,
        "candidates": [],
    }
    path = store.path_for("stale-fp-06")
    _write_raw_state_file(store, "stale-fp-06", stale_run)
    assert "operator_context_metadata" in json.loads(path.read_text())

    fetched = service.get_run("stale-fp-06")
    assert fetched["legacy_operator_context_sanitized"] is not None

    raw_state_after_load = path.read_text()
    assert "operator_context_metadata" not in json.loads(raw_state_after_load)
    assert "a" * 64 not in raw_state_after_load

    with pytest.raises(MigrationRunError, match="recreate"):
        service.apply("stale-fp-06", dry_run=True, confirmation=False)


def test_apply_signature_has_no_operator_context_parameters(tmp_path):
    # Requirement: `apply()` (and the MCP `aos8_apply_migration_run` tool)
    # must not accept `external_object_references`/`ap_group_target_map`/
    # `ap_group_device_serials` at all -- there is no resupply mechanism
    # left to reconcile them against.
    backend = FakeBackend()
    service, store = orchestrator(tmp_path, backend)
    ap_group = _ap_group_candidate("ap-group-no-resupply")
    service.create_run([ap_group], target("classic_central"), run_id="no-resupply-run")

    with pytest.raises(TypeError):
        service.apply(
            "no-resupply-run",
            dry_run=True,
            confirmation=False,
            ap_group_target_map={"ap-group-no-resupply": "Some-Group"},
        )

    import inspect

    apply_params = set(inspect.signature(service.apply).parameters)
    for field in ("external_object_references", "ap_group_target_map", "ap_group_device_serials"):
        assert field not in apply_params

    mcp_apply_params = set(inspect.signature(aos8.aos8_apply_migration_run).parameters)
    for field in ("external_object_references", "ap_group_target_map", "ap_group_device_serials"):
        assert field not in mcp_apply_params

    # A normal apply() call with no operator context still works.
    result = service.apply("no-resupply-run", dry_run=True, confirmation=False)
    assert result["candidates"][0]["status"] == "unsupported"


def test_target_context_rejects_oversized_external_object_references():
    too_many = {f"wlan:candidate-{i}": {"auth_server1": "Server"} for i in range(200)}
    bad_target = {**target("classic_central"), "external_object_references": too_many}
    with pytest.raises(MigrationRunError, match="external_object_references"):
        _target_context(bad_target)


def test_target_context_rejects_oversized_string_in_ap_group_target_map():
    bad_target = {
        **target("classic_central"),
        "ap_group_target_map": {"ap-group-hq": "x" * 500},
    }
    with pytest.raises(MigrationRunError, match="ap_group_target_map"):
        _target_context(bad_target)


def test_target_context_bounds_ap_group_device_serials_per_group():
    bad_target = {
        **target("classic_central"),
        "ap_group_device_serials": {"ap-group-hq": [f"CN{i}" for i in range(100)]},
    }
    with pytest.raises(MigrationRunError, match="ap_group_device_serials"):
        _target_context(bad_target)


def test_target_context_accepts_bounded_operator_context_and_converts_serials_to_tuple():
    good_target = {
        **target("classic_central"),
        "external_object_references": {"wlan:Corp": {"auth_server1": "InternalServer"}},
        "ap_group_target_map": {"ap-group-hq": "HQ-Group"},
        "ap_group_device_serials": {"ap-group-hq": ["CN1234", "CN5678"]},
    }
    context = _target_context(good_target)
    assert context.external_object_references == {
        "wlan:Corp": {"auth_server1": "InternalServer"}
    }
    assert context.ap_group_target_map == {"ap-group-hq": "HQ-Group"}
    assert context.ap_group_device_serials == {"ap-group-hq": ("CN1234", "CN5678")}


def test_target_context_accepts_legitimate_identifiers_with_secret_shaped_words():
    # Regression guard for the review finding this replaces: field-name
    # secret heuristics (`_is_sensitive_key`) must never be applied to
    # arbitrary operator-supplied identifier values. "Token-Group" and
    # "private-key-infra" are ordinary Classic-group/AP-group names that
    # happen to contain the words "token"/"key" -- they must be accepted,
    # not rejected as secret-looking.
    good_target = {
        **target("classic_central"),
        "external_object_references": {
            "wlan:Corp": {"auth_server1": "Token-Group"}
        },
        "ap_group_target_map": {"private-key-infra": "Token-Group"},
        "ap_group_device_serials": {"private-key-infra": ["CN1234"]},
    }
    context = _target_context(good_target)
    assert context.external_object_references == {
        "wlan:Corp": {"auth_server1": "Token-Group"}
    }
    assert context.ap_group_target_map == {"private-key-infra": "Token-Group"}
    assert context.ap_group_device_serials == {"private-key-infra": ("CN1234",)}


def test_target_context_accepts_ordinary_looking_operator_context_values():
    # Regression guard: legitimate group names, GUIDs, and device serials
    # must never trip a secret-like value heuristic (there is none left --
    # only structural bounds apply to these free-form identifier maps).
    good_target = {
        **target("classic_central"),
        "external_object_references": {
            "wlan:Corp": {
                "auth_server1": "InternalServer",
                "guid_ref": "550e8400-e29b-41d4-a716-446655440000",
            }
        },
        "ap_group_target_map": {"ap-group-hq": "HQ-Group-12345"},
        "ap_group_device_serials": {"ap-group-hq": ["CN1234", "CN5678"]},
    }
    context = _target_context(good_target)
    assert context.external_object_references["wlan:Corp"]["auth_server1"] == (
        "InternalServer"
    )
    assert context.ap_group_target_map == {"ap-group-hq": "HQ-Group-12345"}
    assert context.ap_group_device_serials == {"ap-group-hq": ("CN1234", "CN5678")}


def test_target_context_canonicalizes_surrounding_whitespace_structurally():
    # Requirement: transient identifier strings are canonicalized
    # structurally (surrounding-whitespace trimmed) -- never by a
    # secret-word/content heuristic. Whitespace-padded keys and values
    # across all three maps must be trimmed to their canonical form.
    padded_target = {
        **target("classic_central"),
        "external_object_references": {
            "  wlan:Corp  ": {"  auth_server1  ": "  InternalServer  "}
        },
        "ap_group_target_map": {"  ap-group-hq  ": "  HQ-Group  "},
        "ap_group_device_serials": {"  ap-group-hq  ": ["  CN1234  ", "CN5678"]},
    }
    context = _target_context(padded_target)
    assert context.external_object_references == {
        "wlan:Corp": {"auth_server1": "InternalServer"}
    }
    assert context.ap_group_target_map == {"ap-group-hq": "HQ-Group"}
    assert context.ap_group_device_serials == {"ap-group-hq": ("CN1234", "CN5678")}

    # A string that is nothing but whitespace is still rejected as empty
    # (canonicalization trims, it does not invent a non-empty value).
    with pytest.raises(MigrationRunError, match="non-empty"):
        _target_context(
            {**target("classic_central"), "ap_group_target_map": {"ap-group-hq": "   "}}
        )


def _wpa3_enterprise_candidate(name="Enterprise-Test", vlan=40):
    return candidate(
        "wlan",
        name,
        payload={
            "name": name,
            "essid": name,
            "vlan": vlan,
            "aaa_profile": "corp-aaa",
            "security": {
                "mode": "enterprise_dot1x",
                "opmode": "wpa3-aes-ccm-128",
                "ambiguous": False,
                "aaa_profile": "corp-aaa",
                "dot1x_auth_profile": "corp-dot1x",
                "mac_auth_profile": None,
                "passphrase_present": False,
                "psk_hexkey_present": False,
                "wpa3_transition": False,
                "evidence": [],
            },
        },
        unsupported_fields={
            "ssid_profile.opmode": "wpa3-aes-ccm-128",
            "virtual_ap.forward_mode": "bridge",
        },
    )


def test_wpa3_enterprise_reachable_only_via_stateless_preview_never_create_run(
    tmp_path,
):
    # Full contract: WPA3-Enterprise's conditional mapping only ever
    # becomes "ready"/dry-run-reachable when `external_object_references`
    # supplies the already-existing auth-server name. That context is
    # accepted by the stateless `preview()` only -- `create_run()` must
    # reject it outright, never persist it (raw, hashed, or as a resupply
    # count), and a run created *without* that context simply keeps the
    # candidate unsupported, exactly like the missing-reference case
    # always did.
    backend = FakeBackend()
    service, store = orchestrator(tmp_path, backend)
    refs = {"wlan:Enterprise-Test": {"auth_server1": "InternalServer"}}
    wpa3_target = {
        **target("classic_central"),
        "external_object_references": refs,
    }
    wlan = _wpa3_enterprise_candidate()

    preview = service.preview([wlan], wpa3_target)
    # `preview()` is stateless -- it may echo the context back in this one
    # response for operator review.
    assert preview["target"]["external_object_references"] == refs
    action = preview["operations"][0]
    assert action["status"] == "ready"
    assert action["dry_run_only"] is True

    # `create_run()` must reject the same target outright.
    with pytest.raises(MigrationRunError, match="external_object_references"):
        service.create_run([wlan], wpa3_target, run_id="wpa3-ent")
    assert not store.path_for("wpa3-ent").exists()

    # Creating the run without any operator context is allowed, but the
    # candidate remains unsupported -- there is no way to make the
    # auth-server reference reach this run at all, by design.
    created = service.create_run([wlan], target("classic_central"), run_id="wpa3-ent-no-ctx")
    assert created["candidates"][0]["status"] == "unsupported"
    assert "external_object_references" not in created["target"]
    raw_state = store.path_for("wpa3-ent-no-ctx").read_text()
    assert "InternalServer" not in raw_state
    assert "external_object_references" not in json.loads(raw_state)["target"]

    # `apply()` has no operator-context parameters at all -- passing one
    # is a plain TypeError, not a resupply-mismatch error.
    with pytest.raises(TypeError):
        service.apply(
            "wpa3-ent-no-ctx",
            dry_run=True,
            confirmation=False,
            external_object_references=refs,
        )

    # Applying the context-free run stays unsupported/refused throughout.
    dry_run = service.apply("wpa3-ent-no-ctx", dry_run=True, confirmation=False)
    assert dry_run["candidates"][0]["status"] == "unsupported"
    blocked = service.apply("wpa3-ent-no-ctx", dry_run=False, confirmation=True)
    assert blocked["candidates"][0]["status"] == "unsupported"


def test_wpa3_enterprise_mcp_preview_allows_context_create_run_rejects_it(
    tmp_path, monkeypatch
):
    # End-to-end through the real MCP tool signatures
    # (`aos8_preview_migration_run`/`aos8_create_migration_run`), confirming
    # `external_object_references` reaches the adapter via the MCP boundary
    # for stateless preview, and that the same context is rejected -- with
    # a clear, non-crashing error -- by the persistent `aos8_create_migration_run`.
    backend = FakeBackend()
    service, store = orchestrator(tmp_path, backend)
    monkeypatch.setattr(aos8, "_aos8_migration_orchestrator", lambda: service)
    wlan = _wpa3_enterprise_candidate()

    preview = aos8.aos8_preview_migration_run(
        "classic_central",
        candidates=[wlan],
        scope_name="Branch Group",
        external_object_references={
            "wlan:Enterprise-Test": {"auth_server1": "InternalServer"}
        },
    )
    assert preview["operations"][0]["status"] == "ready"
    assert preview["operations"][0]["dry_run_only"] is True
    assert preview["target"]["external_object_references"] == {
        "wlan:Enterprise-Test": {"auth_server1": "InternalServer"}
    }

    rejected = aos8.aos8_create_migration_run(
        "classic_central",
        candidates=[wlan],
        scope_name="Branch Group",
        external_object_references={
            "wlan:Enterprise-Test": {"auth_server1": "InternalServer"}
        },
        run_id="mcp-wpa3-ent",
    )
    assert rejected["status"] == "blocked"
    assert "external_object_references" in rejected["error"]
    assert "InternalServer" not in json.dumps(rejected)
    assert not store.path_for("mcp-wpa3-ent").exists()

    created = aos8.aos8_create_migration_run(
        "classic_central",
        candidates=[wlan],
        scope_name="Branch Group",
        run_id="mcp-wpa3-ent-no-ctx",
    )
    assert created["candidates"][0]["status"] == "unsupported"
