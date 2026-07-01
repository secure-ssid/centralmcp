"""GreenLake Platform (GLP) client.

Handles device-management and subscription-management operations including
async task polling (202 Accepted → GET /tasks/{task_id}).
"""

from __future__ import annotations

import logging
import os
import time
import uuid
from typing import Any, Optional

from pipeline.clients.central_client import CentralClient
from pipeline.clients.token_manager import TokenManager

logger = logging.getLogger(__name__)

_GLP_BASE_URL = "https://global.api.greenlake.hpe.com"
_TASK_POLL_INTERVAL = 10  # seconds
_TASK_POLL_TIMEOUT = 300  # 5 minutes

# Feature flag for the v2beta1 device PATCH write path. Default OFF.
# When unset or "0"/"false", the mutations raise NotImplementedError so
# accidental callers can't fire writes. Set to "1" or "true" once the
# caller has sandbox-validated the payload shape and transactional
# rollback story for their use case.
_V2BETA1_WRITES_FLAG = "CENTRALMCP_GLP_V2BETA1_WRITES"


def _writes_enabled() -> bool:
    return os.environ.get(_V2BETA1_WRITES_FLAG, "").lower() in ("1", "true", "yes")


def _compact_exception_message(exc: Exception, max_chars: int = 240) -> str:
    """Return a compact, structured exception message for tool-friendly output."""
    response = getattr(exc, "response", None)
    if response is None:
        return str(exc)
    try:
        body = response.json()
    except Exception:
        body = response.text
    body_text = str(body or "").strip()
    if len(body_text) > max_chars:
        body_text = f"{body_text[:max_chars]}... [truncated {len(body_text) - max_chars} chars]"
    reason = getattr(response, "reason_phrase", None) or getattr(response, "reason", "")
    return f"HTTP {response.status_code} {reason}: {body_text}"


