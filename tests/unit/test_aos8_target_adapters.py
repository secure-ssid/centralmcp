"""Pure/fake-invoker tests for AOS8 Classic/New Central target adapters."""

from __future__ import annotations

import pytest

from pipeline.aos8_target_adapters import (
    ClassicCentralAdapter,
    ConflictPolicy,
    ContextValidationError,
    DependencySelectionError,
    NewCentralAdapter,
    TargetContext,
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
) -> dict:
    return {
        "object_type": object_type,
        "identifier": identifier,
        "payload": payload or {},
        "dependencies": dependencies or [],
        "apply_order": apply_order,
        "unsupported_fields": unsupported_fields or {},
        "requires_secret_input": requires_secret_input,
        "secret_fields": [],
        "warnings": [],
    }


class FakeBackend:
    def __init__(self, reads=None, failures=None):
        self.reads = reads or {}
        self.failures = failures or {}
        self.read_calls = []
        self.write_calls = []

    def read(self, operation):
        self.read_calls.append(operation)
        value = self.reads.get(operation.name)
        if isinstance(value, Exception):
            raise value
        return value

    def write(self, operation, *, confirmation):
        self.write_calls.append((operation, confirmation))
        failure = self.failures.get(operation.name)
        if failure:
            raise failure
        return {"ok": True, "name": operation.name}


def resolve_scope(context):
    if context.scope_name == "bad":
        raise ValueError("unknown scope")
    return context.scope_id or "100", context.scope_name or "Branch"


def validate_persona(context):
    if context.persona not in {"CAMPUS_AP", "MOBILITY_GW", "ACCESS_SWITCH"}:
        raise ValueError("invalid persona")
    return context.persona


def new_adapter(
    backend,
    *,
    policy=ConflictPolicy.FAIL,
    secrets=None,
    writes=True,
    cluster=False,
):
    return NewCentralAdapter(
        TargetContext(
            target_type=TargetType.NEW_CENTRAL,
            scope_id="100",
            scope_name="Branch",
            persona="CAMPUS_AP",
            cluster_name="cluster-1" if cluster else None,
            cluster_scope_id="200" if cluster else None,
            conflict_policy=policy,
            secret_inputs=secrets or {},
        ),
        scope_resolver=resolve_scope,
        persona_validator=validate_persona,
        read_invoker=backend.read,
        write_invoker=backend.write,
        writes_enabled=lambda target: writes,
    )


def classic_adapter(backend, *, policy=ConflictPolicy.FAIL):
    return ClassicCentralAdapter(
        TargetContext(
            target_type=TargetType.CLASSIC_CENTRAL,
            scope_id="classic-id",
            scope_name="Branch Group",
            persona="CAMPUS_AP",
            conflict_policy=policy,
        ),
        scope_resolver=resolve_scope,
        persona_validator=validate_persona,
        read_invoker=backend.read,
        write_invoker=backend.write,
        writes_enabled=lambda target: True,
    )


def test_context_scope_and_persona_are_validated_by_injected_collaborators():
    backend = FakeBackend()
    with pytest.raises(ContextValidationError, match="unknown scope"):
        NewCentralAdapter(
            TargetContext(
                target_type=TargetType.NEW_CENTRAL,
                scope_name="bad",
                persona="CAMPUS_AP",
            ),
            scope_resolver=resolve_scope,
            persona_validator=validate_persona,
            read_invoker=backend.read,
            write_invoker=backend.write,
            writes_enabled=lambda target: True,
        )
    with pytest.raises(ContextValidationError, match="invalid persona"):
        NewCentralAdapter(
            TargetContext(
                target_type=TargetType.NEW_CENTRAL,
                scope_name="Branch",
                persona="CONTROLLER",
            ),
            scope_resolver=resolve_scope,
            persona_validator=validate_persona,
            read_invoker=backend.read,
            write_invoker=backend.write,
            writes_enabled=lambda target: True,
        )


