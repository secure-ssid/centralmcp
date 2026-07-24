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
        # `invocation="endpoint"` operations duplicate the write body under
        # `arguments["data"]` (consumed by `_aos8_migration_write_invoker`)
        # in addition to the top-level `payload` field below -- both copies
        # must be redacted, or a sensitive nested field (e.g.
        # `shared-secret-config`, `admin-password`) leaks through the
        # unmasked `arguments["data"]` copy even though `payload` looks safe.
        if isinstance(arguments.get("data"), Mapping):
            arguments["data"] = _mask_mapping(arguments["data"], self.sensitive_argument_fields)
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
    # Bounded rollback/DELETE operations for a successfully-applied candidate,
    # in execution order (e.g. unassign-then-delete). `None` means no
    # verified delete/rollback path exists yet for this mapping (distinct
    # from an empty list, which would mean "verified: nothing to delete").
    delete_operations: list[Operation] | None = None
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


# Persona (device-function) family classification shared by every New Central
# mapper that must restrict an object to Gateway/Switch-only or AP-only
# device concepts (docs/aos8-migration-contract-matrix.md §4/§6). Mirrors the
# substring convention `_map_role` already used for its GATEWAY/SWITCH
# `target` field.
_AP_PERSONAS = {"CAMPUS_AP", "MICROBRANCH_AP"}
_GATEWAY_PERSONA_EXACT = {"VPNC", "EC_VPNC"}


def _persona_family(persona: str) -> str:
    upper = str(persona or "").strip().upper()
    if upper in _AP_PERSONAS:
        return "ap"
    if "SWITCH" in upper:
        return "switch"
    if upper.endswith("_GW") or upper in _GATEWAY_PERSONA_EXACT:
        return "gateway"
    return "other"


def _require_persona_family(
    candidate_key: str,
    persona: str,
    allowed: set[str],
    *,
    concept: str,
) -> None:
    family = _persona_family(persona)
    if family not in allowed:
        raise AdapterError(
            f"{candidate_key}: {concept} requires a target device-function in the "
            f"{sorted(allowed)} persona family; {persona!r} resolves to {family!r} "
            "and is not a verified target for this object type."
        )


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
            "read_operation": (
                action.read_operation.with_dry_run(True).preview_dict()
                if action.read_operation is not None
                else None
            ),
            "update_operations": (
                [op.with_dry_run(True).preview_dict() for op in action.update_operations]
                if action.update_operations is not None
                else None
            ),
            "delete_operations": (
                [op.with_dry_run(True).preview_dict() for op in action.delete_operations]
                if action.delete_operations is not None
                else None
            ),
            "verified_rollback_available": action.delete_operations is not None,
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


# Per auth-server `type`, the allow-listed AOS8 source fields with a proven
# New Central mapping, and the device-function persona families each type is
# valid for on New Central (auth-server.json `x-supportedDeviceType`,
# docs/aos8-migration-contract-matrix.md §4/§6.7). RADIUS and TACACS are
# valid everywhere; LDAP is AP+Gateway only (no CX/AOS-S LDAP support
# evidenced). RadSec and all other auth-server `type` values stay
# unimplemented/fail-closed.
_AUTH_SERVER_ALLOWED_UNSUPPORTED: dict[str, set[str]] = {
    "radius": {
        "rad_authport",
        "rad_acctport",
        "rad_key",
        "radius_key",
        "radius_secret",
        "shared_secret",
    },
    "ldap": {
        "ldap_admindn",
        "ldap_adminpasswd",
        "ldap_authport",
        "ldap_keyattribute",
    },
    "tacacs": {
        "tacacs_key",
        "tacacs_tcpport",
        "tacacs_timeout",
    },
}
_AUTH_SERVER_PERSONA_FAMILIES: dict[str, set[str]] = {
    "radius": {"ap", "gateway", "switch"},
    "ldap": {"ap", "gateway"},
    "tacacs": {"ap", "gateway", "switch"},
}
# Device AAA/dot1x/macauth profiles are Gateway/Switch device concepts only;
# they must never be applied to an AP persona (docs/aos8-migration-contract-
# matrix.md §4: "AP WLANs must never receive a device AAA/dot1x/macauth
# profile").
_DEVICE_PROFILE_PERSONA_FAMILIES: set[str] = {"gateway", "switch"}

