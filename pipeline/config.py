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

    # Back-compat for clearer YAML key names. The codebase's original
    # "source_account" / "target_account" names are a holdover from the
    # cross-account migration pipeline. For MCP-only use the clearer
    # "central_account" (for the Central/source creds) and "glp_account"
    # (for the GLP/target creds) keys are accepted as aliases. If both
    # are present in a file, the legacy key wins so existing installs
    # aren't surprised by a rename.
    _SECTION_ALIASES = {
        "source_account": ("central_account",),
        "target_account": ("glp_account",),
    }

    def _section(name: str) -> dict[str, Any]:
        sect = config.get(name)
        if sect:
            return sect
        for alias in _SECTION_ALIASES.get(name, ()):
            alias_sect = config.get(alias)
            if alias_sect:
                return alias_sect
        return {}

    def _get(section: str, key: str, env_var: str, default: str = "") -> str:
        # env var wins; then the canonical section name; then any alias.
        env_val = os.getenv(env_var)
        if env_val:
            return env_val
        return _section(section).get(key, default)

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
            "token_url": os.getenv("GLP_TOKEN_URL", glp_section.get("token_url", "")),
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
    glp_base_url = creds["glp"]["base_url"].rstrip("/")

    def _glp_token_url(workspace_id: str) -> str:
        configured = creds["glp"]["token_url"].strip()
        if configured:
            return configured
        if not workspace_id:
            return ""
        return f"{glp_base_url}/authorization/v2/oauth2/{workspace_id}/token"

    source = AccountContext(
        label="source",
        base_url=creds["source"]["base_url"],
        client_id=creds["source"]["client_id"],
        client_secret=creds["source"]["client_secret"],
        glp_workspace_id=creds["source"]["glp_workspace_id"],
        glp_token_url=_glp_token_url(creds["source"]["glp_workspace_id"]),
        glp_base_url=glp_base_url,
    )

    target = AccountContext(
        label="target",
        base_url=creds["target"]["base_url"],
        client_id=creds["target"]["client_id"],
        client_secret=creds["target"]["client_secret"],
        glp_workspace_id=creds["target"]["glp_workspace_id"],
        glp_token_url=_glp_token_url(creds["target"]["glp_workspace_id"]),
        glp_base_url=glp_base_url,
    )

    return source, target
