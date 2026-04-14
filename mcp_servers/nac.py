"""MCP server — Aruba Central NAC and authentication tools (26 tools).

Covers: Central NAC (CNAC) MAC registrations, Named MPSK registrations, visitor accounts,
RADIUS/auth server profiles, AAA profiles, AAA connectivity testing, authorization policies,
and static classification tags.
Always use dry_run=True first for write operations.

Tools:
  list_mac_registrations     List all MAC auth registrations
  add_mac_registration       Register a MAC address for NAC
  update_mac_registration    Update an existing MAC registration
  delete_mac_registration    Remove a MAC registration
  list_mpsk_registrations    List Named MPSK registrations
  add_mpsk_registration      Create a Named MPSK registration
  delete_mpsk_registration   Remove a Named MPSK registration
  list_visitors              List visitor accounts
  add_visitor                Create a visitor account
  delete_visitor             Remove a visitor account
  list_auth_servers          List RADIUS/auth server profiles
  get_auth_server            Get a single auth server profile by name
  create_auth_server         Create a RADIUS auth server profile
  delete_auth_server         Delete an auth server profile
  list_aaa_profiles          List AAA profiles
  get_aaa_profile            Get a single AAA profile by name
  create_aaa_profile         Create an AAA profile
  delete_aaa_profile         Delete an AAA profile
  test_aaa                   Run an AAA connectivity test from an AP or CX switch (async)
  list_authz_policies        List authorization policies
  get_authz_policy           Get a single authorization policy by ID
  create_authz_policy        Create an authorization policy
  delete_authz_policy        Delete an authorization policy
  list_static_tags           List user-created static classification tags
  create_static_tag          Create a static classification tag
  delete_static_tag          Delete a static classification tag by tag-id
"""
import uuid
from typing import Any

from mcp.server.fastmcp import FastMCP

from mcp_servers.shared import (
    _CX_TROUBLESHOOTING_BASE,
    get_client,
    troubleshoot_async,
)

mcp = FastMCP("aruba-nac")

_CNAC_BASE = "/network-config/v1alpha1"
_AP_TROUBLESHOOTING_BASE = "/network-troubleshooting/v1alpha1/aps"


# ── MAC Registrations ─────────────────────────────────────────────────────────

@mcp.tool()
def list_mac_registrations() -> dict[str, Any]:
    """List all Central NAC MAC address registrations."""
    return get_client().get(f"{_CNAC_BASE}/cnac-mac-reg")


@mcp.tool()
def add_mac_registration(
    mac_address: str,
    display_name: str | None = None,
    tags: list[str] | None = None,
    enable: bool = True,
    dry_run: bool = False,
) -> dict[str, Any]:
    """Register a MAC address for Central NAC authentication. Always dry_run first.

    Args:
        mac_address: MAC address to register (e.g. 'aa:bb:cc:dd:ee:ff').
        display_name: Human-readable label for this registration.
        tags: Optional list of static tag strings.
        enable: Whether the registration is active (default True).
        dry_run: If True, return payload without sending.
    """
    payload: dict[str, Any] = {
        "input": {
            "macAddress": mac_address,
            "enable": enable,
        }
    }
    if display_name:
        payload["input"]["displayName"] = display_name
    if tags:
        payload["input"]["staticTags"] = tags

    if dry_run:
        return {"dry_run": True, "payload": payload}

    client = get_client()
    resp = client._request("POST", f"{_CNAC_BASE}/cnac-mac-reg", json=payload)
    try:
        return resp.json()
    except Exception:
        return {"status_code": resp.status_code, "text": resp.text}


@mcp.tool()
def update_mac_registration(
    registration_id: str,
    mac_address: str,
    display_name: str | None = None,
    tags: list[str] | None = None,
    enable: bool = True,
    dry_run: bool = False,
) -> dict[str, Any]:
    """Update an existing MAC registration by ID. Always dry_run first.

    Args:
        registration_id: The registration ID to update (from list_mac_registrations).
        mac_address: MAC address (required even for update).
        display_name: Human-readable label.
        tags: Static tag strings.
        enable: Whether the registration is active.
        dry_run: If True, return payload without sending.
    """
    payload: dict[str, Any] = {
        "input": {
            "macAddress": mac_address,
            "enable": enable,
        }
    }
    if display_name:
        payload["input"]["displayName"] = display_name
    if tags:
        payload["input"]["staticTags"] = tags

    if dry_run:
        return {"dry_run": True, "registration_id": registration_id, "payload": payload}

    client = get_client()
    resp = client._request("PUT", f"{_CNAC_BASE}/cnac-mac-reg/{registration_id}", json=payload)
    try:
        return resp.json()
    except Exception:
        return {"status_code": resp.status_code, "text": resp.text}


