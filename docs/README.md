# centralmcp documentation

Use this directory as the organized documentation hub for setup, architecture,
router usage, optional products, troubleshooting, and release artifacts.

This folder can also be served as a GitHub Pages site from `main` / `docs`.

## Start here

| Doc | Use it for |
|---|---|
| [index.md](index.md) | GitHub Pages landing page with quick setup, router, optional product, and HTTP paths |
| [getting-started.md](getting-started.md) | Wizard install, credentials, optional products, MCP client setup, and indexes |
| [mcp-client-recipes.md](mcp-client-recipes.md) | Copy/paste stdio and streamable HTTP MCP client setup recipes |
| [optional-products.md](optional-products.md) | Optional product matrix, wizard behavior, env vars, and safety surface |
| [product-workflows.md](product-workflows.md) | Typed ClearPass/Mist/Apstra/AOS8/EdgeConnect/UXI workflow roadmap |
| [aos8-migration-contract-matrix.md](aos8-migration-contract-matrix.md) | Authoritative AOS8-to-Classic/New Central migration contract matrix gating 0.5.0 implementation |
| [aos8-live-dryrun-evaluation.md](aos8-live-dryrun-evaluation.md) | Read-only live/fixture-backed evaluation record of the AOS8 migration pipeline against the contract matrix |
| [release-indexes.md](release-indexes.md) | Download, package, and release prebuilt RAG/OpenAPI indexes |
| [release-notes-0.5.0.md](release-notes-0.5.0.md) | Verified AOS8 migration expansion: source hardening, bounded Classic Central write lifecycle, expanded fail-closed New Central mappings, verification taxonomy, and read-only live/dry-run evaluation |
| [release-notes-0.4.0.md](release-notes-0.4.0.md) | Complete 0.4.0 migration execution, Mist diagnostics, EdgeConnect compatibility, GLP, and Axis changes |
| [release-notes-0.3.0.md](release-notes-0.3.0.md) | Prior 0.3.0 platform, migration, safety, and API-source changes (historical) |
| [capability-gap-matrix.md](capability-gap-matrix.md) | Reproducible executable-tool, generated-operation, and pinned-benchmark comparison plus ranked practical gaps |
| [troubleshooting.md](troubleshooting.md) | Setup wizard, credentials, HTTP mode, router catalog, GitHub Pages deploys, and first-call fixes |
| [example-prompts.md](example-prompts.md) | Practical low-token prompt examples and router call patterns |
| [tool-router.md](tool-router.md) | Low-token router modes, toolsets, optional products, and safe dispatch |
| [tool-catalog.md](tool-catalog.md) | Per-backend counts, capability families, build modes, and safety notes |
| [architecture/system-overview.md](architecture/system-overview.md) | End-to-end MCP architecture diagrams and runtime flow |
| [architecture/RAG-ARCHITECTURE.md](architecture/RAG-ARCHITECTURE.md) | Embedded RAG design, eval results, and migration rationale |

## Documentation sections

| Section | Contents |
|---|---|
| [architecture/](architecture/) | System design, RAG architecture, data stores, eval rationale |

## Repo map

| Path | Purpose |
|---|---|
| `mcp_servers/` | FastMCP servers, low-token router, prompts, middleware, optional product starters |
| `pipeline/` | Migration pipeline, typed clients, credentials loading, state store, SSID helpers |
| `ingestion/` | Docs/API ingestion into LanceDB and SQLite |
| `ingestion/source_manifest.json` | RAG source seed URLs for DevHub, New Central techdocs, Feature Navigator, and developer references |
| `scripts/setup_wizard.py` | Guided install, Central region, credentials, optional products, MCP configs, catalog, and doctor |
| `scripts/download_indexes.py` | Restore prebuilt docs/API/tool indexes from GitHub Releases |
| `scripts/package_indexes.py` | Package local indexes for a GitHub Release asset |
| `scripts/check_openapi_drift.py` / `scripts/check_mist_openapi_drift.py` | Detect Aruba ReadMe registry and official Mist OpenAPI changes |
| `scripts/report_capability_gaps.py` | Generate/check the reproducible capability gap matrix and pinned benchmark comparison |
| `scripts/generate_axis_manifest.py` | Rebuild/verify the deterministic SHA-pinned Axis Atmos Cloud manifest |
| `scripts/generate_edgeconnect_tools.py` | Fail-closed EdgeConnect Swagger/OpenAPI compatibility check and manifest generation |
| `pipeline/aos8_migration_orchestrator.py` / `pipeline/aos8_target_adapters.py` | Resumable AOS8 migration-run execution and guarded New Central/Classic target writes |
| `scripts/run_http_router.sh` | Start the minimal router over streamable HTTP |
| `docker-compose.yml` | Optional localhost-only Redis/Ollama server backend for power users |
| `scripts/doctor.py` | Check local setup without making API calls |
| `scripts/` | Tool-catalog ingestion, release validation, local sync helpers |
| `.mcp.json.example` | Generic stdio MCP client example using the minimal router |
| `.mcp.http.json.example` | Generic streamable HTTP MCP client example |
| `tests/unit/` | Mocked unit coverage for tools, clients, middleware, routing, RAG, release gates |
| `tests/eval/` | RAG/API eval data and runner |
| `data/` | Local built indexes, git-ignored |

## Common commands

```bash
# Guided local setup
python3 scripts/setup_wizard.py

# Guided setup with selected optional products
python3 scripts/setup_wizard.py --products clearpass,mist

# Download prebuilt RAG/OpenAPI indexes
uv run python scripts/download_indexes.py

# Build the router tool catalog
uv run python scripts/ingest_tools.py

# Include optional product starters in the tool catalog
uv run python scripts/ingest_tools.py --products all

# Include every guarded optional write tool (6,162 backend tools)
CENTRALMCP_PRODUCT_ACCESS=read-write CENTRALMCP_GLP_GENERATED_TOOLS=1 uv run python scripts/ingest_tools.py --products all

# Start the model-agnostic HTTP MCP router
MCP_PORT=8010 bash scripts/run_http_router.sh

# Check local setup without API calls
uv run python scripts/doctor.py

# Run unit tests
uv run pytest tests/unit -q

# Run the full local release gate
uv run python scripts/validate_release.py --catalog-products all --strict-rag --strict-tool-index --min-tools 6162
```

The wizard can run `uv sync`, choose common Central API gateways, fill secrets
with no echo, write local `.env` for selected optional products, and add only
the product selector to local stdio MCP configs. The HTTP helper safely loads
expected `.env` assignments first and exits with listener details instead of
starting a duplicate router when the selected port is already in use.

The release helper enforces the documented tool catalog floor and checks local LanceDB tool-index freshness when `data/tools.lance` exists. The unit suite also carries static regression guards for async-safe MCP tools, shared `httpx` client boundaries, project metadata (`centralmcp` package name with no direct sync SDK/`requests` runtime dependencies), committed low-token MCP config examples, local-only config files, router product/toolset docs, bounded generic read-only GET tools, MCP list default bounds, RAG/search top_k bounds, public tool-count claims, tool-count docstrings, tracked Markdown local links and images, Pages sitemap and robots metadata, documented router example arguments, product workflow tool-name tables, and wizard optional-product env tables.
