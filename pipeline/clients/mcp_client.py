"""Read-only adapter for the central-mcp-server tools.

All reads that go through this client use the MCP server's tool layer,
keeping read operations isolated from the write-path CentralClient.
The MCP server must be running and configured with credentials for the
account being queried.

In unit tests, replace this class with a mock — all public methods
take simple Python types and return plain dicts.
"""

from __future__ import annotations

import logging
from typing import Any, Optional

from pipeline.clients.central_client import CentralClient

logger = logging.getLogger(__name__)


class MCPClient:
    """Thin read-only wrapper around New Central monitoring APIs.

    Uses the same CentralClient HTTP layer but only calls read endpoints,
    mirroring the tools exposed by central-mcp-server.
    """

    def __init__(self, central_client: CentralClient):
        self._client = central_client

    # ------------------------------------------------------------------
    # Devices
    # ------------------------------------------------------------------

    def get_device_by_serial(self, serial_number: str) -> Optional[dict[str, Any]]:
        """Return the device inventory record for a given serial, or None."""
        try:
            result = self._client.get(
                "/network-monitoring/v1alpha1/device-inventory",
                params={"serialNumber": serial_number},
            )
            items = result.get("devices", result.get("items", []))
            return items[0] if items else None
        except Exception as exc:
            logger.warning("MCPClient.get_device_by_serial(%s) failed: %s", serial_number, exc)
            return None

    def get_devices(self, filters: Optional[dict[str, Any]] = None) -> list[dict[str, Any]]:
        """Return a list of device inventory records matching optional filters."""
        try:
            result = self._client.get(
                "/network-monitoring/v1alpha1/device-inventory",
                params=filters or {},
            )
            return result.get("devices", result.get("items", []))
        except Exception as exc:
            logger.warning("MCPClient.get_devices failed: %s", exc)
            return []

    # ------------------------------------------------------------------
    # Sites
    # ------------------------------------------------------------------

    def get_sites(self) -> list[dict[str, Any]]:
        """Return all sites with their IDs."""
        try:
            result = self._client.get("/network-monitoring/v1/sites", params={"limit": 100})
            return result.get("items", result.get("sites", []))
        except Exception as exc:
            logger.warning("MCPClient.get_sites failed: %s", exc)
            return []

    def get_device_scope_id(self, serial_number: str) -> Optional[str]:
        """Return the New Central config-layer scope-id for a device by serial.

        Calls GET /network-config/v1alpha1/devices — the one exception still on v1alpha1
        per the runbook, as it is the only endpoint that includes the scopeId field.
        """
        try:
            # Use the session directly — CentralClient.get() re-encodes pre-built query strings
            from urllib.parse import quote
            filter_str = quote(f"scopeName eq '{serial_number}'")
            url = f"{self._client.base_url}/network-config/v1alpha1/devices?filter={filter_str}"
            self._client._ensure_valid_token()
            resp = self._client.session.get(url)
            resp.raise_for_status()
            items = resp.json().get("items", [])
            return items[0].get("scopeId") if items else None
        except Exception as exc:
            logger.warning("MCPClient.get_device_scope_id(%s) failed: %s", serial_number, exc)
            return None

    def get_site_by_name(self, name: str) -> Optional[dict[str, Any]]:
        """Return a site record by name, or None if not found."""
        sites = self.get_sites()
        for site in sites:
            site_name = site.get("siteName") or site.get("name", "")
            if site_name.lower() == name.lower():
                return site
        return None

    # ------------------------------------------------------------------
    # Alerts
    # ------------------------------------------------------------------

    def get_alerts(
        self,
        site_id: Optional[str] = None,
        severity: Optional[str] = None,
    ) -> list[dict[str, Any]]:
        """Return active alerts, optionally filtered by site or severity."""
        params: dict[str, Any] = {"status": "Active"}
        if site_id:
            params["siteId"] = site_id
        if severity:
            params["severity"] = severity
        try:
            result = self._client.get("/network-notifications/v1/alerts", params=params)
            return result.get("alerts", result.get("items", []))
        except Exception as exc:
            logger.warning("MCPClient.get_alerts failed: %s", exc)
            return []

    # ------------------------------------------------------------------
    # Events
    # ------------------------------------------------------------------

    def get_events(
        self,
        serial_number: str,
        hours: int = 24,
    ) -> list[dict[str, Any]]:
        """Return events for a device within the last N hours."""
        try:
            result = self._client.get(
                "/network-troubleshooting/v1/events",
                params={
                    "contextType": "SWITCH",
                    "contextIdentifier": serial_number,
                    "timeRange": "last_24h" if hours <= 24 else "last_7d",
                },
            )
            return result.get("events", result.get("items", []))
        except Exception as exc:
            logger.warning("MCPClient.get_events(%s) failed: %s", serial_number, exc)
            return []