def test_new_vlan_preview_has_verified_tool_scope_and_deterministic_order():
    backend = FakeBackend()
    adapter = new_adapter(backend)
    vlan = candidate("vlan", "20", payload={"description": "Corp"}, apply_order=10)
    role = candidate(
        "role",
        "employee",
        payload={"name": "employee", "vlan": 20, "policies": ["allowall"]},
        dependencies=["vlan:20"],
        apply_order=30,
    )

    preview = adapter.preview([role, vlan])

    assert [item["candidate"] for item in preview["operations"]] == [
        "vlan:20",
        "role:employee",
    ]
    vlan_operation = preview["operations"][0]["operations"][0]
    assert vlan_operation["tool_or_endpoint"] == "create_vlan"
    assert vlan_operation["arguments"]["scope_id"] == "100"
    role_assignment = preview["operations"][1]["operations"][1]
    assert role_assignment["tool_or_endpoint"] == ("/network-config/v1alpha1/config-assignments")
    assert role_assignment["payload"]["config-assignment"][0]["device-function"] == ("CAMPUS_AP")
    assert preview["checkpoint_and_rollback"] == {
        "post_change_checkpoint_policy_only": True,
        "automatic_rollback_supported": True,
        "manual_checkpoint_restore_supported": False,
        "guidance": (
            "Optionally configure build_config_checkpoint_policy before migration. "
            "New Central can generate a post-change checkpoint and devices can "
            "automatically revert a failed push; there is no manual checkpoint "
            "listing or restore operation."
        ),
        "provenance": (
            "mcp_servers.config.build_config_checkpoint_policy and "
            "get_config_rollback_status; "
            "developer.arubanetworks.com/new-central-config/reference/config-checkpoint"
        ),
    }


def test_selection_adds_dependency_closure_and_can_reject_omissions():
    backend = FakeBackend()
    adapter = new_adapter(backend)
    vlan = candidate("vlan", "20")
    role = candidate(
        "role",
        "employee",
        payload={"policies": ["allowall"]},
        dependencies=["vlan:20"],
        apply_order=30,
    )
    preview = adapter.preview([role, vlan], selected={"role:employee"})
    assert [item["candidate"] for item in preview["operations"]] == [
        "vlan:20",
        "role:employee",
    ]

    with pytest.raises(DependencySelectionError, match="was not selected"):
        adapter.preview(
            [role, vlan],
            selected={"role:employee"},
            include_dependency_closure=False,
        )

    unresolved = adapter.preview(
        [role, vlan],
        selected={"role:employee"},
        include_dependency_closure=False,
        allow_unresolved_blockers=True,
    )
    assert unresolved["operations"][0]["status"] == "blocked"
    assert "was not selected" in unresolved["operations"][0]["blockers"][0]


def test_missing_candidate_dependency_rejected_unless_previewing_blocker():
    backend = FakeBackend()
    adapter = new_adapter(backend)
    role = candidate(
        "role",
        "employee",
        payload={"policies": ["allowall"]},
        dependencies=["vlan:20"],
    )
    with pytest.raises(DependencySelectionError, match="absent"):
        adapter.preview([role])
    preview = adapter.preview([role], allow_unresolved_blockers=True)
    assert preview["operations"][0]["status"] == "blocked"


def test_conflict_fail_skip_and_update_behaviors():
    existing = {"items": [{"name": "employee"}]}
    role = candidate("role", "employee", payload={"policies": ["allowall"]})

    failed = new_adapter(FakeBackend(reads={"list_roles": existing})).preview([role])
    assert failed["operations"][0]["status"] == "blocked"

    skipped = new_adapter(
        FakeBackend(reads={"list_roles": existing}),
        policy=ConflictPolicy.SKIP_EXISTING,
    ).preview([role])
    assert skipped["operations"][0]["status"] == "skipped"

    updated = new_adapter(
        FakeBackend(reads={"list_roles": existing}),
        policy=ConflictPolicy.UPDATE,
    ).preview([role])
    assert updated["operations"][0]["conflict"] == "update"
    assert updated["operations"][0]["operations"][0]["tool_or_endpoint"] == "update_role"


def test_update_policy_blocks_when_verified_update_is_unavailable():
    auth = candidate(
        "auth_server",
        "radius:rad1",
        payload={"name": "rad1", "server_type": "radius", "host": "10.0.0.10"},
        requires_secret_input=True,
    )
    backend = FakeBackend(reads={"get_auth_server": {"name": "rad1"}})
    adapter = new_adapter(
        backend,
        policy=ConflictPolicy.UPDATE,
        secrets={"auth_server:radius:rad1": {"shared_secret": "real-secret"}},
    )
    preview = adapter.preview([auth])
    assert preview["operations"][0]["status"] == "blocked"
    assert "no update operation" in preview["operations"][0]["blockers"][0]


