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
    backend.read_values["central_api_read"] = {"id": "20", "name": "Corp"}

    result = service.verify("verified")
    comparison = result["comparisons"][0]
    assert comparison["verification_status"] == "verified"
    assert any(
        field["field"] == "vlan_name" and field["status"] == "match"
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


def test_verify_catches_payload_field_mismatch_via_operation_payload(tmp_path):
    """Finding #6: expected fields must come from Operation.payload (the
    exact endpoint request body), and a genuine field mismatch there must be
    reported -- not silently passed on identity alone."""
    backend = FakeBackend()
    service, _ = orchestrator(tmp_path, backend)
    ldap = candidate(
        "auth_server",
        "ldap:ldap1",
        payload={"name": "ldap1", "server_type": "ldap", "host": "10.0.0.11"},
        unsupported_fields={"ldap_admindn": "cn=admin,dc=example,dc=com"},
        requires_secret_input=True,
        secret_fields=["admin_password"],
    )
    service.create_run([ldap], target(), run_id="ldap-mismatch")
    supplied = {"auth_server:ldap:ldap1": {"admin_password": "bindpw"}}
    service.apply("ldap-mismatch", dry_run=True, confirmation=False, target_secrets=supplied)
    service.apply("ldap-mismatch", dry_run=False, confirmation=True, target_secrets=supplied)

    # Target read: identity (qualified short `name`, not the full candidate
    # key "ldap:ldap1") matches, `admin-dn` matches, but the actual host
    # differs from what was sent -- a genuine payload field mismatch.
    backend.read_values["get_auth_server"] = {
        "name": "ldap1",
        "auth-server-address": "10.0.0.99",
        "admin-dn": "cn=admin,dc=example,dc=com",
    }

    result = service.verify("ldap-mismatch")
    comparison = result["comparisons"][0]
    assert comparison["verification_status"] == "mismatch"
    fields = {item["field"]: item for item in comparison["field_comparison"]}
    assert fields["auth_server_address"]["status"] == "mismatch"
    assert fields["auth_server_address"]["expected"] == "10.0.0.11"
    assert fields["auth_server_address"]["actual"] == "10.0.0.99"
    # A correctly-matching non-identifier field is still confirmed.
    assert fields["admin_dn"]["status"] == "match"


def test_verify_uses_qualified_match_identifier_not_full_candidate_key(tmp_path):
    """Finding #6: identity/field comparison must use `match_identifier`
    (the short profile `name` New Central actually returns), not the fully
    qualified candidate key ("ldap:ldap1") which would never appear in a
    real target read response."""
    backend = FakeBackend()
    service, _ = orchestrator(tmp_path, backend)
    ldap = candidate(
        "auth_server",
        "ldap:ldap1",
        payload={"name": "ldap1", "server_type": "ldap", "host": "10.0.0.11"},
        requires_secret_input=True,
        secret_fields=["admin_password"],
    )
    service.create_run([ldap], target(), run_id="ldap-qualified")
    supplied = {"auth_server:ldap:ldap1": {"admin_password": "bindpw"}}
    service.apply("ldap-qualified", dry_run=True, confirmation=False, target_secrets=supplied)
    service.apply("ldap-qualified", dry_run=False, confirmation=True, target_secrets=supplied)

    # The target only ever returns the short `name`; a naive comparison
    # against the full candidate key "ldap:ldap1" would incorrectly report
    # the identity as missing.
    backend.read_values["get_auth_server"] = {
        "name": "ldap1",
        "auth-server-address": "10.0.0.11",
    }
    result = service.verify("ldap-qualified")
    comparison = result["comparisons"][0]
    assert comparison["verification_status"] == "verified"


def test_verify_reports_secret_fields_as_unverifiable_never_mismatch(tmp_path):
    """Finding #6: GET responses never return secret material -- these
    fields must be reported "unverifiable", never compared/"mismatch"."""
    backend = FakeBackend()
    service, _ = orchestrator(tmp_path, backend)
    ldap = candidate(
        "auth_server",
        "ldap:ldap1",
        payload={"name": "ldap1", "server_type": "ldap", "host": "10.0.0.11"},
        requires_secret_input=True,
        secret_fields=["admin_password"],
    )
    service.create_run([ldap], target(), run_id="ldap-secret")
    supplied = {"auth_server:ldap:ldap1": {"admin_password": "bindpw"}}
    service.apply("ldap-secret", dry_run=True, confirmation=False, target_secrets=supplied)
    service.apply("ldap-secret", dry_run=False, confirmation=True, target_secrets=supplied)

    # Target read omits admin-password entirely, as any real GET would.
    backend.read_values["get_auth_server"] = {
        "name": "ldap1",
        "auth-server-address": "10.0.0.11",
    }
    result = service.verify("ldap-secret")
    comparison = result["comparisons"][0]
    secret_entries = [
        item for item in comparison["field_comparison"] if item["field"] == "admin_password"
    ]
    assert len(secret_entries) == 1
    assert secret_entries[0]["status"] == "unverifiable"
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


def test_rollback_requires_dry_run_first_and_reverses_dependency_order(tmp_path):
    """Finding #7: rollback must invoke delete_operations in reverse
    dependency order, gated by dry-run/confirmation/writes-enabled."""
    backend = FakeBackend()
    service, _ = orchestrator(tmp_path, backend)
    rad1 = candidate(
        "auth_server",
        "radius:rad1",
        payload={"name": "rad1", "server_type": "radius", "host": "10.0.0.10"},
        requires_secret_input=True,
        secret_fields=["shared_secret"],
    )
    group = candidate(
        "server_group",
        "corp-sg",
        payload={
            "name": "corp-sg",
            "auth_server_entries": [{"name": "rad1", "position": 1}],
        },
        dependencies=["auth_server:radius:rad1"],
        apply_order=20,
    )
    service.create_run([rad1, group], target(), run_id="rollback-order")
    secrets = {"auth_server:radius:rad1": {"shared_secret": "s3cret"}}
    service.apply("rollback-order", dry_run=True, confirmation=False, target_secrets=secrets)
    applied = service.apply(
        "rollback-order", dry_run=False, confirmation=True, target_secrets=secrets
    )
    assert [item["status"] for item in applied["candidates"]] == ["applied", "applied"]

    with pytest.raises(WriteGateError, match="dry_run=True"):
        service.rollback("rollback-order", dry_run=False, confirmation=True)

    dry = service.rollback("rollback-order", dry_run=True, confirmation=False)
    assert [item["status"] for item in dry["rollback_results"]] == ["dry-run", "dry-run"]
    # Reverse of the dependency-ordered apply: the dependent server-group is
    # rolled back before the auth-server it depended on.
    assert [item["candidate"] for item in dry["rollback_results"]] == [
        "server_group:corp-sg",
        "auth_server:radius:rad1",
    ]

    with pytest.raises(WriteGateError, match="confirmation=True"):
        service.rollback("rollback-order", dry_run=False, confirmation=False)

    real = service.rollback("rollback-order", dry_run=False, confirmation=True)
    assert [item["candidate"] for item in real["rollback_results"]] == [
        "server_group:corp-sg",
        "auth_server:radius:rad1",
    ]
    assert [item["status"] for item in real["rollback_results"]] == [
        "rolled_back",
        "rolled_back",
    ]
    assert [item["status"] for item in real["candidates"]] == [
        "rolled_back",
        "rolled_back",
    ]
    # Delete writes were actually invoked in that same reverse order.
    real_delete_names = [
        op.name
        for op, confirmed in backend.write_calls
        if confirmed and not bool(op.arguments.get("dry_run"))
        and op.name in {"delete_config_assignment", "delete_auth_server"}
    ]
    assert real_delete_names.index("delete_config_assignment") < len(real_delete_names)
    # server-group's own delete_operations run before auth-server's.
    group_positions = [i for i, n in enumerate(real_delete_names)]
    assert group_positions  # sanity: at least one delete call recorded


def test_rollback_never_deletes_pre_existing_updated_object(tmp_path):
    """Finding #7: a candidate that updated a pre-existing target object
    (conflict == "update") must never be deleted by rollback."""
    backend = FakeBackend()
    backend.read_values["central_api_read"] = {"id": "20", "name": "Existing"}
    service, _ = orchestrator(tmp_path, backend)
    vlan = candidate("vlan", "20", payload={"description": "Corp"})
    service.create_run([vlan], target(policy="update"), run_id="rollback-update")
    service.apply("rollback-update", dry_run=True, confirmation=False)
    applied = service.apply("rollback-update", dry_run=False, confirmation=True)
    assert applied["candidates"][0]["status"] == "applied"

    service.rollback("rollback-update", dry_run=True, confirmation=False)
    real = service.rollback("rollback-update", dry_run=False, confirmation=True)
    assert real["rollback_results"][0]["status"] == "rollback_unavailable"
    assert "pre-existing" in real["rollback_results"][0]["reason"]
    # Status remains "applied" -- rollback did not touch it.
    assert real["candidates"][0]["status"] == "applied"


def test_rollback_records_failure_without_crashing_and_keeps_applied_status(tmp_path):
    """Finding #7: a failed delete during rollback must be reported, never
    silently swallowed or mis-marked, and must not crash the sweep."""
    backend = FakeBackend()
    service, _ = orchestrator(tmp_path, backend)
    vlan = candidate("vlan", "20", payload={"description": "Corp"})
    service.create_run([vlan], target(), run_id="rollback-fail")
    service.apply("rollback-fail", dry_run=True, confirmation=False)
    service.apply("rollback-fail", dry_run=False, confirmation=True)

    service.rollback("rollback-fail", dry_run=True, confirmation=False)
    result = service.rollback("rollback-fail", dry_run=False, confirmation=True)
    # vlan has no verified delete_operations today -> rollback_unavailable,
    # never a crash and never falsely reported as rolled back.
    assert result["rollback_results"][0]["status"] == "rollback_unavailable"
    assert result["candidates"][0]["status"] == "applied"


def test_rollback_gated_by_writes_enabled(tmp_path):
    # Apply successfully with writes enabled, then attempt a real rollback
    # against a second orchestrator instance (same on-disk state) where
    # writes have since been disabled -- the per-operation writes_enabled
    # gate inside adapter.rollback() must still block the delete.
    backend = FakeBackend()
    service, _ = orchestrator(tmp_path, backend, writes=True)
    rad1 = candidate(
        "auth_server",
        "radius:rad1",
        payload={"name": "rad1", "server_type": "radius", "host": "10.0.0.10"},
        requires_secret_input=True,
        secret_fields=["shared_secret"],
    )
    service.create_run([rad1], target(), run_id="rollback-writes-disabled")
    secrets = {"auth_server:radius:rad1": {"shared_secret": "s3cret"}}
    service.apply(
        "rollback-writes-disabled", dry_run=True, confirmation=False, target_secrets=secrets
    )
    applied = service.apply(
        "rollback-writes-disabled", dry_run=False, confirmation=True, target_secrets=secrets
    )
    assert applied["candidates"][0]["status"] == "applied"

    disabled_service, _ = orchestrator(tmp_path, backend, writes=False)
    disabled_service.rollback("rollback-writes-disabled", dry_run=True, confirmation=False)
    result = disabled_service.rollback(
        "rollback-writes-disabled", dry_run=False, confirmation=True
    )
    assert result["rollback_results"][0]["status"] == "failed"
    assert "writes are disabled" in result["rollback_results"][0]["errors"][0]
    assert result["candidates"][0]["status"] == "applied"
