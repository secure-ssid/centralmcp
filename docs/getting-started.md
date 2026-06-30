# Getting started

This guide gets a local clone running as an MCP server with the low-token router profile.

## 1. Install

```bash
git clone https://github.com/secure-ssid/centralmcp.git
cd centralmcp
uv sync
```

Python 3.10+ is required. `uv` is recommended because the lockfile is maintained for this repo.

## 2. Configure credentials

```bash
cp config/credentials.yaml.example config/credentials.yaml
```

Fill in the preferred sections:

```yaml
central_account:
  base_url: https://internal.api.central.arubanetworks.com
  client_id: YOUR_CENTRAL_CLIENT_ID
  client_secret: YOUR_CENTRAL_CLIENT_SECRET
  glp_workspace_id: YOUR_GLP_WORKSPACE_ID

glp_account:
  base_url: https://internal.api.central.arubanetworks.com
  client_id: YOUR_GLP_CLIENT_ID
  client_secret: YOUR_GLP_CLIENT_SECRET
  glp_workspace_id: YOUR_GLP_WORKSPACE_ID
```

Environment variables override YAML values. Common overrides:

| Variable | Purpose |
|---|---|
| `SOURCE_BASE_URL`, `SOURCE_CLIENT_ID`, `SOURCE_CLIENT_SECRET` | Central/source account |
| `TARGET_BASE_URL`, `TARGET_CLIENT_ID`, `TARGET_CLIENT_SECRET` | GLP/target account |
| `SOURCE_GLP_WORKSPACE`, `TARGET_GLP_WORKSPACE` | Workspace IDs |
| `GLP_TOKEN_URL`, `GLP_BASE_URL` | GLP endpoint overrides |
| `TOKEN_CACHE_DIR` | Token cache directory |

## 3. Configure your MCP client

```bash
cp .mcp.json.example .mcp.json
```

Edit `.mcp.json` and replace `/path/to/centralmcp` with your local clone path.

Recommended default:

```env
CENTRALMCP_ROUTER_MODE=minimal
CENTRALMCP_TOOLSETS=central,glp,rag
```

This exposes only the router discovery/dispatch surface and keeps tool-list token cost low.

## 4. Build the tool catalog

```bash
uv run python scripts/ingest_tools.py
```

Include optional product starters:

```bash
uv run python scripts/ingest_tools.py --products all
```

## 5. Optional: build the docs/API RAG indexes

The router tool catalog is quick. The full docs/API index is larger.

```bash
uv run python ingestion/ingest_docs.py
```

Built indexes live under `data/` and are git-ignored.

## 6. Validate

```bash
uv run pytest tests/unit -q
uv run python scripts/validate_release.py
```

The unit suite includes static guards that keep async MCP tools off sync HTTP calls, prevent direct `CentralClient.session` bypasses, and keep direct runtime dependencies on `httpx` instead of `pycentral` or `requests`.

## Optional product starters

Optional product backends are disabled by default.

```env
CENTRALMCP_PRODUCTS=clearpass,mist,apstra,aos8,edgeconnect
```

| Product | Required variables |
|---|---|
| ClearPass | `CLEARPASS_BASE_URL`, `CLEARPASS_API_TOKEN` |
| Mist | `MIST_HOST`, `MIST_API_TOKEN` |
| Apstra | `APSTRA_BASE_URL`, `APSTRA_API_TOKEN` |
| AOS8 | `AOS8_BASE_URL`, `AOS8_API_TOKEN` |
| EdgeConnect | `EDGECONNECT_BASE_URL`, `EDGECONNECT_API_TOKEN`, optional `EDGECONNECT_AUTH_HEADER` |

## Safety defaults

- GLP writes are disabled unless `CENTRALMCP_GLP_V2BETA1_WRITES=1`.
- Token caches are stored in `~/.cache/centralmcp/` by default with `0600` permissions.
- Use `invoke_read_tool` for read-only router dispatch.
- Use `invoke_tool` only for intentional writes/destructive actions.
