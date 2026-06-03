# centralmcp — docs

| Doc | What |
|-----|------|
| [AUDIT-2026-06-03.md](AUDIT-2026-06-03.md) | Full verified deep-dive audit — 92 findings (2 critical, 20 high, 27 medium, 43 low), with file:line and fixes. |
| [RAG-ARCHITECTURE.md](RAG-ARCHITECTURE.md) | RAG backend decision (embedded LanceDB + SQLite lookup + fastembed, no Docker), migration plan, eval harness + measured results. **Shipped 2026-06-03** — final eval: `api_exact` 1.00, `howto_recall@5` 0.90, `mrr` 0.90 (vs 0.50/0.80/0.34 Redis baseline). |
| [hpe-support-events-endpoint.md](hpe-support-events-endpoint.md) | HPE support events endpoint notes. |
| `../tests/eval/` | RAG eval set (`rag_eval.yaml`) + runner (`run_eval.py`) — measure retrieval quality before/after changes. |

**Mirror / index in Obsidian:** `Central-MCP-Obsidian/Projects/centralmcp — Audit & RAG Redesign (2026-06-03)`
**Repo:** https://github.com/secure-ssid/centralmcp

Run the eval baseline:
```bash
uv run --with pyyaml python tests/eval/run_eval.py --k 5 --verbose
```
