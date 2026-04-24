"""
Ingest Aruba/HPE docs into Qdrant.

Usage:
    python ingestion/ingest_docs.py                     # all sources
    python ingestion/ingest_docs.py --source nac_docs   # one source
    python ingestion/ingest_docs.py --source openapi_specs
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
from qdrant_client.models import PointStruct

from ingestion.chunking import chunk_text
from pipeline.clients.ollama_client import OllamaClient
from pipeline.clients.qdrant_client import (
    DOCS_COLLECTION,
    ensure_collection,
    get_client,
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


def _existing_ids(qdrant, collection: str, ids: list[str]) -> set[str]:
    """Return subset of ids already in Qdrant."""
    results = qdrant.retrieve(collection_name=collection, ids=ids, with_payload=False, with_vectors=False)
    return {str(r.id) for r in results}


def upload(records: list[dict], ollama: OllamaClient, qdrant, collection: str):
    skipped = 0
    uploaded = 0
    for batch_start in range(0, len(records), UPLOAD_BATCH):
        batch = records[batch_start : batch_start + UPLOAD_BATCH]
        # Skip records already in Qdrant (safe to re-run after failures)
        existing = _existing_ids(qdrant, collection, [r["id"] for r in batch])
        new = [r for r in batch if r["id"] not in existing]
        skipped += len(batch) - len(new)
        if not new:
            continue
        texts = [r["text"] for r in new]
        vectors = ollama.embed_batch(texts)
        points = [
            PointStruct(
                id=r["id"],
                vector=vec,
                payload={k: v for k, v in r.items() if k != "id"},
            )
            for r, vec in zip(new, vectors)
        ]
        qdrant.upsert(collection_name=collection, points=points)
        uploaded += len(new)
        print(f"    uploaded {uploaded} new / {skipped} skipped / {len(records)} total")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", help="Ingest one source folder only")
    parser.add_argument("--dry-run", action="store_true", help="Count chunks, no upload")
    parser.add_argument("--collection", default=DOCS_COLLECTION)
    args = parser.parse_args()

    sources = (
        {args.source: SOURCE_META.get(args.source, "unknown")}
        if args.source
        else SOURCE_META
    )

    all_records: list[dict] = []
    for folder, doc_type in sources.items():
        source_dir = SOURCES_DIR / folder
        if not source_dir.exists():
            print(f"SKIP: {source_dir} not found")
            continue
        records = collect_points(source_dir, doc_type)
        all_records.extend(records)
        print(f"  → {len(records)} chunks")

    print(f"\nTotal chunks: {len(all_records)}")

    if args.dry_run:
        print("Dry run — no upload.")
        return

    print("\nConnecting to Qdrant + Ollama...")
    qdrant = get_client()
    ensure_collection(qdrant, args.collection)

    with OllamaClient() as ollama:
        print(f"Uploading to collection '{args.collection}'...")
        upload(all_records, ollama, qdrant, args.collection)

    print("Done.")


if __name__ == "__main__":
    main()
