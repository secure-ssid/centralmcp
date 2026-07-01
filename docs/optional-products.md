# Optional product starters

centralmcp keeps optional products disabled by default so normal MCP sessions
stay low-token. Enable only the starters you want for the current setup.

```bash
python3 scripts/setup_wizard.py --products clearpass,mist
```

Use every starter only when you intentionally want the broader catalog:

```bash
python3 scripts/setup_wizard.py --with-products
```

## Product matrix

| Product | Enables | Required settings | Safety surface |
|---|---|---|---|
| ClearPass | status, guarded GET/write, typed endpoint/auth/NAD/guest reads and lab writes | `CLEARPASS_BASE_URL`, `CLEARPASS_API_TOKEN` | Read/write starter; writes dry-run by default |
| Juniper Mist | status, guarded GET/write, typed site/client/WLAN/alarm reads and lab writes | `MIST_HOST`, `MIST_API_TOKEN` | Read/write starter; writes dry-run by default |
| Apstra | status, guarded GET/write, blueprint and anomaly reads | `APSTRA_BASE_URL`, `APSTRA_API_TOKEN` | Read/write starter; writes dry-run by default |
| ArubaOS 8 | status, guarded GET/write, show-command, AP inventory, and WLAN/AP-group reads | `AOS8_BASE_URL`, `AOS8_API_TOKEN` | Read/write starter; writes dry-run by default |
| EdgeConnect | status, guarded GET/write, appliance inventory/system/alarm/tunnel reads | `EDGECONNECT_BASE_URL`, `EDGECONNECT_API_TOKEN`, optional `EDGECONNECT_AUTH_HEADER` | Read/write starter; writes dry-run by default |

The generic GET tools reject absolute URLs and stay bounded to the configured
product host. List-like responses are paged with `limit` and `offset` when
possible so broad API calls do not flood the MCP context.

Write-capable optional product tools are intended for lab and controlled
operations. They are annotated as write/destructive, default to `dry_run=True`,
and require `dry_run=False` plus `confirm=True` before sending API changes.
Set `CENTRALMCP_PRODUCT_ACCESS=read-only` to hide optional product write tools
from router discovery and block write-tool execution. The setup wizard defaults
to `read-write` so lab workflows can preview and execute writes when explicitly
confirmed.

Product base URLs must use HTTPS and public hostnames by default. For local lab
testing against localhost or private IPs, set
`CENTRALMCP_ALLOW_LOCAL_PRODUCT_URLS=1` only in that trusted lab environment.

## What the wizard writes

When you select products, the setup wizard:

1. Adds `CENTRALMCP_PRODUCTS`, `CENTRALMCP_PRODUCT_ACCESS`, and product
   URL/token settings to local `.env`.
2. Adds only `CENTRALMCP_PRODUCTS` and `CENTRALMCP_PRODUCT_ACCESS` to local MCP
   config files, leaving product tokens in `.env`.
3. Builds the router tool catalog with the selected product starters and access
   mode unless you use `--with-products`.
4. Lets `scripts/doctor.py` confirm required product variables are present.

Real `.env`, `.mcp.json`, and `.vscode/mcp.json` files are git-ignored.

## Manual setup

The wizard defaults optional products to read/write lab mode and records that
access mode in local `.env` / MCP config files:

```bash
python3 scripts/setup_wizard.py --products clearpass,mist --product-access read-write
```

Use `--product-access read-only` if you want generated local configs to document
a read-only operating posture.

For manual shell setup:

```bash
export CENTRALMCP_PRODUCTS=clearpass,mist
export CENTRALMCP_PRODUCT_ACCESS=read-write
export CLEARPASS_BASE_URL=https://clearpass.example.com
export CLEARPASS_API_TOKEN=...
export MIST_HOST=https://api.mist.com
export MIST_API_TOKEN=...
uv run python scripts/ingest_tools.py --products clearpass,mist
```

For streamable HTTP, `scripts/run_http_router.sh` safely loads expected local
`.env` assignments before starting the router:

```bash
MCP_PORT=8010 bash scripts/run_http_router.sh
```

## When to add product-specific tools

The starters are intentionally small. Add product-specific tools when a workflow
is common enough to deserve a typed, named function instead of a generic GET
call, for example:

| Workflow type | Better as a typed tool? |
|---|---|
| "Show ClearPass endpoint status for this MAC" | Yes |
| "List Mist sites with client counts" | Yes |
| "Fetch this one documented endpoint while exploring" | Generic GET is fine |
| "Perform a write/remediation action" | Yes, with explicit destructive annotations and confirmation |

See [Typed product workflow roadmap](product-workflows.md) for implemented
ClearPass, Mist, Apstra, ArubaOS 8, and EdgeConnect workflows plus candidates.
