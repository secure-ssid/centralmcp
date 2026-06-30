"""SQLite structured index over the Aruba OpenAPI specs — exact API lookup.

Vector search is the wrong tool for "what enum values does field X accept" or
"which endpoint configures Y": those need lossless, authoritative answers.
This module parses ingestion/sources/openapi_specs/*.json into SQLite with
FTS5 keyword search, giving exact endpoint / schema / field / enum lookup.
Stdlib only — no new dependencies. See docs/architecture/RAG-ARCHITECTURE.md.

Build:   python -m pipeline.clients.specs_index --build
Query:   python -m pipeline.clients.specs_index --query "auth-type"
"""

from __future__ import annotations

import json
import re
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


# ---------------------------------------------------------------------------
# High-level natural-language lookup (backs the lookup_api MCP tool)
# ---------------------------------------------------------------------------

# Question scaffolding + terms so generic in an API-spec corpus they only add
# noise ("config", "endpoint", "value" appear in nearly every row).
_STOPWORDS = frozenset("""
a an the is are was were be been being do does did can could should would will
what which who whose when where why how there here this that these those it its
i you we they my your of for to in on at by with from into over under about as
and or not no if then than but exist exists existing available use used uses
using new central api apis valid value values field fields enum enums key keys
required need needed needs http https method methods url uri endpoint endpoints
response request list lists get read set sets type types kind name names
configure configures configured configuration config
accept accepts allow allows allowed support supports supported
""".split())

_TOKEN_RE = re.compile(r"[a-z0-9][a-z0-9\-._]*")

# Tight, curated domain synonyms — Aruba docs use these interchangeably
# (specs say "wlan"/"essid" where users say "SSID"). Synonyms join the SAME
# concept group, so they corroborate a match without inflating the
# distinct-concept count that the relevance threshold counts.
_SYNONYMS: dict[str, list[str]] = {
    "ssid": ["wlan", "essid"],
    "wlan": ["ssid", "essid"],
    "gw": ["gateway"],
    "gateway": ["gw"],
}


def _stem_variants(word: str) -> list[str]:
    """Singular-fold variants of a word. "policies" needs both spellings since
    neither "policy" nor "policie" alone covers the other as a token prefix;
    a plain trailing-s plural folds to one prefix-safe stem."""
    if word.endswith("ies") and len(word) > 4:
        return [word[:-3] + "y", word[:-1]]
    if word.endswith("s") and len(word) > 3:
        return [word[:-1]]
    return [word]


def _query_groups(query: str) -> list[list[str]]:
    """Concept groups of lightly-stemmed terms from a natural-language query.

    Each non-stopword token becomes ONE group holding the whole token plus, for
    hyphenated tokens, its components ("device-firmware-upgrade" also yields
    device/firmware/upgrade for recall when the corpus spells it differently).
    Scoring counts GROUPS hit, not raw stems — otherwise a single hyphenated
    concept would corroborate itself through its own components and defeat the
    relevance threshold.
    """
    groups: list[list[str]] = []
    seen_tokens: set[str] = set()
    for tok in _TOKEN_RE.findall(query.lower()):
        tok = tok.strip("-._")
        if not tok or tok in seen_tokens:
            continue
        seen_tokens.add(tok)
        stems: list[str] = []
        for part in [tok] + (tok.split("-") if "-" in tok else []):
            # Check the RAW word against stopwords too — stemming first would
            # let scaffolding sneak through ("does" -> "doe" is not a stopword).
            if part in _STOPWORDS:
                continue
            for v in _stem_variants(part):
                if len(v) < 3 or v.isdigit() or v in _STOPWORDS or v in stems:
                    continue
                stems.append(v)
        for s in list(stems):
            for syn in _SYNONYMS.get(s, []):
                if syn not in stems:
                    stems.append(syn)
        if stems:
            groups.append(stems)
    return groups


def _fmt_endpoint(row: dict[str, Any]) -> str:
    url = f"{row['server']}{row['path']}" if row.get("server") else row["path"]
    desc = (row.get("description") or "")[:300]
    return f"{row['method']} {url} — {row.get('summary', '')} {desc}".strip()


