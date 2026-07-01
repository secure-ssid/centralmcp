#!/usr/bin/env python3
"""Check local centralmcp setup without making network or API calls."""

from __future__ import annotations

import argparse
import importlib.util
import os
import shutil
import socket
import sys
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_HTTP_PORT = 8010


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
    checks.append(
        Check(
            "OK" if local_stdio.exists() else "WARN",
            "Local stdio MCP config",
            ".mcp.json exists"
            if local_stdio.exists()
            else "copy .mcp.json.example to .mcp.json for local stdio clients",
        )
    )

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
    toolsets = os.getenv("CENTRALMCP_TOOLSETS", "central,glp,rag")
    mode = os.getenv("CENTRALMCP_ROUTER_MODE", "minimal")

    return [
        Check(
            "OK" if mode == "minimal" else "WARN",
            "Router mode",
            f"CENTRALMCP_ROUTER_MODE={mode!r}; use 'minimal' for low-token clients",
        ),
        Check(
            "OK",
            "Router toolsets",
            f"CENTRALMCP_TOOLSETS={toolsets!r}",
        ),
        Check(
            "OK" if not products else "WARN",
            "Optional products",
            "disabled by default"
            if not products
            else f"CENTRALMCP_PRODUCTS={products!r}; optional backends increase tool catalog scope",
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


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--strict",
        action="store_true",
        help="exit nonzero on WARN as well as FAIL",
    )
    args = parser.parse_args()

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
