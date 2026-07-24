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
    # Explicit, operator-supplied non-secret object references required by a
    # narrow set of conditional/dry-run-only mappings (e.g. an already-existing
    # Classic auth-server profile name for WPA3-Enterprise). Never a secret;
    # never used to invent/auto-provision a missing dependency.
    external_object_references: Mapping[str, Mapping[str, str]] = field(
        default_factory=dict
    )
    # Explicit, operator-provided AOS8 ap_group name -> Classic Central group
    # name mapping. AOS8 AP groups are never automatically translated into a
    # Classic group; the operator must name the target group themselves.
    ap_group_target_map: Mapping[str, str] = field(default_factory=dict)
    # Explicit, operator-provided AOS8 ap_group name -> device serial numbers
    # to move into the mapped Classic group. Never inferred from the AOS8
    # export (which carries no device/serial data today).
    ap_group_device_serials: Mapping[str, tuple[str, ...]] = field(
        default_factory=dict
    )


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
    # Bounded, single-item post-write verification read. Distinct from
    # `read_operation` (preflight, run before any write): this is metadata an
    # orchestrator uses *after* a write to confirm it actually applied — never
    # trust a successful write response alone (see the Classic group-create
    # read-back footgun cited in the contract matrix, §3 item 8).
    read_back_operation: Operation | None = None
    # Metadata-only rollback (e.g. Classic full_wlan DELETE) describing how to
    # undo this candidate's write. Never auto-invoked by `_invoke_actions`; an
    # orchestrator must call it explicitly after confirming a rollback is
    # actually desired.
    rollback_operations: list[Operation] = field(default_factory=list)
    # When True, this action is previewable/dry-run-only: `execute(dry_run=False)`
    # always refuses to invoke its write operations, regardless of confirmation
    # or write-gate state, until `dry_run_only_reason`'s precondition has live
    # evidence recorded in the migration contract matrix.
    dry_run_only: bool = False
    dry_run_only_reason: str | None = None
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
    """Mask top-level sensitive keys plus one level of dotted-path nesting.

    A field name containing a literal ``.`` (e.g. ``"wlan.wpa_passphrase"``)
    masks ``value["wlan"]["wpa_passphrase"]`` instead of only matching a
    top-level key named ``"wlan.wpa_passphrase"``. This lets a nested Classic
    `full_wlan` secret (`{"wlan": {"wpa_passphrase": ...}}`) be redacted from
    preview output without collapsing the whole `wlan` object.
    """
    top: set[str] = set()
    nested: dict[str, set[str]] = {}
    for field_name in sensitive_fields:
        if "." in field_name:
            head, _, rest = field_name.partition(".")
            nested.setdefault(head, set()).add(rest)
        else:
            top.add(field_name)
    masked: dict[str, Any] = {}
    for key, item in value.items():
        if key in top:
            masked[key] = "***"
        elif key in nested and isinstance(item, Mapping):
            masked[key] = _mask_mapping(item, nested[key])
        else:
            masked[key] = item
    return masked


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
                except Exception as exc:
                    action.status = "blocked"
                    action.blockers.append(f"preflight read failed: {exc}")
                else:
                    unavailable = self._read_unavailable_reason(existing)
                    if unavailable:
                        action.compatibility_errors.append(unavailable)
                        action.status = "unsupported"
                    else:
                        match_identifier = action.read_operation.match_identifier or str(
                            candidate.get("identifier")
                        )
                        if _contains_identifier(existing, match_identifier):
                            self._apply_conflict_policy(action)
                        else:
                            action.conflict = "absent"
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

    def _read_unavailable_reason(self, existing: Any) -> str | None:
        """Override to detect a preflight read that succeeded but indicates
        the underlying object API itself is unavailable for this account or
        target (distinct from "this specific item does not exist yet", which
        is a normal, safe-to-create `absent` result). Returning a non-empty
        string marks the candidate `unsupported` with that message instead of
        treating it as `absent`."""
        return None

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
            "read_back": (
                action.read_back_operation.with_dry_run(True).preview_dict()
                if action.read_back_operation is not None
                else None
            ),
            "rollback": [
                operation.with_dry_run(True).preview_dict()
                for operation in action.rollback_operations
            ],
            "dry_run_only": action.dry_run_only,
            "dry_run_only_reason": action.dry_run_only_reason,
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
            if action.dry_run_only and not dry_run:
                results.append(
                    {
                        "candidate": action.key,
                        "status": "blocked",
                        "results": [],
                        "errors": [
                            action.dry_run_only_reason
                            or (
                                "this candidate is conditional/dry-run-only pending "
                                "live validation; real (non-dry-run) execution is "
                                "refused"
                            )
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


# Precise, per-family manual guidance for AOS8 candidate object types that have
# no verified Classic Central object REST in the audited surface (contract
# matrix §5/§6.3-§6.13). These are never a live write path; the guidance
# exists to make the *reason* and the safe manual/AP-CLI/template fallback
# explicit instead of a single generic "not verified" message for every type.
_CLASSIC_MANUAL_FAMILY_GUIDANCE: dict[str, str] = {
    "aaa_profile": "No verified Classic Central object REST exists for AAA profiles.",
    "dot1x_auth_profile": (
        "No verified Classic Central object REST exists for device 802.1X "
        "authentication profiles."
    ),
    "mac_auth_profile": (
        "No verified Classic Central object REST exists for device MAC-auth "
        "profiles."
    ),
    "server_group": "No verified Classic Central object REST exists for server groups.",
    "auth_server": (
        "No verified Classic Central object REST exists for RADIUS/LDAP/TACACS "
        "auth servers."
    ),
    "role": "No verified Classic Central object REST exists for roles.",
    "policy": (
        "No verified Classic Central object REST exists for session ACLs/policies."
    ),
    "route": "No verified Classic Central object REST exists for IPv4/IPv6 static routes.",
    "vrrp": "No verified Classic Central object REST exists for VRRP/VRRPv6/tracking.",
    "controller": (
        "AOS8 controllers/Mobility Conductors are not migrated as Classic Central "
        "objects; onboard replacement gateways/APs individually."
    ),
}

# Actionable, mode-specific guidance for AOS8 WLAN security intents that remain
# blocked on the Classic target without live goldens (contract matrix §6.2).
# Every message names *why* it is blocked so an operator/orchestrator never
# has to guess whether it is a temporary gap or a permanent rejection.
_CLASSIC_WLAN_MODE_GUIDANCE: dict[str, str] = {
    "wpa2_personal": (
        "WPA2-Personal has a separate, documented Classic v2 WLAN contract that "
        "has not been reconciled with this full_wlan (v1) shape in this "
        "repository; the two are never interchangeable. Recreate this "
        "WPA2-Personal WLAN manually until the v2 contract's exact "
        "method/path/body/read-back/update/delete semantics are read live "
        "against the same target group and recorded in the migration contract "
        "matrix."
    ),
    "wpa3_transition_personal": (
        "WPA2/WPA3 transition (mixed personal) mode is ambiguous in the "
        "audited Classic samples; no live confirmation of the exact "
        "opmode/transition-flag behavior exists in this repository. Recreate "
        "this transition WLAN manually."
    ),
    "enhanced_open": (
        "Enhanced Open (OWE) has no verified Classic full_wlan opmode value "
        "recorded in this repository. Recreate this WLAN manually until an "
        "official sample or live read confirms the exact opmode token."
    ),
    "mac_auth_only": (
        "MAC-authentication WLANs require an AOS8 aaa_profile/server-group "
        "dependency chain with no verified Classic Central object REST in "
        "this repository. Recreate this WLAN and its MAC-auth policy "
        "manually."
    ),
    "mac_auth_psk": (
        "MAC-auth + PSK WLANs carry the same unverified AAA/server-group "
        "dependency chain as MAC-auth-only. Recreate this WLAN manually."
    ),
    "unknown": (
        "Source security intent could not be classified from the AOS8 export "
        "(ambiguous opmode/passphrase/aaa_profile signal). Confirm the real "
        "opmode and credential material by hand before recreating this WLAN."
    ),
}


class ClassicCentralAdapter(BaseCentralTargetAdapter):
    target_type = TargetType.CLASSIC_CENTRAL

    def __init__(self, context: TargetContext, **kwargs: Any) -> None:
        super().__init__(context, **kwargs)
        self._validate_classic_target_context()

    def _validate_classic_target_context(self) -> None:
        """Fail closed unless `scope_name` is an explicit Classic group name,
        GUID, or device serial number — the literal `full_wlan`
        `{group_name_or_guid_or_serial_number}` path segment
        (developer.arubanetworks.com/central/reference/apifull_wlancreate_wlan) —
        never a raw New Central `scope_id`. New Central scope IDs are coerced
        to `int` throughout `mcp_servers/config.py` (e.g. `"scope-id":
        int(scope_id)`); a purely numeric Classic target is accepted nowhere
        in the audited official samples (group names, GUIDs, and Aruba device
        serial numbers are never bare digit strings), so it is rejected here
        as a likely wrong-target mistake rather than silently accepted.
        """
        scope_name = str(self.context.scope_name or "").strip()
        if not scope_name:
            raise ContextValidationError(
                "ClassicCentralAdapter requires an explicit Classic group name, "
                "GUID, or device serial number for the full_wlan "
                "{group_name_or_guid_or_serial_number} path segment "
                "(developer.arubanetworks.com/central/reference/"
                "apifull_wlancreate_wlan)."
            )
        if scope_name.isdigit():
            raise ContextValidationError(
                f"ClassicCentralAdapter target {scope_name!r} looks like a raw "
                "New Central scope_id (a purely numeric value); the Classic "
                "full_wlan {group_name_or_guid_or_serial_number} path segment "
                "must be an explicit Classic group name, GUID, or device "
                "serial number, never a New Central scope_id. Resolve the "
                "correct Classic group/GUID/serial before constructing this "
                "TargetContext."
            )

    def _read_unavailable_reason(self, existing: Any) -> str | None:
        """Detect a preflight read that succeeded (no exception) but signals
        the full_wlan API itself is unavailable for this account/group —
        distinct from a normal per-item "not found yet" result, which is safe
        to treat as `absent` and proceed to create. Unavailability is never
        silently treated as absent: it is reported as `unsupported` with an
        explicit, actionable message instead."""
        if isinstance(existing, Mapping):
            error = str(existing.get("error", "")).strip()
            if error:
                lowered = error.lower()
                broad_markers = (
                    "not supported",
                    "not available",
                    "unsupported",
                    "license",
                    "invalid group",
                    "invalid target",
                    "no such group",
                    "not enabled",
                    "not entitled",
                    "not applicable",
                )
                if any(marker in lowered for marker in broad_markers):
                    return (
                        "Classic full_wlan API appears unavailable for this "
                        f"account/group context ({self.context.scope_name!r}): "
                        f"{error}. Confirm the target group/GUID/serial and "
                        "account entitlement for the full_wlan API before "
                        "retrying (contract matrix §5)."
                    )
        return None

    def checkpoint_guidance(self) -> dict[str, Any]:
        return {
            "post_change_checkpoint_policy_only": False,
            "automatic_rollback_supported": False,
            "manual_checkpoint_restore_supported": False,
            "guidance": (
                "Export the current Classic Central group configuration before "
                "apply. This adapter has no verified checkpoint or automatic "
                "rollback operation; use the per-candidate `rollback` metadata "
                "(a DELETE against the same full_wlan item) as a manual, "
                "explicitly-invoked undo instead. A successful POST/PUT "
                "response is never sufficient proof the group actually applied "
                "the change — Classic Central group/device writes are known to "
                "report success without applying; a bounded GET read-back "
                "against the single-item full_wlan endpoint is mandatory "
                "wherever a group/device write is represented, both before "
                "(preflight) and after (read-back) the write."
            ),
        }

    def _map_candidate(self, candidate: Mapping[str, Any]) -> CandidateAction:
        object_type = str(candidate.get("object_type"))
        if object_type == "wlan":
            try:
                return self._map_wlan(candidate)
            except (AdapterError, TypeError, ValueError) as exc:
                return CandidateAction(
                    key=_candidate_key(candidate),
                    candidate=candidate,
                    compatibility_errors=[str(exc)],
                )
        if object_type == "ap_group":
            return self._map_ap_group(candidate)
        return self._manual_guidance(candidate)

    def _manual_guidance(self, candidate: Mapping[str, Any]) -> CandidateAction:
        object_type = str(candidate.get("object_type"))
        detail = _CLASSIC_MANUAL_FAMILY_GUIDANCE.get(
            object_type,
            f"Classic Central {object_type!r} target operation is not verified "
            "in this repository.",
        )
        return CandidateAction(
            key=_candidate_key(candidate),
            candidate=candidate,
            compatibility_errors=[
                f"{detail} Candidate remains unapplied; recreate manually via "
                "the Central UI, an AP-CLI command set, or a Mobility "
                "Controller template — never treat a whole-config/template "
                "push as a safe, idempotent per-object write (contract "
                "matrix §5)."
            ],
        )

    def _map_ap_group(self, candidate: Mapping[str, Any]) -> CandidateAction:
        """AOS8 AP groups are never automatically translated into a Classic
        Central group or device move (contract matrix §1.3/§5/§6.11). This
        only ever escalates through explicit, operator-provided input; it
        never becomes `ready` because no verified Classic device-move object
        REST exists in the audited surface, and inventing one is exactly the
        failure mode this adapter must never produce."""
        key = _candidate_key(candidate)
        ap_group_name = str(candidate.get("identifier"))
        target_group = self.context.ap_group_target_map.get(ap_group_name)
        if not target_group or not str(target_group).strip():
            return CandidateAction(
                key=key,
                candidate=candidate,
                compatibility_errors=[
                    f"{key}: AOS8 AP groups are not Classic Central groups; no "
                    "automatic 1:1 group creation or membership is ever "
                    "inferred. Supply an explicit operator-provided mapping "
                    "via context.ap_group_target_map[{!r}] naming the target "
                    "Classic group before this candidate can be considered.".format(
                        ap_group_name
                    )
                ],
            )
        serials = tuple(self.context.ap_group_device_serials.get(ap_group_name, ()))
        if not serials:
            return CandidateAction(
                key=key,
                candidate=candidate,
                compatibility_errors=[
                    f"{key}: an explicit Classic group mapping ({target_group!r}) "
                    "was supplied, but no explicit device serial number(s) were "
                    f"supplied via context.ap_group_device_serials[{ap_group_name!r}] "
                    "for a device-move operation; this candidate remains manual "
                    "until real serials are provided."
                ],
            )
        return CandidateAction(
            key=key,
            candidate=candidate,
            compatibility_errors=[
                f"{key}: an explicit Classic group mapping ({target_group!r}) and "
                f"device serial(s) ({list(serials)!r}) were supplied, but no "
                "verified Classic Central device-move object REST exists in "
                "this repository (contract matrix §5/§6.11). This candidate "
                "remains manual/unsupported until a live-verified move-device "
                "endpoint is read and recorded in the migration contract "
                "matrix — it is never fabricated."
            ],
        )

    def _full_wlan_endpoint(self, name: str) -> str:
        scope_reference = quote(str(self.context.scope_name), safe="")
        encoded_name = quote(name, safe="")
        return f"/configuration/full_wlan/{scope_reference}/{encoded_name}"

    def _base_full_wlan_body(
        self,
        name: str,
        vlan: Any,
        *,
        opmode: str,
        wlan_type: str,
        access_type: str,
        wpa_passphrase: str = "",
        auth_server1: str = "",
        transition_disable: bool = True,
        mac_authentication: bool = False,
    ) -> dict[str, Any]:
        """Complete-body `full_wlan` `{"wlan": ..., "access_rule": ...}` shape,
        matching the official Create/Update WLAN samples
        (developer.arubanetworks.com/central/reference/apifull_wlancreate_wlan,
        apifull_wlanupdate_wlan) and the corroborating Aruba
        central-python-workflows Classic-Central/wlan_config/configurations/
        {open_network,psk_network,enterprise-network}.yaml samples. PUT update
        reuses this same complete body — Classic full_wlan has no verified
        partial-update/PATCH contract."""
        return {
            "wlan": {
                "access_type": access_type,
                "auth_server1": auth_server1,
                "auth_server2": "",
                "blacklist": True,
                "broadcast_filter": "arp",
                "captive_portal": "disable",
                "deny_intra_vlan_traffic": False,
                "dynamic_vlans": [],
                "name": name,
                "essid": name,
                "type": wlan_type,
                "opmode": opmode,
                "opmode_transition_disable": transition_disable,
                "vlan": str(vlan),
                "disable_ssid": False,
                "hide_ssid": False,
                "mac_authentication": mac_authentication,
                "radius_accounting": False,
                "rf_band": "all",
                "roles": [],
                "ssid_encoding": "utf8",
                "user_bridging": False,
                "wpa_passphrase": wpa_passphrase,
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

    def _full_wlan_operations(
        self,
        name: str,
        body: Mapping[str, Any],
        *,
        provenance_extra: str = "",
        sensitive_argument_fields: tuple[str, ...] = (),
    ) -> tuple[Operation, Operation, Operation, Operation, Operation]:
        """Return (create, update, preflight-read, read-back, rollback-delete)
        operations for the verified Classic full_wlan lifecycle: `POST` create,
        complete-body `PUT` update, bounded single-item `GET` for both
        preflight and post-write read-back, and `DELETE` rollback — all keyed
        by the same `{group_name_or_guid_or_serial_number}/{wlan_name}` path
        (developer.arubanetworks.com/central/reference/apifull_wlancreate_wlan,
        apifull_wlanupdate_wlan, apifull_wlanget_wlan, apifull_wlandelete_wlan).
        """
        endpoint = self._full_wlan_endpoint(name)
        provenance = (
            "Official Classic Central full_wlan lifecycle: "
            "developer.arubanetworks.com/central/reference/"
            "apifull_wlancreate_wlan (POST), apifull_wlanupdate_wlan (PUT, "
            "complete-body replace), apifull_wlanget_wlan (GET, single item), "
            "apifull_wlandelete_wlan (DELETE); the "
            "{group_name_or_guid_or_serial_number} path segment is an explicit "
            "Classic group name/GUID/device serial, never a New Central "
            "scope_id."
        )
        if provenance_extra:
            provenance = f"{provenance} {provenance_extra}"
        create = Operation(
            invocation="endpoint",
            name="central_api_request",
            arguments={"method": "POST", "endpoint": endpoint, "dry_run": True},
            method="POST",
            endpoint=endpoint,
            payload=body,
            provenance=provenance,
            sensitive_argument_fields=sensitive_argument_fields,
        )
        update = Operation(
            invocation="endpoint",
            name="central_api_request",
            arguments={"method": "PUT", "endpoint": endpoint, "dry_run": True},
            method="PUT",
            endpoint=endpoint,
            payload=body,
            provenance=provenance,
            sensitive_argument_fields=sensitive_argument_fields,
        )
        read_item = Operation(
            invocation="endpoint",
            name="central_api_read",
            arguments={"method": "GET", "endpoint": endpoint},
            method="GET",
            endpoint=endpoint,
            provenance=(
                "Official Classic Central Get WLAN: developer.arubanetworks.com/"
                "central/reference/apifull_wlanget_wlan (bounded single-item "
                "read, used as preflight)."
            ),
            dry_run_field=None,
            match_identifier=name,
        )
        read_back = replace(
            read_item,
            name="central_api_read_back",
            provenance=(
                "Official Classic Central Get WLAN: developer.arubanetworks.com/"
                "central/reference/apifull_wlanget_wlan (bounded single-item "
                "read, used as mandatory post-write read-back). A successful "
                "POST/PUT response is not sufficient proof the group actually "
                "applied the change; always confirm with this bounded read "
                "before trusting the write (contract matrix §3 item 8)."
            ),
        )
        delete = Operation(
            invocation="endpoint",
            name="central_api_request",
            arguments={"method": "DELETE", "endpoint": endpoint, "dry_run": True},
            method="DELETE",
            endpoint=endpoint,
            provenance=(
                "Official Classic Central Delete WLAN: "
                "developer.arubanetworks.com/central/reference/"
                "apifull_wlandelete_wlan — rollback operation; metadata only, "
                "never auto-invoked by this adapter."
            ),
        )
        return create, update, read_item, read_back, delete

    def _map_wlan(self, candidate: Mapping[str, Any]) -> CandidateAction:
        key = _candidate_key(candidate)
        unsupported = _unsupported_values(candidate)
        # Fields consumed elsewhere (opmode/forward-mode drive the mapping
        # dispatch below; the passphrase/hexkey/transition presence signals
        # are consumed via `payload["security"]`, never silently dropped —
        # the real secret value itself is only ever obtained through
        # `_secret_value`/`context.secret_inputs`, never from this raw field).
        allowed = {
            "ssid_profile.opmode",
            "virtual_ap.forward_mode",
            "ssid_profile.wpa_passphrase",
            "ssid_profile.wpa_hexkey",
            "ssid_profile.wpa3_transition",
        }
        remaining = {
            field_name: value
            for field_name, value in unsupported.items()
            if field_name not in allowed and _nonempty(value)
        }
        if remaining:
            raise AdapterError(
                f"{key}: unmapped source fields prevent safe apply: "
                f"{sorted(remaining)}"
            )
        payload = dict(candidate.get("payload", {}))
        name = str(candidate["identifier"])
        if payload.get("essid", name) != name:
            raise AdapterError(
                f"{key}: Classic full_wlan mapping requires profile name and "
                "ESSID to match"
            )
        vlan = payload.get("vlan")
        if not _nonempty(vlan):
            raise AdapterError(f"{key}: VLAN is required")
        forward_mode = str(unsupported.get("virtual_ap.forward_mode", "bridge")).lower()
        if forward_mode not in {"bridge", "bridged"}:
            raise AdapterError(
                f"{key}: verified Classic full_wlan mapping is limited to "
                "bridged WLANs"
            )
        security = dict(payload.get("security") or {})
        mode = str(security.get("mode", "unknown"))
        aaa_profile = payload.get("aaa_profile")

        if mode in {"open", "wpa3_sae"} and _nonempty(aaa_profile):
            raise AdapterError(
                f"{key}: Classic full_wlan mapping does not translate AOS8 AAA "
                "profiles for this security mode"
            )

        if mode == "open":
            return self._open_wlan_action(candidate, name, vlan)
        if mode == "wpa3_sae":
            return self._wpa3_personal_wlan_action(candidate, name, vlan, security)
        if mode == "enterprise_dot1x":
            opmode_raw = str(security.get("opmode") or "").lower()
            if "wpa3" in opmode_raw:
                return self._wpa3_enterprise_wlan_action(candidate, name, vlan, security)
            raise AdapterError(
                f"{key}: WPA2-Enterprise (802.1X) Classic mapping remains "
                "unsupported; only WPA3-Enterprise has an official-sample-"
                "backed full_wlan payload and an explicit already-existing "
                "auth-server precondition in this repository"
            )
        raise AdapterError(
            f"{key}: "
            + _CLASSIC_WLAN_MODE_GUIDANCE.get(
                mode,
                "no verified Classic full_wlan mapping exists for this "
                "security mode; recreate this WLAN manually",
            )
        )

    def _inline_vlan_dependencies(self, candidate: Mapping[str, Any]) -> set[str]:
        return {
            dependency
            for dependency in candidate.get("dependencies", [])
            if str(dependency).startswith("vlan:")
        }

    def _open_wlan_action(
        self, candidate: Mapping[str, Any], name: str, vlan: Any
    ) -> CandidateAction:
        body = self._base_full_wlan_body(
            name,
            vlan,
            opmode="opensystem",
            wlan_type="guest",
            access_type="unrestricted",
        )
        create, update, read_item, read_back, delete = self._full_wlan_operations(
            name,
            body,
            provenance_extra=(
                "Open sample: Aruba central-python-workflows "
                "Classic-Central/wlan_config/configurations/open_network.yaml."
            ),
        )
        return CandidateAction(
            key=_candidate_key(candidate),
            candidate=candidate,
            operations=[create],
            update_operations=[update],
            read_operation=read_item,
            read_back_operation=read_back,
            rollback_operations=[delete],
            inline_dependencies=self._inline_vlan_dependencies(candidate),
        )

    def _wpa3_personal_wlan_action(
        self,
        candidate: Mapping[str, Any],
        name: str,
        vlan: Any,
        security: Mapping[str, Any],
    ) -> CandidateAction:
        key = _candidate_key(candidate)
        if security.get("wpa3_transition"):
            raise AdapterError(
                f"{key}: verified Classic WPA3-Personal mapping requires "
                "transition mode disabled; a WPA2/WPA3 mixed-transition "
                "personal WLAN remains unsupported without live goldens "
                "confirming the transition field behavior (contract matrix "
                "§6.2)"
            )
        if not security.get("passphrase_present"):
            raise AdapterError(
                f"{key}: WPA3-Personal requires the source WLAN to have "
                "carried a wpa_passphrase; none was detected on this AOS8 "
                "WLAN"
            )
        passphrase = _secret_value(self.context, key, "wpa_passphrase")
        body = self._base_full_wlan_body(
            name,
            vlan,
            opmode="wpa3-sae-aes",
            wpa_passphrase=passphrase,
            wlan_type="employee",
            access_type="unrestricted",
        )
        create, update, read_item, read_back, delete = self._full_wlan_operations(
            name,
            body,
            provenance_extra=(
                "WPA3-Personal sample: opmode=wpa3-sae-aes, "
                "opmode_transition_disable=true (developer.arubanetworks.com/"
                "central/reference/apifull_wlancreate_wlan); Aruba "
                "central-python-workflows Classic-Central/wlan_config/"
                "configurations/psk_network.yaml (secondary corroboration)."
            ),
            sensitive_argument_fields=("wlan.wpa_passphrase",),
        )
        return CandidateAction(
            key=key,
            candidate=candidate,
            operations=[create],
            update_operations=[update],
            read_operation=read_item,
            read_back_operation=read_back,
            rollback_operations=[delete],
            inline_dependencies=self._inline_vlan_dependencies(candidate),
        )

    def _wpa3_enterprise_wlan_action(
        self,
        candidate: Mapping[str, Any],
        name: str,
        vlan: Any,
        security: Mapping[str, Any],
    ) -> CandidateAction:
        key = _candidate_key(candidate)
        if not security.get("dot1x_auth_profile"):
            raise AdapterError(
                f"{key}: WPA3-Enterprise mapping requires a resolved "
                "dot1x_auth_profile on the source aaa_profile"
            )
        references = self.context.external_object_references.get(key, {})
        auth_server1 = references.get("auth_server1")
        if (
            not isinstance(auth_server1, str)
            or not auth_server1.strip()
            or auth_server1.strip() in _REDACTED_MARKERS
        ):
            raise AdapterError(
                f"{key}: WPA3-Enterprise requires an explicit, already-existing "
                "Classic auth-server reference supplied via "
                "context.external_object_references[<candidate>]['auth_server1']; "
                "missing AAA/server-group dependencies are never auto-provisioned"
            )
        body = self._base_full_wlan_body(
            name,
            vlan,
            opmode="wpa3-aes-ccm-128",
            wlan_type="employee",
            access_type="network_based",
            auth_server1=auth_server1.strip(),
        )
        create, update, read_item, read_back, delete = self._full_wlan_operations(
            name,
            body,
            provenance_extra=(
                "WPA3-Enterprise sample: opmode=wpa3-aes-ccm-128, "
                "opmode_transition_disable=true, access_type=network_based, "
                "auth_server1=<already-existing Classic auth-server profile> "
                "(developer.arubanetworks.com/central/reference/"
                "apifull_wlancreate_wlan); Aruba central-python-workflows "
                "Classic-Central/wlan_config/configurations/"
                "enterprise-network.yaml (secondary corroboration)."
            ),
        )
        return CandidateAction(
            key=key,
            candidate=candidate,
            operations=[create],
            update_operations=[update],
            read_operation=read_item,
            read_back_operation=read_back,
            rollback_operations=[delete],
            inline_dependencies=self._inline_vlan_dependencies(candidate),
            dry_run_only=True,
            dry_run_only_reason=(
                f"{key}: WPA3-Enterprise Classic full_wlan mapping is "
                "conditional/dry-run-only pending live validation of the "
                "auth_server1 dependency and full field set against a real "
                "lab group (contract matrix §5); real execution is refused "
                "until that evidence is recorded."
            ),
        )