@mcp.tool()
def delete_mac_registration(
    registration_id: str,
    dry_run: bool = False,
) -> dict[str, Any]:
    """Delete a MAC registration by ID. Always dry_run first.

    Args:
        registration_id: ID from list_mac_registrations.
    """
    if dry_run:
        return {"dry_run": True, "registration_id": registration_id}

    client = get_client()
    resp = client._request("DELETE", f"{_CNAC_BASE}/cnac-mac-reg/{registration_id}")
    try:
        return resp.json()
    except Exception:
        return {"status_code": resp.status_code, "text": resp.text}


# ── Named MPSK Registrations ──────────────────────────────────────────────────

@mcp.tool()
def list_mpsk_registrations() -> dict[str, Any]:
    """List all Central NAC Named MPSK registrations."""
    return get_client().get(f"{_CNAC_BASE}/cnac-named-mpsk-reg")


@mcp.tool()
def add_mpsk_registration(
    name: str,
    network: str,
    user_role: str | None = None,
    password_policy: str = "WORDS",
    enable: bool = True,
    dry_run: bool = False,
) -> dict[str, Any]:
    """Create a Named MPSK registration. Always dry_run first.

    Args:
        name: Unique name for this MPSK registration.
        network: SSID/network name this MPSK applies to.
        user_role: Optional role to assign on connection.
        password_policy: Password generation policy — "WORDS" (default) or other supported values.
        enable: Whether registration is active (default True).
        dry_run: If True, return payload without sending.
    """
    payload: dict[str, Any] = {
        "input": {
            "name": name,
            "network": network,
            "passwordPolicy": password_policy,
            "enable": enable,
        }
    }
    if user_role:
        payload["input"]["userRole"] = user_role

    if dry_run:
        return {"dry_run": True, "payload": payload}

    client = get_client()
    resp = client._request("POST", f"{_CNAC_BASE}/cnac-named-mpsk-reg", json=payload)
    try:
        return resp.json()
    except Exception:
        return {"status_code": resp.status_code, "text": resp.text}


@mcp.tool()
def delete_mpsk_registration(
    registration_id: str,
    dry_run: bool = False,
) -> dict[str, Any]:
    """Delete a Named MPSK registration by ID. Always dry_run first.

    Args:
        registration_id: ID from list_mpsk_registrations.
    """
    if dry_run:
        return {"dry_run": True, "registration_id": registration_id}

    client = get_client()
    resp = client._request("DELETE", f"{_CNAC_BASE}/cnac-named-mpsk-reg/{registration_id}")
    try:
        return resp.json()
    except Exception:
        return {"status_code": resp.status_code, "text": resp.text}


# ── Visitor Accounts ──────────────────────────────────────────────────────────

@mcp.tool()
def list_visitors() -> dict[str, Any]:
    """List all Central NAC visitor accounts."""
    return get_client().get(f"{_CNAC_BASE}/cnac-visitor")


@mcp.tool()
def add_visitor(
    display_name: str,
    name: str,
    email: str | None = None,
    phone: str | None = None,
    company_name: str | None = None,
    expire_at: str | None = None,
    enable: bool = True,
    dry_run: bool = False,
) -> dict[str, Any]:
    """Create a Central NAC visitor account. Always dry_run first.

    Args:
        display_name: Display name shown in the portal.
        name: Username / login name for the visitor.
        email: Visitor email address (used for credential delivery).
        phone: Visitor phone number.
        company_name: Company affiliation.
        expire_at: Expiry timestamp in ISO 8601 format (e.g. '2026-05-01T00:00:00Z').
        enable: Whether account is active (default True).
        dry_run: If True, return payload without sending.
    """
    payload: dict[str, Any] = {
        "input": {
            "displayName": display_name,
            "name": name,
            "enable": enable,
        }
    }
    if email:
        payload["input"]["email"] = email
    if phone:
        payload["input"]["phone"] = phone
    if company_name:
        payload["input"]["companyName"] = company_name
    if expire_at:
        payload["input"]["expireAt"] = expire_at

    if dry_run:
        return {"dry_run": True, "payload": payload}

    client = get_client()
    resp = client._request("POST", f"{_CNAC_BASE}/cnac-visitor", json=payload)
    try:
        return resp.json()
    except Exception:
        return {"status_code": resp.status_code, "text": resp.text}