# Verified `payload.security.mode` -> New Central WLAN `opmode` enum values
# (wlan.json `ArubaWlanSecurity_WlanSecurityConfig.opmode`; there is no
# `WPA2_PSK` member -- the correct value is `WPA2_PERSONAL`, see
# docs/aos8-migration-contract-matrix.md §4).
_WLAN_MODE_TO_OPMODE: dict[str, str] = {
    "open": "OPEN",
    "wpa2_personal": "WPA2_PERSONAL",
    "wpa3_sae": "WPA3_SAE",
    "enhanced_open": "ENHANCED_OPEN",
}
_WLAN_PASSPHRASE_MODES: set[str] = {"wpa2_personal", "wpa3_sae"}
# Modes that require an authentication-server chain (802.1X or MAC-auth) with
# no verified New Central mapping today; kept unsupported (AdapterError),
# never optimistically written.
_WLAN_AAA_GATED_MODES: set[str] = {
    "enterprise_dot1x",
    "mac_auth_only",
    "mac_auth_psk",
}


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

    def _spec_endpoint_operation(
        self,
        method: str,
        endpoint: str,
        payload: Mapping[str, Any],
        *,
        provenance: str,
        sensitive_fields: tuple[str, ...] = (),
    ) -> Operation:
        """Build a raw generated-spec WRITE endpoint Operation (POST/PATCH/DELETE).

        Used whenever the generated New Central OpenAPI manifest is
        authoritative but either no curated `mcp_servers` tool exists for the
        resource, or the curated tool's path diverges from the generated
        spec (docs/aos8-migration-contract-matrix.md §2). Never routes
        through a curated tool's own (possibly stale) path assumptions.

        `dry_run` is left at its default `arguments["dry_run"]`-driven
        behaviour (mirrors `_map_role`'s hand-built config-assignment
        Operation): `_aos8_migration_write_invoker` reads
        `arguments.get("dry_run", False)` directly, so every write endpoint
        Operation MUST carry an explicit `dry_run` argument key and keep the
        default `dry_run_field="dry_run"` so `with_dry_run(...)` can flip it.
        """
        arguments = {"method": method, "endpoint": endpoint, "data": dict(payload), "dry_run": True}
        return Operation(
            invocation="endpoint",
            name="central_api_request",
            arguments=arguments,
            method=method,
            endpoint=endpoint,
            payload=payload,
            provenance=provenance,
            sensitive_argument_fields=sensitive_fields,
        )

    def _spec_endpoint_read(
        self, endpoint: str, *, provenance: str, match_identifier: str
    ) -> Operation:
        return Operation(
            invocation="endpoint",
            name="central_api_read",
            arguments={},
            method="GET",
            endpoint=endpoint,
            provenance=provenance,
            dry_run_field=None,
            match_identifier=match_identifier,
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
        delete_assignment = Operation(
            invocation="tool",
            name="delete_config_assignment",
            arguments={
                "scope_id": self.context.scope_id,
                "device_function": self.context.persona,
                "profile_type": "roles",
                "profile_instance": candidate["identifier"],
                "dry_run": True,
            },
            provenance="mcp_servers.config.delete_config_assignment",
        )
        delete_role = Operation(
            invocation="tool",
            name="delete_role",
            arguments={"name": candidate["identifier"], "dry_run": True},
            provenance=(
                "mcp_servers.config.delete_role; pre-reqs per its own docstring: "
                "delete_role_acl then delete_config_assignment for all scopes"
            ),
        )
        return CandidateAction(
            key=_candidate_key(candidate),
            candidate=candidate,
            operations=[create, assignment],
            update_operations=[update, assignment],
            delete_operations=[delete_assignment, delete_role],
            read_operation=Operation(
                invocation="tool",
                name="list_roles",
                arguments={"full_list": True},
                provenance="mcp_servers.config.list_roles",
                dry_run_field=None,
                match_identifier=str(candidate["identifier"]),
            ),
        )

    def _auth_server_body(
        self,
        server_type: str,
        name: str,
        host: str,
        unsupported: Mapping[str, Any],
        secret: str,
    ) -> dict[str, Any]:
        if server_type == "radius":
            return {
                "name": name,
                "type": "RADIUS",
                "auth-server-address": host,
                "auth-port": int(unsupported.get("rad_authport", 1812)),
                "acct-port": int(unsupported.get("rad_acctport", 1813)),
                "shared-secret-config": {
                    "secret-type": "PLAIN_TEXT",
                    "plaintext-value": secret,
                },
            }
        if server_type == "tacacs":
            body: dict[str, Any] = {
                "name": name,
                "type": "TACACS",
                "auth-server-address": host,
                "shared-secret-config": {
                    "secret-type": "PLAIN_TEXT",
                    "plaintext-value": secret,
                },
            }
            if _nonempty(unsupported.get("tacacs_tcpport")):
                body["tcp-port"] = int(unsupported["tacacs_tcpport"])
            if _nonempty(unsupported.get("tacacs_timeout")):
                body["timeout"] = int(unsupported["tacacs_timeout"])
            return body
        if server_type == "ldap":
            body = {
                "name": name,
                "type": "LDAP",
                "auth-server-address": host,
                # `admin-password` (flat) is the LDAP-specific bind-password
                # field on auth-server.json; distinct from the nested
                # `shared-secret-config` object reserved for RADIUS/TACACS.
                "admin-password": secret,
            }
            if _nonempty(unsupported.get("ldap_admindn")):
                body["admin-dn"] = unsupported["ldap_admindn"]
            if _nonempty(unsupported.get("ldap_authport")):
                body["auth-port"] = int(unsupported["ldap_authport"])
            if _nonempty(unsupported.get("ldap_keyattribute")):
                body["key-attribute"] = unsupported["ldap_keyattribute"]
            return body
        raise AssertionError(f"unreachable server_type {server_type!r}")

    def _map_auth_server(self, candidate: Mapping[str, Any]) -> CandidateAction:
        payload = dict(candidate.get("payload", {}))
        server_type = payload.get("server_type")
        key = _candidate_key(candidate)
        if server_type not in _AUTH_SERVER_ALLOWED_UNSUPPORTED:
            raise AdapterError(
                f"{key}: New Central auth-server mapping only supports RADIUS, "
                "LDAP, and TACACS today; RadSec and other auth-server types "
                f"remain unimplemented/fail-closed (received {server_type!r})"
            )
        _require_persona_family(
            key,
            str(self.context.persona),
            _AUTH_SERVER_PERSONA_FAMILIES[server_type],
            concept=f"a {server_type.upper()} auth-server",
        )
        allowed = _AUTH_SERVER_ALLOWED_UNSUPPORTED[server_type]
        self._reject_unmapped(candidate, allowed=allowed)
        unsupported = _unsupported_values(candidate)
        name = payload.get("name") or str(candidate["identifier"]).split(":", 1)[-1]
        host = payload.get("host")
        if not host:
            raise AdapterError(f"{key}: {server_type.upper()} host is required")

        secret_name = "admin_password" if server_type == "ldap" else "shared_secret"
        secret = _secret_value(self.context, key, secret_name)
        body = self._auth_server_body(server_type, name, host, unsupported, secret)
        sensitive_field = "admin-password" if server_type == "ldap" else "shared-secret-config"
        endpoint = f"/network-config/v1alpha1/auth-servers/{quote(name, safe='')}"

        if server_type == "radius":
            create_operation = Operation(
                invocation="tool",
                name="create_auth_server",
                arguments={
                    "name": name,
                    "auth_server_address": host,
                    "shared_secret": secret,
                    "auth_port": body["auth-port"],
                    "acct_port": body["acct-port"],
                    "dry_run": True,
                },
                provenance=(
                    "mcp_servers.nac.create_auth_server: "
                    "/network-config/v1alpha1/auth-servers/{name}; RADIUS only"
                ),
                sensitive_argument_fields=("shared_secret",),
            )
        else:
            create_operation = self._spec_endpoint_operation(
                "POST",
                endpoint,
                body,
                provenance=(
                    f"auth-server.json POST {endpoint}; no curated {server_type.upper()} "
                    "tool exists, spec-correct endpoint used directly per "
                    "docs/aos8-migration-contract-matrix.md §2"
                ),
                sensitive_fields=(sensitive_field,),
            )
        update_operation = self._spec_endpoint_operation(
            "PATCH",
            endpoint,
            body,
            provenance=f"auth-server.json PATCH {endpoint}",
            sensitive_fields=(sensitive_field,),
        )
        delete_operation = Operation(
            invocation="tool",
            name="delete_auth_server",
            arguments={"name": name, "dry_run": True},
            provenance="mcp_servers.nac.delete_auth_server (type-agnostic)",
        )
        read_operation = Operation(
            invocation="tool",
            name="get_auth_server",
            arguments={"name": name},
            provenance="mcp_servers.nac.get_auth_server (type-agnostic)",
            dry_run_field=None,
            match_identifier=name,
        )
        return CandidateAction(
            key=key,
            candidate=candidate,
            operations=[create_operation],
            update_operations=[update_operation],
            delete_operations=[delete_operation],
            read_operation=read_operation,
        )

    def _map_aaa_profile(self, candidate: Mapping[str, Any]) -> CandidateAction:
        key = _candidate_key(candidate)
        _require_persona_family(
            key,
            str(self.context.persona),
            _DEVICE_PROFILE_PERSONA_FAMILIES,
            concept="an AAA profile (Gateway/Switch device concept)",
        )
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
                f"{key}: create_aaa_profile cannot preserve {unsupported_fields}"
            )
        name = str(candidate["identifier"])
        arguments = {
            "name": name,
            "auth_role": payload.get("default_user_role"),
            "fallback_role": None,
            "acct_server_group": payload.get("accounting_server_group"),
            "description": None,
            "dry_run": True,
        }
        create_operation = Operation(
            invocation="tool",
            name="create_aaa_profile",
            arguments=arguments,
            provenance=(
                "mcp_servers.nac.create_aaa_profile: "
                "/network-config/v1alpha1/aaa-profile/{name}"
            ),
        )
        # Mirror mcp_servers.nac.create_aaa_profile's proven body shape
        # exactly (auth-server.json/aaa-profile.json only exposes a nested
        # `authorization.auth-role`/`authorization.fallback-role` object, not
        # a flat `auth-role` top-level property).
        update_body: dict[str, Any] = {"name": name}
        if arguments["auth_role"]:
            update_body["authorization"] = {"auth-role": arguments["auth_role"]}
        if arguments["acct_server_group"]:
            update_body["acct-server-group"] = arguments["acct_server_group"]
        update_operation = self._spec_endpoint_operation(
            "PATCH",
            f"/network-config/v1alpha1/aaa-profile/{quote(name, safe='')}",
            update_body,
            provenance=(
                "aaa-profile.json PATCH /network-config/v1alpha1/aaa-profile/{name}; "
                "body shape mirrors mcp_servers.nac.create_aaa_profile"
            ),
        )
        delete_operation = Operation(
            invocation="tool",
            name="delete_aaa_profile",
            arguments={"name": name, "dry_run": True},
            provenance="mcp_servers.nac.delete_aaa_profile",
        )
        return CandidateAction(
            key=key,
            candidate=candidate,
            operations=[create_operation],
            update_operations=[update_operation],
            delete_operations=[delete_operation],
            read_operation=Operation(
                invocation="tool",
                name="get_aaa_profile",
                arguments={"name": name},
                provenance="mcp_servers.nac.get_aaa_profile",
                dry_run_field=None,
                match_identifier=name,
            ),
        )

    def _map_device_auth_profile(
        self,
        candidate: Mapping[str, Any],
        *,
        resource: str,
        concept: str,
    ) -> CandidateAction:
        key = _candidate_key(candidate)
        _require_persona_family(
            key,
            str(self.context.persona),
            _DEVICE_PROFILE_PERSONA_FAMILIES,
            concept=concept,
        )
        unsupported = _unsupported_values(candidate)
        remaining = {k: v for k, v in unsupported.items() if _nonempty(v)}
        if remaining:
            raise AdapterError(
                f"{key}: {concept} has no verified 1:1 New Central field mapping "
                f"for {sorted(remaining)}; only a bare name-only profile (no "
                "additional settings) is a verified mapping today"
            )
        name = str(candidate["identifier"])
        endpoint = f"/network-config/v1alpha1/{resource}/{quote(name, safe='')}"
        body = {"name": name}
        create_operation = self._spec_endpoint_operation(
            "POST",
            endpoint,
            body,
            provenance=(
                f"{resource}.json POST {endpoint}; curated mcp_servers.config "
                "create_aaa_{dot1xauth,macauth}_profile tools use a stale "
                "/network-config/v1 path -- the spec-correct v1alpha1 endpoint "
                "is used directly per docs/aos8-migration-contract-matrix.md §2"
            ),
        )
        update_operation = self._spec_endpoint_operation(
            "PATCH", endpoint, body, provenance=f"{resource}.json PATCH {endpoint}"
        )
        delete_operation = self._spec_endpoint_operation(
            "DELETE", endpoint, {}, provenance=f"{resource}.json DELETE {endpoint}"
        )
        read_operation = self._spec_endpoint_read(
            endpoint, provenance=f"{resource}.json GET {endpoint}", match_identifier=name
        )
        return CandidateAction(
            key=key,
            candidate=candidate,
            operations=[create_operation],
            update_operations=[update_operation],
            delete_operations=[delete_operation],
            read_operation=read_operation,
        )

    def _map_dot1x_auth_profile(self, candidate: Mapping[str, Any]) -> CandidateAction:
        return self._map_device_auth_profile(
            candidate,
            resource="dot1xauth",
            concept="an 802.1X device authentication profile (Gateway/Switch concept)",
        )

    def _map_mac_auth_profile(self, candidate: Mapping[str, Any]) -> CandidateAction:
        return self._map_device_auth_profile(
            candidate,
            resource="macauth",
            concept="a MAC-auth device authentication profile (Gateway/Switch concept)",
        )

    def _map_server_group(self, candidate: Mapping[str, Any]) -> CandidateAction:
        key = _candidate_key(candidate)
        payload = dict(candidate.get("payload", {}))
        unsupported = _unsupported_values(candidate)
        if _nonempty(unsupported.get("auth_server_type_collisions")):
            raise AdapterError(
                f"{key}: one or more referenced auth-server names are ambiguous "
                "across server types (auth_server_type_collisions); refusing to "
                "guess an ordered servers list"
            )
        if _nonempty(payload.get("derivation_rules")):
            raise AdapterError(
                f"{key}: AOS8 derivation-rules (vlan/role) have no verified "
                "New Central server-groups mapping; recreate manually"
            )
        self._reject_unmapped(candidate)

        dependency_types: set[str] = set()
        for dependency in candidate.get("dependencies", []):
            parts = str(dependency).split(":", 2)
            if len(parts) == 3 and parts[0] == "auth_server":
                dependency_types.add(parts[1])
        if not dependency_types:
            raise AdapterError(
                f"{key}: server-group has no resolved auth-server dependency; "
                "nothing safe to build"
            )
        if len(dependency_types) > 1:
            raise AdapterError(
                f"{key}: server-group mixes auth-server types "
                f"{sorted(dependency_types)}; only a single homogeneous type is "
                "a verified New Central server-groups mapping"
            )
        server_type = next(iter(dependency_types))
        _require_persona_family(
            key,
            str(self.context.persona),
            _AUTH_SERVER_PERSONA_FAMILIES[server_type],
            concept=(
                f"a {server_type.upper()} server-group (inherits its member "
                "auth-server type's persona restriction)"
            ),
        )

        resolved_names = {
            str(dependency).split(":", 2)[-1]
            for dependency in candidate.get("dependencies", [])
            if str(dependency).startswith("auth_server:")
        }
        entries_raw = payload.get("auth_server_entries") or []
        servers: list[dict[str, Any]] = []
        for index, entry in enumerate(entries_raw):
            name = None
            position = None
            if isinstance(entry, Mapping):
                for field_name in (
                    "name",
                    "server",
                    "auth_server",
                    "rad_server_name",
                    "ldap_server_name",
                    "tacacs_server_name",
                ):
                    if _nonempty(entry.get(field_name)):
                        name = str(entry[field_name])
                        break
                position = entry.get("position")
            elif _nonempty(entry):
                name = str(entry)
            if name is None or name not in resolved_names:
                raise AdapterError(
                    f"{key}: server-group entry {entry!r} does not resolve to a "
                    "verified auth-server dependency; refusing to guess ordering"
                )
            servers.append(
                {
                    "server-name": name,
                    "position": int(position) if _nonempty(position) else index + 1,
                }
            )
        if not servers:
            raise AdapterError(f"{key}: server-group has no orderable server entries")
        servers.sort(key=lambda item: item["position"])

        name = str(candidate["identifier"])
        body: dict[str, Any] = {"name": name, "type": server_type.upper(), "servers": servers}
        if payload.get("fail_through") is not None:
            body["fail-through"] = bool(payload["fail_through"])
        if payload.get("load_balance") is not None:
            body["load-balance"] = bool(payload["load_balance"])

        endpoint = f"/network-config/v1alpha1/server-groups/{quote(name, safe='')}"
        create_operation = self._spec_endpoint_operation(
            "POST",
            endpoint,
            body,
            provenance=(
                f"auth-server-group.json POST {endpoint}; no curated server-group "
                "tool exists in mcp_servers"
            ),
        )
        update_operation = self._spec_endpoint_operation(
            "PATCH", endpoint, body, provenance=f"auth-server-group.json PATCH {endpoint}"
        )
        delete_operation = self._spec_endpoint_operation(
            "DELETE", endpoint, {}, provenance=f"auth-server-group.json DELETE {endpoint}"
        )
        read_operation = self._spec_endpoint_read(
            endpoint,
            provenance=f"auth-server-group.json GET {endpoint}",
            match_identifier=name,
        )
        assignment_payload = {
            "config-assignment": [
                {
                    "scope-id": self.context.scope_id,
                    "device-function": self.context.persona,
                    "profile-type": "server-groups",
                    "profile-instance": name,
                }
            ]
        }
        assignment_operation = self._spec_endpoint_operation(
            "POST",
            "/network-config/v1alpha1/config-assignments",
            assignment_payload,
            provenance=(
                "Official New Central Working with Library Profiles: POST "
                "/network-config/v1alpha1/config-assignments with "
                "scope-id/device-function/profile-type=server-groups/profile-instance"
            ),
        )
        delete_assignment_operation = Operation(
            invocation="tool",
            name="delete_config_assignment",
            arguments={
                "scope_id": self.context.scope_id,
                "device_function": self.context.persona,
                "profile_type": "server-groups",
                "profile_instance": name,
                "dry_run": True,
            },
            provenance="mcp_servers.config.delete_config_assignment",
        )
        return CandidateAction(
            key=key,
            candidate=candidate,
            operations=[create_operation, assignment_operation],
            update_operations=[update_operation, assignment_operation],
            delete_operations=[delete_assignment_operation, delete_operation],
            read_operation=read_operation,
        )

    def _map_wlan(self, candidate: Mapping[str, Any]) -> CandidateAction:
        unsupported = _unsupported_values(candidate)
        consumed = {"ssid_profile.opmode", "virtual_ap.forward_mode"}
        self._reject_unmapped(candidate, allowed=consumed)
        payload = dict(candidate.get("payload", {}))
        name = str(candidate["identifier"])
        key = _candidate_key(candidate)
        if payload.get("essid", name) != name:
            raise AdapterError(
                f"{key}: build SSID tools require profile name and ESSID to match"
            )

        security = payload.get("security")
        if not isinstance(security, Mapping):
            raise AdapterError(
                f"{key}: candidate has no `payload.security` intent summary; "
                "refusing to guess a target security mode (see "
                "pipeline.aos8_migration._wlan_security_intent)"
            )
        mode = str(security.get("mode", "unknown"))
        ambiguous = bool(security.get("ambiguous", True))

        if mode in _WLAN_AAA_GATED_MODES:
            raise AdapterError(
                f"{key}: {mode!r} requires an attached AOS8 AAA-profile "
                "authentication chain (802.1X or MAC-auth server-group) with "
                "no verified New Central WLAN mapping; MAC-auth and enterprise "
                "802.1X WLANs remain unsupported "
                "(docs/aos8-migration-contract-matrix.md §6.2)"
            )

        if mode == "wpa3_transition_personal":
            return CandidateAction(
                key=key,
                candidate=candidate,
                blockers=[
                    f"{key}: WPA3 Personal transition maps only to the "
                    "unvalidated `wpa3-transition-mode-enable` flag "
                    "(wlan.json); live validation against a real "
                    "WPA3-transition-mode SSID is required before this "
                    "candidate can be applied "
                    "(docs/aos8-migration-contract-matrix.md §6.2)."
                ],
            )

        if mode == "unknown" or ambiguous:
            raise AdapterError(
                f"{key}: WLAN security intent is unverified (opmode="
                f"{security.get('opmode')!r}, ambiguous={ambiguous}); refusing "
                "to guess a target security mode"
            )
        if mode not in _WLAN_MODE_TO_OPMODE:
            raise AdapterError(
                f"{key}: security mode {mode!r} has no verified New Central mapping"
            )

        vlan = payload.get("vlan")
        if not _nonempty(vlan):
            raise AdapterError(f"{key}: VLAN is required")

        target_opmode = _WLAN_MODE_TO_OPMODE[mode]
        passphrase = None
        sensitive_fields: tuple[str, ...] = ()
        if mode in _WLAN_PASSPHRASE_MODES:
            # Never recovered from source state -- always an explicit,
            # caller-supplied transient secret (docs/aos8-migration-
            # contract-matrix.md instructions item 5).
            passphrase = _secret_value(self.context, key, "wpa_passphrase")
            sensitive_fields = ("passphrase",)

        forward_mode = str(unsupported.get("virtual_ap.forward_mode", "bridge")).lower()
        if forward_mode in {"bridge", "bridged"}:
            operation = Operation(
                invocation="tool",
                name="build_underlay_ssid",
                arguments={
                    "ssid_name": name,
                    "scope_id": self.context.scope_id,
                    "persona": self.context.persona,
                    "opmode": target_opmode,
                    "passphrase": passphrase,
                    "vlan_ids": [int(vlan)],
                    "mac_auth_server_group": None,
                    "default_role": None,
                    "dry_run": True,
                },
                provenance=(
                    "mcp_servers.config.build_underlay_ssid and pipeline.create_ssid: "
                    "POST /network-config/v1/wlan-ssids/{name} plus scope-map"
                ),
                sensitive_argument_fields=sensitive_fields,
            )
            delete_operations = [
                Operation(
                    invocation="tool",
                    name="delete_underlay_ssid",
                    arguments={
                        "ssid_name": name,
                        "scope_id": self.context.scope_id,
                        "dry_run": True,
                    },
                    provenance="mcp_servers.config.delete_underlay_ssid",
                )
            ]
        elif forward_mode in {"tunnel", "tunneled"}:
            if not self.context.cluster_name or not self.context.cluster_scope_id:
                raise AdapterError(
                    f"{key}: tunneled WLAN requires cluster_name and cluster_scope_id"
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
                    "opmode": target_opmode,
                    "passphrase": passphrase,
                    "mac_auth_server_group": None,
                    "policy_name": None,
                    "dry_run": True,
                },
                provenance=(
                    "mcp_servers.config.build_overlay_ssid and pipeline.create_ssid "
                    "verified New Central tunneled-SSID workflow"
                ),
                sensitive_argument_fields=sensitive_fields,
            )
            delete_operations = [
                Operation(
                    invocation="tool",
                    name="delete_overlay_ssid",
                    arguments={"profile_name": name, "dry_run": True},
                    provenance="mcp_servers.config.delete_overlay_ssid",
                )
            ]
        else:
            raise AdapterError(f"{key}: unsupported forward mode {forward_mode!r}")

        return CandidateAction(
            key=key,
            candidate=candidate,
            operations=[operation],
            delete_operations=delete_operations,
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
