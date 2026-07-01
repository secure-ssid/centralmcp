# centralmcp — RAG Architecture & Decision (2026-06-03)

**Repo:** https://github.com/secure-ssid/centralmcp
**Companion:** [../audits/AUDIT-2026-06-03.md](../audits/AUDIT-2026-06-03.md)

---

## TL;DR decision

> **Current default backend = embedded, no Docker, no background services:**
> - **LanceDB** — prose docs (developer/tech/NAC/VSG/aos), with native **hybrid (vector + BM25) + reranking**.
> - **SQLite** — OpenAPI specs as **exact structured lookup** (endpoints / schemas / fields / enums), *not* embeddings.
> - **fastembed** — embeddings in-process (ONNX); no Ollama required. Can run the same `nomic-embed-text-v1.5`.
> - **Ship a prebuilt index** as a GitHub Release asset so `git clone → uv sync → run` works with zero ingest.
> - **The portal consumes via the MCP** (`search_docs` / `ask_docs` over stdio or streamable-HTTP) — it never touches the store directly, so no shared server is needed.
>
> **Redis Stack** remains a documented, supported *server option* for anyone who wants it — but it is **not** the default for the cloned-and-run experience.

### Why this and not Redis (reconciling the audit)
The audit recommended **Redis Stack** — correctly, *for its scope*: "two backends are running and the git history is mid-flip; converge on one with the least code change." Redis is already wired in the working tree and holds both the docs and tool indexes.

But the project's primary goal is **"anyone can download the repo and run it,"** with the portal as a *consumer of the MCP*. Against that goal, a Redis/Docker service is the exact friction we want to remove. LanceDB delivers the same capabilities the audit credited to Redis (hybrid BM25+vector, one store for docs+tools, metadata filtering) **without a server**, and uniquely allows shipping a prebuilt index file. `fastembed` removes the last service (Ollama).

| Axis | Redis Stack (audit pick) | **LanceDB + fastembed (current default)** |
|---|---|---|
| Docker / services | Redis container (or local install) + Ollama | **none** (in-process) |
| "clone → run" UX | install Docker, start Redis, ingest 40k docs | **`uv sync` → run** (ship prebuilt index) |
| Hybrid (BM25+vector) | yes (RediSearch) | **yes (native)** |
| Reranking | build it | **built-in (RRF default)** |
| One store for docs + tools | yes | **yes** |
| Embeddings | Ollama service | **fastembed in-process (same nomic model)** |
| Migration effort | none (already wired) | one-time storage-layer rewrite + re-ingest |

Net: Redis wins only on "no work today." For a distributable tool, the one-time migration buys a dramatically simpler install for every future user.

---

## Why retrieval quality goes **up**, not down

Deployment (embedded vs server) does not affect retrieval quality — the *design* does. The target design is strictly better than today's vector-only Redis path:

1. **API/field/enum/endpoint questions → exact SQLite lookup, not vectors.** A large slice of the corpus is OpenAPI specs (structured JSON). Embedding them is lossy; vector search returns *fuzzy-similar* prose instead of the authoritative enum/field list. A `lookup_api(endpoint|schema|field)` tool over the parsed specs is exact and lossless — and doubles as the API-correctness checker the audit needed.
2. **Prose questions → hybrid (BM25 + vector) + rerank.** Today's path is vector-only and *misses exact identifiers* (`WPA3_SAE`, endpoint paths, error codes). BM25 catches those; a cross-encoder rerank promotes the truly relevant chunk. (~+15–30% precision in practice; Anthropic measured up to **67%** retrieval-failure reduction with contextual + hybrid + rerank.)
3. **Same embeddings, fixed prefixes.** fastembed can run `nomic-embed-text-v1.5` in-process — identical semantics to today — while fixing the **missing `search_query:`/`search_document:` prefixes** (see fix R3).
4. **Agentic safety net.** `search_docs`/`ask_docs` are called by an LLM that can re-query when results are thin.

---

## Backend-agnostic RAG fixes (from the audit — apply regardless of backend)

These are correctness/quality fixes; most are inherited or simplified by the LanceDB move.

