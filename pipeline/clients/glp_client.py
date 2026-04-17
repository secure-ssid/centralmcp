"""GreenLake Platform (GLP) client.

Handles device-management and subscription-management operations including
async task polling (202 Accepted → GET /tasks/{task_id}).
"""

from __future__ import annotations

import logging
import time
from typing import Any, Optional

from pipeline.clients.token_manager import TokenManager
from pipeline.clients.central_client import CentralClient

logger = logging.getLogger(__name__)

_GLP_BASE_URL = "https://global.api.greenlake.hpe.com"
_TASK_POLL_INTERVAL = 10  # seconds
_TASK_POLL_TIMEOUT = 300  # 5 minutes


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
    return f"HTTP {response.status_code} {response.reason}: {body_text}"


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

    # ------------------------------------------------------------------
    # Device management
    # ------------------------------------------------------------------

    def get_device(self, serial_number: str) -> Optional[dict[str, Any]]:
        """Look up a device in GLP by serial number. Returns None if not found."""
        try:
            result = self._client.get(
                "/devices/v1/devices",
                params={"filter": f"serial eq '{serial_number}'"},
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
            if status in ("completed", "success"):
                return result
            if status in ("failed", "error"):
                raise RuntimeError(f"GLP async-op {task_id} failed: {result}")
            time.sleep(interval)
        raise RuntimeError(f"GLP async-op {task_id} timed out after {timeout}s")

    # ------------------------------------------------------------------
    # Subscriptions
    # ------------------------------------------------------------------

    # ------------------------------------------------------------------
    # Mutating ops — DISABLED pending correct endpoint paths
    #
    # The previous implementations posted to `/license/assign`,
    # `/license/unassign`, `/device-inventory/archive`, and
    # `/device-inventory/unarchive` against the GLP base URL
    # (https://global.api.greenlake.hpe.com). Those paths are stale Classic
    # Central routes (not GLP) and they are NOT present in the official
    # Aruba "New Central" Postman collections (MRT + Configuration APIs)
    # either. Calling them at the GLP host returns 404.
    #
    # Correct GLP paths need to come from the GLP Subscriptions and Device
    # Management service references. Until those are confirmed, raise
    # NotImplementedError so broken mutations can't silently fire.
    #
    # See https://developer.greenlake.hpe.com/docs/greenlake/services/
    # ------------------------------------------------------------------

    def assign_subscription(self, serial_number: str, subscription_key: str) -> dict[str, Any]:
        """Assign a subscription to a device. Disabled — see module comment."""
        raise NotImplementedError(
            "assign_subscription is disabled: the previous path "
            "POST /license/assign is not a valid GLP endpoint. "
            "Correct path TBD from GLP Subscriptions service reference."
        )

    def unassign_subscription(self, serial_number: str) -> dict[str, Any]:
        """Remove all subscriptions from a device. Disabled — see module comment."""
        raise NotImplementedError(
            "unassign_subscription is disabled: the previous path "
            "POST /license/unassign is not a valid GLP endpoint. "
            "Correct path TBD from GLP Subscriptions service reference."
        )

    def archive_device(self, serial_number: str) -> dict[str, Any]:
        """Archive a device. Disabled — see module comment."""
        raise NotImplementedError(
            "archive_device is disabled: the previous path "
            "POST /device-inventory/archive is not a valid GLP endpoint. "
            "Correct path TBD from GLP Device Management service reference."
        )

    def unarchive_device(self, serial_number: str) -> dict[str, Any]:
        """Unarchive a device. Disabled — see module comment."""
        raise NotImplementedError(
            "unarchive_device is disabled: the previous path "
            "POST /device-inventory/unarchive is not a valid GLP endpoint. "
            "Correct path TBD from GLP Device Management service reference."
        )

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
        limit: int = 300,
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
