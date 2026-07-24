"""Register generated OpenAPI operations as FastMCP tools.

Each manifest operation becomes one FastMCP tool with a *typed* signature
derived from its path/query/header parameters (plus ``body``/``dry_run``/
``confirm`` for writes). The runtime:

* keeps auth headers/cookies out of the model-visible arguments;
* URL-escapes path values and rejects traversal-style values;
* preserves ``False`` / ``0`` / list query values, dropping only unset (``None``)
  ones;
* dispatches through platform-supplied executors (which inject trusted auth
  last and apply response bounding);
* classifies reads as read-only (executed directly) and writes/destructive
  operations behind the platform write gate + dry-run/confirm enforcement,
  performed inside the platform ``write_executor``.

A per-platform feature flag (``CENTRALMCP_<PLATFORM>_GENERATED_TOOLS``) controls
registration. When unset it defaults *on* if the committed manifest exists, so a
missing manifest never breaks import.
"""

from __future__ import annotations

import inspect
import keyword
import os
import re
from collections.abc import Awaitable, Callable
from typing import Any, Optional
from urllib.parse import quote

from mcp_servers.openapi_gen.manifest import load_manifest, manifest_exists
from mcp_servers.openapi_gen.naming import digest, snake
from mcp_servers.shared import DESTRUCTIVE, DIAGNOSTIC, IDEMPOTENT_WRITE, READ_ONLY, WRITE

# Executor protocols (implemented per platform in the backend module).
ReadExecutor = Callable[[str, str, dict[str, Any], dict[str, str]], Awaitable[dict[str, Any]]]
WriteExecutor = Callable[
    [str, str, str, dict[str, Any], dict[str, str], Any, str, bool, bool],
    Awaitable[dict[str, Any]],
]

_PATH_PLACEHOLDER = re.compile(r"\{([^}]+)\}")

# Header/cookie parameter names that carry credentials must never become
# model-visible arguments; trusted auth is injected by the executor instead.
_AUTH_PARAM_NAMES = {
    "authorization",
    "cookie",
    "x-csrftoken",
    "x-csrf-token",
    "apitoken",
    "api-token",
    "x-api-token",
    "x-api-key",
    "apikey",
    "api-key",
    "token",
    "x-auth-token",
}

# Argument names the write-tool signature injects (body/dry_run/confirm). A
# spec parameter that snake-cases to one of these (e.g. a real ``dry-run`` query
# param on some GreenLake writes) must be renamed to avoid a duplicate-parameter
# signature error while still preserving its original API name for the request.
_WRITE_RESERVED_ARG_NAMES = frozenset({"body", "dry_run", "confirm"})


_PY_TYPES: dict[str, Any] = {
    "string": str,
    "integer": int,
    "number": float,
    "boolean": bool,
    "array": list,
    "object": dict,
    "any": Any,
}

_MAX_DESC = 200


class _ParamSpec:
    """Resolved binding between a model argument and an API parameter."""

    __slots__ = ("arg", "api", "location", "py_type", "required", "default", "description", "enum")

    def __init__(
        self,
        arg: str,
        api: str,
        location: str,
        py_type: Any,
        required: bool,
        default: Any,
        description: str,
        enum: list[Any] | None,
    ) -> None:
        self.arg = arg
        self.api = api
        self.location = location
        self.py_type = py_type
        self.required = required
        self.default = default
        self.description = description
        self.enum = enum


def is_auth_param(name: str) -> bool:
    return name.strip().lower() in _AUTH_PARAM_NAMES


def _safe_arg_name(name: str, taken: set[str]) -> str:
    arg = snake(name) or "arg"
    if arg[0].isdigit():
        arg = f"p_{arg}"
    if keyword.iskeyword(arg):
        arg = f"{arg}_"
    base = arg
    i = 2
    while arg in taken:
        arg = f"{base}_{i}"
        i += 1
    taken.add(arg)
    return arg


def _py_type(schema_type: str, item_type: str | None = None) -> Any:
    if schema_type == "array":
        elem = _PY_TYPES.get(item_type or "any", Any)
        return list if elem is Any else list
    return _PY_TYPES.get(schema_type, Any)


def _param_specs(op: dict[str, Any], reserved: frozenset[str] = frozenset()) -> list[_ParamSpec]:
    specs: list[_ParamSpec] = []
    # Pre-seed reserved names (write control args) so a colliding spec param is
    # deterministically renamed; its original API name is preserved via .api.
    taken: set[str] = set(reserved)
    for raw in op.get("parameters", []):
        location = raw.get("in")
        if location not in ("path", "query", "header", "cookie"):
            continue
        name = raw.get("name", "")
        if location in ("header", "cookie") and is_auth_param(name):
            # Auth carried out-of-band by the executor.
            continue
        arg = _safe_arg_name(name, taken)
        required = bool(raw.get("required", location == "path"))
        specs.append(
            _ParamSpec(
                arg=arg,
                api=name,
                location=location,
                py_type=_py_type(raw.get("type", "any"), raw.get("item_type")),
                required=required,
                default=raw.get("default"),
                description=str(raw.get("description", "")),
                enum=raw.get("enum"),
            )
        )
    return specs


