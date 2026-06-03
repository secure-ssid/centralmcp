"""
Ingest Aruba/HPE docs into the RAG backend.

Default backend is the embedded stack (no servers): chunk prose -> fastembed
(in-process ONNX, nomic prefixes) -> LanceDB at data/, plus parse OpenAPI
specs -> SQLite (data/specs.sqlite). `--backend redis` keeps the legacy
Redis Stack + Ollama path for the optional server deployment.

Usage:
    python ingestion/ingest_docs.py                     # all sources -> LanceDB (full rebuild)
    python ingestion/ingest_docs.py --backend redis     # legacy Redis Stack path
    python ingestion/ingest_docs.py --source nac_docs   # one source (redis only; lancedb always rebuilds all)
    python ingestion/ingest_docs.py --dry-run           # count chunks, no upload
"""

import argparse
import hashlib
import json
import sys
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from bs4 import BeautifulSoup

from ingestion.chunking import chunk_text
from pipeline.clients.ollama_client import OllamaClient
from pipeline.clients.redis_client import (
    DOCS_INDEX,
    ensure_index,
    get_client,
    upsert_docs,
    doc_count,
)

SOURCES_DIR = Path(__file__).parent / "sources"

# Maps source folder name → doc_type tag.
# Each source has a distinct doc_type so provenance survives filtering.
# `doc_type` is kept for back-compat; new code should filter by `source`.
SOURCE_META = {
    "developer_docs": "developer-docs",
    "tech_docs": "tech-docs",
    "nac_docs": "nac",
    "vsg_docs": "vsg",
    "techdocs_html": "techdocs-html",
    "openapi_specs": "openapi",
    "aos_techdocs": "aos-techdocs",
}

UPLOAD_BATCH = 100


def read_file(path: Path) -> str | None:
    suffix = path.suffix.lower()
    try:
        if suffix in (".md", ".txt"):
            return path.read_text(encoding="utf-8", errors="ignore")
        if suffix in (".htm", ".html"):
            soup = BeautifulSoup(
                path.read_text(encoding="utf-8", errors="ignore"), "html.parser"
            )
            return soup.get_text(separator="\n")
    except Exception as e:
        print(f"  SKIP {path.name}: {e}")
    return None


def _md5_uuid(key: str) -> str:
    """Return MD5 hash formatted as UUID string (matches Qdrant's auto-conversion)."""
    return str(uuid.UUID(hashlib.md5(key.encode()).hexdigest()))


def stable_id(path: Path, chunk_index: int) -> str:
    return _md5_uuid(f"{path}:{chunk_index}")


def _schema_to_text(spec_name: str, schema_name: str, schema: dict) -> str | None:
    """Convert a single OpenAPI schema object to a human-readable text chunk."""
    lines = [f"API spec: {spec_name}", f"Schema: {schema_name}"]

    if desc := schema.get("description"):
        lines.append(f"Description: {desc}")

    props = schema.get("properties", {})
    if not props:
        return None

    field_lines = []
    for field, fdef in props.items():
        parts = [f"  - {field}"]
        if fdesc := fdef.get("description"):
            parts.append(f": {fdesc}")
        if ftype := fdef.get("type"):
            parts.append(f" (type: {ftype})")
        if enum_vals := fdef.get("enum"):
            parts.append(f"\n    Valid values: {', '.join(str(v) for v in enum_vals)}")
            if enum_desc := fdef.get("x-enumDescriptions"):
                for val, vdesc in enum_desc.items():
                    parts.append(f"\n      {val}: {vdesc}")
        field_lines.append("".join(parts))

    if not field_lines:
        return None

    lines.append("Fields:")
    lines.extend(field_lines)
    return "\n".join(lines)


def _endpoint_to_text(spec_name: str, path: str, method: str, op: dict) -> str:
    """Convert an OpenAPI path operation to a human-readable text chunk."""
    lines = [
        f"API spec: {spec_name}",
        f"Endpoint: {method.upper()} {path}",
    ]
    if summary := op.get("summary"):
        lines.append(f"Summary: {summary}")
    if desc := op.get("description"):
        lines.append(f"Description: {desc}")
    return "\n".join(lines)


def collect_openapi_points(source_dir: Path) -> list[dict]:
    """Parse OpenAPI JSON specs and emit one chunk per schema and per endpoint."""
    records = []
    files = sorted(source_dir.glob("*.json"))
    print(f"  {source_dir.name}: {len(files)} JSON files")

    for path in files:
        try:
            spec = json.loads(path.read_text(encoding="utf-8", errors="ignore"))
        except Exception as e:
            print(f"  SKIP {path.name}: {e}")
            continue

        spec_name = spec.get("info", {}).get("title", path.stem)
        rel_path = str(path.resolve().relative_to(SOURCES_DIR.resolve()))

        # One chunk per schema
        schemas = spec.get("components", {}).get("schemas", {})
        for schema_name, schema in schemas.items():
            text = _schema_to_text(spec_name, schema_name, schema)
            if not text or not text.strip():
                continue
            chunk_key = f"{rel_path}:schema:{schema_name}"
            records.append({
                "id": _md5_uuid(chunk_key),
                "text": text,
                "source": source_dir.name,
                "doc_type": "openapi",
                "file_path": rel_path,
                "chunk_index": len(records),
            })

        # One chunk per endpoint operation
        for api_path, path_item in spec.get("paths", {}).items():
            for method, op in path_item.items():
                if method not in ("get", "post", "put", "patch", "delete"):
                    continue
                if not isinstance(op, dict):
                    continue
                text = _endpoint_to_text(spec_name, api_path, method, op)
                chunk_key = f"{rel_path}:path:{method}:{api_path}"
                records.append({
                    "id": _md5_uuid(chunk_key),
                    "text": text,
                    "source": source_dir.name,
                    "doc_type": "openapi",
                    "file_path": rel_path,
                    "chunk_index": len(records),
                })

    return records


