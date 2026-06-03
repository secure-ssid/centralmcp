"""SQLite structured index over the Aruba OpenAPI specs — exact API lookup.

Vector search is the wrong tool for "what enum values does field X accept" or
"which endpoint configures Y": those need lossless, authoritative answers.
This module parses ingestion/sources/openapi_specs/*.json into SQLite with
FTS5 keyword search, giving exact endpoint / schema / field / enum lookup.
Stdlib only — no new dependencies. See docs/RAG-ARCHITECTURE.md.

Build:   python -m pipeline.clients.specs_index --build
Query:   python -m pipeline.clients.specs_index --query "auth-type"
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
SPECS_DIR = ROOT / "ingestion" / "sources" / "openapi_specs"
DB_PATH = ROOT / "data" / "specs.sqlite"

_SCHEMA = """
CREATE TABLE IF NOT EXISTS endpoints (
    id INTEGER PRIMARY KEY,
    spec_name TEXT, spec_file TEXT, server TEXT,
    method TEXT, path TEXT, summary TEXT, description TEXT
);
CREATE TABLE IF NOT EXISTS schemas (
    id INTEGER PRIMARY KEY,
    spec_name TEXT, spec_file TEXT, name TEXT, description TEXT
);
CREATE TABLE IF NOT EXISTS fields (
    id INTEGER PRIMARY KEY,
    spec_name TEXT, spec_file TEXT, schema_name TEXT,
    field_name TEXT, path TEXT, type TEXT, description TEXT,
    enums TEXT, enum_descriptions TEXT
);
CREATE VIRTUAL TABLE IF NOT EXISTS fts USING fts5(
    kind, spec_file, ref, body
);
CREATE INDEX IF NOT EXISTS idx_fields_name ON fields(field_name);
CREATE INDEX IF NOT EXISTS idx_fields_schema ON fields(schema_name);
CREATE INDEX IF NOT EXISTS idx_endpoints_path ON endpoints(path);
"""


def connect(db_path: Path = DB_PATH) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    return conn


# ---------------------------------------------------------------------------
# Build
# ---------------------------------------------------------------------------

def _walk_fields(node: Any, path: str, depth: int = 0):
    """Recursively yield (field_path, field_name, fdef) from a schema node.

    Fields hide inside items/allOf/anyOf/oneOf nesting (e.g.
    properties/profile/items/allOf[8]/properties/auth-type), so a
    top-level-properties walk misses most of them.
    """
    if depth > 12 or not isinstance(node, dict):
        return
    for field, fdef in (node.get("properties") or {}).items():
        if not isinstance(fdef, dict):
            continue
        fpath = f"{path}.{field}" if path else field
        yield fpath, field, fdef
        yield from _walk_fields(fdef, fpath, depth + 1)
    items = node.get("items")
    if isinstance(items, dict):
        yield from _walk_fields(items, f"{path}[]", depth + 1)
    for comb in ("allOf", "anyOf", "oneOf"):
        for sub in node.get(comb) or []:
            yield from _walk_fields(sub, path, depth + 1)


def build(specs_dir: Path = SPECS_DIR, db_path: Path = DB_PATH) -> dict[str, int]:
    """Parse all OpenAPI specs into the SQLite index. Recreates tables."""
    db_path.parent.mkdir(parents=True, exist_ok=True)
    if db_path.exists():
        db_path.unlink()
    conn = connect(db_path)
    conn.executescript(_SCHEMA)

    counts = {"specs": 0, "endpoints": 0, "schemas": 0, "fields": 0, "skipped": 0}
    for path in sorted(specs_dir.glob("*.json")):
        try:
            spec = json.loads(path.read_text(encoding="utf-8", errors="ignore"))
        except Exception:
            counts["skipped"] += 1
            continue
        counts["specs"] += 1
        spec_name = spec.get("info", {}).get("title", path.stem)
        spec_file = path.name
        servers = spec.get("servers") or []
        server = servers[0].get("url", "") if servers else ""

        for api_path, item in (spec.get("paths") or {}).items():
            if not isinstance(item, dict):
                continue
            for method, op in item.items():
                if method not in ("get", "post", "put", "patch", "delete") or not isinstance(op, dict):
                    continue
                summary = op.get("summary", "")
                desc = op.get("description", "")
                conn.execute(
                    "INSERT INTO endpoints (spec_name, spec_file, server, method, path, summary, description) VALUES (?,?,?,?,?,?,?)",
                    (spec_name, spec_file, server, method.upper(), api_path, summary, desc),
                )
                conn.execute(
                    "INSERT INTO fts (kind, spec_file, ref, body) VALUES (?,?,?,?)",
                    ("endpoint", spec_file, f"{method.upper()} {api_path}",
                     f"{spec_name} {api_path} {summary} {desc}"),
                )
                counts["endpoints"] += 1

        for schema_name, schema in (spec.get("components", {}).get("schemas") or {}).items():
            if not isinstance(schema, dict):
                continue
            s_desc = schema.get("description", "")
            conn.execute(
                "INSERT INTO schemas (spec_name, spec_file, name, description) VALUES (?,?,?,?)",
                (spec_name, spec_file, schema_name, s_desc),
            )
            counts["schemas"] += 1
            prop_texts = []
            for fpath, field, fdef in _walk_fields(schema, ""):
                enums = fdef.get("enum")
                enum_desc = fdef.get("x-enumDescriptions")
                conn.execute(
                    "INSERT INTO fields (spec_name, spec_file, schema_name, field_name, path, type, description, enums, enum_descriptions) VALUES (?,?,?,?,?,?,?,?,?)",
                    (spec_name, spec_file, schema_name, field, fpath,
                     str(fdef.get("type", "")), fdef.get("description", ""),
                     json.dumps(enums) if enums else None,
                     json.dumps(enum_desc) if enum_desc else None),
                )
                counts["fields"] += 1
                if enums or fdef.get("description"):
                    prop_texts.append(f"{field} {fdef.get('description','')} {' '.join(map(str, enums or []))}")
            conn.execute(
                "INSERT INTO fts (kind, spec_file, ref, body) VALUES (?,?,?,?)",
                ("schema", spec_file, schema_name,
                 f"{spec_name} {schema_name} {s_desc} {' '.join(prop_texts)}"),
            )
    conn.commit()
    conn.close()
    return counts


# ---------------------------------------------------------------------------
# Query helpers (used by the lookup_api MCP tool)
# ---------------------------------------------------------------------------

def _fts_escape(q: str) -> str:
    """Quote each term so FTS5 treats hyphens/slashes literally."""
    terms = [t for t in q.replace('"', " ").split() if t]
    return " ".join(f'"{t}"' for t in terms)


def search(query: str, kind: str | None = None, limit: int = 10,
           db_path: Path = DB_PATH) -> list[dict[str, Any]]:
    """FTS keyword search across endpoints + schemas. kind: endpoint|schema."""
    conn = connect(db_path)
    sql = "SELECT kind, spec_file, ref, snippet(fts, 3, '', '', '…', 24) AS snippet FROM fts WHERE fts MATCH ?"
    params: list[Any] = [_fts_escape(query)]
    if kind:
        sql += " AND kind = ?"
        params.append(kind)
    sql += " LIMIT ?"
    params.append(limit)
    try:
        rows = conn.execute(sql, params).fetchall()
    finally:
        conn.close()
    return [dict(r) for r in rows]


def get_endpoint(path_contains: str, method: str | None = None,
                 limit: int = 10, db_path: Path = DB_PATH) -> list[dict[str, Any]]:
    """Exact-ish endpoint lookup by path substring (and optional method)."""
    conn = connect(db_path)
    sql = "SELECT spec_name, spec_file, server, method, path, summary, description FROM endpoints WHERE path LIKE ?"
    params: list[Any] = [f"%{path_contains}%"]
    if method:
        sql += " AND method = ?"
        params.append(method.upper())
    sql += " LIMIT ?"
    params.append(limit)
    try:
        rows = conn.execute(sql, params).fetchall()
    finally:
        conn.close()
    return [dict(r) for r in rows]


def get_schema(name_contains: str, limit: int = 5,
               db_path: Path = DB_PATH) -> list[dict[str, Any]]:
    """Schema lookup with its full field list (types, enums)."""
    conn = connect(db_path)
    try:
        schemas = conn.execute(
            "SELECT spec_name, spec_file, name, description FROM schemas WHERE name LIKE ? LIMIT ?",
            (f"%{name_contains}%", limit),
        ).fetchall()
        out = []
        for s in schemas:
            fields = conn.execute(
                "SELECT field_name, path, type, description, enums, enum_descriptions FROM fields WHERE schema_name = ? AND spec_file = ?",
                (s["name"], s["spec_file"]),
            ).fetchall()
            out.append({
                **dict(s),
                "fields": [
                    {**dict(f),
                     "enums": json.loads(f["enums"]) if f["enums"] else None,
                     "enum_descriptions": json.loads(f["enum_descriptions"]) if f["enum_descriptions"] else None}
                    for f in fields
                ],
            })
    finally:
        conn.close()
    return out


def get_enum(field_name: str, schema_contains: str | None = None,
             limit: int = 10, db_path: Path = DB_PATH) -> list[dict[str, Any]]:
    """Authoritative enum values for a field, across all specs."""
    conn = connect(db_path)
    sql = ("SELECT spec_name, spec_file, schema_name, field_name, path, type, description, enums, enum_descriptions "
           "FROM fields WHERE field_name = ? AND enums IS NOT NULL")
    params: list[Any] = [field_name]
    if schema_contains:
        sql += " AND schema_name LIKE ?"
        params.append(f"%{schema_contains}%")
    sql += " LIMIT ?"
    params.append(limit)
    try:
        rows = conn.execute(sql, params).fetchall()
    finally:
        conn.close()
    return [
        {**dict(r),
         "enums": json.loads(r["enums"]),
         "enum_descriptions": json.loads(r["enum_descriptions"]) if r["enum_descriptions"] else None}
        for r in rows
    ]


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--build", action="store_true")
    ap.add_argument("--query")
    ap.add_argument("--enum")
    args = ap.parse_args()
    if args.build:
        print(json.dumps(build(), indent=2))
    if args.query:
        print(json.dumps(search(args.query), indent=2))
    if args.enum:
        print(json.dumps(get_enum(args.enum), indent=2))