@mcp.tool()
def delete_visitor(
    visitor_id: str,
    dry_run: bool = False,
) -> dict[str, Any]:
    """Delete a visitor account by ID. Always dry_run first.

    Args:
        visitor_id: ID from list_visitors.
    """
    if dry_run:
        return {"dry_run": True, "visitor_id": visitor_id}

    client = get_client()
    resp = client._request("DELETE", f"{_CNAC_BASE}/cnac-visitor/{visitor_id}")
    try:
        return resp.json()
    except Exception:
        return {"status_code": resp.status_code, "text": resp.text}


# ── Auth Server Profiles ──────────────────────────────────────────────────────

@mcp.tool()
def list_auth_servers() -> dict[str, Any]:
    """List all RADIUS/auth server profiles configured in Central."""
    return get_client().get(f"{_CNAC_BASE}/auth-servers")


@mcp.tool()
def get_auth_server(name: str) -> dict[str, Any]:
    """Get a single RADIUS/auth server profile by name.

    Args:
        name: Profile name (as returned by list_auth_servers).
    """
    return get_client().get(f"{_CNAC_BASE}/auth-servers/{name}")


@mcp.tool()
def create_auth_server(
    name: str,
    auth_server_address: str,
    shared_secret: str,
    server_type: str = "RADIUS",
    auth_port: int = 1812,
    acct_port: int = 1813,
    enable: bool = True,
    dry_run: bool = False,
) -> dict[str, Any]:
    """Create a RADIUS auth server profile. Always dry_run first.

    Args:
        name: Unique profile name.
        auth_server_address: IP or hostname of the RADIUS server.
        shared_secret: Shared secret (plaintext — stored securely by Central).
        server_type: Server type — "RADIUS" (default), "RADSEC", "LDAP", "TACACS".
        auth_port: Authentication port (default 1812).
        acct_port: Accounting port (default 1813).
        enable: Whether profile is active (default True).
        dry_run: If True, return payload without sending.
    """
    payload: dict[str, Any] = {
        "name": name,
        "type": server_type,
        "auth-server-address": auth_server_address,
        "auth-port": auth_port,
        "acct-port": acct_port,
        "enable": str(enable).lower(),
        "shared-secret-config": {
            "secret-type": "PLAIN_TEXT",
            "plaintext-value": shared_secret,
        },
    }

    if dry_run:
        # Mask secret in dry-run output
        safe = {**payload, "shared-secret-config": {"secret-type": "PLAIN_TEXT", "plaintext-value": "***"}}
        return {"dry_run": True, "name": name, "payload": safe}

    client = get_client()
    resp = client._request("POST", f"{_CNAC_BASE}/auth-servers/{name}", json=payload)
    try:
        return resp.json()
    except Exception:
        return {"status_code": resp.status_code, "text": resp.text}


@mcp.tool()
def delete_auth_server(
    name: str,
    dry_run: bool = False,
) -> dict[str, Any]:
    """Delete a RADIUS/auth server profile by name. Always dry_run first.

    Args:
        name: Profile name (as returned by list_auth_servers).
    """
    if dry_run:
        return {"dry_run": True, "name": name}

    client = get_client()
    resp = client._request("DELETE", f"{_CNAC_BASE}/auth-servers/{name}")
    try:
        return resp.json()
    except Exception:
        return {"status_code": resp.status_code, "text": resp.text}


# ── AAA Profiles ──────────────────────────────────────────────────────────────

@mcp.tool()
def list_aaa_profiles() -> dict[str, Any]:
    """List all AAA profiles configured in Central."""
    return get_client().get(f"{_CNAC_BASE}/aaa-profile")


@mcp.tool()
def get_aaa_profile(name: str) -> dict[str, Any]:
    """Get a single AAA profile by name.

    Args:
        name: Profile name (as returned by list_aaa_profiles).
    """
    return get_client().get(f"{_CNAC_BASE}/aaa-profile/{name}")


