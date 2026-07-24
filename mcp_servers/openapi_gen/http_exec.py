"""Reusable async HTTP executors for generated OpenAPI tools.

The shared :mod:`mcp_servers.openapi_gen.runtime` registers each manifest
operation as a FastMCP tool that dispatches through a platform-supplied
``read_executor`` / ``write_executor``. Central and GLP need the *same*
execution behavior -- response bounding, content-type handling (JSON,
merge-patch, SCIM, form, multipart, raw), auth injected last, a path allow-list,
and dry_run/confirm write gating -- differing only in *which* account/token they
use. This module factors that behavior out so each platform module only has to
supply:

* an ``async resolve(extra_headers) -> (base_url, headers)`` that reuses that
  platform's existing client/token pattern and injects trusted auth **last**;
* a callable returning the allowed path prefixes (defense-in-depth);
* a write-gate predicate + blocked-response builder.

Nothing here is model-visible; auth headers are never returned to the caller.
"""

from __future__ import annotations

import json as _json
from collections.abc import Awaitable, Callable
from typing import Any

import httpx

from mcp_servers.shared import (
    bound_collection_response,
    bounded_response_payload,
    clamp_limit,
    redact_sensitive,
)

# resolve(extra_headers) -> (base_url, headers-with-auth-last). May raise on
# missing configuration; the executor converts that into an {"error": ...} dict.
AuthResolver = Callable[[dict[str, str] | None], Awaitable[tuple[str, dict[str, str]]]]
PrefixGetter = Callable[[], tuple[str, ...]]
WritesAllowed = Callable[[], bool]
BlockedResponse = Callable[[str], dict[str, Any]]

_JSON_LIKE_CONTENT_TYPES = {
    "application/json",
    "application/merge-patch+json",
    "application/scim+json",
    "application/json-patch+json",
}

_TIMEOUT = 30.0


def _clean_params(query: dict[str, Any]) -> dict[str, Any]:
    # None already dropped by the runtime; keep False / 0 / [].
    return {k: v for k, v in query.items() if v is not None}


def _path_ok(path: str, prefixes: tuple[str, ...]) -> bool:
    return bool(prefixes) and path.startswith(prefixes)


def _apply_body(
    kwargs: dict[str, Any],
    req_headers: dict[str, str],
    body: Any,
    content_type: str,
) -> dict[str, Any] | None:
    """Attach ``body`` to httpx request kwargs per ``content_type``.

    Returns an error dict on an invalid body shape, else ``None``.
    """
    if body is None:
        return None
    if content_type in _JSON_LIKE_CONTENT_TYPES:
        kwargs["json"] = body
        # httpx defaults JSON bodies to application/json; honor the declared
        # variant (merge-patch / scim) explicitly.
        if content_type != "application/json":
            req_headers["Content-Type"] = content_type
    elif content_type == "multipart/form-data":
        if not isinstance(body, dict):
            return {"error": "multipart/form-data body must be an object of form fields"}
        files: dict[str, tuple[Any, ...]] = {}
        for key, value in body.items():
            if isinstance(value, bytes):
                files[str(key)] = (str(key), value, "application/octet-stream")
            elif isinstance(value, (dict, list)):
                files[str(key)] = (None, _json.dumps(value), "application/json")
            else:
                files[str(key)] = (None, "" if value is None else str(value))
        kwargs["files"] = files  # httpx sets multipart Content-Type + boundary
    elif content_type == "application/x-www-form-urlencoded":
        if not isinstance(body, dict):
            return {"error": "form-urlencoded body must be an object of form fields"}
        kwargs["data"] = body
        req_headers.setdefault("Content-Type", content_type)
    else:
        kwargs["content"] = body if isinstance(body, (bytes, str)) else str(body)
        req_headers.setdefault("Content-Type", content_type)
    return None


def make_read_executor(
    *,
    resolve: AuthResolver,
    allowed_prefixes: PrefixGetter,
    not_configured: str = "backend not configured",
) -> Callable[[str, str, dict[str, Any], dict[str, str]], Awaitable[dict[str, Any]]]:
    """Build the read executor (GET/HEAD, bounded, direct)."""

    async def _read(
        method: str,
        path: str,
        query: dict[str, Any],
        headers: dict[str, str],
    ) -> dict[str, Any]:
        prefixes = allowed_prefixes()
        if not _path_ok(path, prefixes):
            return {"error": f"Generated path must begin with one of {prefixes}."}
        try:
            base_url, req_headers = await resolve(headers)
        except Exception as exc:  # config / credential errors
            return {"error": f"{not_configured}: {exc}"}
        url = f"{base_url}{path}"
        params = _clean_params(query)
        try:
            async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
                resp = await client.request(method, url, headers=req_headers, params=params)
            payload = bound_collection_response(
                bounded_response_payload(resp), limit=clamp_limit(None), offset=0
            )
            return {"status_code": resp.status_code, "data": payload, "url": url}
        except httpx.HTTPError as exc:
            return {"error": str(exc), "url": url}

    return _read


def make_write_executor(
    *,
    resolve: AuthResolver,
    allowed_prefixes: PrefixGetter,
    writes_allowed: WritesAllowed,
    blocked_response: BlockedResponse,
    execute_hint: str,
    not_configured: str = "backend not configured",
) -> Callable[..., Awaitable[dict[str, Any]]]:
    """Build the write executor (gate + dry_run/confirm + content types)."""

    async def _write(
        name: str,
        method: str,
        path: str,
        query: dict[str, Any],
        headers: dict[str, str],
        body: Any,
        content_type: str,
        dry_run: bool,
        confirm: bool,
    ) -> dict[str, Any]:
        if not writes_allowed():
            return blocked_response(name)
        prefixes = allowed_prefixes()
        if not _path_ok(path, prefixes):
            return {"error": f"Generated path must begin with one of {prefixes}."}
        params = _clean_params(query)
        try:
            base_url, req_headers = await resolve(headers)
        except Exception as exc:
            return {"error": f"{not_configured}: {exc}"}
        url = f"{base_url}{path}"
        preview: dict[str, Any] = {
            "method": method,
            "path": path,
            "url": url,
            "params": redact_sensitive(params),
            "json": redact_sensitive(body),
            "content_type": content_type,
        }
        if dry_run:
            return {"dry_run": True, **preview, "execute_hint": execute_hint}
        if not confirm:
            return {
                "error": "confirm=True is required when dry_run=False.",
                "dry_run": True,
                **preview,
            }
        kwargs: dict[str, Any] = {"headers": req_headers, "params": params}
        body_error = _apply_body(kwargs, req_headers, body, content_type)
        if body_error is not None:
            return body_error
        try:
            async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
                resp = await client.request(method, url, **kwargs)
            return {
                "status_code": resp.status_code,
                "data": redact_sensitive(bounded_response_payload(resp)),
                "url": url,
            }
        except httpx.HTTPError as exc:
            return {"error": str(exc), "url": url}

    return _write
