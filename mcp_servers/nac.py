"""MCP server — Aruba Central NAC and authentication tools (31 tools).

Covers: CNAC MAC registrations, Named MPSK registrations, visitor accounts,
RADIUS/auth server profiles, AAA profiles, AAA connectivity testing, authorization
policies, and static classification tags.
"""
import uuid
from typing import Any

from mcp.server.fastmcp import FastMCP

from mcp_servers.shared import (
    _CX_TROUBLESHOOTING_BASE,
    bound_collection_response,
    get_client,
    resp_json,
    troubleshoot_async,
)

mcp = FastMCP("aruba-nac")

_CNAC_BASE = "/network-config/v1alpha1"
_AP_TROUBLESHOOTING_BASE = "/network-troubleshooting/v1alpha1/aps"


# ── MAC Registrations ─────────────────────────────────────────────────────────

@mcp.tool()
def list_mac_registrations(
    limit: int = 50,
    offset: int = 0,
    full_list: bool = False,
) -> dict[str, Any]:
    """List Central NAC MAC address registrations (bounded by default).

    Args:
        limit: Max rows to return (1–200, default 50).
        offset: Skip this many rows (pagination).
        full_list: If True, return the full API JSON without slicing (may be large).
    """
    data = get_client().get(f"{_CNAC_BASE}/cnac-mac-reg")
    if full_list:
        return data
    return bound_collection_response(data, limit=limit, offset=offset)


@mcp.tool()
def add_mac_registration(
    mac_address: str,
    display_name: str | None = None,
    tags: list[str] | None = None,
    enable: bool = True,
    dry_run: bool = False,
) -> dict[str, Any]:
    """Register a MAC address for Central NAC authentication.

    Args:
        mac_address: MAC address to register (e.g. 'aa:bb:cc:dd:ee:ff').
        display_name: Human-readable label.
        tags: Optional static tag strings.
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
    return resp_json(resp)


@mcp.tool()
def update_mac_registration(
    registration_id: str,
    mac_address: str,
    display_name: str | None = None,
    tags: list[str] | None = None,
    enable: bool = True,
    dry_run: bool = False,
) -> dict[str, Any]:
    """Update an existing MAC registration by ID.

    Args:
        registration_id: From list_mac_registrations.
        mac_address: Required even for updates.
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
    return resp_json(resp)


@mcp.tool()
def delete_mac_registration(
    registration_id: str,
    dry_run: bool = False,
) -> dict[str, Any]:
    """Delete a MAC registration by ID (from list_mac_registrations)."""
    if dry_run:
        return {"dry_run": True, "registration_id": registration_id}

    client = get_client()
    resp = client._request("DELETE", f"{_CNAC_BASE}/cnac-mac-reg/{registration_id}")
    return resp_json(resp)


# ── Named MPSK Registrations ──────────────────────────────────────────────────

@mcp.tool()
def list_mpsk_registrations(
    limit: int = 50,
    offset: int = 0,
    full_list: bool = False,
) -> dict[str, Any]:
    """List Central NAC Named MPSK registrations (bounded by default)."""
    data = get_client().get(f"{_CNAC_BASE}/cnac-named-mpsk-reg")
    if full_list:
        return data
    return bound_collection_response(data, limit=limit, offset=offset)


