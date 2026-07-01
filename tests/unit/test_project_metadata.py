import ast
from pathlib import Path

PYPROJECT = Path(__file__).resolve().parents[2] / "pyproject.toml"
REPO_ROOT = PYPROJECT.parents[0]
ACTIVE_CODE_DIRS = ("mcp_servers", "pipeline", "scripts")
SCRAPER = REPO_ROOT / "ingestion" / "scrape.py"
BOUNDED_GENERIC_GET_TOOLS = {
    "mcp_servers/glp.py": "glp_get",
    "mcp_servers/clearpass.py": "clearpass_get",
    "mcp_servers/mist.py": "mist_get",
    "mcp_servers/apstra.py": "apstra_get",
    "mcp_servers/aos8.py": "aos8_get",
    "mcp_servers/edgeconnect.py": "edgeconnect_get",
}
MAX_MCP_LIST_DEFAULT = 200


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


def _function_node(tree: ast.Module, name: str) -> ast.FunctionDef | ast.AsyncFunctionDef:
    for node in tree.body:
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef) and node.name == name:
            return node
    raise AssertionError(f"missing function {name}")


def _calls_name(node: ast.AST, name: str) -> bool:
    for child in ast.walk(node):
        if isinstance(child, ast.Call) and isinstance(child.func, ast.Name):
            if child.func.id == name:
                return True
    return False


def _is_mcp_tool(node: ast.FunctionDef | ast.AsyncFunctionDef) -> bool:
    for decorator in node.decorator_list:
        if (
            isinstance(decorator, ast.Call)
            and isinstance(decorator.func, ast.Attribute)
            and decorator.func.attr == "tool"
            and isinstance(decorator.func.value, ast.Name)
            and decorator.func.value.id == "mcp"
        ):
            return True
    return False


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


def test_active_runtime_code_does_not_reference_pycentral_sdk():
    violations: list[str] = []

    for dirname in ACTIVE_CODE_DIRS:
        for path in sorted((REPO_ROOT / dirname).rglob("*.py")):
            if "pycentral" in path.read_text().lower():
                violations.append(str(path.relative_to(REPO_ROOT)))

    assert violations == []


def test_doc_scraper_excludes_pycentral_specific_pages():
    assert "pycentral" not in SCRAPER.read_text().lower()


def test_direct_runtime_dependencies_do_not_include_pycentral_or_requests():
    dependencies = _project_dependencies(PYPROJECT.read_text())
    names = {
        dependency.split("[", 1)[0].split(">", 1)[0].split("=", 1)[0]
        for dependency in dependencies
    }

    assert "pycentral" not in names
    assert "requests" not in names
    assert "httpx" in names


def test_generic_read_only_get_tools_bound_list_responses():
    violations: list[str] = []

    for relative_path, function_name in BOUNDED_GENERIC_GET_TOOLS.items():
        path = REPO_ROOT / relative_path
        function = _function_node(ast.parse(path.read_text()), function_name)
        arg_names = [arg.arg for arg in function.args.args]

        if "limit" not in arg_names or "offset" not in arg_names:
            violations.append(f"{relative_path}:{function_name} missing limit/offset")
        docstring = ast.get_docstring(function) or ""
        if "limit" not in docstring or "offset" not in docstring:
            violations.append(
                f"{relative_path}:{function_name} docstring does not describe limit/offset"
            )
        if not _calls_name(function, "bound_collection_response"):
            violations.append(
                f"{relative_path}:{function_name} does not call bound_collection_response"
            )

    assert violations == []


def test_mcp_tool_limit_defaults_do_not_exceed_project_bound():
    violations: list[str] = []

    for path in sorted((REPO_ROOT / "mcp_servers").glob("*.py")):
        tree = ast.parse(path.read_text())
        for node in ast.walk(tree):
            if not isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
                continue
            if not _is_mcp_tool(node):
                continue
            defaults = [None] * (len(node.args.args) - len(node.args.defaults))
            defaults.extend(node.args.defaults)
            for arg, default in zip(node.args.args, defaults):
                if arg.arg != "limit" or not isinstance(default, ast.Constant):
                    continue
                if isinstance(default.value, int) and default.value > MAX_MCP_LIST_DEFAULT:
                    violations.append(
                        f"{path.relative_to(REPO_ROOT)}:{node.name} limit default "
                        f"{default.value} exceeds {MAX_MCP_LIST_DEFAULT}"
                    )

    assert violations == []
