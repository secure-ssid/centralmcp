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
                "/device-management/v1/devices",
                params={"filter": f"serial eq '{serial_number}'"},
            )
            items = result.get("items", result.get("devices", []))
            return items[0] if items else None
        except Exception as exc:
            logger.warning("GLP get_device failed for %s: %s", serial_number, exc)
            return None

    def add_device(self, serial_number: str, mac_address: Optional[str] = None) -> str:
        """Add a device to the GLP workspace. Returns task_id for polling."""
        body: dict[str, Any] = {"serialNumber": serial_number}
        if mac_address:
            body["macAddress"] = mac_address
        result = self._client.post("/device-management/v1/devices", data=body)
        task_id = result.get("taskId") or result.get("task_id", "")
        logger.info("GLP add_device %s → taskId=%s", serial_number, task_id)
        return task_id

    def poll_task(
        self,
        task_id: str,
        timeout: int = _TASK_POLL_TIMEOUT,
        interval: int = _TASK_POLL_INTERVAL,
    ) -> dict[str, Any]:
        """Poll a GLP async task until completion or timeout.

        Returns the final task response dict.
        Raises RuntimeError on timeout or task failure.
        """
        deadline = time.time() + timeout
        while time.time() < deadline:
            result = self._client.get(f"/device-management/v1/tasks/{task_id}")
            status = result.get("status", "").lower()
            logger.debug("GLP task %s status=%s", task_id, status)
            if status in ("completed", "success"):
                return result
            if status in ("failed", "error"):
                raise RuntimeError(f"GLP task {task_id} failed: {result}")
            time.sleep(interval)
        raise RuntimeError(f"GLP task {task_id} timed out after {timeout}s")

    # ------------------------------------------------------------------
    # Subscriptions
    # ------------------------------------------------------------------

    def assign_subscription(self, serial_number: str, subscription_key: str) -> dict[str, Any]:
        """Assign a subscription to a device."""
        return self._client.post(
            "/license/assign",
            data={"serials": [serial_number], "license_type": subscription_key},
        )

    def unassign_subscription(self, serial_number: str) -> dict[str, Any]:
        """Remove all subscriptions from a device."""
        return self._client.post(
            "/license/unassign",
            data={"serials": [serial_number]},
        )

    # ------------------------------------------------------------------
    # Device inventory (Classic Central API, still used in New Central flows)
    # ------------------------------------------------------------------

    def archive_device(self, serial_number: str) -> dict[str, Any]:
        """Archive a device in the source Central account (removes from Central, stays in GLP)."""
        return self._client.post(
            "/device-inventory/archive",
            data={"serials": [serial_number]},
        )

    def unarchive_device(self, serial_number: str) -> dict[str, Any]:
        """Unarchive a device (returns it to GLP unassigned state)."""
        return self._client.post(
            "/device-inventory/unarchive",
            data={"serials": [serial_number]},
        )