@mcp.tool()
def add_mpsk_registration(
    name: str,
    network: str,
    user_role: str | None = None,
    password_policy: str = "WORDS",
    enable: bool = True,
    dry_run: bool = False,
) -> dict[str, Any]:
    """Create a Named MPSK registration.

    Args:
        name: Unique name.
        network: SSID this MPSK applies to.
        user_role: Optional role to assign on connection.
        password_policy: "WORDS" (default) or other supported values.
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
    return resp_json(resp)


@mcp.tool()
def delete_mpsk_registration(
    registration_id: str,
    dry_run: bool = False,
) -> dict[str, Any]:
    """Delete a Named MPSK registration by ID (from list_mpsk_registrations)."""
    if dry_run:
        return {"dry_run": True, "registration_id": registration_id}

    client = get_client()
    resp = client._request("DELETE", f"{_CNAC_BASE}/cnac-named-mpsk-reg/{registration_id}")
    return resp_json(resp)


# ── Visitor Accounts ──────────────────────────────────────────────────────────

@mcp.tool()
def list_visitors(
    limit: int = 50,
    offset: int = 0,
    full_list: bool = False,
) -> dict[str, Any]:
    """List Central NAC visitor accounts (bounded by default)."""
    data = get_client().get(f"{_CNAC_BASE}/cnac-visitor")
    if full_list:
        return data
    return bound_collection_response(data, limit=limit, offset=offset)


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
    """Create a Central NAC visitor account.

    Args:
        display_name: Display name shown in portal.
        name: Login username.
        expire_at: ISO 8601 expiry (e.g. '2026-05-01T00:00:00Z').
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
    return resp_json(resp)


@mcp.tool()
def delete_visitor(
    visitor_id: str,
    dry_run: bool = False,
) -> dict[str, Any]:
    """Delete a visitor account by ID (from list_visitors)."""
    if dry_run:
        return {"dry_run": True, "visitor_id": visitor_id}

    client = get_client()
    resp = client._request("DELETE", f"{_CNAC_BASE}/cnac-visitor/{visitor_id}")
    return resp_json(resp)


# ── Auth Server Profiles ──────────────────────────────────────────────────────

@mcp.tool()
def list_auth_servers(
    limit: int = 50,
    offset: int = 0,
    full_list: bool = False,
) -> dict[str, Any]:
    """List RADIUS/auth server profiles (bounded by default)."""
    data = get_client().get(f"{_CNAC_BASE}/auth-servers")
    if full_list:
        return data
    return bound_collection_response(data, limit=limit, offset=offset)


@mcp.tool()
def get_auth_server(name: str) -> dict[str, Any]:
    """Get a single RADIUS/auth server profile by name (from list_auth_servers)."""
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
    """Create a RADIUS auth server profile.

    Args:
        auth_server_address: IP or hostname of the RADIUS server.
        shared_secret: Plaintext secret (stored securely by Central).
        server_type: "RADIUS" (default), "RADSEC", "LDAP", or "TACACS".
        dry_run: If True, return payload without sending (secret masked).
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
    return resp_json(resp)


@mcp.tool()
def delete_auth_server(
    name: str,
    dry_run: bool = False,
) -> dict[str, Any]:
    """Delete a RADIUS/auth server profile by name (from list_auth_servers)."""
    if dry_run:
        return {"dry_run": True, "name": name}

    client = get_client()
    resp = client._request("DELETE", f"{_CNAC_BASE}/auth-servers/{name}")
    return resp_json(resp)


# ── AAA Profiles ──────────────────────────────────────────────────────────────

@mcp.tool()
def list_aaa_profiles(
    limit: int = 50,
    offset: int = 0,
    full_list: bool = False,
) -> dict[str, Any]:
    """List AAA profiles (bounded by default)."""
    data = get_client().get(f"{_CNAC_BASE}/aaa-profile")
    if full_list:
        return data
    return bound_collection_response(data, limit=limit, offset=offset)


@mcp.tool()
def get_aaa_profile(name: str) -> dict[str, Any]:
    """Get a single AAA profile by name (from list_aaa_profiles)."""
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
    """Create an AAA profile.

    Args:
        auth_role: Role after successful authentication.
        fallback_role: Role when auth server is unreachable.
        acct_server_group: Accounting server group name.
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
    return resp_json(resp)