@mcp.tool()
def create_aaa_profile(
    name: str,
    auth_role: str | None = None,
    fallback_role: str | None = None,
    acct_server_group: str | None = None,
    description: str | None = None,
    dry_run: bool = False,
) -> dict[str, Any]:
    """Create an AAA profile. Always dry_run first.

    Args:
        name: Unique profile name.
        auth_role: Role assigned after successful authentication.
        fallback_role: Role assigned when auth server is unreachable.
        acct_server_group: Accounting server group name.
        description: Optional description.
        dry_run: If True, return payload without sending.
    """
    payload: dict[str, Any] = {"name": name}
    if description:
        payload["description"] = description
    if auth_role or fallback_role:
        payload["authorization"] = {}
        if auth_role:
            payload["authorization"]["auth-role"] = auth_role
        if fallback_role:
            payload["authorization"]["fallback-role"] = fallback_role
    if acct_server_group:
        payload["acct-server-group"] = acct_server_group

    if dry_run:
        return {"dry_run": True, "name": name, "payload": payload}

    client = get_client()
    resp = client._request("POST", f"{_CNAC_BASE}/aaa-profile/{name}", json=payload)
    try:
        return resp.json()
    except Exception:
        return {"status_code": resp.status_code, "text": resp.text}


@mcp.tool()
def delete_aaa_profile(
    name: str,
    dry_run: bool = False,
) -> dict[str, Any]:
    """Delete an AAA profile by name. Always dry_run first.

    Args:
        name: Profile name (as returned by list_aaa_profiles).
    """
    if dry_run:
        return {"dry_run": True, "name": name}

    client = get_client()
    resp = client._request("DELETE", f"{_CNAC_BASE}/aaa-profile/{name}")
    try:
        return resp.json()
    except Exception:
        return {"status_code": resp.status_code, "text": resp.text}


# ── AAA Test ──────────────────────────────────────────────────────────────────

@mcp.tool()
def test_aaa(
    serial_number: str,
    username: str,
    password: str,
    device_type: str = "AP",
    server_name: str | None = None,
    radius_server_ip: str | None = None,
    auth_method: str = "chap",
) -> dict[str, Any]:
    """Run an AAA connectivity test from an AP or CX switch (async, polls ~60s).

    Tests that a device can reach and authenticate against a RADIUS server.

    Args:
        serial_number: Device serial number.
        username: Test username to authenticate with.
        password: Test password.
        device_type: "AP" (default) or "CX".
        server_name: Auth server profile name — required for AP tests.
        radius_server_ip: RADIUS server IP — required for CX tests.
        auth_method: Auth method for CX tests — "chap" (default) or "pap".

    Returns:
        Async task result with status and output from the device.
    """
    errors: list[str] = []
    client = get_client()

    if device_type.upper() == "AP":
        if not server_name:
            return {"status": None, "errors": ["server_name is required for AP AAA tests"]}
        endpoint = f"{_AP_TROUBLESHOOTING_BASE}/{serial_number}/aaa"
        payload: dict[str, Any] = {
            "serverName": server_name,
            "username": username,
            "password": password,
        }
    else:
        if not radius_server_ip:
            return {"status": None, "errors": ["radius_server_ip is required for CX AAA tests"]}
        endpoint = f"{_CX_TROUBLESHOOTING_BASE}/{serial_number}/aaa"
        payload = {
            "authMethodType": auth_method,
            "radiusServerIp": radius_server_ip,
            "username": username,
            "password": password,
        }

    return troubleshoot_async(client, endpoint, payload, errors)


# ── Authz Policies ────────────────────────────────────────────────────────────

@mcp.tool()
def list_authz_policies() -> dict[str, Any]:
    """List all CNAC authorization policies (tag/category → role mappings)."""
    return get_client().get(f"{_CNAC_BASE}/authz-policies")


@mcp.tool()
def get_authz_policy(policy_id: str) -> dict[str, Any]:
    """Get a single CNAC authz policy by ID.

    Args:
        policy_id: Policy ID (from list_authz_policies).
    """
    return get_client().get(f"{_CNAC_BASE}/authz-policies/{policy_id}")


