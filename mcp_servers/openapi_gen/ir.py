"""OpenAPI 3.0/3.1 parser and intermediate representation (IR).

This module turns a raw OpenAPI document into a deterministic, flattened list
of :class:`OperationIR` records that the manifest builder and runtime consume.
It supports the subset of OpenAPI needed for the current Mist and Aruba Central
specs:

* local (``#/...``) ``$ref`` resolution for parameters, request bodies, and
  schemas, with cycle detection and explicit errors for unresolved refs;
* path / query / header / cookie parameters (inline or referenced);
* request bodies with a chosen content type;
* arrays / objects (maps) / enums / defaults;
* enough ``allOf`` / ``oneOf`` / ``anyOf`` handling to classify a schema's
  effective type without exploding the full object graph.

Determinism: :meth:`SpecParser.operations` walks paths in sorted order and
methods in a fixed canonical order so regeneration is byte-stable.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

# Canonical method ordering used for deterministic operation walking.
HTTP_METHODS: tuple[str, ...] = ("get", "put", "post", "delete", "patch", "head", "options")

# Cap on how deep we chase $ref / allOf chains when inferring a schema type.
_MAX_RESOLVE_DEPTH = 64


class OpenApiError(Exception):
    """Base error for OpenAPI parsing problems."""


class UnresolvedRefError(OpenApiError):
    """Raised when a ``$ref`` cannot be resolved to a local component."""


@dataclass
class ParamIR:
    """One request parameter (path/query/header/cookie)."""

    name: str
    location: str  # "path" | "query" | "header" | "cookie"
    required: bool
    schema_type: str  # string|integer|number|boolean|array|object|any
    description: str = ""
    enum: list[Any] | None = None
    default: Any = None
    item_type: str | None = None  # element type when schema_type == "array"

    def to_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {
            "name": self.name,
            "in": self.location,
            "required": self.required,
            "type": self.schema_type,
        }
        if self.description:
            out["description"] = self.description
        if self.enum is not None:
            out["enum"] = self.enum
        if self.default is not None:
            out["default"] = self.default
        if self.item_type is not None:
            out["item_type"] = self.item_type
        return out


@dataclass
class RequestBodyIR:
    """A request body with a single chosen content type."""

    required: bool
    content_type: str
    schema_type: str  # object|array|string|number|integer|boolean|any
    description: str = ""
    item_type: str | None = None
    properties: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {
            "required": self.required,
            "content_type": self.content_type,
            "schema_type": self.schema_type,
        }
        if self.description:
            out["description"] = self.description
        if self.item_type is not None:
            out["item_type"] = self.item_type
        if self.properties:
            out["properties"] = self.properties
        return out


@dataclass
class OperationIR:
    """A single flattened API operation."""

    method: str  # upper-case HTTP verb
    path: str
    operation_id: str | None
    summary: str
    description: str
    parameters: list[ParamIR]
    request_body: RequestBodyIR | None
    tags: list[str] = field(default_factory=list)

    @property
    def key(self) -> str:
        """Stable operation key: ``"METHOD /path"``."""
        return f"{self.method} {self.path}"


_TYPE_ALIASES = {
    "string": "string",
    "integer": "integer",
    "number": "number",
    "boolean": "boolean",
    "array": "array",
    "object": "object",
}


class SpecParser:
    """Resolve refs and flatten operations for one OpenAPI document."""

    def __init__(self, spec: dict[str, Any]):
        if not isinstance(spec, dict):
            raise OpenApiError("spec must be a JSON object")
        self.spec = spec
        self.version = str(spec.get("openapi") or spec.get("swagger") or "")
        if not self.version.startswith(("3.0", "3.1")):
            raise OpenApiError(
                f"unsupported OpenAPI version {self.version!r}; expected 3.0.x or 3.1.x"
            )

    # -- ref resolution ------------------------------------------------
    def resolve_ref(self, ref: str) -> Any:
        if not isinstance(ref, str) or not ref.startswith("#/"):
            raise UnresolvedRefError(f"only local '#/...' refs are supported, got {ref!r}")
        node: Any = self.spec
        for raw in ref[2:].split("/"):
            token = raw.replace("~1", "/").replace("~0", "~")
            if not isinstance(node, dict) or token not in node:
                raise UnresolvedRefError(f"unresolved ref: {ref}")
            node = node[token]
        return node

    def _deref(self, node: Any, _depth: int = 0) -> Any:
        """Follow a top-level ``$ref`` (non-recursively into children)."""
        seen: set[str] = set()
        while isinstance(node, dict) and "$ref" in node:
            ref = node["$ref"]
            if ref in seen:
                raise UnresolvedRefError(f"cyclic ref: {ref}")
            seen.add(ref)
            if len(seen) > _MAX_RESOLVE_DEPTH:
                raise UnresolvedRefError(f"ref chain too deep starting at {ref}")
            node = self.resolve_ref(ref)
        return node

    # -- schema type inference ----------------------------------------
    def schema_type(
        self, schema: Any, _depth: int = 0
    ) -> tuple[str, str | None, list[Any] | None, Any, list[str]]:
        """Return ``(type, item_type, enum, default, property_names)`` for a schema.

        Handles ``$ref``, ``allOf`` (merged), and ``oneOf``/``anyOf`` (first
        member that yields a concrete type). Unknown shapes map to ``"any"``.
        """
        if _depth > _MAX_RESOLVE_DEPTH:
            return "any", None, None, None, []
        schema = self._deref(schema)
        if not isinstance(schema, dict):
            return "any", None, None, None, []

        default = schema.get("default")
        enum = schema.get("enum")

        # Composition keywords.
        if "allOf" in schema and isinstance(schema["allOf"], list):
            merged_type = "object"
            props: list[str] = []
            for sub in schema["allOf"]:
                st, it, en, de, pr = self.schema_type(sub, _depth + 1)
                if st != "object" and st != "any":
                    merged_type = st
                props.extend(pr)
            return merged_type, None, enum, default, sorted(set(props))
        for comp in ("oneOf", "anyOf"):
            if comp in schema and isinstance(schema[comp], list):
                for sub in schema[comp]:
                    st, it, en, de, pr = self.schema_type(sub, _depth + 1)
                    if st != "any":
                        return st, it, enum or en, default if default is not None else de, pr
                return "any", None, enum, default, []

        raw_type = schema.get("type")
        # OpenAPI 3.1 allows a list of types; pick the first non-null.
        if isinstance(raw_type, list):
            raw_type = next((t for t in raw_type if t != "null"), None)

        if raw_type == "array":
            item_type, _, _, _, _ = self.schema_type(schema.get("items", {}), _depth + 1)
            return "array", item_type, enum, default, []
        if raw_type == "object" or "properties" in schema or "additionalProperties" in schema:
            props = sorted((schema.get("properties") or {}).keys())
            return "object", None, enum, default, props
        if raw_type in _TYPE_ALIASES:
            return _TYPE_ALIASES[raw_type], None, enum, default, []
        if enum:
            # Infer from enum member types.
            if all(isinstance(v, bool) for v in enum):
                return "boolean", None, enum, default, []
            if all(isinstance(v, int) for v in enum):
                return "integer", None, enum, default, []
            return "string", None, enum, default, []
        return "any", None, enum, default, []

    # -- parameters ----------------------------------------------------
    def _parse_param(self, raw: Any) -> ParamIR:
        param = self._deref(raw)
        if not isinstance(param, dict) or "name" not in param or "in" not in param:
            raise OpenApiError(f"invalid parameter object: {raw!r}")
        st, item_type, enum, default, _ = self.schema_type(param.get("schema", {}))
        required = bool(param.get("required", param.get("in") == "path"))
        return ParamIR(
            name=str(param["name"]),
            location=str(param["in"]),
            required=required,
            schema_type=st,
            description=str(param.get("description", "")).strip(),
            enum=enum,
            default=default,
            item_type=item_type,
        )

    def _parse_request_body(self, raw: Any) -> RequestBodyIR | None:
        body = self._deref(raw)
        if not isinstance(body, dict):
            return None
        content = body.get("content")
        if not isinstance(content, dict) or not content:
            return None
        # Prefer application/json, else the first declared content type.
        content_type = "application/json"
        if content_type not in content:
            content_type = sorted(content.keys())[0]
        media = content.get(content_type) or {}
        st, item_type, _, _, props = self.schema_type(media.get("schema", {}))
        return RequestBodyIR(
            required=bool(body.get("required", False)),
            content_type=content_type,
            schema_type=st,
            description=str(body.get("description", "")).strip(),
            item_type=item_type,
            properties=props,
        )

    # -- operation walk ------------------------------------------------
    def operations(self) -> list[OperationIR]:
        paths = self.spec.get("paths")
        if not isinstance(paths, dict):
            raise OpenApiError("spec has no 'paths' object")
        ops: list[OperationIR] = []
        for path in sorted(paths.keys()):
            item = paths[path]
            if not isinstance(item, dict):
                continue
            item = self._deref(item)
            shared_params = item.get("parameters", []) if isinstance(item, dict) else []
            for method in HTTP_METHODS:
                op = item.get(method)
                if not isinstance(op, dict):
                    continue
                raw_params = list(shared_params) + list(op.get("parameters", []))
                params = [self._parse_param(p) for p in raw_params]
                request_body = None
                if "requestBody" in op:
                    request_body = self._parse_request_body(op["requestBody"])
                ops.append(
                    OperationIR(
                        method=method.upper(),
                        path=path,
                        operation_id=op.get("operationId"),
                        summary=str(op.get("summary", "")).strip(),
                        description=str(op.get("description", "")).strip(),
                        parameters=params,
                        request_body=request_body,
                        tags=[str(t) for t in op.get("tags", []) if isinstance(t, str)],
                    )
                )
        return ops