@mcp.tool()
def delete_aaa_profile(
    name: str,
    dry_run: bool = False,
) -> dict[str, Any]:
    """Delete an AAA profile by name (from list_aaa_profiles)."""
    if dry_run:
        return {"dry_run": True, "name": name}

    client = get_client()
    resp = client._request("DELETE", f"{_CNAC_BASE}/aaa-profile/{name}")
    return resp_json(resp)


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
    """Test AAA connectivity from an AP or CX switch (async, polls ~60s).

    Args:
        device_type: "AP" (default) or "CX".
        server_name: Auth server profile name — required for AP tests.
        radius_server_ip: RADIUS server IP — required for CX tests.
        auth_method: CX only — "chap" (default) or "pap".
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
def list_authz_policies(
    limit: int = 50,
    offset: int = 0,
    full_list: bool = False,
) -> dict[str, Any]:
    """List CNAC authorization policies (bounded by default)."""
    data = get_client().get(f"{_CNAC_BASE}/authz-policies")
    if full_list:
        return data
    return bound_collection_response(data, limit=limit, offset=offset)


@mcp.tool()
def get_authz_policy(policy_id: str) -> dict[str, Any]:
    """Get a single CNAC authz policy by ID (from list_authz_policies)."""
    return get_client().get(f"{_CNAC_BASE}/authz-policies/{policy_id}")


@mcp.tool()
def create_authz_policy(
    policy_name: str,
    rule_name: str,
    role: str,
    tag_id: str | None = None,
    position: int = 1,
    dry_run: bool = False,
) -> dict[str, Any]:
    """Create a CNAC authz policy that assigns a role to devices.

    Two modes: omit tag_id for catch-all (every authenticated device gets the role),
    or provide tag_id to match only devices with that static tag.

    Args:
        tag_id: UUID from list_static_tags. Omit for catch-all.
        position: Priority (lower = higher, default 1). Must be unique.
        dry_run: If True, return payload without sending.
    """
    policy_id = str(uuid.uuid4())

    rule: dict[str, Any] = {
        "position": 1,
        "rule-id": str(uuid.uuid4()),
        "rule-name": rule_name,
        "enable": True,
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

    if tag_id:
        rule["conditions"] = {
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
        }

    payload: dict[str, Any] = {
        "name": policy_name,
        "position": position,
        "enable": True,
        "rule": [rule],
    }

    if dry_run:
        return {"dry_run": True, "policy_id": policy_id, "policy_name": policy_name, "payload": payload}

    client = get_client()
    resp = client._request("POST", f"{_CNAC_BASE}/authz-policies/{policy_id}", json=payload)
    result = resp_json(resp)
    result["policy_id"] = policy_id
    return result


@mcp.tool()
def delete_authz_policy(
    policy_id: str,
    dry_run: bool = False,
) -> dict[str, Any]:
    """Delete a CNAC authz policy by ID (from list_authz_policies)."""
    if dry_run:
        return {"dry_run": True, "policy_id": policy_id}

    client = get_client()
    resp = client._request("DELETE", f"{_CNAC_BASE}/authz-policies/{policy_id}")
    return resp_json(resp)


_STATIC_TAG_BASE = "/network-config/v1alpha1/static-tag"


@mcp.tool()
def list_static_tags(
    limit: int = 50,
    offset: int = 0,
    full_list: bool = False,
) -> dict[str, Any]:
    """List user-created static classification tags (bounded by default).

    Note: system-generated tags (e.g. IoT) are not returned by this endpoint.
    """
    data = get_client().get(_STATIC_TAG_BASE)
    if full_list:
        return data
    return bound_collection_response(data, limit=limit, offset=offset)


@mcp.tool()
def create_static_tag(
    name: str,
    dry_run: bool = False,
) -> dict[str, Any]:
    """Create a static classification tag (tag-id is auto-generated).

    Tags are used in authz policies to assign roles to classified clients.

    Args:
        name: Tag display name (e.g. 'AV', 'IoT', 'Console').
        dry_run: If True, return payload without sending.
    """
    tag_id = str(uuid.uuid4())
    payload = {"name": name}
    if dry_run:
        return {"payload": payload, "tag_id": tag_id, "url": f"{_STATIC_TAG_BASE}/{tag_id}"}
    client = get_client()
    resp = client._request("POST", f"{_STATIC_TAG_BASE}/{tag_id}", json=payload)
    result = resp_json(resp)
    result["tag_id"] = tag_id
    return result


@mcp.tool()
def delete_static_tag(tag_id: str) -> dict[str, Any]:
    """Delete a static classification tag by its UUID tag-id (from list_static_tags)."""
    client = get_client()
    resp = client._request("DELETE", f"{_STATIC_TAG_BASE}/{tag_id}")
    return resp_json(resp)


# ── Auth Profiles ─────────────────────────────────────────────────────────────

_AUTH_PROFILE_BASE = "/network-config/v1alpha1/auth-profiles"

# UUID of the built-in Central NAC MAC Address Store identity store
_MAC_ADDRESS_STORE_ID = "4c6c406a-7c1f-442a-8e43-c627090e8624"


@mcp.tool()
def list_auth_profiles(
    limit: int = 50,
    offset: int = 0,
    full_list: bool = False,
) -> dict[str, Any]:
    """List Central NAC authentication profiles (bounded by default)."""
    data = get_client().get(_AUTH_PROFILE_BASE)
    if full_list:
        return data
    return bound_collection_response(data, limit=limit, offset=offset)


@mcp.tool()
def get_auth_profile(profile_id: str) -> dict[str, Any]:
    """Get a single Central NAC auth profile by UUID (from list_auth_profiles)."""
    return get_client().get(f"{_AUTH_PROFILE_BASE}/{profile_id}")


@mcp.tool()
def create_mac_auth_profile(
    name: str,
    networks: list[str],
    allow_all: bool = True,
    identity_store_id: str = _MAC_ADDRESS_STORE_ID,
    description: str = "",
    dry_run: bool = False,
) -> dict[str, Any]:
    """Create a Central NAC MAC authentication (MAB) profile for wireless SSIDs.

    Each SSID can only belong to one auth profile. SSIDs must have cloud-auth=True and
    mac-authentication=True.

    Args:
        networks: SSID names to associate (e.g. ["Central-MacAuth"]).
        allow_all: True = allow all MACs; False = only pre-registered MACs.
        identity_store_id: Defaults to built-in MAC Address Store. Use list_identity_stores for others.
        dry_run: If True, return payload without sending.
    """
    profile_id = str(uuid.uuid4())
    payload: dict[str, Any] = {
        "auth-profile-id": profile_id,
        "name": name,
        "description": description,
        "auth-type": "MAB",
        "networks": networks,
        "wired": False,
        "identity-stores": [identity_store_id],
        "mab": {"allow-all": allow_all},
    }

    if dry_run:
        return {"dry_run": True, "profile_id": profile_id, "payload": payload}

    client = get_client()
    resp = client._request("POST", f"{_AUTH_PROFILE_BASE}/{profile_id}", json=payload)
    result = resp_json(resp)
    result["profile_id"] = profile_id
    return result


@mcp.tool()
def delete_auth_profile(
    profile_id: str,
    dry_run: bool = False,
) -> dict[str, Any]:
    """Delete a Central NAC authentication profile by UUID (from list_auth_profiles)."""
    if dry_run:
        return {"dry_run": True, "profile_id": profile_id}

    client = get_client()
    resp = client._request("DELETE", f"{_AUTH_PROFILE_BASE}/{profile_id}")
    return resp_json(resp)


@mcp.tool()
def list_identity_stores(
    limit: int = 50,
    offset: int = 0,
    full_list: bool = False,
) -> dict[str, Any]:
    """List Central NAC identity stores (bounded by default)."""
    data = get_client().get(f"{_CNAC_BASE}/identity-stores")
    if full_list:
        return data
    return bound_collection_response(data, limit=limit, offset=offset)


if __name__ == "__main__":
    from mcp_servers._cache_hygiene import stable_list_tools
    stable_list_tools(mcp)
    mcp.run()
