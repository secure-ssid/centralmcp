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
        """Return the device inventory record for a given serial, or None.

        Note: the device-inventory API does not filter server-side by serialNumber,
        so we fetch all devices and filter client-side.
        """
        try:
            result = self._client.get(
                "/network-monitoring/v1alpha1/device-inventory",
                params={"limit": 200},
            )
            items = result.get("devices", result.get("items", []))
            for item in items:
                if item.get("serialNumber", "").lower() == serial_number.lower():
                    return item
            return None
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
            result = self._client.get("/network-config/v1/sites")
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
        """Return a site record by name, or None if not found.

        New Central sites use 'scopeName' as the human-readable name field.
        """
        sites = self.get_sites()
        for site in sites:
            site_name = site.get("scopeName") or site.get("siteName") or site.get("name", "")
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

    # ------------------------------------------------------------------
    # Clients
    # ------------------------------------------------------------------

    def get_clients(
        self,
        site_id: Optional[str] = None,
        serial_number: Optional[str] = None,
    ) -> list[dict[str, Any]]:
        """Return connected clients, optionally filtered by site or device serial."""
        params: dict[str, Any] = {"limit": 100}
        if site_id:
            params["siteId"] = site_id
        if serial_number:
            params["serial"] = serial_number
        try:
            result = self._client.get("/network-monitoring/v1/clients", params=params)
            return result.get("clients", result.get("items", []))
        except Exception as exc:
            logger.warning("MCPClient.get_clients failed: %s", exc)
            return []

    def find_client(self, mac_or_ip: str) -> Optional[dict[str, Any]]:
        """Find a single client by MAC address or IP address, or None if not found.

        Note: the clients API does not filter server-side by macAddress or ipAddress,
        so we fetch all clients and filter client-side.
        """
        try:
            result = self._client.get(
                "/network-monitoring/v1/clients",
                params={"limit": 100},
            )
            items = result.get("clients", result.get("items", []))
            normalized = mac_or_ip.lower()
            for client in items:
                if client.get("macAddress", "").lower() == normalized:
                    return client
                if client.get("ipv4", "").lower() == normalized:
                    return client
            return None
        except Exception as exc:
            logger.warning("MCPClient.find_client(%s) failed: %s", mac_or_ip, exc)
            return None

    # ------------------------------------------------------------------
    # Gateway Clusters
    # ------------------------------------------------------------------

    def get_gw_clusters(self) -> list[dict[str, Any]]:
        """Return unique gateway clusters by scanning /network-config/v1/overlay-wlan."""
        try:
            result = self._client.get("/network-config/v1/overlay-wlan")
            seen: dict[str, dict[str, Any]] = {}
            for profile in result.get("ssid-cluster", []):
                for entry in profile.get("gw-cluster-list", []):
                    cluster_name = entry.get("cluster")
                    if cluster_name and cluster_name not in seen:
                        seen[cluster_name] = {
                            "cluster": cluster_name,
                            "cluster-scope-id": entry.get("cluster-scope-id"),
                            "cluster-type": entry.get("cluster-type"),
                            "tunnel-type": entry.get("tunnel-type"),
                        }
            return list(seen.values())
        except Exception as exc:
            logger.warning("MCPClient.get_gw_clusters failed: %s", exc)
            return []
