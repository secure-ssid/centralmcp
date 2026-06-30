# centralmcp — docs

| Section | Doc | What |
|-----|------|------|
| Audits | [audits/AUDIT-2026-06-03.md](audits/AUDIT-2026-06-03.md) | Full verified deep-dive audit — 92 findings (2 critical, 20 high, 27 medium, 43 low), with file:line and fixes. |
| Architecture | [architecture/RAG-ARCHITECTURE.md](architecture/RAG-ARCHITECTURE.md) | RAG backend decision (embedded LanceDB + SQLite lookup + fastembed, no Docker), migration plan, eval harness + measured results. **Shipped 2026-06-03** — final eval: `api_exact` 1.00, `howto_recall@5` 0.90, `mrr` 0.90 (vs 0.50/0.80/0.34 Redis baseline). |
| Operations | [operations/hpe-support-events-endpoint.md](operations/hpe-support-events-endpoint.md) | HPE support events endpoint notes. |
| Plans | [plans/agentic-rag-plan.md](plans/agentic-rag-plan.md) | Agentic RAG implementation plan and milestones. |
| Eval harness | `../tests/eval/` | RAG eval set (`rag_eval.yaml`) + runner (`run_eval.py`) — measure retrieval quality before/after changes. |
| CI | `../.github/workflows/ci.yml` | GitHub Actions unit-test gate plus conditional RAG/API eval gate when local indexes are available. |

**Mirror / index in Obsidian:** `Central-MCP-Obsidian/Projects/centralmcp — Audit & RAG Redesign (2026-06-03)`
**Repo:** https://github.com/secure-ssid/centralmcp

## Current repo structure

| Path | Purpose |
|---|---|
| `mcp_servers/` | FastMCP domain servers, optional product starter backends, router, prompts, and middleware. |
| `pipeline/` | Migration pipeline, typed clients, credentials loading, state store, and SSID helpers. |
| `ingestion/` | Docs/API ingestion into LanceDB + SQLite. |
| `scripts/` | Tool-catalog ingestion, release validation, and local sync helpers. |
| `docs/audits/` | Deep audits and remediation findings. |
| `docs/architecture/` | Architecture decisions, especially embedded RAG. |
| `docs/operations/` | Operational endpoint notes and runbook-style docs. |
| `docs/plans/` | Implementation plans and future work. |
| `tests/unit/` | Mocked unit coverage for tools, clients, middleware, routing, RAG, and release gates. |
| `tests/eval/` | RAG/API quality eval data and runner. |
| `.github/workflows/` | GitHub Actions validation for unit tests, catalog floor, and optional RAG eval. |

Run the eval baseline:
```bash
uv run --with pyyaml python tests/eval/run_eval.py --k 5 --verbose
```

Run the release/CI gate:
```bash
uv run --with pyyaml python tests/eval/run_eval.py --ci
```

Run the same unit-test gate used by GitHub Actions:
```bash
uv run pytest tests/unit -q
```

Run the local release validation helper:
```bash
uv run python scripts/validate_release.py
```

The helper enforces the documented tool catalog floor by default; use
`--min-tools <count>` only when intentionally changing catalog scope. If a local
LanceDB tool index exists, the helper also verifies it is not stale relative to
the registered tools.