- **R1 — Cosine math (Redis only).** `redis_client.py:126,242` use `1 - distance/2`; correct is `1 - distance` (verified: dist 0.2795 ↔ true sim 0.7205). *N/A under LanceDB* — it returns distance/score directly. If we ship a Redis interim, fix this first.
- **R2 — OpenAPI specs missing from the index.** The "ground-truth" source (`openapi_specs`, +0.08 boost) returns **0 results** from the live index; `aos_techdocs` also absent. *Resolved by design* under the target: specs go to SQLite structured lookup, not the vector index. (If staying on Redis: `ingest_docs.py --source openapi_specs --source aos_techdocs` + a post-ingest assert that every source has >0 docs.)
- **R3 — nomic task prefixes.** Embed passages as `search_document: <chunk>` and queries as `search_query: <q>`. ⚠️ The current 40,900-doc corpus was embedded **without** prefixes, so adding a query-only prefix *worsens* results — apply **both sides together + a full re-ingest**. Centralize in `embed_document()` / `embed_query()`.
- **R4 — Batched embeddings.** The default embedded path batches through fastembed (ONNX), and the optional Redis/Ollama path uses Ollama `/api/embed` with `{"input":[...]}` before falling back to legacy `/api/embeddings`. Full re-ingests now use batched embedding paths instead of serial per-chunk requests.
- **R5 — Hybrid + rerank.** Native in LanceDB (`.search(..., query_type="hybrid")` + a reranker; RRF default). Replaces the brittle static `_SOURCE_BOOST`.
- **R6 — Chunking.** 800/100 `RecursiveCharacterTextSplitter` cuts parameter tables/enum lists mid-structure. Use a header-aware splitter, ~1000–1200 chars / ~150 overlap (nomic context 2048 tokens). Re-ingest after.
- **R7 — `ask_docs(question)` tool.** Retrieve hybrid top-k → synthesize a **cited** answer with a small local model → return `{answer, citations}`. Keeps `search_docs` for raw chunks; cuts the per-question token cost the CLAUDE.md RAG-first rule otherwise forces.

---

## Target module layout

```
pipeline/clients/
  lance_client.py     # open table, hybrid search(query, k, source_filter) -> hits for the default embedded path
  embed_client.py     # fastembed wrapper: embed_document(list) / embed_query(str); model nomic-embed-text-v1.5
  specs_index.py      # build + query SQLite over OpenAPI specs: get_endpoint / get_schema / get_field / get_enum
mcp_servers/
  rag.py              # search_docs (hybrid) + ask_docs (cited) + lookup_api (exact)  — all READ_ONLY
ingestion/
  ingest_docs.py      # chunk prose -> embed_document -> LanceDB ; parse specs -> SQLite ; emit prebuilt artifacts
data/                 # prebuilt, shippable: docs.lance/  +  specs.sqlite   (attach to GitHub Release)
```

No `docker-compose.yml` requirement for the default path. `redis-stack` stays documented under an optional "Server backend" section for power users.

---

## Implemented migration sequence

This sequence is complete for the default local path. Redis remains optional; Qdrant is historical context only.

1. Add deps: `lancedb`, `fastembed`; keep `redis` only for the optional server backend.
2. `embed_client.py` (fastembed, `nomic-embed-text-v1.5`, `embed_document`/`embed_query` with prefixes — R3).
3. `lance_client.py`: create a hybrid table (vector + FTS on `text`), `search()` with `source` filter + reranker (R5).
4. `specs_index.py`: parse `ingestion/sources/openapi_specs/*.json` → SQLite (`endpoints`, `schemas`, `fields` tables) with FTS; query helpers (R2 resolved).
5. Rewrite `ingest_docs.py`: prose → LanceDB (header-aware chunking R6, batched embeds R4); specs → SQLite. Emit `data/docs.lance` + `data/specs.sqlite`.
6. `rag.py`: `search_docs` (hybrid), `lookup_api` (exact), `ask_docs` (cited R7). Point `tool_router`'s `aruba_tools` index at LanceDB too.
7. Re-ingest once; run the eval harness (below) to confirm quality ≥ current.
8. Keep generated `data/*` out of git; use release assets or local ingest for prebuilt indexes. Redis remains an optional backend.

