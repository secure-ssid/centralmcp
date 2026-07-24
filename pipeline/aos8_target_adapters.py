"""Safe, injectable target adapters for dependency-ordered AOS8 candidates."""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from enum import Enum
from typing import Any, Callable, Iterable, Mapping, Protocol
from urllib.parse import quote


class TargetType(str, Enum):
    CLASSIC_CENTRAL = "classic_central"
    NEW_CENTRAL = "new_central"


class ConflictPolicy(str, Enum):
    FAIL = "fail"
    SKIP_EXISTING = "skip-existing"
    UPDATE = "update"


class AdapterError(ValueError):
    """Base adapter validation error."""


class DependencySelectionError(AdapterError):
    """A selection omitted a required dependency."""


class ContextValidationError(AdapterError):
    """The injected target-context resolver rejected the context."""


class WriteGateError(PermissionError):
    """Execution was attempted without all required write gates."""


@dataclass(frozen=True)
class TargetContext:
    target_type: TargetType
    scope_id: str | None = None
    scope_name: str | None = None
    persona: str | None = None
    cluster_name: str | None = None
    cluster_scope_id: str | None = None
    gateway_name: str | None = None
    gateway_scope_id: str | None = None
    conflict_policy: ConflictPolicy = ConflictPolicy.FAIL
    secret_inputs: Mapping[str, Mapping[str, str]] = field(default_factory=dict)


@dataclass(frozen=True)
class Operation:
    invocation: str
    name: str
    arguments: Mapping[str, Any]
    method: str | None = None
    endpoint: str | None = None
    payload: Mapping[str, Any] | None = None
    provenance: str = ""
    dry_run_field: str | None = "dry_run"
    sensitive_argument_fields: tuple[str, ...] = ()
    match_identifier: str | None = None

    def with_dry_run(self, dry_run: bool) -> Operation:
        if self.dry_run_field is None:
            return self
        arguments = dict(self.arguments)
        arguments[self.dry_run_field] = dry_run
        return replace(self, arguments=arguments)

    def preview_dict(self) -> dict[str, Any]:
        arguments = dict(self.arguments)
        for field_name in self.sensitive_argument_fields:
            if field_name in arguments:
                arguments[field_name] = "***"
        payload = _mask_mapping(self.payload or {}, self.sensitive_argument_fields)
        return {
            "invocation": self.invocation,
            "tool_or_endpoint": self.name if self.invocation == "tool" else self.endpoint,
            "method": self.method,
            "arguments": arguments,
            "payload": payload or None,
            "provenance": self.provenance,
        }


@dataclass
class CandidateAction:
    key: str
    candidate: Mapping[str, Any]
    operations: list[Operation] = field(default_factory=list)
    read_operation: Operation | None = None
    update_operations: list[Operation] | None = None
    inline_dependencies: set[str] = field(default_factory=set)
    compatibility_errors: list[str] = field(default_factory=list)
    blockers: list[str] = field(default_factory=list)
    conflict: str = "not-checked"
    status: str = "ready"

    @property
    def supported(self) -> bool:
        return not self.compatibility_errors


class ReadInvoker(Protocol):
    def __call__(self, operation: Operation) -> Any: ...


class WriteInvoker(Protocol):
    def __call__(self, operation: Operation, *, confirmation: bool) -> Any: ...


ScopeResolver = Callable[[TargetContext], tuple[str, str]]
PersonaValidator = Callable[[TargetContext], str]
WritesEnabled = Callable[[TargetType], bool]

_REDACTED_MARKERS = {
    "***",
    "******",
    "<redacted:empty>",
    "<redacted:present>",
}


def _mask_mapping(value: Mapping[str, Any], sensitive_fields: Iterable[str]) -> dict[str, Any]:
    sensitive = set(sensitive_fields)
    return {key: "***" if key in sensitive else item for key, item in value.items()}


def _candidate_key(candidate: Mapping[str, Any]) -> str:
    return f"{candidate.get('object_type')}:{candidate.get('identifier')}"


def _nonempty(value: Any) -> bool:
    return value not in (None, "", [], {})


def _unsupported_values(candidate: Mapping[str, Any]) -> dict[str, Any]:
    unsupported = candidate.get("unsupported_fields", {})
    return dict(unsupported) if isinstance(unsupported, Mapping) else {}


