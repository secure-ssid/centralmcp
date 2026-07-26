# Troubleshooting

Use the local doctor first. It does not call Central, GLP, or optional product
APIs.

```bash
uv run python scripts/doctor.py
```

## Setup wizard

| Symptom | Fix |
|---|---|
| `uv` is missing | Install `uv`, or rerun the wizard after installing it. |
| Existing local config was not overwritten | Re-run with `--force` if you want to replace `.mcp.json`, `.mcp.http.json`, or `config/credentials.yaml`. Existing `.env` files are merged by default so selected products/access mode update while non-placeholder token values are preserved. |
| You only want a no-credentials trial | Run `python3 scripts/setup_wizard.py --yes --skip-credentials` and skip API-backed tools until credentials are added. |
| You picked the wrong products | Re-run with `--products clearpass,mist` to merge the selector/access mode into `.env`, or use `--force` if you intentionally want to replace generated local config files. |

## Credentials and Central regions

The wizard offers common Central API gateway choices:

| Gateway | Base URL |
|---|---|
| US / common API gateway | `https://apigw-prod2.central.arubanetworks.com` |
| EU Central | `https://apigw-eucentral3.central.arubanetworks.com` |
| APAC | `https://apigw-apac.central.arubanetworks.com` |
| Legacy/internal gateway | `https://internal.api.central.arubanetworks.com` |

If your tenant uses a different host, choose the custom URL option. Environment
variables override YAML values, so check both shell variables and
`config/credentials.yaml` when troubleshooting auth.

## HTTP MCP mode

Start the local HTTP router:

```bash
MCP_PORT=8010 bash scripts/run_http_router.sh
```

Connect the MCP client to:

```text
http://127.0.0.1:8010/mcp
```

| Symptom | Fix |
|---|---|
| Port already in use | The helper prints listener details. Stop the old process with `kill <PID>` or choose another `MCP_PORT`. |
| `curl` returns `406` | Expected for plain curl. Real MCP clients send streaming headers such as `Accept: text/event-stream`. |
| Optional products work in stdio but not HTTP | Confirm local `.env` exists next to the repo root; the HTTP helper safely loads assignments from it before starting. |
| Client URL does not match the server | Update `.mcp.http.json` if you changed `MCP_HOST` or `MCP_PORT`. |
| Non-loopback HTTP startup is refused | Set explicit non-wildcard `MCP_ALLOWED_HOSTS` and `MCP_ALLOWED_ORIGINS`. Add `MCP_HTTP_BEARER_TOKEN` when the listener is reachable outside the local host. |
| Health probe needed without MCP negotiation | Request `/livez`, `/readyz`, or `/healthz`; these do not call vendor APIs. |
| HTTP client receives `401` | Include `Authorization: Bearer <token>` when the shared HTTP token is enabled. |
| SSE startup is refused when a bearer token is set | Static bearer enforcement is supported only by `streamable-http`; switch `MCP_TRANSPORT` or unset the token. The server fails closed rather than starting an apparently protected SSE listener. |

## API source and RAG freshness

Aruba's July 2026 developer-portal migration retired the old internal-UI
OpenAPI JSON URLs. Refresh through the ReadMe registry flow:

```bash
uv run python ingestion/scrape_openapi.py
uv run python ingestion/scrape_cnac_spec.py
uv run python ingestion/fetch_mist_openapi.py
uv run python ingestion/scrape_security_lifecycle.py
uv run python scripts/check_openapi_drift.py
uv run python scripts/check_mist_openapi_drift.py
uv run python ingestion/ingest_docs.py
```

| Symptom | Fix |
|---|---|
| Drift checker exits 2 | No `ingestion/openapi_registry_manifest.json` exists yet; run the OpenAPI scrapers first. |
| Drift checker exits 1 | Vendor specs or page pointers changed; refresh sources, rebuild indexes, and rerun the checker. |
| `lookup_api` returns an older path/version | Rebuild `data/specs.sqlite` after refreshing the registry specs. |
| `ask_docs` misses a security advisory or end-of-sale notice | Run `ingestion/scrape_security_lifecycle.py`, then rebuild `data/docs.lance`. Aruba advisories refresh incrementally from the official CSAF `changes.csv`; HPE lifecycle notices come from the all-product End of Sale XML feed. |
| `check_security_lifecycle_drift.py` reports `stale`/`unavailable`/`changed` | See [Source lifecycle coverage](source-lifecycle-coverage.md). `stale` means a count regressed below its committed minimum; `unavailable` means the source could not be fetched (network/HTTP); `changed` means the source no longer matches its reviewed provenance pin (`ingestion/provenance/*.json`) and needs review before regenerating the pin. `coverage_gap` (e.g. current Aruba-branded lifecycle) is expected and does not fail the check. |
| macOS docs rebuild stalls in fastembed multiprocessing | Current `ingest_docs.py` automatically disables subprocess parallelism on macOS. Stop any older stale rebuild by exact PID, update the checkout, and rerun the command. |
| Docs index is larger because OpenAPI JSON was embedded | Rebuild with the current ingestion path. OpenAPI records now remain in SQLite only; the current prose corpus contains 51,737 chunks. |

