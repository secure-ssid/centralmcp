#!/usr/bin/env python3
"""Check local centralmcp setup without making network or API calls."""

from __future__ import annotations

import argparse
import importlib.util
import json
import os
import shlex
import shutil
import socket
import sys
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_HTTP_PORT = 8010
OPTIONAL_PRODUCT_ENVS = {
    "clearpass": ("CLEARPASS_BASE_URL", "CLEARPASS_API_TOKEN"),
    "mist": ("MIST_HOST", "MIST_API_TOKEN"),
    "apstra": ("APSTRA_BASE_URL", "APSTRA_API_TOKEN"),
    "aos8": ("AOS8_BASE_URL", "AOS8_API_TOKEN"),
    "edgeconnect": ("EDGECONNECT_BASE_URL", "EDGECONNECT_API_TOKEN"),
}


@dataclass(frozen=True)
class Check:
    status: str
    name: str
    detail: str


def _status_line(check: Check) -> str:
    return f"[{check.status}] {check.name}: {check.detail}"


def _command_exists(name: str) -> bool:
    return shutil.which(name) is not None


def _module_available(name: str) -> bool:
    return importlib.util.find_spec(name) is not None


def _display_path(path: Path) -> str:
    try:
        return str(path.relative_to(ROOT))
    except ValueError:
        return str(path)


def _path_check(path: Path, name: str, *, missing_detail: str) -> Check:
    if path.exists():
        return Check("OK", name, f"{_display_path(path)} exists")
    return Check("WARN", name, missing_detail)


def _load_json(path: Path) -> tuple[dict[str, object] | None, str | None]:
    try:
        data = json.loads(path.read_text())
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        return None, str(exc)
    if not isinstance(data, dict):
        return None, "top-level JSON value must be an object"
    return data, None


def _load_local_env(path: Path) -> None:
    if not path.exists():
        return
    for raw_line in path.read_text().splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[len("export ") :].strip()
        key, sep, value = line.partition("=")
        if not sep:
            continue
        key = key.strip()
        if not key or key in os.environ:
            continue
        try:
            parsed = shlex.split(value, posix=True)
        except ValueError:
            continue
        os.environ[key] = parsed[0] if len(parsed) == 1 else value.strip()


def _server_map(data: dict[str, object]) -> dict[str, object] | None:
    servers = data.get("mcpServers") or data.get("servers")
    return servers if isinstance(servers, dict) else None


def _router_server(data: dict[str, object]) -> dict[str, object] | None:
    servers = _server_map(data)
    if servers is None:
        return None
    server = servers.get("aruba-tool-router") or servers.get("centralmcp")
    return server if isinstance(server, dict) else None


def _router_env_checks(data: dict[str, object]) -> list[Check]:
    server = _router_server(data)
    if server is None:
        return [
            Check(
                "WARN",
                "Local stdio router profile",
                "missing aruba-tool-router or centralmcp server entry",
            )
        ]

    env = server.get("env")
    if not isinstance(env, dict):
        return [Check("WARN", "Local stdio router profile", "missing env object")]

    mode = env.get("CENTRALMCP_ROUTER_MODE")
    toolsets = env.get("CENTRALMCP_TOOLSETS")
    products = env.get("CENTRALMCP_PRODUCTS")
    checks = [
        Check(
            "OK" if mode == "minimal" else "WARN",
            "Local stdio router mode",
            "CENTRALMCP_ROUTER_MODE is minimal"
            if mode == "minimal"
            else "set CENTRALMCP_ROUTER_MODE=minimal for low-token clients",
        ),
        Check(
            "OK" if toolsets == "central,glp,rag" else "WARN",
            "Local stdio router toolsets",
            "CENTRALMCP_TOOLSETS is central,glp,rag"
            if toolsets == "central,glp,rag"
            else "set CENTRALMCP_TOOLSETS=central,glp,rag for the default low-token profile",
        ),
    ]
    if products:
        checks.append(
            Check(
                "WARN",
                "Local stdio optional products",
                "CENTRALMCP_PRODUCTS is set; optional products increase tool catalog scope",
            )
        )
    return checks


def _stdio_config_checks(path: Path) -> list[Check]:
    if not path.exists():
        return []

    checks: list[Check] = []
    data, error = _load_json(path)
    checks.append(
        Check(
            "OK" if error is None else "FAIL",
            "Local stdio MCP config JSON",
            "valid JSON object" if error is None else f"invalid JSON: {error}",
        )
    )

    text = path.read_text()
    checks.append(
        Check(
            "WARN" if "/path/to/centralmcp" in text else "OK",
            "Local stdio MCP config paths",
            "replace /path/to/centralmcp placeholders"
            if "/path/to/centralmcp" in text
            else "no example placeholders found",
        )
    )
    if data is not None:
        checks.extend(_router_env_checks(data))
    return checks


