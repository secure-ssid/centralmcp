import re
import subprocess
from pathlib import Path
from urllib.parse import unquote

REPO_ROOT = Path(__file__).resolve().parents[2]
MARKDOWN_LINK_RE = re.compile(r"(?<!!)\[[^\]]+\]\(([^)]+)\)")


def _tracked_markdown_files() -> list[Path]:
    output = subprocess.check_output(
        ["git", "ls-files", "*.md"],
        cwd=REPO_ROOT,
        text=True,
    )
    return [REPO_ROOT / line for line in output.splitlines()]


def test_tracked_markdown_local_links_resolve():
    missing = []

    for path in _tracked_markdown_files():
        for match in MARKDOWN_LINK_RE.finditer(path.read_text(errors="replace")):
            target = match.group(1).split("#", 1)[0].strip()
            if not target or target.startswith(("http://", "https://", "mailto:", "#")):
                continue
            if target.startswith("<") and target.endswith(">"):
                target = target[1:-1]

            candidate = (path.parent / unquote(target)).resolve()
            if not candidate.exists():
                missing.append(f"{path.relative_to(REPO_ROOT)} -> {target}")

    assert missing == []


def test_validation_docs_describe_current_guard_coverage():
    expected_phrases = [
        "committed low-token MCP config examples",
        "local-only config files",
        "router product/toolset docs",
        "public tool-count claims",
        "tool-count docstrings",
        "tracked Markdown local links",
    ]
    combined_docs = "\n".join(
        [
            (REPO_ROOT / "README.md").read_text(),
            (REPO_ROOT / "docs" / "README.md").read_text(),
        ]
    )

    for phrase in expected_phrases:
        assert phrase in combined_docs


def test_current_rag_architecture_avoids_removed_qdrant_implementation_names():
    architecture = (REPO_ROOT / "docs" / "architecture" / "RAG-ARCHITECTURE.md").read_text()

    assert "qdrant_client" not in architecture
    assert "qdrant-client" not in architecture
