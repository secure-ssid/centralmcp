import ast
import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
TOOL_COUNT_RE = re.compile(r"\((\d+) tools\)")


def _registered_tool_count(tree: ast.Module) -> int:
    count = 0
    for node in ast.walk(tree):
        if not isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
            continue
        for decorator in node.decorator_list:
            if (
                isinstance(decorator, ast.Call)
                and isinstance(decorator.func, ast.Attribute)
                and decorator.func.attr == "tool"
                and isinstance(decorator.func.value, ast.Name)
                and decorator.func.value.id == "mcp"
            ):
                count += 1
    return count


def test_module_docstring_tool_counts_match_registered_tools():
    counted_modules = []

    for path in sorted((REPO_ROOT / "mcp_servers").glob("*.py")):
        tree = ast.parse(path.read_text())
        docstring = ast.get_docstring(tree) or ""
        first_line = docstring.splitlines()[0] if docstring else ""
        match = TOOL_COUNT_RE.search(first_line)
        if match is None:
            continue

        counted_modules.append(path.name)
        expected = int(match.group(1))
        assert _registered_tool_count(tree) == expected, path.relative_to(REPO_ROOT)

    assert counted_modules
