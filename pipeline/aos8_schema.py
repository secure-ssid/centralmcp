"""Normalized ArubaOS 8 dataclasses and Classic/New Central migration candidates.

Pure-python, no network calls and no dependency on `mcp_servers/`. These
dataclasses are the shared vocabulary between `pipeline/aos8_parsers.py`
(export -> normalized objects) and `pipeline/aos8_migration.py` (normalized
objects -> deterministic migration candidates).
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any

# ---------------------------------------------------------------------------
# Normalized AOS8 source objects
# ---------------------------------------------------------------------------


@dataclass
class AOS8Wlan:
    """A merged AOS8 WLAN: one SSID profile plus its linked virtual AP, if any."""

    profile_name: str
    essid: str | None = None
    opmode: str | None = None
    vlan: str | int | None = None
    forward_mode: str | None = None
    aaa_profile: str | None = None
    virtual_ap_profile: str | None = None
    raw: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class AOS8Role:
    rolename: str
    vlan: str | int | None = None
    acl: str | None = None
    captive_portal_profile: str | None = None
    raw: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class AOS8Vlan:
    vlan_id: str | int
    description: str | None = None
    raw: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class AOS8ApGroup:
    profile_name: str
    virtual_ap_profiles: list[str] = field(default_factory=list)
    raw: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class AOS8Controller:
    name: str | None = None
    ip_address: str | None = None
    model: str | None = None
    version: str | None = None
    raw: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class AOS8Policy:
    """An AOS8 session ACL (`acl_sess`), referred to as a "policy" in the GUI."""

    name: str
    rule_count: int | None = None
    raw: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


# ---------------------------------------------------------------------------
# Migration candidates
# ---------------------------------------------------------------------------


@dataclass
class ClassicCentralCandidate:
    """A candidate object for migration into Aruba Central (Classic)."""

    object_type: str
    identifier: str
    payload: dict[str, Any]
    warnings: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class NewCentralCandidate:
    """A candidate object for migration into HPE Aruba Networking Central (New Central)."""

    object_type: str
    identifier: str
    payload: dict[str, Any]
    warnings: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


# ---------------------------------------------------------------------------
# Known-lossy fields — explicit warnings, never a silent drop.
# ---------------------------------------------------------------------------

UNSUPPORTED_FIELDS: dict[str, dict[str, str]] = {
    "wlan": {
        "opmode": (
            "AOS8 `opmode` cipher suites (e.g. WPA-TKIP-only, mixed WEP) have no "
            "direct Central/New Central WLAN security equivalent; map manually."
        ),
        "forward_mode": (
            "AOS8 per-virtual-AP forward mode (tunnel/bridge/split-tunnel) is "
            "controlled differently in Central/New Central; verify VLAN and "
            "gateway-role assignment after migration."
        ),
    },
    "role": {
        "captive_portal_profile": (
            "AOS8 captive-portal profiles bound to a user role are not migrated "
            "automatically; recreate the captive portal policy on the target."
        ),
    },
    "policy": {
        "rule_count": (
            "AOS8 session ACL (`acl_sess`) rule bodies are not translated; only "
            "the policy name and rule count are captured for manual review."
        ),
    },
}
