from pathlib import Path

PYPROJECT = Path(__file__).resolve().parents[2] / "pyproject.toml"
REPO_ROOT = PYPROJECT.parents[0]
ACTIVE_CODE_DIRS = ("mcp_servers", "pipeline", "scripts")


def _project_dependencies(pyproject_text: str) -> list[str]:
    in_project_dependencies = False
    dependencies: list[str] = []

    for line in pyproject_text.splitlines():
        stripped = line.strip()
        if stripped == "dependencies = [":
            in_project_dependencies = True
            continue
        if in_project_dependencies and stripped == "]":
            break
        if in_project_dependencies and stripped.startswith('"'):
            dependencies.append(stripped.split('"', 2)[1])

    return dependencies


def test_project_name_matches_repo_name():
    text = PYPROJECT.read_text()

    assert 'name = "centralmcp"' in text
    assert 'name = "api-central"' not in text


def test_active_code_does_not_use_legacy_project_aliases():
    legacy_aliases = ("api-central", "API-Central", "central-mcp-server")
    violations: list[str] = []

    for dirname in ACTIVE_CODE_DIRS:
        for path in sorted((REPO_ROOT / dirname).rglob("*.py")):
            text = path.read_text()
            for alias in legacy_aliases:
                if alias in text:
                    violations.append(f"{path.relative_to(REPO_ROOT)} contains {alias}")

    assert violations == []


def test_active_runtime_code_does_not_reference_removed_qdrant_backend():
    violations: list[str] = []

    for dirname in ACTIVE_CODE_DIRS:
        for path in sorted((REPO_ROOT / dirname).rglob("*.py")):
            if "qdrant" in path.read_text().lower():
                violations.append(str(path.relative_to(REPO_ROOT)))

    assert violations == []


def test_direct_runtime_dependencies_do_not_include_pycentral_or_requests():
    dependencies = _project_dependencies(PYPROJECT.read_text())
    names = {
        dependency.split("[", 1)[0].split(">", 1)[0].split("=", 1)[0]
        for dependency in dependencies
    }

    assert "pycentral" not in names
    assert "requests" not in names
    assert "httpx" in names
