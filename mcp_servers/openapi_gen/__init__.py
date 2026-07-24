"""Shared generated-OpenAPI tool foundation.

This package provides a reusable pipeline that turns a vendor OpenAPI document
into named FastMCP tools:

* :mod:`ir` -- Swagger 2.0/OpenAPI 3.0/3.1 normalization and IR.
* :mod:`naming` -- deterministic, globally-unique tool naming.
* :mod:`classify` -- read/write/destructive capability classification.
* :mod:`manifest` -- build/serialize/load the committed operation manifest.
* :mod:`runtime` -- register manifest operations as FastMCP tools.

Platform backends (e.g. :mod:`mcp_servers.mist`) call
:func:`mcp_servers.openapi_gen.runtime.register_generated_tools` at import time
to expose the generated tools directly, guarded by a per-platform feature flag
that defaults *on* when the committed manifest exists.
"""

from __future__ import annotations

from mcp_servers.openapi_gen.manifest import (
    load_manifest,
    manifest_exists,
    manifest_operation_count,
    manifest_path,
)

__all__ = [
    "load_manifest",
    "manifest_exists",
    "manifest_operation_count",
    "manifest_path",
]