def _substitute_path(template: str, path_values: dict[str, Any]) -> str:
    def repl(match: re.Match[str]) -> str:
        key = match.group(1)
        if key not in path_values or path_values[key] is None:
            raise ValueError(f"missing required path parameter {key!r}")
        value = str(path_values[key])
        if value == "" or value in (".", "..") or "/" in value or "\\" in value:
            raise ValueError(f"invalid path parameter value for {key!r}")
        return quote(value, safe="")

    return _PATH_PLACEHOLDER.sub(repl, template)


def _build_query(specs: list[_ParamSpec], kwargs: dict[str, Any]) -> dict[str, Any]:
    query: dict[str, Any] = {}
    for spec in specs:
        if spec.location != "query":
            continue
        value = kwargs.get(spec.arg)
        if value is None:  # unset -> omit; False/0/[] are preserved
            continue
        query[spec.api] = value
    return query


def _build_headers(specs: list[_ParamSpec], kwargs: dict[str, Any]) -> dict[str, str]:
    headers: dict[str, str] = {}
    for spec in specs:
        if spec.location != "header":
            continue
        value = kwargs.get(spec.arg)
        if value is None:
            continue
        headers[spec.api] = str(value)
    return headers


def _path_values(specs: list[_ParamSpec], kwargs: dict[str, Any]) -> dict[str, Any]:
    return {spec.api: kwargs.get(spec.arg) for spec in specs if spec.location == "path"}


def _build_signature(
    specs: list[_ParamSpec], *, is_write: bool, body_type: Any, body_required: bool
) -> tuple[inspect.Signature, dict[str, Any]]:
    parameters: list[inspect.Parameter] = []
    annotations: dict[str, Any] = {}
    # Required first (path + required query/header), then optionals.
    ordered = sorted(specs, key=lambda s: (not s.required, s.location != "path"))
    for spec in ordered:
        if spec.required:
            annotation = spec.py_type
            default = inspect.Parameter.empty
        else:
            annotation = Optional[spec.py_type] if spec.py_type is not Any else Any
            default = None
        annotations[spec.arg] = annotation
        parameters.append(
            inspect.Parameter(
                spec.arg,
                kind=inspect.Parameter.KEYWORD_ONLY,
                default=default,
                annotation=annotation,
            )
        )
    if is_write:
        if body_required:
            annotations["body"] = body_type
            parameters.append(
                inspect.Parameter(
                    "body", kind=inspect.Parameter.KEYWORD_ONLY, annotation=body_type
                )
            )
        else:
            annotations["body"] = Optional[body_type] if body_type is not Any else Any
            parameters.append(
                inspect.Parameter(
                    "body",
                    kind=inspect.Parameter.KEYWORD_ONLY,
                    default=None,
                    annotation=annotations["body"],
                )
            )
        annotations["dry_run"] = bool
        parameters.append(
            inspect.Parameter(
                "dry_run", kind=inspect.Parameter.KEYWORD_ONLY, default=True, annotation=bool
            )
        )
        annotations["confirm"] = bool
        parameters.append(
            inspect.Parameter(
                "confirm", kind=inspect.Parameter.KEYWORD_ONLY, default=False, annotation=bool
            )
        )
    annotations["return"] = dict[str, Any]
    return inspect.Signature(parameters), annotations


def _docstring(op: dict[str, Any], capability: str) -> str:
    action = op.get("summary") or op.get("operation_id") or op["name"]
    lines = [f"{action} ({op['method']} {op['path']})."]
    if op.get("description") and op["description"] != op.get("summary"):
        lines.append("")
        lines.append(op["description"][:400])
    if capability != "read":
        lines.append("")
        lines.append(
            "Side effect: generated write. Requires the platform write gate; "
            "defaults to dry_run=True (set dry_run=False and confirm=True to execute)."
        )
    return "\n".join(lines)


def _short_description(op: dict[str, Any]) -> str:
    text = op.get("summary") or op.get("description") or op.get("operation_id") or op["name"]
    text = " ".join(str(text).split())
    if len(text) > _MAX_DESC:
        text = text[: _MAX_DESC - 1].rstrip() + "\u2026"
    return f"[{op['method']}] {text}"


def _make_read_tool(op: dict[str, Any], read_executor: ReadExecutor) -> Callable[..., Any]:
    specs = _param_specs(op)
    method = op["method"]
    template = op["path"]

    async def _tool(**kwargs: Any) -> dict[str, Any]:
        try:
            path = _substitute_path(template, _path_values(specs, kwargs))
        except ValueError as exc:
            return {"error": str(exc)}
        query = _build_query(specs, kwargs)
        headers = _build_headers(specs, kwargs)
        return await read_executor(method, path, query, headers)

    signature, annotations = _build_signature(
        specs, is_write=False, body_type=Any, body_required=False
    )
    _finalize(_tool, op, signature, annotations)
    return _tool