---

## Eval harness (measure "is it selecting correct info" — before/after)

A small, labeled question set + runner so the backend swap is **proven**, not asserted. Lives at `tests/eval/`.

- `tests/eval/rag_eval.yaml` — ~20 questions, each tagged `api-lookup` (expects an exact field/enum/endpoint via `lookup_api`) or `howto` (expects a prose chunk via `search_docs`), with `expect_sources` (file_path substrings) and `expect_keywords`.
- `tests/eval/run_eval.py` — calls the RAG tools, computes **recall@k**, **source-hit@k**, and keyword presence; prints a per-question pass/fail table and an aggregate score. Run before and after migration; require no regression.

Metrics: `recall@5` (did an expected source appear in top-5), `mrr` (rank of first correct), `api_exact` (did `lookup_api` return the exact enum/field). Target: api-lookup `api_exact` = 100% (it's structured), howto `recall@5` ≥ today's baseline.

**Baseline measured 2026-06-03** (current Redis, vector-only, no prefixes, specs missing from index), and **re-measured the same day after wiring `lookup_api`** (SQLite specs index, H13 cosine fix + boost recalibration, query/document prefixes):

| Metric | Baseline (Redis, vector-only) | After `lookup_api` (2026-06-03) | **Final: embedded LanceDB hybrid (2026-06-03)** | Target |
|---|---|---|---|---|
| `howto_recall@5` (prose) | 0.80 | 0.80 | **0.90** | ≥ 0.80 ✅ |
| `api_exact` (API lookups) | **0.50** | 0.90 | **1.00** | 1.00 ✅ |
| `source_hit@5` (overall) | 0.50 | 0.80 | **0.90** | ≥ 0.75 ✅ |
| `mrr` | 0.339 | 0.679 | **0.90** | ≥ 0.50 ✅ |
| `keyword_hit` | — | 0.80 | **1.00** | — |

**Final corpus (full rebuild):** 53,052 chunks / 7 sources (Redis index had 40,900 and was missing `aos_techdocs`, `openapi_specs`, and most of `techdocs_html`) + 213-spec SQLite index + router tool index (currently 194 core tools / 235 with optional product starters). 18/20 eval questions hit at rank 1. Shippable artifacts: `data/docs.lance` (190 MB), `data/specs.sqlite` (18 MB), `data/tools.lance` (0.6 MB).

The API-lookup rows almost all missed the spec sources at baseline — direct empirical evidence of **R2** (OpenAPI specs absent from the active index). `howto` retrieval is already decent, confirming the redesign's value is concentrated in (a) structured API lookup and (b) hybrid+rerank for exact identifiers, not in replacing vector search wholesale. Re-run `uv run --with pyyaml python tests/eval/run_eval.py` after each change.

~~The remaining `api_exact` miss was `mac-reg-update-url`: the CNAC MAC-registration API was not in the 212 ingested config specs.~~ **Closed 2026-06-03:** the Central NAC Service spec (25 paths, 60 schemas — cnac-mac-reg/visitor/named-mpsk/dpp/certificates/jobs) is not served by the internal-ui cnxconfig docs host, but the readme.io reference pages embed the full OAS document; `ingestion/scrape_cnac_spec.py` extracts it to `cnac-client-registration.json` (213 specs total). With it indexed, **`api_exact` = 1.00** — all 10 api-lookup questions resolve through `lookup_api` with the correct spec at rank 1, no prose fallback needed.

---

## Original open questions and current defaults

These questions were captured during the migration decision. The current repository defaults are embedded LanceDB + SQLite, `fastembed`, release/ignored `data/*` indexes, and Redis as an optional server backend.

1. **Embedding model:** keep `nomic-embed-text-v1.5` (via fastembed) for identical semantics, or move to `bge-base-en-v1.5`? (Both good; nomic = no quality change, just drops Ollama.)
2. **Ship prebuilt index in-repo or as a Release asset?** Release asset keeps the repo small; in-repo is zero-step but bloats clones.
3. **Keep a Redis "server option" appendix**, or go all-in embedded and remove Redis entirely? Current default: embedded LanceDB + SQLite, with Redis still available as an optional backend.