def _fmt_enum_field(row: dict[str, Any]) -> str:
    enums = row.get("enums") or []
    text = (f"{row['schema_name']}.{row['path']} ({row['spec_name']}) "
            f"type={row.get('type', '')}: {(row.get('description') or '')[:200]} "
            f"Enum: {', '.join(map(str, enums[:24]))}")
    return text.strip()


def lookup(query: str, top_k: int = 10, db_path: Path = DB_PATH) -> list[dict[str, Any]]:
    """Exact API lookup for a natural-language question. Returns [] when the
    specs have no confident answer (caller should fall back to prose search).

    Three strategies, merged and de-duplicated:
      1. exact enum/field match for field-like terms (get_enum)
      2. exact endpoint match for hyphenated path-like tokens, with one
         progressive right-trim (device-firmware-upgrade -> device-firmware)
      3. FTS prefix search re-ranked by how many distinct query terms the row
         actually contains (bm25 alone ranks short generic rows too high)

    Hits are {"text", "source", "file_path", "kind", "score"} — file_path is a
    precise locator (openapi_specs/<spec_file>#<ref>) for source attribution.
    """
    if not Path(db_path).exists():
        raise FileNotFoundError(
            f"specs index missing at {db_path} — build it with "
            "`python -m pipeline.clients.specs_index --build`"
        )
    try:
        return _lookup(query, top_k, db_path)
    except sqlite3.Error as exc:
        # A present-but-unreadable DB (corrupt file, or the empty/schemaless
        # window an interrupted --build leaves behind) must not crash the MCP
        # tool — surface it the same way as a missing index.
        raise FileNotFoundError(
            f"specs index at {db_path} is unreadable ({exc}) — rebuild it with "
            "`python -m pipeline.clients.specs_index --build`"
        ) from exc