def _make_write_tool(op: dict[str, Any], write_executor: WriteExecutor) -> Callable[..., Any]:
    specs = _param_specs(op, reserved=_WRITE_RESERVED_ARG_NAMES)
    method = op["method"]
    template = op["path"]
    rb = op.get("request_body") or {}
    content_type = rb.get("content_type", "application/json")
    body_schema_type = rb.get("schema_type", "object")
    body_type = _py_type(body_schema_type, rb.get("item_type"))
    if body_type is Any:
        body_type = dict
    body_required = bool(rb.get("required", False))
    name = op["name"]

    async def _tool(**kwargs: Any) -> dict[str, Any]:
        try:
            path = _substitute_path(template, _path_values(specs, kwargs))
        except ValueError as exc:
            return {"error": str(exc)}
        query = _build_query(specs, kwargs)
        headers = _build_headers(specs, kwargs)
        body = kwargs.get("body")
        dry_run = bool(kwargs.get("dry_run", True))
        confirm = bool(kwargs.get("confirm", False))
        return await write_executor(
            name, method, path, query, headers, body, content_type, dry_run, confirm
        )

    signature, annotations = _build_signature(
        specs, is_write=True, body_type=body_type, body_required=body_required
    )
    _finalize(_tool, op, signature, annotations)
    return _tool


def _make_diagnostic_tool(
    op: dict[str, Any], write_executor: WriteExecutor
) -> Callable[..., Any]:
    specs = _param_specs(op)
    method = op["method"]
    template = op["path"]
    rb = op.get("request_body") or {}
    content_type = rb.get("content_type", "application/json")
    body_type = _py_type(rb.get("schema_type", "object"), rb.get("item_type"))
    if body_type is Any:
        body_type = dict
    body_required = bool(rb.get("required", False))
    name = op["name"]

    async def _tool(**kwargs: Any) -> dict[str, Any]:
        try:
            path = _substitute_path(template, _path_values(specs, kwargs))
        except ValueError as exc:
            return {"error": str(exc)}
        return await write_executor(
            name,
            method,
            path,
            _build_query(specs, kwargs),
            _build_headers(specs, kwargs),
            kwargs.get("body"),
            content_type,
            False,
            True,
        )

    signature, annotations = _build_signature(
        specs, is_write=True, body_type=body_type, body_required=body_required
    )
    parameters = [
        parameter
        for parameter in signature.parameters.values()
        if parameter.name not in {"dry_run", "confirm"}
    ]
    annotations.pop("dry_run", None)
    annotations.pop("confirm", None)
    _finalize(_tool, op, signature.replace(parameters=parameters), annotations)
    return _tool


def _finalize(
    fn: Callable[..., Any],
    op: dict[str, Any],
    signature: inspect.Signature,
    annotations: dict[str, Any],
) -> None:
    fn.__name__ = op["name"]
    fn.__qualname__ = op["name"]
    fn.__doc__ = _docstring(op, op["capability"])
    fn.__signature__ = signature  # type: ignore[attr-defined]
    fn.__annotations__ = annotations


def generated_tools_enabled(platform: str, *, flag_env: str | None = None) -> bool:
    """Whether generated tools should register for ``platform``.

    Resolution: explicit ``CENTRALMCP_<PLATFORM>_GENERATED_TOOLS`` truthy/falsy
    wins; otherwise default *on* when the committed manifest exists.
    """
    env = flag_env or f"CENTRALMCP_{platform.upper()}_GENERATED_TOOLS"
    raw = os.environ.get(env)
    if raw is not None:
        return raw.strip().lower() not in {"0", "false", "no", "off", ""}
    return manifest_exists(platform)


def register_generated_tools(
    mcp: Any,
    platform: str,
    *,
    read_executor: ReadExecutor,
    write_executor: WriteExecutor,
    manifest: dict[str, Any] | None = None,
    flag_env: str | None = None,
) -> list[str]:
    """Register all manifest operations for ``platform`` as FastMCP tools.

    Returns the list of registered tool names. No-op (returns ``[]``) when the
    feature flag is off or the manifest is missing.
    """
    if not generated_tools_enabled(platform, flag_env=flag_env):
        return []
    if manifest is None:
        if not manifest_exists(platform):
            return []
        manifest = load_manifest(platform)

    existing = set(mcp._tool_manager._tools.keys())
    registered: list[str] = []
    for op in manifest.get("operations", []):
        name = op["name"]
        if name in existing:
            # Collision with a curated tool: deterministic generated suffix.
            name = f"{name}_g{digest(op['method'], op['path'])}"
            if name in existing:
                raise RuntimeError(f"generated tool collision for {name!r}")
            op = {**op, "name": name}
        capability = op.get("capability", "read")
        if capability == "read":
            fn = _make_read_tool(op, read_executor)
            annotations = READ_ONLY
        elif capability == "diagnostic":
            fn = _make_diagnostic_tool(op, write_executor)
            annotations = DIAGNOSTIC
        else:
            fn = _make_write_tool(op, write_executor)
            if capability == "destructive":
                annotations = DESTRUCTIVE
            elif op["method"] == "PUT":
                annotations = IDEMPOTENT_WRITE
            else:
                annotations = WRITE
        mcp.add_tool(
            fn,
            name=name,
            description=_short_description(op),
            annotations=annotations,
        )
        existing.add(name)
        registered.append(name)
    return registered
