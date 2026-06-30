from pathlib import Path

PYPROJECT = Path(__file__).resolve().parents[2] / "pyproject.toml"


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


def test_direct_runtime_dependencies_do_not_include_pycentral_or_requests():
    dependencies = _project_dependencies(PYPROJECT.read_text())
    names = {dependency.split("[", 1)[0].split(">", 1)[0].split("=", 1)[0] for dependency in dependencies}

    assert "pycentral" not in names
    assert "requests" not in names
    assert "httpx" in names
