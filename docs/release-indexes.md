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
data/SOURCE-MANIFEST.json
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
| `data/SOURCE-MANIFEST.json` | humans / release audit | Copy of the tracked RAG source manifest used for the rebuild |
| `data/INDEX-MANIFEST.json` | humans / doctor output | Build metadata, artifact sizes, and source-manifest checksum/source names |

## Refresh RAG source inputs

Scraped source files live under git-ignored `ingestion/sources/`; keep the
tracked source list in [`ingestion/source_manifest.json`](../ingestion/source_manifest.json)
current before rebuilding public indexes.

| Source | Seed / target | Destination |
|---|---|---|
| DevHub | `https://devhub.arubanetworks.com` | `ingestion/sources/devhub` |
| New Central developer docs | `https://developer.arubanetworks.com/new-central/...` | `ingestion/sources/developer_docs` and `ingestion/sources/openapi_specs` |
| Tech docs | `https://arubanetworking.hpe.com/techdocs/` | `ingestion/sources/tech_docs` |
| NAC docs | CNAC/NAC developer and reference pages | `ingestion/sources/nac_docs` |
| Validated Solution Guides | `https://arubanetworking.hpe.com/techdocs/VSG/docs/` | `ingestion/sources/vsg_docs` |
| New Central techdocs | `https://arubanetworking.hpe.com/techdocs/new-central/content/home.htm` plus `ingestion/techdocs_paths.json` | `ingestion/sources/techdocs_html` |
| Switching Feature Navigator | `https://feature-navigator.arubanetworking.hpe.com/wired?mode=explore` | `ingestion/sources/feature_navigator` |
| OpenAPI specs | New Central OpenAPI JSON specs | `ingestion/sources/openapi_specs` |
| AOS techdocs | `https://arubanetworking.hpe.com/techdocs/aos/` | `ingestion/sources/aos_techdocs` |

The New Central techdocs host can block plain HTTP clients, so use the paced
Playwright scraper (`ingestion/scrape_techdocs_pw.py`) when refreshing that
source. Do not commit scraped content; rebuild `data/docs.lance` and package the
index archive instead.
