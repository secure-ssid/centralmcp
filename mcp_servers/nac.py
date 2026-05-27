"""MCP server — Aruba Central NAC and authentication tools (31 tools).

Covers: CNAC MAC registrations, Named MPSK registrations, visitor accounts,
RADIUS/auth server profiles, AAA profiles, AAA connectivity testing, authorization
policies, and static classification tags.
"""
import uuid
from typing import Any

from mcp.server.fastmcp import FastMCP

from mcp_servers.shared import (
    DESTRUCTIVE,
    DIAGNOSTIC,
    IDEMPOTENT_WRITE,
    READ_ONLY,
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

@mcp.tool(annotations=READ_ONLY)
def list_mac_registrations(
    limit: int = 50,
    offset: int = 0,
    full_list: bool = False,
) -> dict[str, Any]:
    """List Central NAC MAC address registrations (bounded by default)."""
    data = get_client().get(f"{_CNAC_BASE}/cnac-mac-reg")
    if full_list:
        return data
    return bound_collection_response(data, limit=limit, offset=offset)


@mcp.tool(annotations=IDEMPOTENT_WRITE)
def add_mac_registration(
    mac_address: str,
    display_name: str | None = None,
    tags: list[str] | None = None,
    enable: bool = True,
    dry_run: bool = False,
) -> dict[str, Any]:
    """Register a MAC address for Central NAC. mac_address e.g. 'aa:bb:cc:dd:ee:ff'."""
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


@mcp.tool(annotations=IDEMPOTENT_WRITE)
def update_mac_registration(
    registration_id: str,
    mac_address: str,
    display_name: str | None = None,
    tags: list[str] | None = None,
    enable: bool = True,
    dry_run: bool = False,
) -> dict[str, Any]:
    """Update an existing MAC registration by ID. mac_address required even for updates."""
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


@mcp.tool(annotations=DESTRUCTIVE)
def delete_mac_registration(
    registration_id: str,
    dry_run: bool = False,
) -> dict[str, Any]:
    """Delete a MAC registration by ID."""
    if dry_run:
        return {"dry_run": True, "registration_id": registration_id}

    client = get_client()
    resp = client._request("DELETE", f"{_CNAC_BASE}/cnac-mac-reg/{registration_id}")
    return resp_json(resp)


# ── Named MPSK Registrations ──────────────────────────────────────────────────

@mcp.tool(annotations=READ_ONLY)
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


@mcp.tool(annotations=IDEMPOTENT_WRITE)
def add_mpsk_registration(
    name: str,
    network: str,
    user_role: str | None = None,
    password_policy: str = "WORDS",
    enable: bool = True,
    dry_run: bool = False,
) -> dict[str, Any]:
    """Create a Named MPSK registration. network = SSID; password_policy default "WORDS"."""
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


@mcp.tool(annotations=DESTRUCTIVE)
def delete_mpsk_registration(
    registration_id: str,
    dry_run: bool = False,
) -> dict[str, Any]:
    """Delete a Named MPSK registration by ID."""
    if dry_run:
        return {"dry_run": True, "registration_id": registration_id}

    client = get_client()
    resp = client._request("DELETE", f"{_CNAC_BASE}/cnac-named-mpsk-reg/{registration_id}")
    return resp_json(resp)


# ── Visitor Accounts ──────────────────────────────────────────────────────────

@mcp.tool(annotations=READ_ONLY)
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


@mcp.tool(annotations=IDEMPOTENT_WRITE)
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
    """Create a Central NAC visitor account. name=login username; expire_at ISO 8601."""
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


@mcp.tool(annotations=DESTRUCTIVE)
def delete_visitor(
    visitor_id: str,
    dry_run: bool = False,
) -> dict[str, Any]:
    """Delete a visitor account by ID."""
    if dry_run:
        return {"dry_run": True, "visitor_id": visitor_id}

    client = get_client()
    resp = client._request("DELETE", f"{_CNAC_BASE}/cnac-visitor/{visitor_id}")
    return resp_json(resp)


# ── Auth Server Profiles ──────────────────────────────────────────────────────

@mcp.tool(annotations=READ_ONLY)
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


@mcp.tool(annotations=READ_ONLY)
def get_auth_server(name: str) -> dict[str, Any]:
    """Get a single RADIUS/auth server profile by name."""
    try:
        return get_client().get(f"{_CNAC_BASE}/auth-servers/{name}")
    except Exception as exc:
        return {"error": str(exc)}


@mcp.tool(annotations=IDEMPOTENT_WRITE)
def create_auth_server(
    name: str,
    auth_server_address: str,
    shared_secret: str,
    radius_server_mode: str = "AUTH_AND_COA",
    enable_radsec: bool = False,
    radsec_port: int = 2083,
    radsec_client_cert: str | None = None,
    radsec_trusted_cacert_name: str | None = None,
    radsec_trusted_servercert_name: str | None = None,
    auth_port: int = 1812,
    acct_port: int = 1813,
    enable: bool = True,
    dry_run: bool = False,
) -> dict[str, Any]:
    """Create a RADIUS auth server profile.

    The endpoint only supports type=RADIUS. RadSec is a RADIUS variant enabled
    via enable_radsec + cert fields (not a distinct `type`). LDAP / TACACS are
    NOT supported on this endpoint.

    Args:
        auth_server_address: IP or hostname of the RADIUS server.
        shared_secret: Plaintext secret (stored securely by Central).
        radius_server_mode: AUTH_ONLY, COA_ONLY, or AUTH_AND_COA (default).
        enable_radsec: If True, wrap RADIUS in TLS (RadSec) — requires
                       radsec_client_cert and radsec_trusted_cacert_name.
        radsec_port: TLS port (default 2083). Only used when enable_radsec=True.
        radsec_client_cert: Client cert name for RadSec mutual auth.
        radsec_trusted_cacert_name: Trusted CA cert name that signs the
                                    RADIUS server's cert.
        radsec_trusted_servercert_name: Optional pinned server cert name.
        dry_run: If True, return payload without sending (secret masked).
    """
    payload: dict[str, Any] = {
        "name": name,
        "type": "RADIUS",
        "radius-server-mode": radius_server_mode,
        "auth-server-address": auth_server_address,
        "auth-port": auth_port,
        "acct-port": acct_port,
        "enable": str(enable).lower(),
        "shared-secret-config": {
            "secret-type": "PLAIN_TEXT",
            "plaintext-value": shared_secret,
        },
    }
    if enable_radsec:
        payload["enable-radsec"] = True
        payload["radsec-port"] = radsec_port
        if radsec_client_cert is not None:
            payload["radsec-client-cert"] = radsec_client_cert
        if radsec_trusted_cacert_name is not None:
            payload["radsec-trusted-cacert-name"] = radsec_trusted_cacert_name
        if radsec_trusted_servercert_name is not None:
            payload["radsec-trusted-servercert-name"] = radsec_trusted_servercert_name

    if dry_run:
        # Mask secret in dry-run output
        safe = {**payload, "shared-secret-config": {"secret-type": "PLAIN_TEXT", "plaintext-value": "***"}}
        return {"dry_run": True, "name": name, "payload": safe}

    client = get_client()
    resp = client._request("POST", f"{_CNAC_BASE}/auth-servers/{name}", json=payload)
    return resp_json(resp)


@mcp.tool(annotations=DESTRUCTIVE)
def delete_auth_server(
    name: str,
    dry_run: bool = False,
) -> dict[str, Any]:
    """Delete a RADIUS/auth server profile by name."""
    if dry_run:
        return {"dry_run": True, "name": name}

    client = get_client()
    resp = client._request("DELETE", f"{_CNAC_BASE}/auth-servers/{name}")
    return resp_json(resp)


# ── AAA Profiles ──────────────────────────────────────────────────────────────

@mcp.tool(annotations=READ_ONLY)
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


@mcp.tool(annotations=READ_ONLY)
def get_aaa_profile(name: str) -> dict[str, Any]:
    """Get a single AAA profile by name."""
    try:
        return get_client().get(f"{_CNAC_BASE}/aaa-profile/{name}")
    except Exception as exc:
        return {"error": str(exc)}


@mcp.tool(annotations=IDEMPOTENT_WRITE)
def create_aaa_profile(
    name: str,
    auth_role: str | None = None,
    fallback_role: str | None = None,
    acct_server_group: str | None = None,
    description: str | None = None,
    dry_run: bool = False,
) -> dict[str, Any]:
    """Create an AAA profile. fallback_role applies when auth server unreachable."""
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


@mcp.tool(annotations=DESTRUCTIVE)
def delete_aaa_profile(
    name: str,
    dry_run: bool = False,
) -> dict[str, Any]:
    """Delete an AAA profile by name."""
    if dry_run:
        return {"dry_run": True, "name": name}

    client = get_client()
    resp = client._request("DELETE", f"{_CNAC_BASE}/aaa-profile/{name}")
    return resp_json(resp)


# ── AAA Test ──────────────────────────────────────────────────────────────────

@mcp.tool(annotations=DIAGNOSTIC)
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

    AP: server_name required. CX: radius_server_ip required; auth_method chap/pap.
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

@mcp.tool(annotations=READ_ONLY)
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


@mcp.tool(annotations=READ_ONLY)
def get_authz_policy(policy_id: str) -> dict[str, Any]:
    """Get a single CNAC authz policy by ID."""
    try:
        return get_client().get(f"{_CNAC_BASE}/authz-policies/{policy_id}")
    except Exception as exc:
        return {"error": str(exc)}


@mcp.tool(annotations=IDEMPOTENT_WRITE)
def create_authz_policy(
    policy_name: str,
    rule_name: str,
    role: str,
    tag_id: str | None = None,
    position: int = 1,
    policy_type: str | None = None,
    dry_run: bool = False,
) -> dict[str, Any]:
    """Create a CNAC authz policy that assigns a role to devices.

    Two modes: omit tag_id for catch-all (every authenticated device gets the role),
    or provide tag_id to match only devices with that static tag.

    Args:
        tag_id: UUID from list_static_tags. Omit for catch-all.
        position: Priority (lower = higher, default 1). Must be unique.
        policy_type: Optional authorization-policy type enum (e.g. DEVICE, USER,
                     VISITOR, NAMED_MPSK). If None, API chooses the default
                     (DEVICE for MAC-auth flows). Only set when you need a
                     non-device flow like user 802.1X or visitor policies.
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
                        {"attr-name": "ATTR_POLICY_ACTION", "value": "Accept"},
                        {"attr-name": "ATTR_ARUBA_ROLE", "value": role},
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
    if policy_type is not None:
        payload["policy-type"] = policy_type

    if dry_run:
        return {"dry_run": True, "policy_id": policy_id, "policy_name": policy_name, "payload": payload}

    client = get_client()
    resp = client._request("POST", f"{_CNAC_BASE}/authz-policies/{policy_id}", json=payload)
    result = resp_json(resp)
    result["policy_id"] = policy_id
    return result


@mcp.tool(annotations=DESTRUCTIVE)
def delete_authz_policy(
    policy_id: str,
    dry_run: bool = False,
) -> dict[str, Any]:
    """Delete a CNAC authz policy by ID."""
    if dry_run:
        return {"dry_run": True, "policy_id": policy_id}

    client = get_client()
    resp = client._request("DELETE", f"{_CNAC_BASE}/authz-policies/{policy_id}")
    return resp_json(resp)


_STATIC_TAG_BASE = "/network-config/v1alpha1/static-tag"


@mcp.tool(annotations=READ_ONLY)
def list_static_tags(
    limit: int = 50,
    offset: int = 0,
    full_list: bool = False,
) -> dict[str, Any]:
    """List user-created static classification tags (bounded). System tags (e.g. IoT) not returned."""
    data = get_client().get(_STATIC_TAG_BASE)
    if full_list:
        return data
    return bound_collection_response(data, limit=limit, offset=offset)


@mcp.tool(annotations=IDEMPOTENT_WRITE)
def create_static_tag(
    name: str,
    dry_run: bool = False,
) -> dict[str, Any]:
    """Create a static classification tag (UUID auto-generated). Used in authz policies to assign roles."""
    tag_id = str(uuid.uuid4())
    payload = {"name": name}
    if dry_run:
        return {"payload": payload, "tag_id": tag_id, "url": f"{_STATIC_TAG_BASE}/{tag_id}"}
    client = get_client()
    resp = client._request("POST", f"{_STATIC_TAG_BASE}/{tag_id}", json=payload)
    result = resp_json(resp)
    result["tag_id"] = tag_id
    return result


@mcp.tool(annotations=DESTRUCTIVE)
def delete_static_tag(tag_id: str) -> dict[str, Any]:
    """Delete a static classification tag by UUID."""
    client = get_client()
    resp = client._request("DELETE", f"{_STATIC_TAG_BASE}/{tag_id}")
    return resp_json(resp)


# ── Auth Profiles ─────────────────────────────────────────────────────────────

_AUTH_PROFILE_BASE = "/network-config/v1alpha1/auth-profiles"

# UUID of the built-in Central NAC MAC Address Store identity store
_MAC_ADDRESS_STORE_ID = "4c6c406a-7c1f-442a-8e43-c627090e8624"

# Central tenant/org name — required in auth profiles so NAC routes RADIUS to the right tenant.
# UI-created profiles always have this; API-created ones must include it or NAC rejects all MAB.
_CENTRAL_ORG_NAME = "SecureSSID-LAB"


@mcp.tool(annotations=READ_ONLY)
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


@mcp.tool(annotations=READ_ONLY)
def get_auth_profile(profile_id: str) -> dict[str, Any]:
    """Get a single Central NAC auth profile by UUID."""
    try:
        return get_client().get(f"{_AUTH_PROFILE_BASE}/{profile_id}")
    except Exception as exc:
        return {"error": str(exc)}


@mcp.tool(annotations=IDEMPOTENT_WRITE)
def create_mac_auth_profile(
    name: str,
    networks: list[str],
    allow_all: bool = True,
    identity_store_id: str = _MAC_ADDRESS_STORE_ID,
    description: str = "",
    dry_run: bool = False,
) -> dict[str, Any]:
    """Create a Central NAC MAC authentication (MAB) profile for wireless SSIDs.

    Each SSID can only belong to one auth profile. SSIDs must have cloud-auth=True
    and mac-authentication=True.

    GOTCHA: API-created profiles sometimes lack hidden UI-side bindings and fail
    at runtime. If you see "Unexpected Client Data" or silent auth failures after
    creating via this tool, fall back to PATCHing an existing UI-created profile
    (add SSID names to its `networks` list) — the documented workaround.

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
        "organization-name": _CENTRAL_ORG_NAME,
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


@mcp.tool(annotations=IDEMPOTENT_WRITE)
def create_dot1x_auth_profile(
    name: str,
    networks: list[str],
    identity_store_ids: list[str],
    wired: bool = False,
    description: str = "",
    dry_run: bool = False,
) -> dict[str, Any]:
    """Create a Central NAC 802.1X auth profile (wireless or wired).

    Counterpart to create_mac_auth_profile. Each SSID/port-profile binds to one
    auth profile. Wireless SSIDs need cloud-auth=True and dot1x-authentication=True.
    DOT1X typically uses LDAP/AD/external RADIUS stores (not MAC Address Store).

    GOTCHA: API-created profiles may lack hidden UI bindings — if it misbehaves,
    PATCH an existing UI-created profile instead (same workaround as MAB).
    """
    profile_id = str(uuid.uuid4())
    payload: dict[str, Any] = {
        "auth-profile-id": profile_id,
        "name": name,
        "description": description,
        "auth-type": "DOT1X",
        "networks": networks,
        "wired": wired,
        "organization-name": _CENTRAL_ORG_NAME,
        "identity-stores": identity_store_ids,
    }

    if dry_run:
        return {"dry_run": True, "profile_id": profile_id, "payload": payload}

    client = get_client()
    resp = client._request("POST", f"{_AUTH_PROFILE_BASE}/{profile_id}", json=payload)
    result = resp_json(resp)
    result["profile_id"] = profile_id
    return result


@mcp.tool(annotations=DESTRUCTIVE)
def delete_auth_profile(
    profile_id: str,
    dry_run: bool = False,
) -> dict[str, Any]:
    """Delete a Central NAC authentication profile by UUID."""
    if dry_run:
        return {"dry_run": True, "profile_id": profile_id}

    client = get_client()
    resp = client._request("DELETE", f"{_AUTH_PROFILE_BASE}/{profile_id}")
    return resp_json(resp)


@mcp.tool(annotations=READ_ONLY)
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
    from mcp_servers._middleware import (
        NullStripMiddleware,
        RateLimitMiddleware,
        install_middleware,
    )
    stable_list_tools(mcp)
    install_middleware(mcp, [NullStripMiddleware(), RateLimitMiddleware(rate=8.0)])
    from mcp_servers.shared import run_server
    run_server(mcp)
