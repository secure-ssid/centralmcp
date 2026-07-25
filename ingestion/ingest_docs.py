"""
Ingest Aruba/HPE docs into the RAG backend.

Default backend is the embedded stack (no servers): chunk prose -> fastembed
(in-process ONNX, nomic prefixes) -> LanceDB at data/, plus parse OpenAPI
specs -> SQLite (data/specs.sqlite). `--backend redis` keeps the optional
Redis Stack + Ollama server deployment path.

Usage:
    uv run python ingestion/ingest_docs.py                     # full LanceDB rebuild
    uv run python ingestion/ingest_docs.py --backend redis     # optional Redis Stack path
    uv run python ingestion/ingest_docs.py --source nac_docs   # one source only
    uv run python ingestion/ingest_docs.py --dry-run           # count chunks, no upload
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
)

SOURCES_DIR = Path(__file__).parent / "sources"

# Maps source folder name → doc_type tag.
# Each source has a distinct doc_type so provenance survives filtering.
# `doc_type` is kept for back-compat; new code should filter by `source`.
SOURCE_META = {
    "devhub": "devhub",
    "developer_docs": "developer-docs",
    "tech_docs": "tech-docs",
    "nac_docs": "nac",
    "vsg_docs": "vsg",
    "techdocs_html": "techdocs-html",
    "feature_navigator": "feature-navigator",
    "openapi_specs": "openapi",
    "aos_techdocs": "aos-techdocs",
    "security_advisories": "security-advisory",
    "lifecycle_notices": "lifecycle",
    "juniper_lifecycle": "lifecycle",
    "juniper_security_advisories": "security-advisory",
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
    """Return a stable UUID string derived from an MD5 hash."""
    return str(uuid.UUID(hashlib.md5(key.encode()).hexdigest()))


def stable_id(path: Path, chunk_index: int) -> str:
    return _md5_uuid(f"{path}:{chunk_index}")


def content_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


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
                    "content_hash": content_hash(chunk),
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


WRITE_BATCH_LANCE = 512


def safe_parallel_workers(requested: int | None) -> int | None:
    """Disable fastembed multiprocessing on macOS where forkserver workers
    can deadlock during long index rebuilds."""
    if requested is not None and requested < 1:
        raise ValueError("--parallel must be at least 1")
    if sys.platform == "darwin" and requested is not None:
        print(
            "  macOS detected: disabling fastembed multiprocessing to avoid "
            "forkserver deadlocks; using the in-process embedder.",
            flush=True,
        )
        return None
    return requested


def source_uses_structured_index(folder: str, backend: str) -> bool:
    """OpenAPI stays exact in SQLite for the embedded backend, not vectors."""
    return backend == "lancedb" and folder == "openapi_specs"


def upload_lancedb(records: list[dict], ingested_sources: list[str],
                   parallel: int | None = None) -> None:
    """Full rebuild of the LanceDB docs table: stream embeddings from fastembed
    (one embed pass so parallel workers spawn once), add rows in batches, build
    the FTS index once at the end, then assert every ingested source landed
    >0 chunks (R2 — a silently-empty source poisoned the old index).
    """
    from pipeline.clients import lance_client
    from pipeline.clients.embed_client import EmbedClient

    db = lance_client.connect()
    embedder = EmbedClient()
    vectors = embedder.iter_embed_documents(
        (r["text"] for r in records), parallel=safe_parallel_workers(parallel)
    )
    table = None
    buf: list[dict] = []
    done = 0
    for record, vec in zip(records, vectors):
        buf.append({**record, "vector": vec})
        if len(buf) >= WRITE_BATCH_LANCE:
            if table is None:
                table = lance_client.create_docs_table(db, buf)
            else:
                table.add(buf)
            done += len(buf)
            buf = []
            print(f"    embedded+added {done}/{len(records)}", flush=True)
    if buf:
        if table is None:
            table = lance_client.create_docs_table(db, buf)
        else:
            table.add(buf)
        done += len(buf)
        print(f"    embedded+added {done}/{len(records)}", flush=True)
    if table is None:
        raise SystemExit("No records to ingest — check ingestion/sources/")
    print("  building FTS index...", flush=True)
    lance_client.build_fts_index(table)

    counts = lance_client.source_counts(db)
    print(f"  per-source counts: {counts}")
    empty = [s for s in ingested_sources if counts.get(s, 0) == 0]
    if empty:
        raise SystemExit(f"FAIL: sources with 0 indexed chunks: {empty}")


def upload_lancedb_incremental(
    records: list[dict],
    ingested_sources: list[str],
    parallel: int | None = None,
) -> bool:
    """Upsert changed chunks and delete removed chunks.

    Returns ``False`` when the existing table predates content hashes so the
    caller can perform one full rebuild and establish the incremental schema.
    """
    from pipeline.clients import lance_client
    from pipeline.clients.embed_client import EmbedClient

    db = lance_client.connect()
    if "content_hash" not in lance_client.docs_columns(db):
        print(
            "  existing docs table has no content_hash column; "
            "performing one full rebuild",
            flush=True,
        )
        return False

    existing = {
        str(row["id"]): str(row.get("content_hash") or "")
        for row in lance_client.docs_metadata(db)
        if row.get("source") in ingested_sources
    }
    current_ids = {str(record["id"]) for record in records}
    changed = [
        record
        for record in records
        if existing.get(str(record["id"])) != record["content_hash"]
    ]
    removed = sorted(set(existing) - current_ids)
    print(
        f"  incremental diff: {len(changed)} changed/new, "
        f"{len(removed)} removed, {len(records) - len(changed)} unchanged",
        flush=True,
    )

    if changed:
        embedder = EmbedClient()
        vectors = embedder.iter_embed_documents(
            (record["text"] for record in changed),
            parallel=safe_parallel_workers(parallel),
        )
        batch: list[dict] = []
        completed = 0
        for record, vector in zip(changed, vectors):
            batch.append({**record, "vector": vector})
            if len(batch) >= WRITE_BATCH_LANCE:
                lance_client.merge_docs_rows(db, batch)
                completed += len(batch)
                batch = []
                print(
                    f"    embedded+merged {completed}/{len(changed)}",
                    flush=True,
                )
        if batch:
            lance_client.merge_docs_rows(db, batch)
            completed += len(batch)
            print(f"    embedded+merged {completed}/{len(changed)}", flush=True)
    if removed:
        lance_client.delete_docs_ids(db, removed)

    table = lance_client.docs_table(db)
    if table is None:
        raise SystemExit("LanceDB docs table disappeared during incremental ingest")
    if changed or removed:
        print("  rebuilding FTS index...", flush=True)
        lance_client.build_fts_index(table)

    counts = lance_client.source_counts(db)
    empty = [source for source in ingested_sources if counts.get(source, 0) == 0]
    if empty:
        raise SystemExit(f"FAIL: sources with 0 indexed chunks: {empty}")
    print(f"  per-source counts: {counts}")
    return True


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--backend", choices=("lancedb", "redis"), default="lancedb")
    parser.add_argument("--source", help="Ingest one source folder only (redis backend)")
    parser.add_argument("--dry-run", action="store_true", help="Count chunks, no upload")
    parser.add_argument(
        "--incremental",
        action="store_true",
        help="upsert changed LanceDB chunks instead of rebuilding every embedding",
    )
    parser.add_argument("--index", default=DOCS_INDEX, dest="index")
    parser.add_argument(
        "--parallel",
        type=int,
        default=None,
        help=(
            "fastembed worker processes "
            "(Linux; macOS safely falls back in-process)"
        ),
    )
    args = parser.parse_args()

    if args.backend == "lancedb" and args.source:
        parser.error(
            "--source only applies to --backend redis; lancedb always rebuilds all sources"
        )
    if args.backend == "redis" and args.incremental:
        parser.error("--incremental applies only to the lancedb backend")

    sources = (
        {args.source: SOURCE_META.get(args.source, "unknown")}
        if args.source
        else SOURCE_META
    )

    all_records: list[dict] = []
    ingested_sources: list[str] = []
    openapi_specs_present = False
    for folder, doc_type in sources.items():
        source_dir = SOURCES_DIR / folder
        if not source_dir.exists():
            print(f"SKIP: {source_dir} not found")
            continue
        records = collect_points(source_dir, doc_type)
        if source_uses_structured_index(folder, args.backend):
            openapi_specs_present = True
            print(f"  → {len(records)} structured API records (SQLite only)")
            continue
        all_records.extend(records)
        ingested_sources.append(folder)
        print(f"  → {len(records)} chunks")

    print(f"\nTotal chunks: {len(all_records)}")

    if args.dry_run:
        print("Dry run — no upload.")
        return

    if args.backend == "lancedb":
        print("\nRebuilding embedded indexes (LanceDB + specs SQLite)...")
        incremental_done = args.incremental and upload_lancedb_incremental(
            all_records,
            ingested_sources,
            parallel=args.parallel,
        )
        if not incremental_done:
            upload_lancedb(all_records, ingested_sources, parallel=args.parallel)
        if openapi_specs_present:
            from pipeline.clients import advisory_index, specs_index
            print("  rebuilding specs.sqlite...")
            print(f"  {specs_index.build()}")
            print("  rebuilding structured advisory/lifecycle tables...")
            print(f"  {advisory_index.build()}")
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