@mcp.tool()
def create_authz_policy(
    policy_name: str,
    rule_name: str,
    tag_id: str,
    role: str,
    position: int = 1,
    dry_run: bool = False,
) -> dict[str, Any]:
    """Create a CNAC authz policy that assigns a role to devices with a given tag. Always dry_run first.

    This is the API backing the GUI's "Client Classification → tag → role" mapping.
    When a device is classified and tagged, it receives the specified role.
    Both the policy UUID and all condition/rule UUIDs are auto-generated.

    Args:
        policy_name: Human-readable name for the policy (e.g. 'AV-Policy').
        rule_name: Human-readable name for the rule inside the policy (e.g. 'AV-Rule').
        tag_id: The UUID of the static tag to match — get this from create_static_tag or list_static_tags.
        role: Wireless role to assign when the tag matches (e.g. 'AV-Role').
        position: Policy priority (lower = higher priority, default 1). Must be unique across policies.
        dry_run: If True, return the payload without sending.

    Returns:
        API response or dry-run payload. Includes generated policy_id UUID.
    """
    policy_id = str(uuid.uuid4())
    payload: dict[str, Any] = {
        "name": policy_name,
        "position": position,
        "rule": [
            {
                "position": 1,
                "rule-id": str(uuid.uuid4()),
                "rule-name": rule_name,
                "enable": True,
                "conditions": {
                    "combinator-operator": "COMB_OP_AND",
                    "condition": [
                        {
                            "position": 1,
                            "condition-id": str(uuid.uuid4()),
                            "combinator-operator": "COMB_OP_AND",
                            "condition-group": [
                                {
                                    "position": 1,
                                    "condition-group-id": str(uuid.uuid4()),
                                    "attr": "TAGS",
                                    "operator": "OP_CONTAINS_ELEM",
                                    "value": tag_id,
                                }
                            ],
                        }
                    ],
                },
                "enf-profile": [
                    {
                        "profile-id": str(uuid.uuid4()),
                        "type": "ENF_RADIUS",
                        "radius-profile": {
                            "defined-attr": [
                                {
                                    "attr-name": "ATTR_ARUBA_ROLE",
                                    "value": role,
                                }
                            ]
                        },
                    }
                ],
            }
        ],
    }

    if dry_run:
        return {"dry_run": True, "policy_id": policy_id, "policy_name": policy_name, "payload": payload}

    client = get_client()
    resp = client._request("POST", f"{_CNAC_BASE}/authz-policies/{policy_id}", json=payload)
    try:
        result = resp.json()
    except Exception:
        result = {"status_code": resp.status_code, "text": resp.text}
    result["policy_id"] = policy_id
    return result


@mcp.tool()
def delete_authz_policy(
    policy_id: str,
    dry_run: bool = False,
) -> dict[str, Any]:
    """Delete a CNAC authz policy by ID. Always dry_run first.

    Args:
        policy_id: Policy ID (from list_authz_policies).
    """
    if dry_run:
        return {"dry_run": True, "policy_id": policy_id}

    client = get_client()
    resp = client._request("DELETE", f"{_CNAC_BASE}/authz-policies/{policy_id}")
    try:
        return resp.json()
    except Exception:
        return {"status_code": resp.status_code, "text": resp.text}


_STATIC_TAG_BASE = "/network-config/v1alpha1/static-tag"


@mcp.tool()
def list_static_tags() -> dict[str, Any]:
    """List all user-created static classification tags.

    Returns a dict with a 'tag' list. Each entry has 'tag-id' (UUID) and 'name'.
    Note: system-generated tags (e.g. IoT) are not returned by this endpoint.
    """
    return get_client().get(_STATIC_TAG_BASE)


@mcp.tool()
def create_static_tag(
    name: str,
    dry_run: bool = False,
) -> dict[str, Any]:
    """Create a static classification tag for client classification.

    Tags are used in authz policies to assign roles to classified clients.
    tag-id is auto-generated as a UUID.

    Args:
        name: Display name for the tag (e.g. 'AV', 'IoT', 'Console').
        dry_run: If True, return the payload without sending.
    """
    tag_id = str(uuid.uuid4())
    payload = {"name": name}
    if dry_run:
        return {"payload": payload, "tag_id": tag_id, "url": f"{_STATIC_TAG_BASE}/{tag_id}"}
    client = get_client()
    resp = client._request("POST", f"{_STATIC_TAG_BASE}/{tag_id}", json=payload)
    try:
        result = resp.json()
    except Exception:
        result = {"status_code": resp.status_code, "text": resp.text}
    result["tag_id"] = tag_id
    return result


@mcp.tool()
def delete_static_tag(tag_id: str) -> dict[str, Any]:
    """Delete a static classification tag by its UUID tag-id.

    Args:
        tag_id: The UUID of the tag to delete (from list_static_tags).
    """
    client = get_client()
    resp = client._request("DELETE", f"{_STATIC_TAG_BASE}/{tag_id}")
    try:
        return resp.json()
    except Exception:
        return {"status_code": resp.status_code, "text": resp.text}


if __name__ == "__main__":
    mcp.run()