def _secret_value(
    context: TargetContext,
    candidate_key: str,
    secret_name: str,
) -> str:
    supplied = context.secret_inputs.get(candidate_key, {})
    value = supplied.get(secret_name)
    if not isinstance(value, str) or not value.strip() or value.strip() in _REDACTED_MARKERS:
        raise AdapterError(
            f"{candidate_key}: caller must supply a non-redacted target secret "
            f"named {secret_name!r}."
        )
    return value


def _secret_bundle_error(
    context: TargetContext,
    candidate: Mapping[str, Any],
) -> str | None:
    if not candidate.get("requires_secret_input"):
        return None
    key = _candidate_key(candidate)
    supplied = context.secret_inputs.get(key)
    if not supplied:
        return f"{key}: secret-bearing candidate requires caller-provided target secrets."
    invalid = [
        name
        for name, value in supplied.items()
        if not isinstance(value, str) or not value.strip() or value.strip() in _REDACTED_MARKERS
    ]
    if invalid:
        return f"{key}: target secret inputs are empty or redacted: {sorted(invalid)}."
    return None


def _contains_identifier(value: Any, identifier: str) -> bool:
    if value is None:
        return False
    if isinstance(value, list):
        return any(_contains_identifier(item, identifier) for item in value)
    if isinstance(value, Mapping):
        if value.get("found") is False:
            return False
        error = str(value.get("error", ""))
        if "404" in error or "not found" in error.lower():
            return False
        identity_fields = (
            "name",
            "ssid",
            "vlan",
            "vlan_id",
            "vlan-id",
            "profile-name",
            "id",
        )
        if any(str(value.get(field)) == identifier for field in identity_fields):
            return True
        return any(_contains_identifier(item, identifier) for item in value.values())
    return False


def _topological_candidates(
    candidates: Iterable[Mapping[str, Any]],
    selected: set[str] | None,
    *,
    include_dependency_closure: bool,
    allow_unresolved_blockers: bool,
    satisfied_dependencies: set[str] | None = None,
) -> tuple[list[Mapping[str, Any]], dict[str, list[str]]]:
    by_key = {_candidate_key(candidate): candidate for candidate in candidates}
    requested = set(by_key) if selected is None else set(selected)
    satisfied = set(satisfied_dependencies or ())
    unknown = sorted(requested - set(by_key))
    if unknown:
        raise DependencySelectionError(f"Unknown selected candidates: {unknown}")

    blockers: dict[str, list[str]] = {}
    pending = list(requested)
    while pending:
        key = pending.pop()
        dependencies = [str(item) for item in by_key[key].get("dependencies", [])]
        for dependency in dependencies:
            if dependency in satisfied:
                continue
            if dependency not in by_key:
                message = f"required dependency {dependency!r} is absent from the candidate set"
                if not allow_unresolved_blockers:
                    raise DependencySelectionError(f"{key}: {message}")
                blockers.setdefault(key, []).append(message)
                continue
            if dependency in requested:
                continue
            if include_dependency_closure:
                requested.add(dependency)
                pending.append(dependency)
            elif allow_unresolved_blockers:
                blockers.setdefault(key, []).append(
                    f"required dependency {dependency!r} was not selected"
                )
            else:
                raise DependencySelectionError(
                    f"{key}: required dependency {dependency!r} was not selected"
                )

    ordered: list[Mapping[str, Any]] = []
    remaining = set(requested)
    while remaining:
        ready = [
            key for key in remaining if not (set(by_key[key].get("dependencies", [])) & remaining)
        ]
        if not ready:
            raise DependencySelectionError(
                f"Dependency cycle among candidates: {sorted(remaining)}"
            )
        ready.sort(key=lambda key: (int(by_key[key].get("apply_order", 100)), key))
        for key in ready:
            ordered.append(by_key[key])
            remaining.remove(key)
    return ordered, blockers


