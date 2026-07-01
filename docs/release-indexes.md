# Prebuilt RAG/OpenAPI indexes

The core router catalog is quick to build locally. The full docs/API RAG index is
larger, so public releases can include a prebuilt archive.

## Download indexes

```bash
uv run python scripts/download_indexes.py
```

This downloads the latest `centralmcp-rag-index-latest.tar.gz` release asset and
unpacks:

```text
data/docs.lance
data/tools.lance
data/specs.sqlite
data/INDEX-MANIFEST.json
```

Then check the local setup:

```bash
uv run python scripts/doctor.py
```

## Package indexes for a release

Build or refresh local indexes first:

```bash
uv run python ingestion/ingest_docs.py
uv run python scripts/ingest_tools.py --products all
```

Package them:

```bash
uv run python scripts/package_indexes.py --version v0.2.1
```

The script writes:

```text
dist/centralmcp-rag-index-v0.2.1.tar.gz
dist/centralmcp-rag-index-v0.2.1.tar.gz.sha256
```

Upload the archive and checksum to the GitHub Release. For convenience, also
upload a copy named `centralmcp-rag-index-latest.tar.gz` so the downloader can
always use the latest release URL.

## What is inside

| Artifact | Used by | Purpose |
|---|---|---|
| `data/docs.lance` | `search_docs`, `ask_docs` | Embedded docs retrieval |
| `data/specs.sqlite` | `lookup_api` | Exact OpenAPI endpoint/schema lookup |
| `data/tools.lance` | `find_tool` | Semantic router tool discovery |
| `data/INDEX-MANIFEST.json` | humans / doctor output | Build metadata and artifact sizes |
