"""Aruba Central REST API client.

Wraps HTTP calls with automatic token refresh and 429 retry/backoff.
Also provides a pycentral v2 NewCentralBase accessor for scopes/provisioning calls
that are not yet directly available via raw REST.

Ported from aruba-central-portal/utils/central_api_client.py.
"""

from __future__ import annotations

import logging
import time
from typing import Any, Optional

import requests

from pipeline.clients.token_manager import TokenManager

logger = logging.getLogger(__name__)

_INITIAL_RETRY_DELAY = 60  # seconds — Central rate-limit window
_MAX_RETRY_DELAY = 300


class CentralClient:
    """HTTP client for Aruba Central REST APIs with token refresh and retry."""

    def __init__(
        self,
        base_url: str,
        token_manager: TokenManager,
    ):
        self.base_url = base_url.rstrip("/")
        self.token_manager = token_manager
        self.session = requests.Session()
        self.session.headers.update({"Content-Type": "application/json"})
        self._refresh_auth_header()

    def _refresh_auth_header(self) -> None:
        token = self.token_manager.get_access_token()
        self.session.headers.update({"Authorization": f"Bearer {token}"})

    def _ensure_valid_token(self) -> None:
        self._refresh_auth_header()

    def _request(
        self,
        method: str,
        endpoint: str,
        max_retries: int = 3,
        **kwargs: Any,
    ) -> requests.Response:
        url = f"{self.base_url}{endpoint}"
        retry_delay = _INITIAL_RETRY_DELAY

        for attempt in range(max_retries + 1):
            self._ensure_valid_token()
            response = self.session.request(method, url, **kwargs)

            if response.status_code == 429 and attempt < max_retries:
                logger.warning(
                    "Rate limit (429) on %s %s — waiting %ds (attempt %d/%d)",
                    method,
                    url,
                    retry_delay,
                    attempt + 1,
                    max_retries,
                )
                time.sleep(retry_delay)
                retry_delay = min(int(retry_delay * 1.5), _MAX_RETRY_DELAY)
                continue

            return response

        return response  # last response after all retries

    def get(self, endpoint: str, params: Optional[dict[str, Any]] = None) -> dict[str, Any]:
        logger.debug("GET %s%s params=%s", self.base_url, endpoint, params)
        response = self._request("GET", endpoint, params=params)
        response.raise_for_status()
        return _parse_json(response)

    def post(
        self,
        endpoint: str,
        data: Optional[dict[str, Any]] = None,
        params: Optional[dict[str, Any]] = None,
    ) -> dict[str, Any]:
        logger.debug("POST %s%s", self.base_url, endpoint)
        response = self._request("POST", endpoint, json=data, params=params)
        response.raise_for_status()
        return _parse_json(response)

    def patch(
        self,
        endpoint: str,
        data: Optional[dict[str, Any]] = None,
        params: Optional[dict[str, Any]] = None,
    ) -> dict[str, Any]:
        logger.debug("PATCH %s%s", self.base_url, endpoint)
        response = self._request("PATCH", endpoint, json=data, params=params)
        response.raise_for_status()
        return _parse_json(response)

    def put(
        self,
        endpoint: str,
        data: Optional[dict[str, Any]] = None,
        params: Optional[dict[str, Any]] = None,
    ) -> dict[str, Any]:
        logger.debug("PUT %s%s", self.base_url, endpoint)
        response = self._request("PUT", endpoint, json=data, params=params)
        response.raise_for_status()
        return _parse_json(response)

    def delete(
        self,
        endpoint: str,
        params: Optional[dict[str, Any]] = None,
    ) -> dict[str, Any]:
        logger.debug("DELETE %s%s", self.base_url, endpoint)
        response = self._request("DELETE", endpoint, params=params)
        response.raise_for_status()
        return _parse_json(response)

    # ------------------------------------------------------------------
    # pycentral v2 accessor
    # ------------------------------------------------------------------

    def get_pycentral_conn(self) -> Any:
        """Return a pycentral v2 NewCentralBase connection object.

        Used for pycentral.scopes and provisioning status checks that wrap
        the Central APIs with convenience helpers.
        """
        try:
            from pycentral import NewCentralBase  # type: ignore[import]
        except ImportError as exc:
            raise RuntimeError(
                "pycentral is not installed. Run: pip install --pre pycentral"
            ) from exc

        token = self.token_manager.get_access_token()
        conn = NewCentralBase(
            token_info={
                "new_central": {
                    "base_url": self.base_url,
                    "access_token": token,
                }
            }
        )
        return conn


def _parse_json(response: requests.Response) -> dict[str, Any]:
    if not response.text or not response.text.strip():
        return {}
    try:
        result = response.json()
        return result if isinstance(result, dict) else {"items": result}
    except ValueError as exc:
        logger.error("Failed to parse JSON: %s — body: %s", exc, response.text[:500])
        return {}
