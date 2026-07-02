"""Every backend's standalone entrypoint must install middleware.

glp.py (core) and uxi.py (optional) were each missing install_middleware(...)
in their __main__ block while every sibling backend had it — those tools got
zero null-stripping or rate-limiting when run standalone (e.g. via
.cursor/mcp.dev.json). This is a static source check (not an import-time
check) since these calls live inside `if __name__ == "__main__":` guards that
never execute on import.
"""

from __future__ import annotations

from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
CORE_BACKENDS = ["monitoring", "config", "ops", "nac", "glp", "rag"]
OPTIONAL_BACKENDS = ["clearpass", "mist", "apstra", "aos8", "edgeconnect", "uxi"]


def test_every_core_backend_installs_middleware_in_main_block():
    missing = []
    for name in CORE_BACKENDS:
        source = (REPO_ROOT / "mcp_servers" / f"{name}.py").read_text()
        main_block = source.split('if __name__ == "__main__":', 1)
        if len(main_block) != 2 or "install_middleware(" not in main_block[1]:
            missing.append(name)
    assert not missing, f"missing install_middleware(...) in __main__ block: {missing}"


def test_every_optional_backend_installs_middleware_in_main_block():
    missing = []
    for name in OPTIONAL_BACKENDS:
        source = (REPO_ROOT / "mcp_servers" / f"{name}.py").read_text()
        main_block = source.split('if __name__ == "__main__":', 1)
        if len(main_block) != 2 or "install_middleware(" not in main_block[1]:
            missing.append(name)
    assert not missing, f"missing install_middleware(...) in __main__ block: {missing}"
