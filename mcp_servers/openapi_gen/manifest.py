"""Build, serialize, and load compact generated-operation manifests.

The manifest is a single committed JSON file per platform under
``mcp_servers/openapi_gen/manifests/<platform>.json``. It records the source
spec digest plus one compact record per operation (name, method, path,
summary/description, parameters, request schema, content type, capability
classification, and stable operation key). We deliberately commit *one*
manifest -- not thousands of generated Python files.

Only manifests derived from specs we are licensed to redistribute (e.g. the
MIT-licensed Mist OpenAPI) should be committed. The raw upstream spec itself is
never committed from a gitignored path.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from mcp_servers.openapi_gen.classify import classify
from mcp_servers.openapi_gen.ir import SpecParser
from mcp_servers.openapi_gen.naming import NameAllocator

SCHEMA_VERSION = 1
_PKG_DIR = Path(__file__).resolve().parent
MANIFEST_DIR = _PKG_DIR / "manifests"
OVERRIDE_DIR = _PKG_DIR / "overrides"


def manifest_path(platform: str) -> Path:
    return MANIFEST_DIR / f"{platform}.json"


def override_path(platform: str) -> Path:
    return OVERRIDE_DIR / f"{platform}.json"


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def load_overrides(platform: str) -> dict[str, str]:
    """Load the capability override map for ``platform`` (empty if absent)."""
    path = override_path(platform)
    if not path.exists():
        return {}
    doc = json.loads(path.read_text())
    overrides = doc.get("capabilities", doc) if isinstance(doc, dict) else {}
    return {str(k): str(v) for k, v in overrides.items()}


def build_manifest(
    spec: dict[str, Any],
    *,
    platform: str,
    source_file: str,
    source_sha256: str,
    overrides: dict[str, str] | None = None,
) -> dict[str, Any]:
    """Build a manifest dict from a parsed spec (deterministic ordering)."""
    parser = SpecParser(spec)
    operations = parser.operations()
    overrides = overrides or {}
    allocator = NameAllocator()

    records: list[dict[str, Any]] = []
    used_override_keys: set[str] = set()
    for op in operations:
        name = allocator.allocate(platform, op.method, op.path, op.operation_id)
        capability = classify(op.method, op.key, overrides)
        if op.key in overrides:
            used_override_keys.add(op.key)
        record: dict[str, Any] = {
            "name": name,
            "key": op.key,
            "method": op.method,
            "path": op.path,
            "capability": capability,
        }
        if op.operation_id:
            record["operation_id"] = op.operation_id
        if op.summary:
            record["summary"] = op.summary
        if op.description:
            record["description"] = op.description
        if op.tags:
            record["tags"] = op.tags
        record["parameters"] = [p.to_dict() for p in op.parameters]
        if op.request_body is not None:
            record["request_body"] = op.request_body.to_dict()
        records.append(record)

    stray = sorted(set(overrides) - used_override_keys)

    info = spec.get("info", {}) if isinstance(spec.get("info"), dict) else {}
    manifest: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "platform": platform,
        "source": {
            "file": source_file,
            "sha256": source_sha256,
            "openapi": parser.version,
            "title": info.get("title", ""),
            "version": info.get("version", ""),
            "license": (info.get("license") or {}).get("name", ""),
            "operation_count": len(records),
        },
        "override_keys_applied": sorted(used_override_keys),
        "override_keys_unmatched": stray,
        "operations": records,
    }
    return manifest


def dumps(manifest: dict[str, Any]) -> str:
    """Serialize a manifest deterministically (stable, diff-friendly)."""
    return json.dumps(manifest, indent=2, ensure_ascii=False, sort_keys=False) + "\n"


def write_manifest(platform: str, manifest: dict[str, Any]) -> Path:
    MANIFEST_DIR.mkdir(parents=True, exist_ok=True)
    path = manifest_path(platform)
    path.write_text(dumps(manifest))
    return path


def load_manifest(platform: str) -> dict[str, Any]:
    path = manifest_path(platform)
    if not path.exists():
        raise FileNotFoundError(f"no generated manifest for platform {platform!r}: {path}")
    return json.loads(path.read_text())


def manifest_exists(platform: str) -> bool:
    return manifest_path(platform).exists()


def manifest_operation_count(platform: str) -> int:
    """Number of operations in the committed manifest (0 if absent)."""
    if not manifest_exists(platform):
        return 0
    return len(load_manifest(platform).get("operations", []))