## Optional product compatibility

| Symptom | Fix |
|---|---|
| AOS8 session authentication fails | Configure `AOS8_USERNAME` and `AOS8_PASSWORD`; the backend establishes a UIDARUBA/X-CSRF session and retries once after an unauthorized response. |
| Apstra session authentication fails | Configure `APSTRA_USERNAME` and `APSTRA_PASSWORD`, or supply a pre-issued `APSTRA_API_TOKEN`; requests use the `AuthToken` header. |
| EdgeConnect operational tool reports `blocked` | Run `edgeconnect_doctor`. The bundled pre-9.3 endpoint map is disabled unless `EDGECONNECT_ALLOW_LEGACY_API=1` is explicitly set for a validated older/lab instance; production 9.3+ use requires the target Swagger spec. |
| Central troubleshooting endpoint returns 404 | The client tries `/network-troubleshooting/v1` first and falls back to `v1alpha1` only on 404. Set `CENTRALMCP_TROUBLESHOOTING_API_VERSION=v1alpha1` only for a tenant that still requires the legacy path. |
| `mist_collect_diagnostic_results` times out or reports `configuration_error` | Confirm `MIST_API_TOKEN` (or the full `MIST_SESSION_COOKIE` + `MIST_CSRF_TOKEN` pair) is set and that `websockets>=14.0` is installed; the tool only connects to the documented regional `WS /api-ws/v1/stream` endpoint derived from `MIST_HOST`. |
| EdgeConnect compatibility check fails closed | Expected for malformed input, unsupported Swagger/OpenAPI versions, digest mismatch, endpoint drift, unsupported auth, or a non-root server base path; export a fresh Swagger/OpenAPI document from the target 9.3+ Orchestrator and re-run `scripts/generate_edgeconnect_tools.py`. |

## Router and catalog

Recommended low-token profile:

```env
CENTRALMCP_ROUTER_MODE=minimal
CENTRALMCP_TOOLSETS=central,glp,rag
```

Rebuild the tool catalog:

```bash
uv run python scripts/ingest_tools.py
```

Include selected optional products:

```bash
uv run python scripts/ingest_tools.py --products clearpass,mist
```

If `find_tool` cannot locate expected optional product tools, confirm
`CENTRALMCP_PRODUCTS` and the catalog were built with the same selected
products.

The complete read-write backend catalog contains 6,699 tools. If release
validation expects that full catalog, rebuild with:

```bash
CENTRALMCP_PRODUCT_ACCESS=read-write CENTRALMCP_GLP_GENERATED_TOOLS=1 uv run python scripts/ingest_tools.py --products all
```

## First useful call flow

```text
find_tool("show active critical alerts")
invoke_read_tool("list_active_alerts", {"severity": "CRITICAL", "limit": 20})
```

Use `invoke_read_tool` for investigations. Use `invoke_tool` only when you
intend to run a write/destructive backend tool.

## GitHub Pages deployment

| Symptom | Fix |
|---|---|
| Pages build succeeds but deploy fails with `due to in progress deployment` | Wait for the earlier Pages deployment to complete and confirm the Pages API is no longer `building`, then rerun the failed workflow or push a follow-up commit. This is a transient GitHub Pages deployment queue race, not a docs/Jekyll build failure. |
| A rerun stays `queued` with no jobs after the live site is `built` | Cancel that exact stuck rerun before pushing again so a stale Pages queue entry does not stack another deployment race. |
| Push is rejected while changing `.github/workflows/ci.yml` | The active GitHub token needs repository write access and the OAuth `workflow` scope. Run `gh auth refresh --hostname github.com --scopes workflow`, complete the device authorization, then retry `git push origin main`. |
