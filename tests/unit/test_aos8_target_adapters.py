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
    if context.persona not in {
        "CAMPUS_AP",
        "MICROBRANCH_AP",
        "MOBILITY_GW",
        "ACCESS_SWITCH",
    }:
        raise ValueError("invalid persona")
    return context.persona


def new_adapter(
    backend,
    *,
    policy=ConflictPolicy.FAIL,
    secrets=None,
    writes=True,
    cluster=False,
    persona="CAMPUS_AP",
):
    return NewCentralAdapter(
        TargetContext(
            target_type=TargetType.NEW_CENTRAL,
            scope_id="100",
            scope_name="Branch",
            persona=persona,
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
    # WLAN mappings have no verified New Central update tool/endpoint today
    # (build_underlay_ssid/build_overlay_ssid are create-only); RADIUS/LDAP/
    # TACACS auth-servers, AAA/dot1x/macauth profiles, server-groups, and
    # roles all now carry a verified PATCH/update path (see
    # test_radius_*_update_and_delete_operations_are_verified and friends),
    # so WLAN is the remaining example of this "no update operation" gate.
    wlan = candidate(
        "wlan",
        "Guest",
        payload={
            "name": "Guest",
            "essid": "Guest",
            "vlan": 20,
            "aaa_profile": None,
            "security": {"mode": "open", "opmode": "open", "ambiguous": False},
        },
        unsupported_fields={
            "ssid_profile.opmode": "open",
            "virtual_ap.forward_mode": "bridge",
        },
    )
    backend = FakeBackend(reads={"get_ssid": {"name": "Guest"}})
    adapter = new_adapter(backend, policy=ConflictPolicy.UPDATE)
    preview = adapter.preview([wlan])
    assert preview["operations"][0]["conflict"] == "existing"
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
    # AAA profiles are a Gateway/Switch device concept only (never AP), per
    # docs/aos8-migration-contract-matrix.md §4; use a Gateway persona.
    aaa = candidate(
        "aaa_profile",
        "guest-aaa",
        payload={
            "name": "guest-aaa",
            "default_user_role": "guest",
            "accounting_server_group": "acct-group",
        },
    )
    preview = new_adapter(FakeBackend(), persona="MOBILITY_GW").preview([aaa])
    operation = preview["operations"][0]["operations"][0]
    assert operation["tool_or_endpoint"] == "create_aaa_profile"
    assert operation["arguments"]["auth_role"] == "guest"
    assert operation["arguments"]["acct_server_group"] == "acct-group"


def test_aaa_profile_rejects_ap_persona_and_exposes_update_delete_operations():
    aaa = candidate(
        "aaa_profile",
        "guest-aaa",
        payload={"name": "guest-aaa", "default_user_role": "guest"},
    )
    rejected = new_adapter(FakeBackend(), persona="CAMPUS_AP").preview([aaa])
    assert rejected["operations"][0]["status"] == "unsupported"
    assert "device-function" in rejected["operations"][0]["unsupported_warnings"][0]

    preview = new_adapter(FakeBackend(), persona="ACCESS_SWITCH").preview([aaa])
    entry = preview["operations"][0]
    assert entry["update_operations"][0]["method"] == "PATCH"
    # SHARED-profile rollback unassigns the config-assignment before deleting
    # the aaa-profile object itself.
    assert entry["delete_operations"][0]["tool_or_endpoint"] == "delete_config_assignment"
    assert entry["delete_operations"][0]["arguments"]["profile_type"] == "aaa-profile"
    assert entry["delete_operations"][1]["tool_or_endpoint"] == "delete_aaa_profile"
    assert entry["verified_rollback_available"] is True
    # Assignment operation is present alongside the create/update writes too.
    assert entry["operations"][1]["payload"]["config-assignment"][0]["profile-type"] == (
        "aaa-profile"
    )
    assert entry["update_operations"][1]["payload"]["config-assignment"][0][
        "profile-type"
    ] == "aaa-profile"


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
        payload={
            "name": "Guest",
            "essid": "Guest",
            "vlan": 20,
            "aaa_profile": None,
            "security": {"mode": "open", "opmode": "open", "ambiguous": False},
        },
        unsupported_fields={
            "ssid_profile.opmode": "open",
            "virtual_ap.forward_mode": "bridge",
        },
    )
    underlay = new_adapter(FakeBackend()).preview([wlan])
    assert underlay["operations"][0]["operations"][0]["tool_or_endpoint"] == ("build_underlay_ssid")
    assert underlay["operations"][0]["delete_operations"][0]["tool_or_endpoint"] == (
        "delete_underlay_ssid"
    )

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
    assert with_cluster["operations"][0]["delete_operations"][0]["tool_or_endpoint"] == (
        "delete_overlay_ssid"
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


# ---------------------------------------------------------------------------
# LDAP / TACACS auth-servers (item 1: extend RADIUS carefully, add LDAP/
# TACACS only with exact spec fields and transient secret requirements).
# ---------------------------------------------------------------------------


def test_radius_auth_server_has_verified_update_and_delete_operations():
    auth = candidate(
        "auth_server",
        "radius:rad1",
        payload={"name": "rad1", "server_type": "radius", "host": "10.0.0.10"},
        unsupported_fields={"rad_authport": 1812, "rad_acctport": 1813},
    )
    preview = new_adapter(
        FakeBackend(),
        secrets={"auth_server:radius:rad1": {"shared_secret": "s3cret"}},
    ).preview([auth])
    entry = preview["operations"][0]
    assert entry["operations"][0]["tool_or_endpoint"] == "create_auth_server"
    assert entry["update_operations"][0]["method"] == "PATCH"
    assert entry["update_operations"][0]["tool_or_endpoint"] == (
        "/network-config/v1alpha1/auth-servers/rad1"
    )
    assert "s3cret" not in str(entry["update_operations"])
    # SHARED-profile rollback unassigns before deleting the auth-server object.
    assert entry["delete_operations"][0]["tool_or_endpoint"] == "delete_config_assignment"
    assert entry["delete_operations"][0]["arguments"]["profile_type"] == "auth-servers"
    assert entry["delete_operations"][1]["tool_or_endpoint"] == "delete_auth_server"
    assert entry["verified_rollback_available"] is True
    assert entry["operations"][1]["payload"]["config-assignment"][0]["profile-type"] == (
        "auth-servers"
    )


def test_ldap_auth_server_maps_exact_fields_and_requires_admin_password_secret():
    ldap = candidate(
        "auth_server",
        "ldap:ldap1",
        payload={"name": "ldap1", "server_type": "ldap", "host": "10.0.0.11"},
        unsupported_fields={
            "ldap_admindn": "cn=admin,dc=example,dc=com",
            "ldap_keyattribute": "uid",
        },
    )
    missing_secret = new_adapter(FakeBackend()).preview([ldap])
    assert missing_secret["operations"][0]["status"] == "unsupported"
    assert "admin_password" in missing_secret["operations"][0]["unsupported_warnings"][0]

    preview = new_adapter(
        FakeBackend(),
        secrets={"auth_server:ldap:ldap1": {"admin_password": "bindpw"}},
    ).preview([ldap])
    entry = preview["operations"][0]
    assert entry["status"] == "ready"
    operation = entry["operations"][0]
    assert operation["method"] == "POST"
    assert operation["tool_or_endpoint"] == "/network-config/v1alpha1/auth-servers/ldap1"
    assert operation["payload"]["type"] == "LDAP"
    assert operation["payload"]["admin-dn"] == "cn=admin,dc=example,dc=com"
    assert operation["payload"]["key-attribute"] == "uid"
    assert operation["payload"]["admin-password"] == "***"
    assert "bindpw" not in str(preview)
    assert entry["update_operations"][0]["method"] == "PATCH"
    assert entry["delete_operations"][0]["tool_or_endpoint"] == "delete_config_assignment"
    assert entry["delete_operations"][1]["tool_or_endpoint"] == "delete_auth_server"


def test_ldap_auth_server_rejected_on_switch_persona():
    ldap = candidate(
        "auth_server",
        "ldap:ldap1",
        payload={"name": "ldap1", "server_type": "ldap", "host": "10.0.0.11"},
    )
    preview = new_adapter(
        FakeBackend(),
        persona="ACCESS_SWITCH",
        secrets={"auth_server:ldap:ldap1": {"admin_password": "bindpw"}},
    ).preview([ldap])
    assert preview["operations"][0]["status"] == "unsupported"
    assert "device-function" in preview["operations"][0]["unsupported_warnings"][0]


def test_tacacs_auth_server_maps_exact_fields_on_every_persona_family():
    tacacs = candidate(
        "auth_server",
        "tacacs:tac1",
        payload={"name": "tac1", "server_type": "tacacs", "host": "10.0.0.12"},
        unsupported_fields={"tacacs_tcpport": 49, "tacacs_timeout": 5},
    )
    for persona in ("CAMPUS_AP", "MOBILITY_GW", "ACCESS_SWITCH"):
        preview = new_adapter(
            FakeBackend(),
            persona=persona,
            secrets={"auth_server:tacacs:tac1": {"shared_secret": "tacsecret"}},
        ).preview([tacacs])
        entry = preview["operations"][0]
        assert entry["status"] == "ready", persona
        payload = entry["operations"][0]["payload"]
        assert payload["type"] == "TACACS"
        assert payload["tcp-port"] == 49
        assert payload["timeout"] == 5
        assert payload["shared-secret-config"] == "***"
        assert "tacsecret" not in str(preview)


def test_radsec_and_other_auth_server_types_stay_unsupported():
    for server_type in ("radsec", "windows", "xmlapi", "local", "rfc3576"):
        auth = candidate(
            "auth_server",
            f"{server_type}:s1",
            payload={"name": "s1", "server_type": server_type, "host": "10.0.0.13"},
        )
        preview = new_adapter(FakeBackend()).preview([auth])
        assert preview["operations"][0]["status"] == "unsupported"


# ---------------------------------------------------------------------------
# Server groups (item 2): ordered `servers` entries with positions,
# type/persona validation, dependency on typed auth-server candidates.
# ---------------------------------------------------------------------------


def test_server_group_builds_ordered_servers_array_from_dependencies():
    group = candidate(
        "server_group",
        "corp-sg",
        payload={
            "name": "corp-sg",
            "auth_servers": ["rad2", "rad1"],
            "auth_server_entries": [
                {"name": "rad2", "position": 2},
                {"name": "rad1", "position": 1},
            ],
            "fail_through": True,
            "load_balance": False,
        },
        dependencies=["auth_server:radius:rad1", "auth_server:radius:rad2"],
    )
    rad1 = candidate(
        "auth_server",
        "radius:rad1",
        payload={"name": "rad1", "server_type": "radius", "host": "10.0.0.10"},
    )
    rad2 = candidate(
        "auth_server",
        "radius:rad2",
        payload={"name": "rad2", "server_type": "radius", "host": "10.0.0.11"},
    )
    preview = new_adapter(
        FakeBackend(),
        secrets={
            "auth_server:radius:rad1": {"shared_secret": "s1"},
            "auth_server:radius:rad2": {"shared_secret": "s2"},
        },
    ).preview([group, rad1, rad2], selected={"server_group:corp-sg"})
    entry = next(
        item for item in preview["operations"] if item["candidate"] == "server_group:corp-sg"
    )
    assert entry["status"] == "ready"
    create_op = entry["operations"][0]
    assert create_op["tool_or_endpoint"] == "/network-config/v1alpha1/server-groups/corp-sg"
    servers = create_op["payload"]["servers"]
    assert servers == [
        {"server-name": "rad1", "position": 1},
        {"server-name": "rad2", "position": 2},
    ]
    assert create_op["payload"]["type"] == "RADIUS"
    assert create_op["payload"]["fail-through"] is True
    assign_op = entry["operations"][1]
    assert assign_op["payload"]["config-assignment"][0]["profile-type"] == "server-groups"
    assert entry["delete_operations"][0]["tool_or_endpoint"] == "delete_config_assignment"
    assert entry["delete_operations"][1]["tool_or_endpoint"] == (
        "/network-config/v1alpha1/server-groups/corp-sg"
    )


def test_server_group_rejects_mixed_auth_server_types():
    group = candidate(
        "server_group",
        "mixed-sg",
        payload={
            "name": "mixed-sg",
            "auth_servers": ["rad1", "ldap1"],
            "auth_server_entries": [
                {"name": "rad1", "position": 1},
                {"name": "ldap1", "position": 2},
            ],
        },
        dependencies=["auth_server:radius:rad1", "auth_server:ldap:ldap1"],
    )
    rad1 = candidate(
        "auth_server",
        "radius:rad1",
        payload={"name": "rad1", "server_type": "radius", "host": "10.0.0.10"},
    )
    ldap1 = candidate(
        "auth_server",
        "ldap:ldap1",
        payload={"name": "ldap1", "server_type": "ldap", "host": "10.0.0.11"},
    )
    preview = new_adapter(FakeBackend()).preview(
        [group, rad1, ldap1], selected={"server_group:mixed-sg"}
    )
    entry = next(
        item for item in preview["operations"] if item["candidate"] == "server_group:mixed-sg"
    )
    assert entry["status"] == "unsupported"
    assert "mixes auth-server types" in entry["unsupported_warnings"][0]


def test_server_group_rejects_type_collision_flag_and_unresolved_entries():
    collision_group = candidate(
        "server_group",
        "ambiguous-sg",
        payload={"name": "ambiguous-sg", "auth_server_entries": [{"name": "rad1", "position": 1}]},
        unsupported_fields={"auth_server_type_collisions": {"rad1": ["ldap", "radius"]}},
        dependencies=[],
    )
    preview = new_adapter(FakeBackend()).preview([collision_group])
    assert preview["operations"][0]["status"] == "unsupported"
    assert "ambiguous across server types" in preview["operations"][0]["unsupported_warnings"][0]

    unresolved_entry_group = candidate(
        "server_group",
        "corp-sg2",
        payload={
            "name": "corp-sg2",
            "auth_server_entries": [{"name": "does-not-exist", "position": 1}],
        },
        dependencies=["auth_server:radius:rad1"],
    )
    rad1 = candidate(
        "auth_server",
        "radius:rad1",
        payload={"name": "rad1", "server_type": "radius", "host": "10.0.0.10"},
    )
    preview2 = new_adapter(FakeBackend()).preview(
        [unresolved_entry_group, rad1], selected={"server_group:corp-sg2"}
    )
    entry2 = next(
        item for item in preview2["operations"] if item["candidate"] == "server_group:corp-sg2"
    )
    assert entry2["status"] == "unsupported"
    assert "does not resolve to a verified auth-server dependency" in (
        entry2["unsupported_warnings"][0]
    )


def test_server_group_ldap_type_rejected_on_switch_persona():
    group = candidate(
        "server_group",
        "ldap-sg",
        payload={
            "name": "ldap-sg",
            "auth_server_entries": [{"name": "ldap1", "position": 1}],
        },
        dependencies=["auth_server:ldap:ldap1"],
    )
    ldap1 = candidate(
        "auth_server",
        "ldap:ldap1",
        payload={"name": "ldap1", "server_type": "ldap", "host": "10.0.0.11"},
    )
    preview = new_adapter(FakeBackend(), persona="ACCESS_SWITCH").preview(
        [group, ldap1], selected={"server_group:ldap-sg"}
    )
    entry = next(
        item for item in preview["operations"] if item["candidate"] == "server_group:ldap-sg"
    )
    assert entry["status"] == "unsupported"
    assert "device-function" in entry["unsupported_warnings"][0]


# ---------------------------------------------------------------------------
# Gateway/switch-only dot1x/macauth device profiles (item 3).
# ---------------------------------------------------------------------------


def test_bare_dot1x_and_macauth_profiles_map_on_gateway_switch_only():
    dot1x = candidate("dot1x_auth_profile", "corp-dot1x", payload={"name": "corp-dot1x"})
    macauth = candidate("mac_auth_profile", "corp-mac", payload={"name": "corp-mac"})

    for object_candidate, resource in ((dot1x, "dot1xauth"), (macauth, "macauth")):
        rejected = new_adapter(FakeBackend(), persona="CAMPUS_AP").preview([object_candidate])
        assert rejected["operations"][0]["status"] == "unsupported"
        assert "device-function" in rejected["operations"][0]["unsupported_warnings"][0]

        for persona in ("MOBILITY_GW", "ACCESS_SWITCH"):
            preview = new_adapter(FakeBackend(), persona=persona).preview([object_candidate])
            entry = preview["operations"][0]
            assert entry["status"] == "ready", (resource, persona)
            create_op = entry["operations"][0]
            assert create_op["method"] == "POST"
            assert resource in create_op["tool_or_endpoint"]
            assert create_op["payload"] == {"name": object_candidate["identifier"]}
            assert entry["update_operations"][0]["method"] == "PATCH"
            # SHARED-profile rollback unassigns the config-assignment first,
            # then deletes the dot1xauth/macauth object itself.
            assert entry["delete_operations"][0]["tool_or_endpoint"] == (
                "delete_config_assignment"
            )
            assert entry["delete_operations"][0]["arguments"]["profile_type"] == resource
            assert entry["delete_operations"][1]["method"] == "DELETE"
            assert entry["operations"][1]["payload"]["config-assignment"][0][
                "profile-type"
            ] == resource
            assert entry["verified_rollback_available"] is True


def test_rich_dot1x_and_macauth_profiles_are_rejected_not_guessed():
    dot1x = candidate(
        "dot1x_auth_profile",
        "corp-dot1x",
        payload={"name": "corp-dot1x"},
        unsupported_fields={"use_session_key": True, "reauthentication": True},
    )
    preview = new_adapter(FakeBackend(), persona="MOBILITY_GW").preview([dot1x])
    assert preview["operations"][0]["status"] == "unsupported"
    assert "no verified 1:1 New Central field mapping" in (
        preview["operations"][0]["unsupported_warnings"][0]
    )


def test_aaa_profile_and_device_profiles_never_apply_to_ap_wlan_personas():
    # Cross-check: every Gateway/Switch-only device concept must reject the
    # AP persona family consistently.
    for object_type, payload in (
        ("aaa_profile", {"name": "p1", "default_user_role": "emp"}),
        ("dot1x_auth_profile", {"name": "d1"}),
        ("mac_auth_profile", {"name": "m1"}),
    ):
        cand = candidate(object_type, payload["name"], payload=payload)
        preview = new_adapter(FakeBackend(), persona="CAMPUS_AP").preview([cand])
        assert preview["operations"][0]["status"] == "unsupported"


# ---------------------------------------------------------------------------
# Secured AP WLANs using normalized `payload.security` (item 5).
# ---------------------------------------------------------------------------


def _wlan_candidate(
    mode, *, forward_mode="bridge", opmode="wpa2-aes", ambiguous=False, **extra_security
):
    security = {"mode": mode, "opmode": opmode, "ambiguous": ambiguous, **extra_security}
    return candidate(
        "wlan",
        "Corp",
        payload={
            "name": "Corp",
            "essid": "Corp",
            "vlan": 20,
            "aaa_profile": None,
            "security": security,
        },
        unsupported_fields={
            "ssid_profile.opmode": opmode,
            "virtual_ap.forward_mode": forward_mode,
        },
    )


def test_wpa2_personal_wlan_requires_transient_passphrase_and_uses_correct_enum():
    wlan = _wlan_candidate("wpa2_personal")
    missing = new_adapter(FakeBackend()).preview([wlan])
    assert missing["operations"][0]["status"] == "unsupported"
    assert "wpa_passphrase" in missing["operations"][0]["unsupported_warnings"][0]

    preview = new_adapter(
        FakeBackend(),
        secrets={"wlan:Corp": {"wpa_passphrase": "SuperSecretPass1"}},
    ).preview([wlan])
    entry = preview["operations"][0]
    assert entry["status"] == "ready"
    operation = entry["operations"][0]
    assert operation["tool_or_endpoint"] == "build_underlay_ssid"
    assert operation["arguments"]["opmode"] == "WPA2_PERSONAL"
    assert operation["arguments"]["passphrase"] == "***"
    assert "SuperSecretPass1" not in str(preview)


def test_wpa3_sae_and_enhanced_open_and_open_wlan_ready_mappings():
    sae = _wlan_candidate("wpa3_sae", opmode="wpa3-sae")
    preview = new_adapter(
        FakeBackend(),
        secrets={"wlan:Corp": {"wpa_passphrase": "AnotherPass99"}},
    ).preview([sae])
    op = preview["operations"][0]["operations"][0]
    assert op["arguments"]["opmode"] == "WPA3_SAE"
    assert op["arguments"]["passphrase"] == "***"

    enhanced_open = _wlan_candidate("enhanced_open", opmode="enhanced-open")
    preview2 = new_adapter(FakeBackend()).preview([enhanced_open])
    op2 = preview2["operations"][0]["operations"][0]
    assert op2["arguments"]["opmode"] == "ENHANCED_OPEN"
    assert op2["arguments"]["passphrase"] is None
    assert preview2["operations"][0]["status"] == "ready"

    open_wlan = _wlan_candidate("open", opmode="open")
    preview3 = new_adapter(FakeBackend()).preview([open_wlan])
    op3 = preview3["operations"][0]["operations"][0]
    assert op3["arguments"]["opmode"] == "OPEN"
    assert preview3["operations"][0]["status"] == "ready"


def test_wpa3_transition_personal_is_blocked_not_unsupported():
    wlan = _wlan_candidate("wpa3_transition_personal", opmode="wpa3-sae-transition")
    preview = new_adapter(FakeBackend()).preview([wlan])
    entry = preview["operations"][0]
    assert entry["status"] == "blocked"
    assert entry["operations"] == []
    assert entry["unsupported_warnings"] == []
    assert "wpa3-transition-mode-enable" in entry["blockers"][0]


@pytest.mark.parametrize("mode", ["mac_auth_only", "mac_auth_psk", "enterprise_dot1x"])
def test_mac_auth_and_enterprise_dot1x_wlans_remain_unsupported(mode):
    wlan = _wlan_candidate(mode, opmode="mac-auth" if "mac" in mode else "wpa2-aes-dot1x")
    preview = new_adapter(FakeBackend()).preview([wlan])
    assert preview["operations"][0]["status"] == "unsupported"
    assert "MAC-auth and enterprise 802.1X" in preview["operations"][0]["unsupported_warnings"][0]


def test_unknown_or_ambiguous_wlan_security_mode_stays_unsupported():
    wlan = _wlan_candidate("unknown", opmode="wpa-tkip", ambiguous=True)
    preview = new_adapter(FakeBackend()).preview([wlan])
    assert preview["operations"][0]["status"] == "unsupported"
    assert "unverified" in preview["operations"][0]["unsupported_warnings"][0]


def test_role_only_aaa_profile_does_not_block_wpa2_personal_classification():
    """A role-only aaa_profile (no dot1x/MAC-auth profile) attached to a
    PSK WLAN must not block the mapping -- `_wlan_security_intent` has
    already verified it carries no authentication intent of its own
    (docs/aos8-migration-contract-matrix.md item 8 / role-only fixture)."""
    wlan = candidate(
        "wlan",
        "Corp",
        payload={
            "name": "Corp",
            "essid": "Corp",
            "vlan": 20,
            "aaa_profile": "role-only-aaa",
            "security": {"mode": "wpa2_personal", "opmode": "wpa2-aes", "ambiguous": False},
        },
        unsupported_fields={
            "ssid_profile.opmode": "wpa2-aes",
            "virtual_ap.forward_mode": "bridge",
        },
    )
    preview = new_adapter(
        FakeBackend(),
        secrets={"wlan:Corp": {"wpa_passphrase": "RolePass123"}},
    ).preview([wlan])
    assert preview["operations"][0]["status"] == "ready"


def test_wlan_missing_security_intent_summary_is_unsupported():
    wlan = candidate(
        "wlan",
        "Corp",
        payload={"name": "Corp", "essid": "Corp", "vlan": 20, "aaa_profile": None},
        unsupported_fields={
            "ssid_profile.opmode": "open",
            "virtual_ap.forward_mode": "bridge",
        },
    )
    preview = new_adapter(FakeBackend()).preview([wlan])
    assert preview["operations"][0]["status"] == "unsupported"
    assert "no `payload.security`" in preview["operations"][0]["unsupported_warnings"][0]


# ---------------------------------------------------------------------------
# Verification metadata / deterministic ordering (item 9) across new object
# types.
# ---------------------------------------------------------------------------


def test_deterministic_ordering_across_new_object_types():
    auth = candidate(
        "auth_server",
        "radius:rad1",
        payload={"name": "rad1", "server_type": "radius", "host": "10.0.0.10"},
        apply_order=10,
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
    dot1x = candidate(
        "dot1x_auth_profile",
        "corp-dot1x",
        payload={"name": "corp-dot1x"},
        apply_order=20,
    )
    aaa = candidate(
        "aaa_profile",
        "corp-aaa",
        payload={"name": "corp-aaa", "default_user_role": "employee"},
        apply_order=40,
    )
    preview = new_adapter(
        FakeBackend(),
        persona="MOBILITY_GW",
        secrets={"auth_server:radius:rad1": {"shared_secret": "s3cret"}},
    ).preview([aaa, dot1x, group, auth])
    # Round-based topological order: `auth` has no dependencies so it is
    # ready in round 1 alongside `dot1x`/`aaa` (neither of which declares a
    # dependency on `group` in this fixture); within a round candidates are
    # ordered by (apply_order, key). `group` depends on `auth` and is only
    # ready once `auth` has been removed from `remaining`, in round 2 --
    # this is what makes the ordering deterministic and dependency-safe,
    # not a strict linear apply_order-family ranking.
    assert [item["candidate"] for item in preview["operations"]] == [
        "auth_server:radius:rad1",
        "dot1x_auth_profile:corp-dot1x",
        "aaa_profile:corp-aaa",
        "server_group:corp-sg",
    ]
    group_index = [item["candidate"] for item in preview["operations"]].index(
        "server_group:corp-sg"
    )
    auth_index = [item["candidate"] for item in preview["operations"]].index(
        "auth_server:radius:rad1"
    )
    assert auth_index < group_index
    for entry in preview["operations"]:
        assert entry["read_operation"] is not None
        assert entry["update_operations"] is not None
        assert entry["delete_operations"] is not None


# ---------------------------------------------------------------------------
# Review-fix regression tests.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "mode,opmode",
    [
        ("open", "open"),
        ("wpa2_personal", "wpa2-aes"),
        ("wpa3_sae", "wpa3-sae"),
        ("enhanced_open", "enhanced-open"),
    ],
)
def test_pure_security_modes_explicitly_set_wpa3_transition_false(mode, opmode):
    """Finding #3: OPEN/WPA2_PERSONAL/WPA3_SAE/ENHANCED_OPEN must never
    inherit a transition=True default; the mapper must set an explicit
    `False` for every currently-supported pure mode."""
    secrets = (
        {"wlan:Corp": {"wpa_passphrase": "SuperSecretPass1"}}
        if mode in {"wpa2_personal", "wpa3_sae"}
        else None
    )
    wlan = _wlan_candidate(mode, opmode=opmode)
    preview = new_adapter(FakeBackend(), secrets=secrets).preview([wlan])
    entry = preview["operations"][0]
    assert entry["status"] == "ready"
    operation = entry["operations"][0]
    assert operation["arguments"]["wpa3_transition"] is False


def test_wpa3_transition_candidate_remains_blocked_never_ready_with_transition_true():
    """Transition candidates stay blocked; they must never surface a
    `wpa3_transition: True` optimistic write."""
    wlan = _wlan_candidate("wpa3_transition_personal", opmode="wpa3-sae-transition")
    preview = new_adapter(FakeBackend()).preview([wlan])
    entry = preview["operations"][0]
    assert entry["status"] == "blocked"
    assert entry["operations"] == []


def test_wlan_candidate_rejected_on_gateway_and_switch_personas():
    """Finding #4: only AP-family personas may map WLAN candidates; Gateway
    and Switch target contexts must return unsupported, never ready."""
    wlan = _wlan_candidate("wpa2_personal")
    for persona in ("MOBILITY_GW", "ACCESS_SWITCH"):
        preview = new_adapter(
            FakeBackend(),
            persona=persona,
            secrets={"wlan:Corp": {"wpa_passphrase": "SuperSecretPass1"}},
        ).preview([wlan])
        entry = preview["operations"][0]
        assert entry["status"] == "unsupported", persona
        assert "device-function" in entry["unsupported_warnings"][0]


def test_wlan_candidate_still_ready_on_ap_family_personas():
    wlan = _wlan_candidate("wpa2_personal")
    for persona in ("CAMPUS_AP", "MICROBRANCH_AP"):
        preview = new_adapter(
            FakeBackend(),
            persona=persona,
            secrets={"wlan:Corp": {"wpa_passphrase": "SuperSecretPass1"}},
        ).preview([wlan])
        entry = preview["operations"][0]
        assert entry["status"] == "ready", persona


def test_server_group_radsec_dependency_is_unsupported_not_a_crash():
    """Finding #5: an unsupported auth-server type dependency (e.g. radsec)
    must return a precise unsupported action, never raise KeyError."""
    group = candidate(
        "server_group",
        "radsec-sg",
        payload={
            "name": "radsec-sg",
            "auth_server_entries": [{"name": "radsec1", "position": 1}],
        },
        dependencies=["auth_server:radsec:radsec1"],
    )
    radsec = candidate(
        "auth_server",
        "radsec:radsec1",
        payload={"name": "radsec1", "server_type": "radsec", "host": "10.0.0.20"},
    )
    # Must not raise -- this is the precise regression for the KeyError risk.
    preview = new_adapter(FakeBackend()).preview(
        [group, radsec], selected={"server_group:radsec-sg"}
    )
    entry = next(
        item for item in preview["operations"] if item["candidate"] == "server_group:radsec-sg"
    )
    assert entry["status"] == "unsupported"
    assert "radsec" in entry["unsupported_warnings"][0]
    assert "no verified New Central server-groups mapping" in entry["unsupported_warnings"][0]


def test_config_assignment_operation_uses_collection_body_endpoint_for_all_shared_profiles():
    """Finding #1: every SHARED profile type (auth-servers, aaa-profile,
    dot1xauth, macauth, server-groups) must add a spec-correct collection-
    body /network-config/v1alpha1/config-assignments write after
    create/update, with unassign-before-delete rollback ordering."""
    cases = [
        (
            candidate(
                "auth_server",
                "radius:rad1",
                payload={"name": "rad1", "server_type": "radius", "host": "10.0.0.10"},
            ),
            "CAMPUS_AP",
            "auth-servers",
            "rad1",
            {"auth_server:radius:rad1": {"shared_secret": "s3cret"}},
        ),
        (
            candidate(
                "aaa_profile",
                "corp-aaa",
                payload={"name": "corp-aaa", "default_user_role": "employee"},
            ),
            "MOBILITY_GW",
            "aaa-profile",
            "corp-aaa",
            None,
        ),
        (
            candidate("dot1x_auth_profile", "corp-dot1x", payload={"name": "corp-dot1x"}),
            "ACCESS_SWITCH",
            "dot1xauth",
            "corp-dot1x",
            None,
        ),
        (
            candidate("mac_auth_profile", "corp-mac", payload={"name": "corp-mac"}),
            "ACCESS_SWITCH",
            "macauth",
            "corp-mac",
            None,
        ),
    ]
    for object_candidate, persona, profile_type, profile_instance, secrets in cases:
        preview = new_adapter(FakeBackend(), persona=persona, secrets=secrets).preview(
            [object_candidate]
        )
        entry = preview["operations"][0]
        assert entry["status"] == "ready", profile_type
        assignment_op = entry["operations"][1]
        assert assignment_op["tool_or_endpoint"] == "/network-config/v1alpha1/config-assignments"
        assert assignment_op["method"] == "POST"
        assignment_body = assignment_op["payload"]["config-assignment"][0]
        assert assignment_body["profile-type"] == profile_type
        assert assignment_body["scope-id"] == "100"
        assert assignment_body["device-function"] == persona
        assert assignment_body["profile-instance"] == profile_instance
        # Update also carries the assignment (idempotent re-assert).
        assert entry["update_operations"][1]["tool_or_endpoint"] == (
            "/network-config/v1alpha1/config-assignments"
        )
        # Rollback order: unassign before deleting the object itself.
        assert entry["delete_operations"][0]["tool_or_endpoint"] == "delete_config_assignment"
        assert entry["delete_operations"][0]["arguments"]["profile_type"] == profile_type
        assert len(entry["delete_operations"]) == 2