def _http_config_checks(path: Path, host: str, port: int) -> list[Check]:
    if not path.exists():
        return []

    checks: list[Check] = []
    data, error = _load_json(path)
    checks.append(
        Check(
            "OK" if error is None else "FAIL",
            "Local HTTP MCP config JSON",
            "valid JSON object" if error is None else f"invalid JSON: {error}",
        )
    )
    if data is None:
        return checks

    servers = _server_map(data)
    if servers is None:
        return [
            *checks,
            Check("WARN", "Local HTTP MCP config URL", "missing mcpServers object"),
        ]

    urls = [
        server.get("url")
        for server in servers.values()
        if isinstance(server, dict) and isinstance(server.get("url"), str)
    ]
    if not urls:
        return [
            *checks,
            Check("WARN", "Local HTTP MCP config URL", "no server URL found"),
        ]

    expected = f"http://{host}:{port}/mcp"
    status = "OK" if expected in urls else "WARN"
    detail = f"matches {expected}" if status == "OK" else f"expected {expected}"
    checks.append(Check(status, "Local HTTP MCP config URL", detail))

    transports = [
        server.get("transport")
        for server in servers.values()
        if isinstance(server, dict) and isinstance(server.get("transport"), str)
    ]
    checks.append(
        Check(
            "OK" if "streamable-http" in transports else "WARN",
            "Local HTTP MCP transport",
            "transport is streamable-http"
            if "streamable-http" in transports
            else "set transport to streamable-http",
        )
    )
    return checks


def _csv_values(value: str | None) -> list[str]:
    if not value:
        return []
    return [item.strip().lower() for item in value.split(",") if item.strip()]


def _enabled_optional_products(products: str, toolsets: str | None) -> set[str]:
    product_values = set(_csv_values(products))
    toolset_values = set(_csv_values(toolsets))
    known_products = set(OPTIONAL_PRODUCT_ENVS)

    enabled = product_values & known_products
    if "all" in toolset_values:
        enabled.update(known_products)
    else:
        enabled.update(toolset_values & known_products)
    return enabled


def _port_listening(host: str, port: int) -> bool:
    target = "127.0.0.1" if host in {"0.0.0.0", "::"} else host
    try:
        addresses = socket.getaddrinfo(target, port, type=socket.SOCK_STREAM)
    except OSError:
        return False

    for family, socktype, proto, _, sockaddr in addresses:
        with socket.socket(family, socktype, proto) as sock:
            sock.settimeout(0.25)
            if sock.connect_ex(sockaddr) == 0:
                return True
    return False


def _dependency_checks() -> list[Check]:
    uv_available = _command_exists("uv")
    checks = [
        Check(
            "OK" if sys.version_info >= (3, 10) else "FAIL",
            "Python version",
            f"{sys.version.split()[0]} detected; centralmcp requires >=3.10",
        ),
        Check(
            "OK" if uv_available else "WARN",
            "uv",
            "uv is available"
            if uv_available
            else "uv not found; install uv or use python directly",
        ),
    ]
    for module in ("httpx", "mcp", "lancedb", "fastembed", "yaml"):
        available = _module_available(module)
        checks.append(
            Check(
                "OK" if available else "WARN",
                f"Python module {module}",
                f"{module} import spec found" if available else f"{module} missing; run `uv sync`",
            )
        )
    return checks


def _config_checks() -> list[Check]:
    creds_path = Path(os.getenv("CREDS_PATH", ROOT / "config" / "credentials.yaml"))
    if not creds_path.is_absolute():
        creds_path = ROOT / creds_path

    checks = [
        _path_check(
            ROOT / "pyproject.toml",
            "Project metadata",
            missing_detail="pyproject.toml missing; run from a centralmcp checkout",
        ),
        _path_check(
            creds_path,
            "Credentials",
            missing_detail=(
                f"{creds_path} missing; copy config/credentials.yaml.example to "
                "config/credentials.yaml and fill in credentials"
            ),
        ),
        _path_check(
            ROOT / ".mcp.json.example",
            "stdio MCP example",
            missing_detail=".mcp.json.example missing",
        ),
        _path_check(
            ROOT / ".mcp.http.json.example",
            "HTTP MCP example",
            missing_detail=".mcp.http.json.example missing",
        ),
        _path_check(
            ROOT / ".claude" / "launch.json",
            "Claude launch profiles",
            missing_detail=".claude/launch.json missing",
        ),
    ]

    local_stdio = ROOT / ".mcp.json"
    local_http = ROOT / ".mcp.http.json"
    checks.append(
        Check(
            "OK" if local_stdio.exists() else "WARN",
            "Local stdio MCP config",
            ".mcp.json exists"
            if local_stdio.exists()
            else "copy .mcp.json.example to .mcp.json for local stdio clients",
        )
    )
    checks.extend(_stdio_config_checks(local_stdio))
    checks.append(
        Check(
            "OK" if local_http.exists() else "WARN",
            "Local HTTP MCP config",
            ".mcp.http.json exists"
            if local_http.exists()
            else "copy .mcp.http.json.example to .mcp.http.json for local HTTP clients",
        )
    )
    raw_port = os.getenv("MCP_PORT", str(DEFAULT_HTTP_PORT))
    try:
        port = int(raw_port)
    except ValueError:
        port = DEFAULT_HTTP_PORT
    checks.extend(_http_config_checks(local_http, os.getenv("MCP_HOST", "127.0.0.1"), port))

    return checks