def collect_points(source_dir: Path, doc_type: str) -> list[dict]:
    """Walk source_dir, chunk files, return records without vectors (added later)."""
    if source_dir.name == "openapi_specs":
        return collect_openapi_points(source_dir)

    records = []
    files = [
        p
        for p in source_dir.rglob("*")
        if p.is_file() and p.suffix.lower() in (".md", ".htm", ".html", ".txt")
    ]
    print(f"  {source_dir.name}: {len(files)} files")

    for path in files:
        text = read_file(path)
        if not text or not text.strip():
            continue
        chunks = chunk_text(text)
        for i, chunk in enumerate(chunks):
            records.append(
                {
                    "id": stable_id(path, i),
                    "text": chunk,
                    "source": source_dir.name,
                    "doc_type": doc_type,
                    "file_path": str(path.relative_to(SOURCES_DIR)),
                    "chunk_index": i,
                }
            )
    return records


def _existing_ids(client, ids: list[str]) -> set[str]:
    """Return subset of ids already in Redis."""
    pipe = client.pipeline(transaction=False)
    for doc_id in ids:
        pipe.exists(f"doc:{doc_id}")
    results = pipe.execute()
    return {doc_id for doc_id, exists in zip(ids, results) if exists}


def upload(records: list[dict], ollama: OllamaClient, client):
    skipped = 0
    uploaded = 0
    for batch_start in range(0, len(records), UPLOAD_BATCH):
        batch = records[batch_start : batch_start + UPLOAD_BATCH]
        existing = _existing_ids(client, [r["id"] for r in batch])
        new = [r for r in batch if r["id"] not in existing]
        skipped += len(batch) - len(new)
        if not new:
            continue
        texts = [r["text"] for r in new]
        vectors = ollama.embed_document(texts)
        docs = [
            {**r, "embedding": vec}
            for r, vec in zip(new, vectors)
        ]
        upsert_docs(client, docs)
        uploaded += len(new)
        print(f"    uploaded {uploaded} new / {skipped} skipped / {len(records)} total")


EMBED_BATCH_LANCE = 512


def upload_lancedb(records: list[dict], ingested_sources: list[str]) -> None:
    """Full rebuild of the LanceDB docs table: embed with fastembed, add in
    batches, build the FTS index once at the end, then assert every ingested
    source landed >0 chunks (R2 — a silently-empty source poisoned the old index).
    """
    from pipeline.clients import lance_client
    from pipeline.clients.embed_client import EmbedClient

    db = lance_client.connect()
    embedder = EmbedClient()
    table = None
    done = 0
    for start in range(0, len(records), EMBED_BATCH_LANCE):
        batch = records[start : start + EMBED_BATCH_LANCE]
        vectors = embedder.embed_document([r["text"] for r in batch])
        rows = [{**r, "vector": vec} for r, vec in zip(batch, vectors)]
        if table is None:
            table = lance_client.create_docs_table(db, rows)
        else:
            table.add(rows)
        done += len(rows)
        print(f"    embedded+added {done}/{len(records)}")
    if table is None:
        raise SystemExit("No records to ingest — check ingestion/sources/")
    print("  building FTS index...")
    lance_client.build_fts_index(table)

    counts = lance_client.source_counts(db)
    print(f"  per-source counts: {counts}")
    empty = [s for s in ingested_sources if counts.get(s, 0) == 0]
    if empty:
        raise SystemExit(f"FAIL: sources with 0 indexed chunks: {empty}")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--backend", choices=("lancedb", "redis"), default="lancedb")
    parser.add_argument("--source", help="Ingest one source folder only (redis backend)")
    parser.add_argument("--dry-run", action="store_true", help="Count chunks, no upload")
    parser.add_argument("--index", default=DOCS_INDEX, dest="index")
    args = parser.parse_args()

    if args.backend == "lancedb" and args.source:
        parser.error("--source only applies to --backend redis; lancedb always rebuilds all sources")

    sources = (
        {args.source: SOURCE_META.get(args.source, "unknown")}
        if args.source
        else SOURCE_META
    )

    all_records: list[dict] = []
    ingested_sources: list[str] = []
    for folder, doc_type in sources.items():
        source_dir = SOURCES_DIR / folder
        if not source_dir.exists():
            print(f"SKIP: {source_dir} not found")
            continue
        records = collect_points(source_dir, doc_type)
        all_records.extend(records)
        ingested_sources.append(folder)
        print(f"  → {len(records)} chunks")

    print(f"\nTotal chunks: {len(all_records)}")

    if args.dry_run:
        print("Dry run — no upload.")
        return

    if args.backend == "lancedb":
        print("\nRebuilding embedded indexes (LanceDB + specs SQLite)...")
        upload_lancedb(all_records, ingested_sources)
        if "openapi_specs" in ingested_sources:
            from pipeline.clients import specs_index
            print("  rebuilding specs.sqlite...")
            print(f"  {specs_index.build()}")
    else:
        print("\nConnecting to Redis Stack + Ollama...")
        client = get_client()
        ensure_index(client, args.index)

        with OllamaClient() as ollama:
            print(f"Uploading to index '{args.index}'...")
            upload(all_records, ollama, client)

    print("Done.")


if __name__ == "__main__":
    main()