class GLPClient:
    """Client for HPE GreenLake Platform device and subscription management APIs."""

    def __init__(
        self,
        token_manager: TokenManager,
        workspace_id: str,
        base_url: str = _GLP_BASE_URL,
    ):
        self._client = CentralClient(base_url=base_url, token_manager=token_manager)
        self.workspace_id = workspace_id
        # Per-instance serial -> deviceId cache. NOT at class scope (that
        # would share across all GLPClient instances in the process; in
        # prod there's only one singleton so harmless, but the per-test
        # isolation and the docstring's "process-local" claim both want
        # the per-instance form).
        self._device_id_cache: dict[str, str] = {}

    # ------------------------------------------------------------------
    # Device management
    # ------------------------------------------------------------------

    def get_device(self, serial_number: str) -> Optional[dict[str, Any]]:
        """Look up a device in GLP by serial number. Returns None if not found."""
        try:
            result = self._client.get(
                "/devices/v1/devices",
                params={"filter": f"serialNumber eq '{serial_number}'"},
            )
            items = result.get("items", result.get("devices", []))
            return items[0] if items else None
        except Exception as exc:
            logger.warning("GLP get_device failed for %s: %s", serial_number, exc)
            return None

    def add_device(self, serial_number: str, mac_address: Optional[str] = None) -> str:
        """Add a single device to the GLP workspace. Returns async-operation ID."""
        return self.add_devices([{"serialNumber": serial_number, "macAddress": mac_address}])

    def add_devices(self, devices: list[dict[str, Any]]) -> str:
        """Add one or more network devices to the GLP workspace in a single call.

        Args:
            devices: List of dicts with 'serialNumber' (required) and 'macAddress' (required).

        Returns:
            Async-operation ID for polling with poll_task().
        """
        for d in devices:
            if not d.get("macAddress"):
                raise ValueError("macAddress is required for network devices")
        network = [
            {k: v for k, v in d.items() if v is not None}
            for d in devices
        ]
        body: dict[str, Any] = {"network": network, "compute": [], "storage": []}
        location = self._client.post_async("/devices/v1/devices", data=body)
        # Location: /devices/v1/async-operations/{id}
        task_id = location.rstrip("/").split("/")[-1]
        serials = [d.get("serialNumber") for d in devices]
        logger.info(
            "GLP add_devices serial_count=%d sample=%s -> async-op id=%s",
            len(serials),
            serials[:5],
            task_id,
        )
        return task_id

    def poll_task(
        self,
        task_id: str,
        timeout: int = _TASK_POLL_TIMEOUT,
        interval: int = _TASK_POLL_INTERVAL,
    ) -> dict[str, Any]:
        """Poll a GLP async-operation until completion or timeout.

        Returns the final task response dict.
        Raises RuntimeError on timeout or task failure.
        """
        deadline = time.time() + timeout
        while time.time() < deadline:
            result = self._client.get(f"/devices/v1/async-operations/{task_id}")
            status = result.get("status", "").lower()
            logger.debug("GLP async-op %s status=%s", task_id, status)
            if status in ("completed", "success", "succeeded"):
                return result
            if status in ("failed", "error", "timeout", "cancelled"):
                raise RuntimeError(f"GLP async-op {task_id} failed: {result}")
            time.sleep(interval)
        raise RuntimeError(f"GLP async-op {task_id} timed out after {timeout}s")

    # ------------------------------------------------------------------
    # Device ID resolution (serial → GLP device UUID)
    # ------------------------------------------------------------------
    #
    # Central-style callers pass serial numbers. GLP v2beta1 identifies
    # devices by their GLP UUID (the `id` field on each device record),
    # not by serial. We resolve on demand with a small in-memory cache.
    # The cache is process-local and **not** persisted — a restart
    # re-fetches, which is fine and keeps stale mappings from sticking.

    # Serial numbers are restricted to ASCII alphanumerics, dashes, and
    # underscores in practice (HPE spec; matches every serial format we've
    # seen on AOS-S / CX / AP / gateway hardware). We reject anything else
    # defensively before interpolating into an OData filter, so a serial
    # containing ``'`` can't terminate the quoted string and inject query
    # fragments. This is belt-and-suspenders — the filter value itself is
    # unlikely to be attacker-controlled in normal MCP use.
    _SERIAL_SAFE_CHARS = set(
        "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_"
    )

    @classmethod
    def _is_safe_serial(cls, serial_number: str) -> bool:
        return bool(serial_number) and all(c in cls._SERIAL_SAFE_CHARS for c in serial_number)

    def resolve_device_id(self, serial_number: str) -> Optional[str]:
        """Return the GLP device UUID for ``serial_number``, or None if not found.

        Looks up via ``GET /devices/v1/devices?filter=serialNumber eq '<s>'``.
        Results are memoised for the lifetime of this client instance.
        Rejects malformed serials before interpolating into the filter so a
        rogue caller can't break out of the quoted string.
        """
        if not self._is_safe_serial(serial_number):
            logger.warning(
                "resolve_device_id: rejecting serial %r (must be ASCII alnum/dash/underscore)",
                serial_number,
            )
            return None
        cached = self._device_id_cache.get(serial_number)
        if cached is not None:
            return cached
        try:
            result = self._client.get(
                "/devices/v1/devices",
                params={"filter": f"serialNumber eq '{serial_number}'", "limit": 1},
            )
            items = result.get("items", [])
            if not items:
                return None
            device_id = items[0].get("id")
            if not device_id:
                return None
            self._device_id_cache[serial_number] = device_id
            return device_id
        except Exception as exc:
            logger.warning("resolve_device_id(%s) failed: %s", serial_number, exc)
            return None

    # ------------------------------------------------------------------
    # v2beta1 device PATCH — archive, unarchive, subscription assign/unassign
    # ------------------------------------------------------------------
    #
    # Per the official Devices v2beta1 spec
    # (https://developer.greenlake.hpe.com/docs/greenlake/services/device-management/public/openapi/nbapi-inventory-latest/devices-v2beta1/patchdevicesv2beta1),
    # these four operations all share one endpoint:
    #
    #     PATCH /devices/v2beta1/devices?id={uuid}[&id=...]
    #     Content-Type: application/merge-patch+json
    #
    # Body shapes:
    #   archive:            {"archived": true}   — MUST be the only field
    #   unarchive:          {"archived": false}
    #   assign subscription:{"subscription": [{"id": "<subscriptionId>"}]}
    #   unassign all:       {"subscription": []}
    #
    # Response is 202 Accepted with Location header pointing at
    # /devices/v1/async-operations/{id} — which our existing
    # ``poll_task`` already knows how to poll.
    #
    # All four go through ``_patch_devices_v2beta1`` so the feature flag
    # gate, payload validation, deviceId resolution, and async polling
    # live in exactly one place.

    def _patch_devices_v2beta1(
        self,
        serial_number: str,
        body: dict[str, Any],
    ) -> dict[str, Any]:
        """Internal helper: PATCH /devices/v2beta1/devices?id=<resolved-id>."""
        if not _writes_enabled():
            raise NotImplementedError(
                f"GLP v2beta1 writes are gated behind {_V2BETA1_WRITES_FLAG}=1. "
                "Set that env var after sandbox-validating payload + rollback "
                "for your use case. Payload that would have fired: "
                f"{body}"
            )

        device_id = self.resolve_device_id(serial_number)
        if device_id is None:
            raise RuntimeError(
                f"Could not resolve serial {serial_number!r} to a GLP device ID. "
                "Check the device is registered in this workspace."
            )

        # PATCH with merge-patch+json. Central _request accepts custom
        # headers via kwargs; set the content type explicitly.
        resp = self._client._request(
            "PATCH",
            "/devices/v2beta1/devices",
            params={"id": device_id},
            json=body,
            headers={"Content-Type": "application/merge-patch+json"},
        )

        if resp.status_code not in (200, 202):
            raise RuntimeError(
                f"GLP PATCH /devices/v2beta1/devices id={device_id} returned "
                f"HTTP {resp.status_code}: {resp.text[:300]}"
            )

        # 202 → async; poll the Location header's async-operation.
        if resp.status_code == 202:
            location = resp.headers.get("Location", "")
            task_id = location.rstrip("/").split("/")[-1]
            if not task_id:
                raise RuntimeError(
                    "GLP PATCH returned 202 but no Location header — cannot poll."
                )
            return self.poll_task(task_id)

        # 200 = synchronous completion (rare; spec allows it)
        try:
            return resp.json()
        except Exception:
            return {"status": "completed", "rawResponse": resp.text[:500]}

    def _resolve_subscription_id(self, subscription: str) -> str:
        """Return a subscription UUID for ``subscription``.

        Accepts either a GLP subscription UUID (returned as-is) or a
        subscription key string, which is resolved to its UUID via
        ``GET /subscriptions/v1/subscriptions?filter=key eq '<key>'``.
        Raises ValueError if a key can't be resolved.
        """
        try:
            uuid.UUID(subscription)
            return subscription
        except (ValueError, AttributeError, TypeError):
            pass
        result = self._client.get(
            "/subscriptions/v1/subscriptions",
            params={"filter": f"key eq '{subscription}'"},
        )
        items = result.get("items", result.get("subscriptions", []))
        if not items:
            raise ValueError(
                f"Could not resolve subscription key {subscription!r} to a GLP "
                "subscription ID. Check the key exists in this workspace."
            )
        return items[0]["id"]

    def assign_subscription(
        self,
        serial_number: str,
        subscription_id: str,
    ) -> dict[str, Any]:
        """Assign a subscription to a device via v2beta1 device PATCH.

        Args:
            serial_number: Device serial (resolved to GLP UUID internally).
            subscription_id: The GLP subscription UUID, or a subscription key
                string (resolved to its UUID internally). Use
                ``list_subscriptions()`` to find either.

        Guarded by ``CENTRALMCP_GLP_V2BETA1_WRITES=1``.
        """
        resolved_id = self._resolve_subscription_id(subscription_id)
        return self._patch_devices_v2beta1(
            serial_number,
            {"subscription": [{"id": resolved_id}]},
        )

    def unassign_subscription(self, serial_number: str) -> dict[str, Any]:
        """Remove **all** subscriptions from a device via v2beta1 device PATCH.

        Sends ``{"subscription": []}`` per the GLP Devices v2beta1 spec.
        Guarded by ``CENTRALMCP_GLP_V2BETA1_WRITES=1``.
        """
        return self._patch_devices_v2beta1(
            serial_number,
            {"subscription": []},
        )

    def archive_device(self, serial_number: str) -> dict[str, Any]:
        """Archive a device via v2beta1 device PATCH.

        Sends ``{"archived": true}`` — per spec this MUST be the only field
        in the body. Incompatible with combining in a single call with any
        other device mutation.

        Guarded by ``CENTRALMCP_GLP_V2BETA1_WRITES=1``.
        """
        return self._patch_devices_v2beta1(serial_number, {"archived": True})

    def unarchive_device(self, serial_number: str) -> dict[str, Any]:
        """Unarchive a device via v2beta1 device PATCH.

        Sends ``{"archived": false}``. Guarded by
        ``CENTRALMCP_GLP_V2BETA1_WRITES=1``.
        """
        return self._patch_devices_v2beta1(serial_number, {"archived": False})

    # ------------------------------------------------------------------
    # GLP read — devices, subscriptions, users, audit logs
    # ------------------------------------------------------------------

    def list_devices(
        self,
        limit: int = 100,
        offset: int = 0,
        filter: str | None = None,
    ) -> list[dict[str, Any]]:
        """List devices in the GLP workspace."""
        try:
            params: dict[str, Any] = {"limit": limit, "offset": offset}
            if filter:
                params["filter"] = filter
            result = self._client.get("/devices/v1/devices", params=params)
            return result.get("items", result.get("devices", []))
        except Exception as exc:
            msg = _compact_exception_message(exc)
            logger.warning("GLP list_devices failed: %s", msg)
            raise RuntimeError(f"GLP list_devices failed: {msg}") from exc

    def list_subscriptions(
        self,
        limit: int = 100,
        offset: int = 0,
    ) -> list[dict[str, Any]]:
        """List subscriptions in the GLP workspace."""
        try:
            result = self._client.get(
                "/subscriptions/v1/subscriptions",
                params={"limit": limit, "offset": offset},
            )
            return result.get("items", result.get("subscriptions", []))
        except Exception as exc:
            msg = _compact_exception_message(exc)
            logger.warning("GLP list_subscriptions failed: %s", msg)
            raise RuntimeError(f"GLP list_subscriptions failed: {msg}") from exc

    def get_subscription(self, subscription_id: str) -> Optional[dict[str, Any]]:
        """Fetch a single subscription by ID."""
        try:
            return self._client.get(f"/subscriptions/v1/subscriptions/{subscription_id}")
        except Exception as exc:
            logger.warning("GLP get_subscription failed for %s: %s", subscription_id, exc)
            return None

    def list_users(
        self,
        limit: int = 100,
        offset: int = 0,
    ) -> list[dict[str, Any]]:
        """List users in the GLP workspace."""
        try:
            result = self._client.get(
                "/identity/v1/users",
                params={"limit": limit, "offset": offset},
            )
            return result.get("items", result.get("users", []))
        except Exception as exc:
            logger.warning("GLP list_users failed: %s", exc)
            raise RuntimeError(f"GLP list_users failed: {exc}") from exc

    def get_user(self, user_id: str) -> Optional[dict[str, Any]]:
        """Fetch a single user by ID."""
        try:
            return self._client.get(f"/identity/v1/users/{user_id}")
        except Exception as exc:
            logger.warning("GLP get_user failed for %s: %s", user_id, exc)
            return None

    def list_audit_logs(
        self,
        limit: int = 100,
        offset: int = 0,
        category: str | None = None,
    ) -> list[dict[str, Any]]:
        """List audit log entries for the GLP workspace."""
        try:
            params: dict[str, Any] = {"limit": limit, "offset": offset}
            if category:
                params["category"] = category
            result = self._client.get("/audit-log/v1/logs", params=params)
            return result.get("items", result.get("logs", []))
        except Exception as exc:
            logger.warning("GLP list_audit_logs failed: %s", exc)
            raise RuntimeError(f"GLP list_audit_logs failed: {exc}") from exc
