"""Structured security-advisory and product-lifecycle lookup.

The prose RAG index remains useful for explanations, but CVEs, severity,
affected products, notice IDs, SKUs, and lifecycle dates need exact filters.
This module stores those fields beside the OpenAPI tables in ``specs.sqlite``
so the prebuilt release still has one structured SQLite artifact.
"""

from __future__ import annotations

import json
import re
import sqlite3
from pathlib import Path
from typing import Any

from pipeline.clients.specs_index import DB_PATH

ROOT = Path(__file__).resolve().parents[2]
SOURCES_DIR = ROOT / "ingestion" / "sources"

SOURCE_DIRS = {
    "security_advisories": "security",
    "juniper_security_advisories": "security",
    "lifecycle_notices": "lifecycle",
    "juniper_lifecycle": "lifecycle",
}

_SCHEMA = """
DROP TABLE IF EXISTS advisories;
DROP TABLE IF EXISTS lifecycle_events;
DROP TABLE IF EXISTS knowledge_fts;
CREATE TABLE advisories (
    id INTEGER PRIMARY KEY,
    advisory_id TEXT,
    title TEXT NOT NULL,
    severity TEXT,
    status TEXT,
    initial_release TEXT,
    current_release TEXT,
    source_url TEXT,
    source_family TEXT NOT NULL,
    file_path TEXT NOT NULL,
    products TEXT,
    cves TEXT,
    body TEXT NOT NULL
);
CREATE TABLE lifecycle_events (
    id INTEGER PRIMARY KEY,
    notice_id TEXT,
    title TEXT NOT NULL,
    category TEXT,
    published TEXT,
    event_type TEXT,
    source_url TEXT,
    source_family TEXT NOT NULL,
    file_path TEXT NOT NULL,
    product_skus TEXT,
    replacement_skus TEXT,
    body TEXT NOT NULL
);
CREATE VIRTUAL TABLE knowledge_fts USING fts5(
    kind, ref, source_family, body
);
CREATE INDEX idx_advisory_id ON advisories(advisory_id);
CREATE INDEX idx_advisory_severity ON advisories(severity);
CREATE INDEX idx_lifecycle_notice_id ON lifecycle_events(notice_id);
"""

_SOURCE_RE = re.compile(r"<!--\s*source:\s*(.*?)\s*-->")
_BULLET_RE = re.compile(r"^- ([^:]+):\s*(.*)$")
_CVE_RE = re.compile(r"\bCVE-\d{4}-\d{4,}\b", re.IGNORECASE)
_SEVERITY_RANK = {
    "unknown": 0,
    "none": 0,
    "low": 1,
    "medium": 2,
    "moderate": 2,
    "high": 3,
    "important": 3,
    "critical": 4,
}


def _connect(db_path: Path) -> sqlite3.Connection:
    if not db_path.exists():
        raise FileNotFoundError(
            f"Structured RAG index not found at {db_path}; run ingestion/ingest_docs.py"
        )
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    return conn


def _title(text: str, fallback: str) -> str:
    for line in text.splitlines():
        if line.startswith("# "):
            return line[2:].strip() or fallback
    return fallback


def _source_url(text: str) -> str:
    match = _SOURCE_RE.search(text)
    return match.group(1).strip() if match else ""


def _metadata(text: str) -> dict[str, str]:
    values: dict[str, str] = {}
    for line in text.splitlines():
        match = _BULLET_RE.match(line.strip())
        if match:
            values[match.group(1).strip().lower()] = match.group(2).strip()
    return values


def _section_bullets(text: str, heading: str) -> list[str]:
    values: list[str] = []
    active = False
    for line in text.splitlines():
        if line.strip() == f"## {heading}":
            active = True
            continue
        if active and line.startswith("## "):
            break
        if active and line.startswith("- "):
            values.append(line[2:].strip())
    return values


def _json_list(value: str | None) -> list[str]:
    if not value:
        return []
    parsed = json.loads(value)
    return [str(item) for item in parsed] if isinstance(parsed, list) else []


def _parse_lifecycle_skus(text: str) -> tuple[list[str], list[str]]:
    products: set[str] = set()
    replacements: set[str] = set()
    for line in _section_bullets(text, "Affected and replacement products"):
        for part in line.split(";"):
            label, separator, value = part.partition(":")
            if not separator or not value.strip():
                continue
            normalized = label.strip().lower()
            if normalized == "product sku":
                products.add(value.strip())
            elif normalized == "replacement product sku":
                replacements.add(value.strip())
    return sorted(products), sorted(replacements)