def test_radius_requires_caller_secret_and_masks_preview():
    auth = candidate(
        "auth_server",
        "radius:rad1",
        payload={"name": "rad1", "server_type": "radius", "host": "10.0.0.10"},
        unsupported_fields={"rad_authport": 1812, "rad_key": "<redacted:present>"},
        requires_secret_input=True,
    )
    missing = new_adapter(FakeBackend()).preview([auth])
    assert missing["operations"][0]["status"] == "unsupported"
    assert "non-redacted target secret" in missing["operations"][0]["unsupported_warnings"][0]

    redacted = new_adapter(
        FakeBackend(),
        secrets={"auth_server:radius:rad1": {"shared_secret": "<redacted:present>"}},
    ).preview([auth])
    assert redacted["operations"][0]["status"] == "unsupported"

    valid = new_adapter(
        FakeBackend(),
        secrets={"auth_server:radius:rad1": {"shared_secret": "actual-secret"}},
    ).preview([auth])
    arguments = valid["operations"][0]["operations"][0]["arguments"]
    assert arguments["shared_secret"] == "***"
    assert "actual-secret" not in str(valid)


def test_simple_aaa_profile_maps_only_verified_fields():
    aaa = candidate(
        "aaa_profile",
        "guest-aaa",
        payload={
            "name": "guest-aaa",
            "default_user_role": "guest",
            "accounting_server_group": "acct-group",
        },
    )
    preview = new_adapter(FakeBackend()).preview([aaa])
    operation = preview["operations"][0]["operations"][0]
    assert operation["tool_or_endpoint"] == "create_aaa_profile"
    assert operation["arguments"]["auth_role"] == "guest"
    assert operation["arguments"]["acct_server_group"] == "acct-group"


def test_unsupported_objects_and_lossy_mappings_remain_unapplied():
    backend = FakeBackend()
    adapter = new_adapter(backend)
    ldap = candidate(
        "auth_server",
        "ldap:ldap1",
        payload={"name": "ldap1", "server_type": "ldap", "host": "10.0.0.11"},
    )
    route = candidate("route", "ipv4:0.0.0.0")
    custom_role = candidate(
        "role",
        "restricted",
        payload={"policies": ["corp-acl"]},
    )
    preview = adapter.preview([ldap, route, custom_role])
    assert {item["status"] for item in preview["operations"]} == {"unsupported"}
    assert backend.read_calls == []


def test_open_underlay_and_tunneled_wlan_mapping_differences():
    wlan = candidate(
        "wlan",
        "Guest",
        payload={"name": "Guest", "essid": "Guest", "vlan": 20, "aaa_profile": None},
        unsupported_fields={
            "ssid_profile.opmode": "open",
            "virtual_ap.forward_mode": "bridge",
        },
    )
    underlay = new_adapter(FakeBackend()).preview([wlan])
    assert underlay["operations"][0]["operations"][0]["tool_or_endpoint"] == ("build_underlay_ssid")

    tunneled = {
        **wlan,
        "unsupported_fields": {
            "ssid_profile.opmode": "open",
            "virtual_ap.forward_mode": "tunnel",
        },
    }
    missing_cluster = new_adapter(FakeBackend()).preview([tunneled])
    assert missing_cluster["operations"][0]["status"] == "unsupported"
    with_cluster = new_adapter(FakeBackend(), cluster=True).preview([tunneled])
    assert with_cluster["operations"][0]["operations"][0]["tool_or_endpoint"] == (
        "build_overlay_ssid"
    )