def _index_checks() -> list[Check]:
    checks = []
    tool_index = ROOT / "data" / "tools.lance"
    docs_index = ROOT / "data" / "docs.lance"
    specs_index = ROOT / "data" / "specs.sqlite"
    checks.append(
        Check(
            "OK" if tool_index.exists() else "WARN",
            "Router tool index",
            "data/tools.lance exists"
            if tool_index.exists()
            else "missing; run `uv run python scripts/ingest_tools.py --products all`",
        )
    )
    checks.append(
        Check(
            "OK" if docs_index.exists() and specs_index.exists() else "WARN",
            "Docs/API RAG indexes",
            "data/docs.lance and data/specs.sqlite exist"
            if docs_index.exists() and specs_index.exists()
            else (
                "missing or partial; run `uv run python ingestion/ingest_docs.py` "
                "if RAG lookup is needed"
            ),
        )
    )
    return checks


def _runtime_checks() -> list[Check]:
    host = os.getenv("MCP_HOST", "127.0.0.1")
    raw_port = os.getenv("MCP_PORT", str(DEFAULT_HTTP_PORT))
    try:
        port = int(raw_port)
    except ValueError:
        return [
            Check(
                "FAIL",
                "HTTP router port",
                f"MCP_PORT={raw_port!r} is not an integer",
            )
        ]
    listening = _port_listening(host, port)
    products = os.getenv("CENTRALMCP_PRODUCTS", "").strip()
    toolsets = os.getenv("CENTRALMCP_TOOLSETS")
    mode = os.getenv("CENTRALMCP_ROUTER_MODE")

    mode_detail = (
        "unset in this shell; committed MCP client examples set 'minimal'"
        if mode is None
        else f"CENTRALMCP_ROUTER_MODE={mode!r}; use 'minimal' for low-token clients"
    )
    toolsets_detail = (
        "unset in this shell; committed MCP client examples set 'central,glp,rag'"
        if toolsets is None
        else f"CENTRALMCP_TOOLSETS={toolsets!r}"
    )

    unknown_products = sorted(set(_csv_values(products)) - set(OPTIONAL_PRODUCT_ENVS))
    checks = [
        Check(
            "OK" if mode in (None, "minimal") else "WARN",
            "Router mode",
            mode_detail,
        ),
        Check(
            "OK" if toolsets in (None, "central,glp,rag") else "WARN",
            "Router toolsets",
            toolsets_detail,
        ),
        Check(
            "OK" if not products else "WARN",
            "Optional products",
            "disabled by default"
            if not products
            else f"CENTRALMCP_PRODUCTS={products!r}; optional backends increase tool catalog scope",
        ),
        Check(
            "OK" if not unknown_products else "WARN",
            "Optional product names",
            "all CENTRALMCP_PRODUCTS names are recognized"
            if not unknown_products
            else f"unknown names are ignored by the router: {', '.join(unknown_products)}",
        ),
        Check(
            "OK" if listening else "WARN",
            "HTTP router listener",
            f"{host}:{port} is listening"
            if listening
            else (
                f"{host}:{port} is not listening; start with "
                f"`MCP_PORT={port} bash scripts/run_http_router.sh`"
            ),
        ),
    ]

    for product in sorted(_enabled_optional_products(products, toolsets)):
        required = OPTIONAL_PRODUCT_ENVS[product]
        missing = [name for name in required if not os.getenv(name, "").strip()]
        checks.append(
            Check(
                "OK" if not missing else "WARN",
                f"{product} required env",
                "required env vars are set"
                if not missing
                else f"missing: {', '.join(missing)}",
            )
        )

    return checks


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--strict",
        action="store_true",
        help="exit nonzero on WARN as well as FAIL",
    )
    args = parser.parse_args()
    _load_local_env(ROOT / ".env")

    checks = [
        *_dependency_checks(),
        *_config_checks(),
        *_index_checks(),
        *_runtime_checks(),
    ]

    print("centralmcp local doctor\n")
    for check in checks:
        print(_status_line(check))

    failures = [check for check in checks if check.status == "FAIL"]
    warnings = [check for check in checks if check.status == "WARN"]
    ok_count = len(checks) - len(failures) - len(warnings)
    print(f"\nSummary: {len(failures)} fail, {len(warnings)} warn, {ok_count} ok")

    if failures or (args.strict and warnings):
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
