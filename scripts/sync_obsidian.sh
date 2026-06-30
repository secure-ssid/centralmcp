#!/usr/bin/env bash
# Sync key project files to the Obsidian vault.
# Run manually or triggered by git post-commit hook.

VAULT="/Users/stephenchoate/Documents/Central-MCP-Obsidian"
PROJECT="/Users/stephenchoate/Documents/Claude Projects/Active/MCP/centralmcp"
DEST="$VAULT/Projects/Central MCP"

mkdir -p "$DEST"

# Core docs
for f in README.md CLAUDE.md; do
  [ -f "$PROJECT/$f" ] && cp "$PROJECT/$f" "$DEST/$f" && echo "synced $f"
done

# docs/ folder
if [ -d "$PROJECT/docs" ]; then
  mkdir -p "$DEST/docs"
  cp -R "$PROJECT"/docs/. "$DEST/docs/"
  echo "synced docs/"
fi

# Generate a tool index note from mcp_servers
python3 - <<'PYEOF'
import ast, os
from pathlib import Path

project = Path("/Users/stephenchoate/Documents/Claude Projects/Active/MCP/centralmcp")
vault = Path("/Users/stephenchoate/Documents/Central-MCP-Obsidian/Projects/Central MCP")

lines = ["# MCP Tool Index", "", "Auto-generated — do not edit manually.", ""]

servers = [
    ("monitoring.py", "aruba-monitoring"),
    ("config.py", "aruba-config"),
    ("ops.py", "aruba-ops"),
    ("nac.py", "aruba-nac"),
    ("glp.py", "aruba-glp"),
    ("rag.py", "aruba-rag"),
]

for fname, server in servers:
    fpath = project / "mcp_servers" / fname
    if not fpath.exists():
        continue
    tree = ast.parse(fpath.read_text())
    tools = []
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            # Check if decorated with @mcp.tool
            for dec in node.decorator_list:
                if isinstance(dec, ast.Call) and hasattr(dec.func, 'attr') and dec.func.attr == 'tool':
                    doc = ast.get_docstring(node) or ""
                    first_line = doc.split("\n")[0].strip() if doc else ""
                    tools.append((node.name, first_line))
    lines.append(f"## {server} ({len(tools)} tools)")
    for name, desc in sorted(tools):
        lines.append(f"- `{name}` — {desc}" if desc else f"- `{name}`")
    lines.append("")

(vault / "Tool Index.md").write_text("\n".join(lines))
print("synced Tool Index.md")
PYEOF

echo "Obsidian sync complete."
