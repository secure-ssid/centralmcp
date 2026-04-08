"""Load credentials and build AccountContext objects for source and target accounts."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import yaml
from dotenv import load_dotenv

from pipeline.models import AccountContext


def load_credentials(creds_path: str = "config/credentials.yaml") -> dict[str, Any]:
    """Load credentials from YAML + environment variable overrides.

    Environment variables always win over YAML values.
    """
    load_dotenv(override=True)

    config: dict[str, Any] = {}
    creds_file = Path(creds_path)
    if creds_file.exists():
        with open(creds_file) as f:
            config = yaml.safe_load(f) or {}

    def _get(section: str, key: str, env_var: str, default: str = "") -> str:
        return os.getenv(env_var) or config.get(section, {}).get(key, default)

    glp_section = config.get("glp", {})

    return {
        "source": {
            "base_url": _get("source_account", "base_url", "SOURCE_BASE_URL"),
            "client_id": _get("source_account", "client_id", "SOURCE_CLIENT_ID"),
            "client_secret": _get("source_account", "client_secret", "SOURCE_CLIENT_SECRET"),
            "glp_workspace_id": _get("source_account", "glp_workspace_id", "SOURCE_GLP_WORKSPACE"),
        },
        "target": {
            "base_url": _get("target_account", "base_url", "TARGET_BASE_URL"),
            "client_id": _get("target_account", "client_id", "TARGET_CLIENT_ID"),
            "client_secret": _get("target_account", "client_secret", "TARGET_CLIENT_SECRET"),
            "glp_workspace_id": _get("target_account", "glp_workspace_id", "TARGET_GLP_WORKSPACE"),
        },
        "glp": {
            "token_url": os.getenv(
                "GLP_TOKEN_URL",
                glp_section.get("token_url", "https://sso.common.cloud.hpe.com/as/token.oauth2"),
            ),
            "base_url": os.getenv(
                "GLP_BASE_URL",
                glp_section.get("base_url", "https://global.api.greenlake.hpe.com"),
            ),
        },
    }


def build_account_contexts(creds_path: str = "config/credentials.yaml") -> tuple[AccountContext, AccountContext]:
    """Build source and target AccountContext from credentials.

    Returns:
        (source_context, target_context)
    """
    creds = load_credentials(creds_path)

    source = AccountContext(
        label="source",
        base_url=creds["source"]["base_url"],
        client_id=creds["source"]["client_id"],
        client_secret=creds["source"]["client_secret"],
        glp_workspace_id=creds["source"]["glp_workspace_id"],
    )

    target = AccountContext(
        label="target",
        base_url=creds["target"]["base_url"],
        client_id=creds["target"]["client_id"],
        client_secret=creds["target"]["client_secret"],
        glp_workspace_id=creds["target"]["glp_workspace_id"],
    )

    return source, target