def build(
    sources_dir: Path = SOURCES_DIR,
    db_path: Path = DB_PATH,
) -> dict[str, int]:
    """Rebuild advisory/lifecycle tables without disturbing OpenAPI tables."""
    db_path.parent.mkdir(parents=True, exist_ok=True)
    if not db_path.exists():
        sqlite3.connect(db_path).close()
    conn = sqlite3.connect(db_path)
    conn.executescript(_SCHEMA)
    counts = {"advisories": 0, "lifecycle_events": 0, "skipped": 0}

    for source_family, kind in SOURCE_DIRS.items():
        source_dir = sources_dir / source_family
        if not source_dir.exists():
            continue
        for path in sorted(source_dir.rglob("*.md")):
            try:
                text = path.read_text(encoding="utf-8", errors="ignore").strip()
            except OSError:
                counts["skipped"] += 1
                continue
            if not text:
                counts["skipped"] += 1
                continue
            relative = str(path.relative_to(sources_dir))
            title = _title(text, path.stem)
            metadata = _metadata(text)
            source_url = _source_url(text)

            if kind == "security":
                advisory_id = metadata.get("advisory id") or path.stem
                products = _section_bullets(text, "Product catalog")
                cves = sorted({match.upper() for match in _CVE_RE.findall(text)})
                conn.execute(
                    """
                    INSERT INTO advisories (
                        advisory_id, title, severity, status, initial_release,
                        current_release, source_url, source_family, file_path,
                        products, cves, body
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        advisory_id,
                        title,
                        metadata.get("aggregate severity"),
                        metadata.get("status"),
                        metadata.get("initial release"),
                        metadata.get("current release"),
                        source_url,
                        source_family,
                        relative,
                        json.dumps(products),
                        json.dumps(cves),
                        text,
                    ),
                )
                conn.execute(
                    "INSERT INTO knowledge_fts VALUES (?, ?, ?, ?)",
                    ("advisory", advisory_id, source_family, text),
                )
                counts["advisories"] += 1
                continue

            product_skus, replacement_skus = _parse_lifecycle_skus(text)
            notice_id = metadata.get("notice id") or path.stem
            conn.execute(
                """
                INSERT INTO lifecycle_events (
                    notice_id, title, category, published, event_type,
                    source_url, source_family, file_path, product_skus,
                    replacement_skus, body
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    notice_id,
                    title,
                    metadata.get("product category"),
                    metadata.get("published"),
                    "end-of-sale/end-of-life",
                    source_url,
                    source_family,
                    relative,
                    json.dumps(product_skus),
                    json.dumps(replacement_skus),
                    text,
                ),
            )
            conn.execute(
                "INSERT INTO knowledge_fts VALUES (?, ?, ?, ?)",
                ("lifecycle", notice_id, source_family, text),
            )
            counts["lifecycle_events"] += 1

    conn.commit()
    conn.close()
    return counts


def _advisory_row(row: sqlite3.Row) -> dict[str, Any]:
    result = dict(row)
    result["products"] = _json_list(result.pop("products", None))
    result["cves"] = _json_list(result.pop("cves", None))
    result.pop("body", None)
    return result


def lookup_advisories(
    *,
    product: str | None = None,
    cve: str | None = None,
    advisory_id: str | None = None,
    min_severity: str | None = None,
    limit: int = 20,
    db_path: Path = DB_PATH,
) -> list[dict[str, Any]]:
    """Return exact advisory records filtered by identifiers and product text."""
    if not any((product, cve, advisory_id)):
        raise ValueError("provide product, cve, or advisory_id")
    limit = max(1, min(limit, 200))
    minimum = _SEVERITY_RANK.get((min_severity or "unknown").strip().lower())
    if min_severity is not None and minimum is None:
        raise ValueError("min_severity must be low, medium, high, or critical")

    clauses: list[str] = []
    params: list[Any] = []
    if product:
        clauses.append("body LIKE ?")
        params.append(f"%{product}%")
    if cve:
        clauses.append("cves LIKE ?")
        params.append(f"%{cve.upper()}%")
    if advisory_id:
        clauses.append("LOWER(advisory_id) = LOWER(?)")
        params.append(advisory_id)
    sql = "SELECT * FROM advisories WHERE " + " AND ".join(clauses)
    sql += " ORDER BY current_release DESC, advisory_id DESC LIMIT ?"
    params.append(200 if minimum else limit)

    conn = _connect(db_path)
    try:
        rows = conn.execute(sql, params).fetchall()
    except sqlite3.OperationalError as exc:
        raise FileNotFoundError(
            "Structured advisory index is missing; rebuild with ingestion/ingest_docs.py"
        ) from exc
    finally:
        conn.close()
    results = [_advisory_row(row) for row in rows]
    if minimum:
        results = [
            row
            for row in results
            if _SEVERITY_RANK.get(str(row.get("severity") or "").lower(), 0) >= minimum
        ]
    return results[:limit]


def lookup_lifecycle(
    product: str,
    *,
    limit: int = 20,
    db_path: Path = DB_PATH,
) -> list[dict[str, Any]]:
    """Return lifecycle notices whose exact indexed text contains a product/SKU."""
    if not product.strip():
        raise ValueError("product must not be empty")
    limit = max(1, min(limit, 200))
    conn = _connect(db_path)
    try:
        rows = conn.execute(
            """
            SELECT notice_id, title, category, published, event_type,
                   source_url, source_family, file_path, product_skus,
                   replacement_skus
            FROM lifecycle_events
            WHERE body LIKE ?
            ORDER BY published DESC, notice_id DESC
            LIMIT ?
            """,
            (f"%{product}%", limit),
        ).fetchall()
    except sqlite3.OperationalError as exc:
        raise FileNotFoundError(
            "Structured lifecycle index is missing; rebuild with ingestion/ingest_docs.py"
        ) from exc
    finally:
        conn.close()
    return [
        {
            **dict(row),
            "product_skus": _json_list(row["product_skus"]),
            "replacement_skus": _json_list(row["replacement_skus"]),
        }
        for row in rows
    ]