class BaseCentralTargetAdapter:
    target_type: TargetType

    def __init__(
        self,
        context: TargetContext,
        *,
        scope_resolver: ScopeResolver,
        persona_validator: PersonaValidator,
        read_invoker: ReadInvoker,
        write_invoker: WriteInvoker,
        writes_enabled: WritesEnabled,
    ) -> None:
        if context.target_type is not self.target_type:
            raise ContextValidationError(
                f"{self.__class__.__name__} requires target_type={self.target_type.value!r}"
            )
        try:
            scope_id, scope_name = scope_resolver(context)
            persona = persona_validator(context)
        except Exception as exc:
            raise ContextValidationError(str(exc)) from exc
        if not scope_id or not scope_name or not persona:
            raise ContextValidationError(
                "Scope resolver and persona validator must return non-empty values."
            )
        self.context = replace(
            context,
            scope_id=str(scope_id),
            scope_name=str(scope_name),
            persona=str(persona),
        )
        self.read_invoker = read_invoker
        self.write_invoker = write_invoker
        self.writes_enabled = writes_enabled

    def _map_candidate(self, candidate: Mapping[str, Any]) -> CandidateAction:
        raise NotImplementedError

    def checkpoint_guidance(self) -> dict[str, Any]:
        raise NotImplementedError

    def preview(
        self,
        candidates: Iterable[Mapping[str, Any]],
        *,
        selected: set[str] | None = None,
        include_dependency_closure: bool = True,
        allow_unresolved_blockers: bool = False,
    ) -> dict[str, Any]:
        preview, _ = self._prepare_preview(
            candidates,
            selected=selected,
            include_dependency_closure=include_dependency_closure,
            allow_unresolved_blockers=allow_unresolved_blockers,
        )
        return preview

    def _prepare_preview(
        self,
        candidates: Iterable[Mapping[str, Any]],
        *,
        selected: set[str] | None = None,
        include_dependency_closure: bool = True,
        allow_unresolved_blockers: bool = False,
        satisfied_dependencies: set[str] | None = None,
    ) -> tuple[dict[str, Any], list[CandidateAction]]:
        ordered, dependency_blockers = _topological_candidates(
            candidates,
            selected,
            include_dependency_closure=include_dependency_closure,
            allow_unresolved_blockers=allow_unresolved_blockers,
            satisfied_dependencies=satisfied_dependencies,
        )
        actions: list[CandidateAction] = []
        for candidate in ordered:
            key = _candidate_key(candidate)
            action = self._map_candidate(candidate)
            secret_error = _secret_bundle_error(self.context, candidate)
            if secret_error and secret_error not in action.compatibility_errors:
                action.compatibility_errors.append(secret_error)
            action.blockers.extend(dependency_blockers.get(key, []))
            if action.compatibility_errors:
                action.status = "unsupported"
            elif action.blockers:
                action.status = "blocked"
            elif action.read_operation is not None:
                try:
                    existing = self.read_invoker(action.read_operation)
                    match_identifier = action.read_operation.match_identifier or str(
                        candidate.get("identifier")
                    )
                    if _contains_identifier(existing, match_identifier):
                        self._apply_conflict_policy(action)
                    else:
                        action.conflict = "absent"
                except Exception as exc:
                    action.status = "blocked"
                    action.blockers.append(f"preflight read failed: {exc}")
            actions.append(action)

        preview = {
            "target": {
                "type": self.context.target_type.value,
                "scope_id": self.context.scope_id,
                "scope_name": self.context.scope_name,
                "persona": self.context.persona,
                "cluster_name": self.context.cluster_name,
                "cluster_scope_id": self.context.cluster_scope_id,
                "gateway_name": self.context.gateway_name,
                "gateway_scope_id": self.context.gateway_scope_id,
                "conflict_policy": self.context.conflict_policy.value,
            },
            "dry_run": True,
            "write_gate_requirements": {
                "platform_writes_enabled": True,
                "dry_run_must_be_false": True,
                "explicit_confirmation": True,
            },
            "checkpoint_and_rollback": self.checkpoint_guidance(),
            "operations": [self._action_preview(action) for action in actions],
        }
        return preview, actions

    def _apply_conflict_policy(self, action: CandidateAction) -> None:
        policy = self.context.conflict_policy
        action.conflict = "existing"
        if policy is ConflictPolicy.FAIL:
            action.status = "blocked"
            action.blockers.append("target object already exists and conflict policy is fail")
        elif policy is ConflictPolicy.SKIP_EXISTING:
            action.status = "skipped"
        elif action.update_operations is None:
            action.status = "blocked"
            action.blockers.append(
                "target object exists but this verified mapping has no update operation"
            )
        else:
            action.status = "ready"
            action.operations = action.update_operations
            action.conflict = "update"

    def _action_preview(self, action: CandidateAction) -> dict[str, Any]:
        candidate = action.candidate
        return {
            "candidate": action.key,
            "object_type": candidate.get("object_type"),
            "identifier": candidate.get("identifier"),
            "dependencies": list(candidate.get("dependencies", [])),
            "inline_dependencies": sorted(action.inline_dependencies),
            "apply_order": candidate.get("apply_order", 100),
            "status": action.status,
            "conflict": action.conflict,
            "idempotency_and_conflict": (
                f"policy={self.context.conflict_policy.value}; "
                "preflight read is required before write"
            ),
            "write_gate_required": True,
            "dry_run": True,
            "operations": [
                operation.with_dry_run(True).preview_dict() for operation in action.operations
            ],
            "warnings": sorted(set(candidate.get("warnings", []))),
            "unsupported_warnings": sorted(action.compatibility_errors),
            "blockers": sorted(action.blockers),
        }

    def dry_run(
        self,
        candidates: Iterable[Mapping[str, Any]],
        **preview_options: Any,
    ) -> dict[str, Any]:
        satisfied_dependencies = set(
            preview_options.get("satisfied_dependencies") or ()
        )
        preview, actions = self._prepare_preview(candidates, **preview_options)
        return self._invoke_actions(
            preview,
            actions,
            confirmation=False,
            dry_run=True,
            satisfied_dependencies=satisfied_dependencies,
        )

    def execute(
        self,
        candidates: Iterable[Mapping[str, Any]],
        *,
        dry_run: bool,
        confirmation: bool,
        **preview_options: Any,
    ) -> dict[str, Any]:
        if dry_run:
            raise WriteGateError("Execution requires dry_run=False.")
        if not confirmation:
            raise WriteGateError("Execution requires explicit confirmation.")
        if not self.writes_enabled(self.target_type):
            raise WriteGateError(f"Platform writes are disabled for {self.target_type.value}.")
        satisfied_dependencies = set(
            preview_options.get("satisfied_dependencies") or ()
        )
        preview, actions = self._prepare_preview(candidates, **preview_options)
        return self._invoke_actions(
            preview,
            actions,
            confirmation=True,
            dry_run=False,
            satisfied_dependencies=satisfied_dependencies,
        )

    def _invoke_actions(
        self,
        preview: dict[str, Any],
        actions: list[CandidateAction],
        *,
        confirmation: bool,
        dry_run: bool,
        satisfied_dependencies: set[str] | None = None,
    ) -> dict[str, Any]:
        successful: set[str] = set(satisfied_dependencies or ())
        results: list[dict[str, Any]] = []
        for action in actions:
            dependency_failures = [
                dependency
                for dependency in action.candidate.get("dependencies", [])
                if dependency not in successful and dependency not in action.inline_dependencies
            ]
            if action.status == "skipped":
                successful.add(action.key)
                results.append({"candidate": action.key, "status": "skipped", "results": []})
                continue
            if action.status != "ready":
                results.append(
                    {
                        "candidate": action.key,
                        "status": action.status,
                        "results": [],
                        "errors": [
                            *action.compatibility_errors,
                            *action.blockers,
                        ],
                    }
                )
                continue
            if dependency_failures:
                results.append(
                    {
                        "candidate": action.key,
                        "status": "blocked",
                        "results": [],
                        "errors": [
                            f"dependency did not complete successfully: {dependency}"
                            for dependency in dependency_failures
                        ],
                    }
                )
                continue

            operation_results: list[dict[str, Any]] = []
            errors: list[str] = []
            for operation in action.operations:
                invoked = operation.with_dry_run(dry_run)
                try:
                    value = self.write_invoker(invoked, confirmation=confirmation)
                    operation_results.append({"operation": invoked.preview_dict(), "result": value})
                except Exception as exc:
                    errors.append(f"{operation.name}: {exc}")
                    break
            status = "failed" if errors else ("dry-run" if dry_run else "applied")
            if not errors:
                successful.add(action.key)
            results.append(
                {
                    "candidate": action.key,
                    "status": status,
                    "results": operation_results,
                    "errors": errors,
                }
            )
        preview["dry_run"] = dry_run
        preview["results"] = results
        return preview

    def candidate_action(self, candidate: Mapping[str, Any]) -> CandidateAction:
        """Return the mapped action for orchestration and read-only verification."""
        return self._map_candidate(candidate)