def test_classic_maps_only_verified_open_bridged_full_wlan():
    backend = FakeBackend()
    adapter = classic_adapter(backend)
    wlan = candidate(
        "wlan",
        "Guest",
        payload={"name": "Guest", "essid": "Guest", "vlan": 20, "aaa_profile": None},
        unsupported_fields={
            "ssid_profile.opmode": "open",
            "virtual_ap.forward_mode": "bridge",
        },
    )
    preview = adapter.preview([wlan])
    operation = preview["operations"][0]["operations"][0]
    assert operation["method"] == "POST"
    assert operation["tool_or_endpoint"] == ("/configuration/full_wlan/Branch%20Group/Guest")
    assert operation["payload"]["wlan"]["opmode"] == "opensystem"
    assert preview["checkpoint_and_rollback"]["automatic_rollback_supported"] is False

    unsupported = adapter.preview([candidate("vlan", "20")])
    assert unsupported["operations"][0]["status"] == "unsupported"


def test_classic_wlan_embeds_vlan_dependency_while_vlan_candidate_stays_unapplied():
    backend = FakeBackend()
    adapter = classic_adapter(backend)
    vlan = candidate("vlan", "20")
    wlan = candidate(
        "wlan",
        "Guest",
        payload={"name": "Guest", "essid": "Guest", "vlan": 20, "aaa_profile": None},
        dependencies=["vlan:20"],
        apply_order=50,
        unsupported_fields={
            "ssid_profile.opmode": "open",
            "virtual_ap.forward_mode": "bridge",
        },
    )
    result = adapter.execute([wlan, vlan], dry_run=False, confirmation=True)
    assert result["results"][0]["status"] == "unsupported"
    assert result["results"][1]["status"] == "applied"
    assert result["operations"][1]["inline_dependencies"] == ["vlan:20"]


def test_dry_run_invokes_backend_with_dry_run_and_no_confirmation():
    backend = FakeBackend()
    adapter = new_adapter(backend)
    result = adapter.dry_run([candidate("vlan", "20")])
    operation, confirmation = backend.write_calls[0]
    assert operation.arguments["dry_run"] is True
    assert confirmation is False
    assert result["results"][0]["status"] == "dry-run"


def test_execution_requires_all_write_gates():
    vlan = candidate("vlan", "20")
    with pytest.raises(WriteGateError, match="dry_run=False"):
        new_adapter(FakeBackend()).execute([vlan], dry_run=True, confirmation=True)
    with pytest.raises(WriteGateError, match="confirmation"):
        new_adapter(FakeBackend()).execute([vlan], dry_run=False, confirmation=False)
    with pytest.raises(WriteGateError, match="disabled"):
        new_adapter(FakeBackend(), writes=False).execute([vlan], dry_run=False, confirmation=True)


def test_execution_passes_confirmation_and_preserves_partial_failures():
    vlan = candidate("vlan", "20")
    role = candidate(
        "role",
        "employee",
        payload={"policies": ["allowall"]},
        dependencies=["vlan:20"],
        apply_order=30,
    )
    backend = FakeBackend(failures={"create_vlan": RuntimeError("API 503")})
    result = new_adapter(backend).execute([vlan, role], dry_run=False, confirmation=True)
    assert result["results"][0]["status"] == "failed"
    assert result["results"][0]["errors"] == ["create_vlan: API 503"]
    assert result["results"][1]["status"] == "blocked"
    assert all(confirmation is True for _, confirmation in backend.write_calls)
    assert all(call.arguments["dry_run"] is False for call, _ in backend.write_calls)


def test_multi_operation_candidate_preserves_success_before_later_failure():
    role = candidate("role", "employee", payload={"policies": ["allowall"]})
    backend = FakeBackend(failures={"central_api_request": RuntimeError("assignment rejected")})
    result = new_adapter(backend).execute([role], dry_run=False, confirmation=True)
    role_result = result["results"][0]
    assert role_result["status"] == "failed"
    assert len(role_result["results"]) == 1
    assert role_result["results"][0]["operation"]["tool_or_endpoint"] == "create_role"
    assert role_result["errors"] == ["central_api_request: assignment rejected"]


def test_preflight_errors_are_reported_without_writes():
    backend = FakeBackend(reads={"list_roles": RuntimeError("read timeout")})
    adapter = new_adapter(backend)
    role = candidate("role", "employee", payload={"policies": ["allowall"]})
    result = adapter.execute([role], dry_run=False, confirmation=True)
    assert result["results"][0]["status"] == "blocked"
    assert result["results"][0]["errors"] == ["preflight read failed: read timeout"]
    assert backend.write_calls == []
