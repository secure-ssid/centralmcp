# Prebuilt RAG/OpenAPI indexes

The core router catalog is quick to build locally. The full docs/API RAG index is
larger, so public releases can include a prebuilt archive.

## Current 0.7.0 snapshot

| Artifact content | Count |
|---|---:|
| LanceDB prose chunks | 51,737 |
| SQLite OpenAPI specs | 244 |
| Exact endpoints | 3,796 |
| Schemas | 11,293 |
| Fields | 60,568 |
| Security advisories | 102 |
| Lifecycle records | 346 |
| Generated operation manifests | 6,143 |
| Complete backend tool catalog | 6,699 |

OpenAPI documents are parsed only into SQLite exact lookup. They are not
embedded into LanceDB, which keeps prose retrieval smaller and avoids lossy
semantic matching for endpoint paths, fields, and enum values.

## Download indexes

```bash
uv run python scripts/download_indexes.py
```

This downloads the latest `centralmcp-rag-index-latest.tar.gz` release asset and
its `.sha256` checksum, verifies the archive, and safely unpacks only regular
files/directories under `data/`:

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

For custom archives, pass `--url` and optionally `--checksum-url`. Use
`--skip-checksum` only for trusted local files that do not have a matching
checksum.

## Package indexes for a release

Build or refresh local indexes first:

```bash
uv run python ingestion/scrape_openapi.py
uv run python ingestion/scrape_cnac_spec.py
uv run python ingestion/fetch_mist_openapi.py
uv run python ingestion/scrape_security_lifecycle.py
uv run python scripts/check_openapi_drift.py
uv run python scripts/check_mist_openapi_drift.py
uv run python ingestion/ingest_docs.py
CENTRALMCP_PRODUCT_ACCESS=read-write CENTRALMCP_GLP_GENERATED_TOOLS=1 uv run python scripts/ingest_tools.py --products all
```

Package them:

```bash
uv run python scripts/package_indexes.py
```

The script writes:

```text
dist/centralmcp-rag-index-v<project-version>.tar.gz
dist/centralmcp-rag-index-v<project-version>.tar.gz.sha256
dist/centralmcp-rag-index-latest.tar.gz
dist/centralmcp-rag-index-latest.tar.gz.sha256
```

Upload both the versioned archive/checksum and the `latest` archive/checksum to
the GitHub Release so the downloader can always use and verify the latest
release URL. Use `--skip-latest-copy` only if you intentionally want to package
versioned assets without the downloader alias.

For an existing release, upload the four generated assets with:

```bash
VERSION="v<project-version>"
gh release upload "$VERSION" \
  "dist/centralmcp-rag-index-${VERSION}.tar.gz" \
  "dist/centralmcp-rag-index-${VERSION}.tar.gz.sha256" \
  dist/centralmcp-rag-index-latest.tar.gz \
  dist/centralmcp-rag-index-latest.tar.gz.sha256 \
  --repo secure-ssid/centralmcp \
  --clobber
```

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
current before rebuilding public indexes. The table below mirrors the tracked
manifest so release rebuilds can cite the exact source seeds used for DevHub,
New Central, techdocs, Feature Navigator, and OpenAPI lookup.

| Source | Seed / target | Destination |
|---|---|---|
| DevHub | `https://devhub.arubanetworks.com` | `ingestion/sources/devhub` |
| New Central developer docs | `https://developer.arubanetworks.com/new-central/docs/getting-started-with-rest-apis` and `https://developer.arubanetworks.com/new-central/docs/introduction-to-configuration-apis` | `ingestion/sources/developer_docs` |
| Tech docs | `https://arubanetworking.hpe.com/techdocs/` | `ingestion/sources/tech_docs` |
| NAC docs | `https://developer.arubanetworks.com/new-central-config/reference/mac-registration` | `ingestion/sources/nac_docs` |
| Validated Solution Guides | `https://arubanetworking.hpe.com/techdocs/VSG/docs/` | `ingestion/sources/vsg_docs` |
| New Central techdocs | `https://arubanetworking.hpe.com/techdocs/new-central/content/home.htm` plus `ingestion/techdocs_paths.json` | `ingestion/sources/techdocs_html` |
| Switching Feature Navigator | `https://feature-navigator.arubanetworking.hpe.com/wired?mode=explore` | `ingestion/sources/feature_navigator` |
| OpenAPI specs | Aruba reference pages resolved through ReadMe plus the pinned official `mistsys/mist_openapi` snapshot; refreshed by `scrape_openapi.py`, `scrape_cnac_spec.py`, and `fetch_mist_openapi.py` | `ingestion/sources/openapi_specs` |
| AOS techdocs | `https://arubanetworking.hpe.com/techdocs/aos/` | `ingestion/sources/aos_techdocs` |
| Security advisories | Complete official HPE Aruba Networking CSAF archive from `https://csaf.arubanetworking.hpe.com/changes.csv` | `ingestion/sources/security_advisories` |
| HPE lifecycle notices | Historical all-product End of Sale XML, HPE Networking lifecycle policy, and the official hardware SKU End of Sale PDF | `ingestion/sources/lifecycle_notices` |
| Mist / Apstra lifecycle | Official Juniper hardware/software milestone tables used by the optional Mist and Apstra backends | `ingestion/sources/juniper_lifecycle` |
| Mist / Apstra security | Official Juniper support sitemaps plus Playwright-rendered Security Bulletin articles | `ingestion/sources/juniper_security_advisories` |

The New Central techdocs host can block plain HTTP clients, so use the paced
Playwright scraper (`ingestion/scrape_techdocs_pw.py`) when refreshing that
source. Do not commit scraped content; rebuild `data/docs.lance` and package the
index archive instead.

`ingestion/scrape_security_lifecycle.py` converts the official machine-readable
Aruba CSAF archive into searchable advisory documents containing advisory IDs,
CVEs, severity, affected products and versions, remediation, and references.
It also converts HPE's networking End of Sale XML archive into one searchable
notice per announcement, including affected/replacement SKUs, extracts the
official hardware SKU End of Sale PDF, and captures the official Mist/Apstra
lifecycle milestone tables. HPE does not expose a crawlable current index for
every individual modern notice, so lifecycle answers must cite source dates
rather than implying the historical archive is current or exhaustive.
Juniper advisory discovery uses the official support sitemap and renders only
Mist/Apstra Security Bulletin articles because the Salesforce page body is
client-side.

On macOS, `ingestion/ingest_docs.py` disables fastembed subprocess parallelism
to avoid forkserver deadlocks. The rebuild remains batched but runs in one
process. Linux release builders may use the normal parallel path.

Aruba's July 2026 ReadMe SuperHub migration retired the former internal-UI JSON
spec source and the embedded `oasDefinition` page blob. The current scrapers
resolve `oasPublicUrl` through
`https://dash.readme.com/api/v1/api-registry/{id}` and generate
`ingestion/openapi_registry_manifest.json` with the source page, project,
portal/spec version, path count, hash, and fetch timestamp. Run
`scripts/check_openapi_drift.py` on a schedule; exit code 1 means refresh and
rebuild before publishing indexes, while exit code 2 means no registry manifest
has been generated yet.

`ingestion/fetch_mist_openapi.py` pins the official Mist 2606.1.1 spec to
commit `f374cffdd5a275c7954645a306fcab7f1227e7a3` and verifies its SHA-256
before writing the git-ignored RAG source. `scripts/check_mist_openapi_drift.py`
reports when that upstream file advances. Both Aruba and Mist checks run in the
scheduled `api-drift` GitHub Actions job.
