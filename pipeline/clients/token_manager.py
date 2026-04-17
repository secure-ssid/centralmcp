"""OAuth2 token manager for HPE Aruba Central / GLP APIs.

Ported from aruba-central-portal/utils/token_manager.py with support for
a cache_key parameter so source and target accounts use independent caches.
"""

from __future__ import annotations

import json
import logging
import os
import time
from datetime import datetime
from pathlib import Path
from typing import Optional

import requests

logger = logging.getLogger(__name__)

# Default buffer before expiry at which we proactively refresh (seconds).
# Central tokens live ~120 min so 300s buffer is fine; GLP tokens live only
# 15 min so callers should pass a smaller buffer (60-90s) to avoid burning
# a third of every token window. See:
# https://developer.greenlake.hpe.com/docs/greenlake/guides/public/authentication/authentication/
_DEFAULT_EXPIRY_BUFFER = 300


def _default_cache_dir() -> Path:
    """Default token cache directory. Avoids CWD so tokens don't leak
    into whatever directory the MCP server happens to run from."""
    override = os.environ.get("TOKEN_CACHE_DIR")
    if override:
        return Path(override).expanduser()
    xdg = os.environ.get("XDG_CACHE_HOME")
    base = Path(xdg).expanduser() if xdg else Path.home() / ".cache"
    return base / "centralmcp"


class TokenManager:
    """Manages OAuth2 client-credentials tokens with file-based caching."""

    def __init__(
        self,
        client_id: str,
        client_secret: str,
        token_url: str = "https://sso.common.cloud.hpe.com/as/token.oauth2",
        cache_key: str = "central",
        expiry_buffer: int = _DEFAULT_EXPIRY_BUFFER,
    ):
        """
        Args:
            client_id: OAuth2 client ID.
            client_secret: OAuth2 client secret.
            token_url: Token endpoint URL.
            cache_key: Unique key used to name the cache file, e.g. "source" or "target".
                       Produces .token_cache_{cache_key}.json so source/target caches
                       never collide.
            expiry_buffer: Seconds before the token's stated expiry to refresh
                       proactively. Default 300s is right for Central's 120-min
                       tokens. For GLP (15-min tokens), pass 60-90s.
        """
        self.client_id = client_id
        self.client_secret = client_secret
        self.token_url = token_url
        self.expiry_buffer = expiry_buffer

        cache_filename = f".token_cache_{cache_key}.json"
        cache_dir = _default_cache_dir()
        try:
            cache_dir.mkdir(parents=True, exist_ok=True)
            self.cache_file = cache_dir / cache_filename
        except Exception as exc:
            # Fall back to CWD only if the preferred dir is unwritable, and
            # shout about it so the operator notices.
            logger.warning(
                "Could not create token cache dir %s (%s); falling back to CWD",
                cache_dir,
                exc,
            )
            self.cache_file = Path(cache_filename)

        self.access_token: Optional[str] = None
        self.token_expires_at: Optional[float] = None
        self._load_cached_token()

    def _load_cached_token(self) -> None:
        if not self.cache_file.exists():
            return
        try:
            with open(self.cache_file) as f:
                data = json.load(f)
            self.access_token = data.get("access_token")
            self.token_expires_at = data.get("expires_at")
            if self.token_expires_at and time.time() < (self.token_expires_at - self.expiry_buffer):
                logger.debug("Loaded valid token from cache (%s)", self.cache_file)
            else:
                logger.debug("Cached token expired (%s)", self.cache_file)
                self.access_token = None
                self.token_expires_at = None
        except Exception as exc:
            logger.warning("Failed to load token cache %s: %s", self.cache_file, exc)
            self.access_token = None
            self.token_expires_at = None

    def _save_token_to_cache(self) -> None:
        try:
            # Write with 0600 perms so tokens aren't world-readable.
            fd = os.open(
                self.cache_file,
                os.O_WRONLY | os.O_CREAT | os.O_TRUNC,
                0o600,
            )
            with os.fdopen(fd, "w") as f:
                json.dump(
                    {
                        "access_token": self.access_token,
                        "expires_at": self.token_expires_at,
                        "cached_at": time.time(),
                    },
                    f,
                    indent=2,
                )
        except Exception as exc:
            logger.warning("Failed to save token cache: %s", exc)

    def _refresh_token(self) -> None:
        logger.info("Refreshing token (url=%s)", self.token_url)
        try:
            response = requests.post(
                self.token_url,
                data={
                    "grant_type": "client_credentials",
                    "client_id": self.client_id,
                    "client_secret": self.client_secret,
                },
                headers={"Content-Type": "application/x-www-form-urlencoded"},
                timeout=30,
            )
            response.raise_for_status()
            token_data = response.json()
            self.access_token = token_data["access_token"]
            expires_in = token_data.get("expires_in", 7200)
            self.token_expires_at = time.time() + expires_in
            self._save_token_to_cache()
            logger.info(
                "Token refreshed. Expires at %s",
                datetime.fromtimestamp(self.token_expires_at).strftime("%Y-%m-%d %H:%M:%S"),
            )
        except requests.RequestException as exc:
            raise RuntimeError(f"Token refresh failed: {exc}") from exc

    def get_access_token(self, force_refresh: bool = False) -> str:
        needs_refresh = (
            force_refresh
            or not self.access_token
            or not self.token_expires_at
            or time.time() >= (self.token_expires_at - self.expiry_buffer)
        )
        if needs_refresh:
            self._refresh_token()
        return self.access_token  # type: ignore[return-value]

    def is_token_valid(self) -> bool:
        if not self.access_token or not self.token_expires_at:
            return False
        return time.time() < (self.token_expires_at - self.expiry_buffer)
