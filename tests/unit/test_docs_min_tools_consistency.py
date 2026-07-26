"""Guard against the documented complete-catalog ``--min-tools`` value drifting.

``scripts/validate_release.py --catalog-products all --strict-rag
--strict-tool-index --min-tools <N>`` is the release gate every current doc
(README, CONTRIBUTING, getting-started) copy/pastes as the "validate the
complete backend catalog" example. ``<N>`` must track the complete backend
tool catalog total documented in ``docs/capability-gap-matrix.md`` and
``docs/tool-catalog.md`` (currently 6,700) -- not the CLI's low unit-test
default (``scripts.validate_release._DEFAULT_MIN_TOOLS``, currently 204).

Dated release-notes snapshots (``docs/release-notes-*.md``) are historical
records of the floor at release time and are intentionally excluded: they
must stay frozen, not track future catalog growth.
"""

from __future__ import annotations

import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]

# The complete backend tool catalog total -- see docs/capability-gap-matrix.md
# ("Adding 574 curated tools yields 6,700 executable backend tools") and
# docs/tool-catalog.md ("centralmcp registers 6,700 backend tools").
EXPECTED_COMPLETE_CATALOG_MIN_TOOLS = 6700

_COMMAND_PATTERN = re.compile(
    r"--catalog-products all --strict-rag --strict-tool-index --min-tools (\d+)"
)

_EXCLUDED_TOP_LEVEL_DIRS = {".git", "data", "dist", "node_modules"}


def _current_doc_files() -> list[Path]:
    """Every tracked markdown file except historical release-notes snapshots."""
    docs = []
    for path in sorted(REPO_ROOT.rglob("*.md")):
        rel = path.relative_to(REPO_ROOT)
        if rel.parts[0] in _EXCLUDED_TOP_LEVEL_DIRS:
            continue
        if rel.name.startswith("release-notes-"):
            continue
        docs.append(path)
    return docs


def test_complete_catalog_min_tools_examples_match_current_floor():
    checked = []
    for path in _current_doc_files():
        text = path.read_text(encoding="utf-8")
        matches = _COMMAND_PATTERN.findall(text)
        for value in matches:
            assert int(value) == EXPECTED_COMPLETE_CATALOG_MIN_TOOLS, (
                f"{path.relative_to(REPO_ROOT)} documents "
                f"--min-tools {value}, expected "
                f"{EXPECTED_COMPLETE_CATALOG_MIN_TOOLS} (the complete backend "
                "tool catalog total). Update the example or, if the catalog "
                "total legitimately changed, update "
                "EXPECTED_COMPLETE_CATALOG_MIN_TOOLS here alongside "
                "docs/capability-gap-matrix.md and docs/tool-catalog.md."
            )
            checked.append(path)

    assert checked, (
        "No current doc matched the complete-catalog validate_release.py "
        "example command -- the wording likely changed and this regression "
        "test needs its pattern updated so it keeps guarding real drift."
    )


def test_current_docs_referencing_min_tools_use_the_complete_catalog_form():
    """Any current doc mentioning --min-tools at all uses the full command.

    Catches a doc that adds a bare ``--min-tools N`` example without the
    accompanying ``--catalog-products all --strict-rag --strict-tool-index``
    flags, which would otherwise silently skip the check above.
    """
    bare_pattern = re.compile(r"--min-tools \d+")
    for path in _current_doc_files():
        text = path.read_text(encoding="utf-8")
        bare_matches = bare_pattern.findall(text)
        full_matches = _COMMAND_PATTERN.findall(text)
        assert len(bare_matches) == len(full_matches), (
            f"{path.relative_to(REPO_ROOT)} has a --min-tools example that "
            "is not paired with --catalog-products all --strict-rag "
            "--strict-tool-index; add the full command or update this test."
        )