def _lookup(query: str, top_k: int, db_path: Path) -> list[dict[str, Any]]:
    groups = _query_groups(query)
    if not groups:
        return []
    n_terms = len(groups)
    threshold = 1 if n_terms == 1 else (2 if n_terms <= 3 else 3)
    flat: list[str] = []
    for g in groups:
        for s in g:
            if s not in flat:
                flat.append(s)

    def _present(stem: str, low: str) -> bool:
        # Short stems are substring-fragile ("mac" is inside "machine") —
        # require a full word. Longer stems keep substring semantics so
        # "layer2" finds Layer2VlanSchema and "personal" finds WPA2_PERSONAL.
        if len(stem) <= 3:
            return bool(re.search(rf"\b{re.escape(stem)}\b", low))
        return stem in low

    def matched(blob: str) -> int:
        low = blob.lower()
        return sum(1 for g in groups if any(_present(s, low) for s in g))

    hits: dict[tuple[str, str], dict[str, Any]] = {}

    def add(kind: str, spec_file: str, ref: str, text: str, score: int, exact: bool) -> None:
        cur = hits.get((spec_file, ref))
        if cur is None:
            hits[(spec_file, ref)] = {
                "text": text,
                "source": "openapi_specs",
                "file_path": f"openapi_specs/{spec_file}#{ref}",
                "kind": kind,
                "score": score,
                "_exact": exact,
            }
        else:
            # Same row reached by two strategies: keep the best evidence of each
            cur["score"] = max(cur["score"], score)
            cur["_exact"] = cur["_exact"] or exact

    # 1. Exact enum/field lookups for field-like terms. Generous limit — the
    # same field often appears in near-duplicate Get/non-Get schema pairs per
    # spec, and a tight limit crowds out the spec file the query is about
    # (e.g. limit=4 returned only ap-uplink/mesh "opmode", never wlan's).
    for term in flat:
        if len(term) < 4:
            continue
        for row in get_enum(term, limit=16, db_path=db_path):
            blob = (f"{row['spec_file']} {row['schema_name']} {row['path']} "
                    f"{row.get('description') or ''} {' '.join(map(str, row.get('enums') or []))} "
                    f"{row.get('enum_descriptions') or ''}")
            add("enum", row["spec_file"], f"{row['schema_name']}.{row['path']}",
                _fmt_enum_field(row), matched(blob), exact=True)

    # 2. Exact endpoint lookups for hyphenated tokens (one progressive trim)
    for term in (g[0] for g in groups if "-" in g[0]):
        for candidate in (term, term.rsplit("-", 1)[0]):
            if "-" not in candidate:  # trimmed to a single bare word — too generic
                continue
            rows = get_endpoint(candidate, limit=6, db_path=db_path)
            if rows:
                for row in rows:
                    blob = (f"{row['spec_file']} {row['method']} {row['path']} "
                            f"{row.get('summary') or ''} {row.get('description') or ''}")
                    add("endpoint", row["spec_file"], f"{row['method']} {row['path']}",
                        _fmt_endpoint(row), matched(blob), exact=True)
                break

    # 3. FTS prefix search, re-ranked by distinct-concept coverage
    conn = connect(db_path)
    try:
        # Deep candidate fetch: bm25 penalizes long bodies, so with a shallow
        # cap the big multi-term schema rows (the ones that actually clear the
        # coverage threshold) get starved by hundreds of short single-term rows.
        match_expr = " OR ".join(f'"{s}"*' for s in flat)
        rows = conn.execute(
            "SELECT kind, spec_file, ref, body FROM fts WHERE fts MATCH ? "
            "ORDER BY bm25(fts) LIMIT 400",
            (match_expr,),
        ).fetchall()
        for r in rows:
            score = matched(f"{r['spec_file']} {r['body']}")
            if score < threshold:
                continue
            if r["kind"] == "endpoint":
                method, _, path = r["ref"].partition(" ")
                ep = get_endpoint(path, method=method, limit=1, db_path=db_path)
                text = _fmt_endpoint(ep[0]) if ep else r["ref"]
            else:
                # Schema hit: surface only the fields the query actually asked about
                fields = conn.execute(
                    "SELECT field_name, path, type, description, enums FROM fields "
                    "WHERE schema_name = ? AND spec_file = ? LIMIT 400",
                    (r["ref"], r["spec_file"]),
                ).fetchall()
                parts = []
                for f in fields:
                    enums = json.loads(f["enums"]) if f["enums"] else []
                    fblob = f"{f['field_name']} {f['description'] or ''} {' '.join(map(str, enums))}"
                    if matched(fblob):
                        enum_sfx = f" enum: {', '.join(map(str, enums[:24]))}" if enums else ""
                        parts.append(f"{f['path']} ({f['type']}){enum_sfx}")
                    if len(parts) >= 8:
                        break
                text = f"Schema {r['ref']} [{r['spec_file']}]: " + "; ".join(parts)
            add(r["kind"], r["spec_file"], r["ref"], text, score, exact=False)
    finally:
        conn.close()

    # Exact hits first, then by concept coverage. Non-exact hits must clear the
    # full threshold; exact hits are trusted but still need one corroborating
    # concept beyond the name that matched them (a lone field-name collision on
    # a multi-term query is noise, e.g. MVRP "registration" for "MAC registration").
    exact_floor = min(2, n_terms)
    out = [h for h in hits.values()
           if (h["_exact"] and h["score"] >= exact_floor) or h["score"] >= threshold]
    out.sort(key=lambda h: (not h["_exact"], -h["score"]))
    for h in out:
        h.pop("_exact")
    return out[:top_k]


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--build", action="store_true")
    ap.add_argument("--query")
    ap.add_argument("--enum")
    ap.add_argument("--lookup", help="natural-language lookup (as the MCP tool runs it)")
    args = ap.parse_args()
    if args.build:
        print(json.dumps(build(), indent=2))
    if args.query:
        print(json.dumps(search(args.query), indent=2))
    if args.enum:
        print(json.dumps(get_enum(args.enum), indent=2))
    if args.lookup:
        print(json.dumps(lookup(args.lookup), indent=2))