class NewCentralAdapter(BaseCentralTargetAdapter):
    target_type = TargetType.NEW_CENTRAL

    def checkpoint_guidance(self) -> dict[str, Any]:
        return {
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

    def _map_candidate(self, candidate: Mapping[str, Any]) -> CandidateAction:
        object_type = str(candidate.get("object_type"))
        mapper = getattr(self, f"_map_{object_type}", None)
        if mapper is None:
            return self._unsupported(candidate, f"New Central mapping for {object_type!r}")
        try:
            return mapper(candidate)
        except (AdapterError, TypeError, ValueError) as exc:
            return self._unsupported(candidate, str(exc))

    def _unsupported(self, candidate: Mapping[str, Any], reason: str) -> CandidateAction:
        return CandidateAction(
            key=_candidate_key(candidate),
            candidate=candidate,
            compatibility_errors=[reason],
        )

    def _reject_unmapped(
        self,
        candidate: Mapping[str, Any],
        *,
        allowed: set[str] | None = None,
    ) -> None:
        allowed = allowed or set()
        remaining = {
            key: value
            for key, value in _unsupported_values(candidate).items()
            if key not in allowed and _nonempty(value)
        }
        if remaining:
            raise AdapterError(
                f"{_candidate_key(candidate)}: unmapped source fields prevent safe apply: "
                f"{sorted(remaining)}"
            )

    def _map_vlan(self, candidate: Mapping[str, Any]) -> CandidateAction:
        self._reject_unmapped(candidate)
        identifier = str(candidate["identifier"])
        vlan_id = int(identifier)
        payload = dict(candidate.get("payload", {}))
        arguments = {
            "vlan_id": vlan_id,
            "vlan_name": payload.get("description") or identifier,
            "scope_id": self.context.scope_id,
            "persona": self.context.persona,
            "dry_run": True,
        }
        operation = Operation(
            invocation="tool",
            name="create_vlan",
            arguments=arguments,
            provenance=(
                "mcp_servers.config.create_vlan: POST/PUT "
                "/network-config/v1/layer2-vlan/{vlan_id} plus scope-map"
            ),
        )
        return CandidateAction(
            key=_candidate_key(candidate),
            candidate=candidate,
            operations=[operation],
            update_operations=[operation],
            read_operation=Operation(
                invocation="endpoint",
                name="central_api_read",
                arguments={},
                method="GET",
                endpoint=f"/network-config/v1/layer2-vlan/{vlan_id}",
                provenance="New Central layer2-vlan resource used by create_vlan",
                dry_run_field=None,
                match_identifier=identifier,
            ),
        )

    def _map_role(self, candidate: Mapping[str, Any]) -> CandidateAction:
        self._reject_unmapped(candidate)
        payload = dict(candidate.get("payload", {}))
        policies = payload.get("policies", payload.get("acl"))
        if isinstance(policies, str):
            policies = [policies]
        normalized = {str(item).lower() for item in (policies or [])}
        if normalized - {"allowall", "sys_allow_all"}:
            raise AdapterError(
                f"{_candidate_key(candidate)}: create_role only has a verified "
                "allow-all mapping; custom AOS8 ACLs are unsupported"
            )
        vlan = payload.get("vlan")
        target = None
        persona = str(self.context.persona)
        if "SWITCH" in persona:
            target = "SWITCH"
        elif persona.endswith("_GW"):
            target = "GATEWAY"
        common = {
            "name": candidate["identifier"],
            "description": None,
            "allow_all": True,
            "vlan_id": int(vlan) if _nonempty(vlan) else None,
            "target": target,
            "dry_run": True,
        }
        assignment_payload = {
            "config-assignment": [
                {
                    "scope-id": self.context.scope_id,
                    "device-function": self.context.persona,
                    "profile-type": "roles",
                    "profile-instance": candidate["identifier"],
                }
            ]
        }
        assignment = Operation(
            invocation="endpoint",
            name="central_api_request",
            arguments={
                "method": "POST",
                "endpoint": "/network-config/v1alpha1/config-assignments",
                "data": assignment_payload,
                "dry_run": True,
            },
            method="POST",
            endpoint="/network-config/v1alpha1/config-assignments",
            payload=assignment_payload,
            provenance=(
                "Official New Central Working with Library Profiles: POST "
                "/network-config/v1alpha1/config-assignments with "
                "scope-id/device-function/profile-type/profile-instance"
            ),
        )
        create = Operation(
            invocation="tool",
            name="create_role",
            arguments=common,
            provenance="mcp_servers.config.create_role: POST /network-config/v1/roles/{name}",
        )
        update = replace(
            create,
            name="update_role",
            provenance="mcp_servers.config.update_role: PUT /network-config/v1/roles/{name}",
        )
        return CandidateAction(
            key=_candidate_key(candidate),
            candidate=candidate,
            operations=[create, assignment],
            update_operations=[update, assignment],
            read_operation=Operation(
                invocation="tool",
                name="list_roles",
                arguments={"full_list": True},
                provenance="mcp_servers.config.list_roles",
                dry_run_field=None,
                match_identifier=str(candidate["identifier"]),
            ),
        )

    def _map_auth_server(self, candidate: Mapping[str, Any]) -> CandidateAction:
        payload = dict(candidate.get("payload", {}))
        if payload.get("server_type") != "radius":
            raise AdapterError(
                f"{_candidate_key(candidate)}: verified New Central auth-server tool "
                "supports RADIUS only; LDAP and TACACS are unsupported"
            )
        allowed = {
            "rad_authport",
            "rad_acctport",
            "rad_key",
            "radius_key",
            "radius_secret",
            "shared_secret",
        }
        self._reject_unmapped(candidate, allowed=allowed)
        unsupported = _unsupported_values(candidate)
        secret = _secret_value(self.context, _candidate_key(candidate), "shared_secret")
        arguments = {
            "name": payload.get("name") or str(candidate["identifier"]).split(":", 1)[-1],
            "auth_server_address": payload.get("host"),
            "shared_secret": secret,
            "auth_port": int(unsupported.get("rad_authport", 1812)),
            "acct_port": int(unsupported.get("rad_acctport", 1813)),
            "dry_run": True,
        }
        if not arguments["auth_server_address"]:
            raise AdapterError(f"{_candidate_key(candidate)}: RADIUS host is required")
        return CandidateAction(
            key=_candidate_key(candidate),
            candidate=candidate,
            operations=[
                Operation(
                    invocation="tool",
                    name="create_auth_server",
                    arguments=arguments,
                    provenance=(
                        "mcp_servers.nac.create_auth_server: "
                        "/network-config/v1alpha1/auth-servers/{name}; RADIUS only"
                    ),
                    sensitive_argument_fields=("shared_secret",),
                )
            ],
            read_operation=Operation(
                invocation="tool",
                name="get_auth_server",
                arguments={"name": arguments["name"]},
                provenance="mcp_servers.nac.get_auth_server",
                dry_run_field=None,
                match_identifier=str(arguments["name"]),
            ),
        )

    def _map_aaa_profile(self, candidate: Mapping[str, Any]) -> CandidateAction:
        self._reject_unmapped(candidate)
        payload = dict(candidate.get("payload", {}))
        unsupported_fields = [
            name
            for name in (
                "dot1x_auth_profile",
                "dot1x_default_role",
                "dot1x_server_group",
                "mac_auth_profile",
                "mac_default_role",
                "mac_server_group",
            )
            if _nonempty(payload.get(name))
        ]
        if unsupported_fields:
            raise AdapterError(
                f"{_candidate_key(candidate)}: create_aaa_profile cannot preserve "
                f"{unsupported_fields}"
            )
        arguments = {
            "name": candidate["identifier"],
            "auth_role": payload.get("default_user_role"),
            "fallback_role": None,
            "acct_server_group": payload.get("accounting_server_group"),
            "description": None,
            "dry_run": True,
        }
        return CandidateAction(
            key=_candidate_key(candidate),
            candidate=candidate,
            operations=[
                Operation(
                    invocation="tool",
                    name="create_aaa_profile",
                    arguments=arguments,
                    provenance=(
                        "mcp_servers.nac.create_aaa_profile: "
                        "/network-config/v1alpha1/aaa-profile/{name}"
                    ),
                )
            ],
            read_operation=Operation(
                invocation="tool",
                name="get_aaa_profile",
                arguments={"name": candidate["identifier"]},
                provenance="mcp_servers.nac.get_aaa_profile",
                dry_run_field=None,
                match_identifier=str(candidate["identifier"]),
            ),
        )

    def _map_wlan(self, candidate: Mapping[str, Any]) -> CandidateAction:
        unsupported = _unsupported_values(candidate)
        consumed = {"ssid_profile.opmode", "virtual_ap.forward_mode"}
        self._reject_unmapped(candidate, allowed=consumed)
        payload = dict(candidate.get("payload", {}))
        name = str(candidate["identifier"])
        if payload.get("essid", name) != name:
            raise AdapterError(
                f"{_candidate_key(candidate)}: build SSID tools require profile name "
                "and ESSID to match"
            )
        if _nonempty(payload.get("aaa_profile")):
            raise AdapterError(
                f"{_candidate_key(candidate)}: verified build SSID tools cannot attach "
                "an arbitrary migrated AOS8 AAA profile"
            )
        opmode = str(unsupported.get("ssid_profile.opmode", "open")).lower()
        if opmode not in {"open", "opensystem"}:
            raise AdapterError(
                f"{_candidate_key(candidate)}: only open AOS8 WLANs have a verified "
                "lossless security mapping; received opmode "
                f"{unsupported.get('ssid_profile.opmode')!r}"
            )
        vlan = payload.get("vlan")
        if not _nonempty(vlan):
            raise AdapterError(f"{_candidate_key(candidate)}: VLAN is required")
        forward_mode = str(unsupported.get("virtual_ap.forward_mode", "bridge")).lower()
        if forward_mode in {"bridge", "bridged"}:
            operation = Operation(
                invocation="tool",
                name="build_underlay_ssid",
                arguments={
                    "ssid_name": name,
                    "scope_id": self.context.scope_id,
                    "persona": self.context.persona,
                    "opmode": "OPEN",
                    "passphrase": None,
                    "vlan_ids": [int(vlan)],
                    "mac_auth_server_group": None,
                    "default_role": None,
                    "dry_run": True,
                },
                provenance=(
                    "mcp_servers.config.build_underlay_ssid and pipeline.create_ssid: "
                    "POST /network-config/v1/wlan-ssids/{name} plus scope-map"
                ),
            )
        elif forward_mode in {"tunnel", "tunneled"}:
            if not self.context.cluster_name or not self.context.cluster_scope_id:
                raise AdapterError(
                    f"{_candidate_key(candidate)}: tunneled WLAN requires cluster_name "
                    "and cluster_scope_id"
                )
            operation = Operation(
                invocation="tool",
                name="build_overlay_ssid",
                arguments={
                    "ssid_name": name,
                    "scope_id": self.context.scope_id,
                    "cluster_name": self.context.cluster_name,
                    "cluster_scope_id": self.context.cluster_scope_id,
                    "vlan_ids": [int(vlan)],
                    "opmode": "OPEN",
                    "passphrase": None,
                    "mac_auth_server_group": None,
                    "policy_name": None,
                    "dry_run": True,
                },
                provenance=(
                    "mcp_servers.config.build_overlay_ssid and pipeline.create_ssid "
                    "verified New Central tunneled-SSID workflow"
                ),
            )
        else:
            raise AdapterError(
                f"{_candidate_key(candidate)}: unsupported forward mode {forward_mode!r}"
            )
        return CandidateAction(
            key=_candidate_key(candidate),
            candidate=candidate,
            operations=[operation],
            read_operation=Operation(
                invocation="tool",
                name="get_ssid",
                arguments={"ssid_name": name},
                provenance="mcp_servers.config.get_ssid",
                dry_run_field=None,
                match_identifier=name,
            ),
        )


class ClassicCentralAdapter(BaseCentralTargetAdapter):
    target_type = TargetType.CLASSIC_CENTRAL

    def checkpoint_guidance(self) -> dict[str, Any]:
        return {
            "post_change_checkpoint_policy_only": False,
            "automatic_rollback_supported": False,
            "manual_checkpoint_restore_supported": False,
            "guidance": (
                "Export the current Classic Central group configuration before apply. "
                "This adapter has no verified checkpoint or automatic rollback operation."
            ),
        }

    def _map_candidate(self, candidate: Mapping[str, Any]) -> CandidateAction:
        if candidate.get("object_type") != "wlan":
            return CandidateAction(
                key=_candidate_key(candidate),
                candidate=candidate,
                compatibility_errors=[
                    f"Classic Central {candidate.get('object_type')!r} target operation "
                    "is not verified in this repository; candidate remains unapplied"
                ],
            )
        try:
            return self._map_wlan(candidate)
        except (AdapterError, TypeError, ValueError) as exc:
            return CandidateAction(
                key=_candidate_key(candidate),
                candidate=candidate,
                compatibility_errors=[str(exc)],
            )

    def _map_wlan(self, candidate: Mapping[str, Any]) -> CandidateAction:
        unsupported = _unsupported_values(candidate)
        allowed = {"ssid_profile.opmode", "virtual_ap.forward_mode"}
        remaining = {
            key: value
            for key, value in unsupported.items()
            if key not in allowed and _nonempty(value)
        }
        if remaining:
            raise AdapterError(
                f"{_candidate_key(candidate)}: unmapped source fields prevent safe apply: "
                f"{sorted(remaining)}"
            )
        payload = dict(candidate.get("payload", {}))
        name = str(candidate["identifier"])
        if payload.get("essid", name) != name:
            raise AdapterError(
                f"{_candidate_key(candidate)}: Classic full_wlan mapping requires "
                "profile name and ESSID to match"
            )
        if _nonempty(payload.get("aaa_profile")):
            raise AdapterError(
                f"{_candidate_key(candidate)}: Classic full_wlan mapping does not "
                "translate AOS8 AAA profiles"
            )
        opmode = str(unsupported.get("ssid_profile.opmode", "open")).lower()
        forward_mode = str(unsupported.get("virtual_ap.forward_mode", "bridge")).lower()
        if opmode not in {"open", "opensystem"} or forward_mode not in {
            "bridge",
            "bridged",
        }:
            raise AdapterError(
                f"{_candidate_key(candidate)}: verified Classic mapping is limited "
                "to open, bridged WLANs"
            )
        vlan = payload.get("vlan")
        if not _nonempty(vlan):
            raise AdapterError(f"{_candidate_key(candidate)}: VLAN is required")
        scope_reference = quote(str(self.context.scope_name), safe="")
        encoded_name = quote(name, safe="")
        body = {
            "wlan": {
                "access_type": "unrestricted",
                "auth_server1": "",
                "auth_server2": "",
                "blacklist": True,
                "broadcast_filter": "arp",
                "captive_portal": "disable",
                "deny_intra_vlan_traffic": False,
                "dynamic_vlans": [],
                "name": name,
                "essid": name,
                "type": "guest",
                "opmode": "opensystem",
                "vlan": str(vlan),
                "disable_ssid": False,
                "hide_ssid": False,
                "mac_authentication": False,
                "radius_accounting": False,
                "rf_band": "all",
                "roles": [],
                "ssid_encoding": "utf8",
                "user_bridging": False,
                "wpa_passphrase": "",
            },
            "access_rule": {
                "name": name,
                "action": "allow",
                "blacklist": False,
                "eport": "any",
                "ipaddr": "any",
                "log": False,
                "match": "match",
                "netmask": "any",
                "protocol": "any",
                "service_type": "network",
                "source": "default",
                "sport": "any",
                "vlan": 0,
            },
        }
        endpoint = f"/configuration/full_wlan/{scope_reference}/{encoded_name}"
        operation = Operation(
            invocation="endpoint",
            name="central_api_request",
            arguments={
                "method": "POST",
                "endpoint": endpoint,
                "data": body,
                "dry_run": True,
            },
            method="POST",
            endpoint=endpoint,
            payload=body,
            provenance=(
                "Official Classic Central Create a new WLAN: "
                "developer.arubanetworks.com/central/reference/"
                "apifull_wlancreate_wlan; Aruba central-python-workflows "
                "Classic-Central/wlan_config/configurations/open_network.yaml"
            ),
        )
        return CandidateAction(
            key=_candidate_key(candidate),
            candidate=candidate,
            operations=[operation],
            inline_dependencies={
                dependency
                for dependency in candidate.get("dependencies", [])
                if str(dependency).startswith("vlan:")
            },
            read_operation=Operation(
                invocation="endpoint",
                name="central_api_read",
                arguments={
                    "method": "GET",
                    "endpoint": f"/configuration/full_wlan/{scope_reference}",
                },
                method="GET",
                endpoint=f"/configuration/full_wlan/{scope_reference}",
                provenance=(
                    "Official Classic Central Get WLAN list: "
                    "developer.arubanetworks.com/central/reference/"
                    "apifull_wlanget_wlan_list"
                ),
                dry_run_field=None,
                match_identifier=name,
            ),
        )
