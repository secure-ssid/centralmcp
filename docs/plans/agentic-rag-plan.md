# Agentic RAG - Plan for MCP Project

> **Historical note:** this was the original Qdrant/Ollama planning draft. The
> current `centralmcp` implementation uses embedded LanceDB + SQLite by default,
> with Redis only as an optional backend. See
> [`../architecture/RAG-ARCHITECTURE.md`](../architecture/RAG-ARCHITECTURE.md)
> for the active architecture.

Built for: Aruba/HPE network engineering use case
Stack: Python MCP server + Qdrant + Ollama (all self-hosted in Docker)

---

## The Big Picture

```
┌─────────────────────────────────────────────────┐
│  Portal (chat UI + events) — EXISTING PROJECT   │
│  Runs the agent loop, calls MCP tools           │
└────────────────────┬────────────────────────────┘
                     │ MCP protocol
┌────────────────────▼────────────────────────────┐
│  MCP Server (THIS PROJECT) — where RAG lives    │
│                                                  │
│  Phase 1 tools:                                  │
│   - search_docs(query)          [Qdrant]         │
│                                                  │
│  Phase 2 tools (add later):                      │
│   - query_logs(filters, range)  [OpenSearch]     │
│                                                  │
│  Phase 3 tools (add later):                      │
│   - search_incidents(query)     [Qdrant]         │
│   - get_device_status(id)       [Central API]    │
└──┬──────────────────┬─────────────────┬──────────┘
   │                  │                 │
┌──▼────┐        ┌────▼─────┐     ┌────▼──────┐
│Qdrant │        │OpenSearch│     │Central API│
│       │        │(Phase 2) │     │ (Phase 3) │
└───────┘        └──────────┘     └───────────┘
       ┌────────────┐
       │  Ollama    │  ← embeddings for Qdrant
       │(embeddings)│    (and later, maybe chat LLM too)
       └────────────┘
```

**The key idea:** your portal already calls MCP tools. We're just adding new tools to the MCP server. Portal code doesn't change much.

---

## Phase 1 Scope (what we build first)

**Goal:** ask your bot a question about Aruba/Juniper docs, get a good answer with sources.

**Deliverables:**
- Qdrant running in Docker on your network
- Ollama running in Docker with `nomic-embed-text` model
- Ingestion script that chunks docs + embeds + uploads to Qdrant
- One MCP tool: `search_docs(query, top_k=5, filters=None)`
- Docker compose file tying it all together

**Out of scope for Phase 1:** logs, incidents, live device state, query rewriting, reranking. Add those later once the basics work.

---

## Folder Layout

Add this structure to your existing MCP project. Adjust to match what you already have.

```
your-mcp-project/
├── docker-compose.yml          # UPDATE: add qdrant + ollama services
├── .env                        # UPDATE: add new config vars
├── requirements.txt            # UPDATE: add new deps
│
├── src/
│   ├── server.py               # EXISTING: your MCP server entry point
│   │
│   ├── tools/                  # NEW: organize tools by capability
│   │   ├── __init__.py
│   │   ├── search_docs.py      # PHASE 1: the docs RAG tool
│   │   ├── query_logs.py       # PHASE 2: stubbed out, add later
│   │   └── device_status.py    # PHASE 3: stubbed out, add later
│   │
│   ├── clients/                # NEW: data store clients (connection logic)
│   │   ├── __init__.py
│   │   ├── qdrant_client.py    # PHASE 1
│   │   ├── ollama_client.py    # PHASE 1
│   │   ├── opensearch_client.py # PHASE 2
│   │   └── central_client.py   # PHASE 3
│   │
│   └── config.py               # NEW: central config (env vars, constants)
│
├── ingestion/                  # NEW: scripts to populate data stores
│   ├── ingest_docs.py          # PHASE 1: chunk + embed + upload docs
│   ├── chunking.py             # PHASE 1: chunking logic
│   └── sources/                # PHASE 1: where your raw docs live
│       ├── aruba/
│       └── juniper/
│
└── tests/
    └── test_search_docs.py     # PHASE 1: basic sanity tests
```

**Why this layout works for growth:**
- Each tool is its own file. Adding Phase 2 = add a file, not refactor existing ones.
- Clients folder separates "how to connect" from "what to do with the data."
- Ingestion is its own folder because it's batch work, not request/response.

---

## Build Steps (Phase 1)

### Step 1: Infrastructure (Docker)

Update your `docker-compose.yml`:

```yaml
services:
  qdrant:
    image: qdrant/qdrant:latest
    container_name: qdrant
    ports:
      - "6333:6333"   # HTTP + dashboard
      - "6334:6334"   # gRPC
    volumes:
      - ./qdrant_data:/qdrant/storage
    restart: unless-stopped

  ollama:
    image: ollama/ollama:latest
    container_name: ollama
    ports:
      - "11434:11434"
    volumes:
      - ./ollama_data:/root/.ollama
    restart: unless-stopped
    # For GPU: uncomment below (requires nvidia-container-toolkit)
    # deploy:
    #   resources:
    #     reservations:
    #       devices:
    #         - driver: nvidia
    #           count: 1
    #           capabilities: [gpu]

  mcp-server:
    build: .
    container_name: mcp-server
    environment:
      - QDRANT_URL=http://qdrant:6333
      - OLLAMA_URL=http://ollama:11434
      - EMBEDDING_MODEL=nomic-embed-text
      - DOCS_COLLECTION=network_docs
    depends_on:
      - qdrant
      - ollama
    restart: unless-stopped
```

Bring it up:
```bash
docker compose up -d
docker exec ollama ollama pull nomic-embed-text
```

Verify:
- Qdrant dashboard: `http://your-ip:6333/dashboard`
- Ollama: `curl http://your-ip:11434/api/tags`

### Step 2: Python Dependencies

Add to `requirements.txt`:
```
qdrant-client>=1.9.0
httpx>=0.27.0            # for Ollama calls
pydantic>=2.0            # config
python-dotenv>=1.0       # env vars
# chunking helpers:
langchain-text-splitters>=0.2.0   # just for the splitter, not full langchain
# doc parsing (pick what you need):
pypdf>=4.0               # PDFs
beautifulsoup4>=4.12     # HTML
markdown>=3.5            # if you have markdown docs
```

### Step 3: Config file (`src/config.py`)

```python
import os
from dotenv import load_dotenv

load_dotenv()

# Qdrant
QDRANT_URL = os.getenv("QDRANT_URL", "http://localhost:6333")
DOCS_COLLECTION = os.getenv("DOCS_COLLECTION", "network_docs")

# Ollama
OLLAMA_URL = os.getenv("OLLAMA_URL", "http://localhost:11434")
EMBEDDING_MODEL = os.getenv("EMBEDDING_MODEL", "nomic-embed-text")
EMBEDDING_DIMS = 768  # nomic-embed-text is 768-dim

# Chunking
CHUNK_SIZE = 800         # tokens-ish, roughly
CHUNK_OVERLAP = 100

# Retrieval
DEFAULT_TOP_K = 5
```

### Step 4: Clients

`src/clients/ollama_client.py`:
```python
import httpx
from src.config import OLLAMA_URL, EMBEDDING_MODEL

class OllamaClient:
    def __init__(self, url=OLLAMA_URL, model=EMBEDDING_MODEL):
        self.url = url
        self.model = model
        self.client = httpx.Client(timeout=60.0)

    def embed(self, text: str) -> list[float]:
        resp = self.client.post(
            f"{self.url}/api/embeddings",
            json={"model": self.model, "prompt": text},
        )
        resp.raise_for_status()
        return resp.json()["embedding"]

    def embed_batch(self, texts: list[str]) -> list[list[float]]:
        # Ollama doesn't have true batch - loop. Fine for ingestion.
        return [self.embed(t) for t in texts]
```

`src/clients/qdrant_client.py`:
```python
from qdrant_client import QdrantClient
from qdrant_client.models import Distance, VectorParams, PointStruct
from src.config import QDRANT_URL, DOCS_COLLECTION, EMBEDDING_DIMS

def get_client():
    return QdrantClient(url=QDRANT_URL)

def ensure_collection(client, name=DOCS_COLLECTION):
    existing = [c.name for c in client.get_collections().collections]
    if name not in existing:
        client.create_collection(
            collection_name=name,
            vectors_config=VectorParams(size=EMBEDDING_DIMS, distance=Distance.COSINE),
        )
```

### Step 5: Ingestion (`ingestion/ingest_docs.py`)

Pseudo-code outline (build it out):
```python
# 1. Walk ingestion/sources/ for files
# 2. Parse each file based on extension (pdf/html/md/txt)
# 3. Chunk the text (use langchain's RecursiveCharacterTextSplitter)
# 4. For each chunk, build metadata:
#    {
#      "source": "aruba" | "juniper",
#      "product": "cx-switches" | "aos10" | etc,
#      "doc_type": "config-guide" | "cli-reference" | etc,
#      "file_path": "...",
#      "chunk_index": 0,
#    }
# 5. Embed via Ollama (batch for speed)
# 6. Upload to Qdrant via upsert (batches of 100-500)
```

**Chunking tip:** use `RecursiveCharacterTextSplitter` with separators `["\n\n", "\n", ". ", " "]` - tries paragraph breaks first, falls back to sentences, then words. Way better than raw character split.

**Metadata is gold.** Every chunk gets tagged. Later you can do "search only Aruba CX docs" by filtering on metadata - Qdrant handles this natively via its filter API.

### Step 6: The MCP Tool (`src/tools/search_docs.py`)

```python
from src.clients.qdrant_client import get_client
from src.clients.ollama_client import OllamaClient
from src.config import DOCS_COLLECTION, DEFAULT_TOP_K

# The actual tool function
def search_docs(query: str, top_k: int = DEFAULT_TOP_K, source: str | None = None):
    """
    Search network documentation for relevant chunks.
    
    Args:
        query: natural language question or search terms
        top_k: how many chunks to return (default 5)
        source: optional filter - 'aruba' or 'juniper'
    
    Returns:
        list of chunks with text, source, and score
    """
    ollama = OllamaClient()
    qdrant = get_client()
    
    # Embed the query
    query_vector = ollama.embed(query)
    
    # Build filter if source specified
    query_filter = None
    if source:
        from qdrant_client.models import Filter, FieldCondition, MatchValue
        query_filter = Filter(
            must=[FieldCondition(key="source", match=MatchValue(value=source))]
        )
    
    # Search
    results = qdrant.search(
        collection_name=DOCS_COLLECTION,
        query_vector=query_vector,
        query_filter=query_filter,
        limit=top_k,
    )
    
    return [
        {
            "text": r.payload.get("text", ""),
            "source": r.payload.get("source"),
            "product": r.payload.get("product"),
            "file_path": r.payload.get("file_path"),
            "score": r.score,
        }
        for r in results
    ]
```

Then register it with your MCP server however your current project does that (varies by MCP SDK version).

### Step 7: Smoke Test

```python
# tests/test_search_docs.py
from src.tools.search_docs import search_docs

def test_basic_search():
    results = search_docs("how do I configure WPA3 on an AP?")
    assert len(results) > 0
    for r in results:
        print(f"[{r['score']:.3f}] {r['source']} - {r['text'][:100]}...")
```

---

## Where Phases 2 and 3 Plug In

This is the whole point of building it clean now.

### Phase 2: Central Event Logs

**What you add:**
1. New service in docker-compose: `opensearch` (and optionally `opensearch-dashboards`)
2. New client: `src/clients/opensearch_client.py`
3. New tool: `src/tools/query_logs.py`
4. New ingestion: `ingestion/ingest_central_logs.py` (or a streaming pipeline via Central API)
5. Register new tool with MCP server

**What you DON'T change:** any of your Phase 1 code. `search_docs` keeps working. The LLM in your portal just gets a new tool it can choose to call.

**Tool signature preview:**
```python
def query_logs(
    device_id: str | None = None,
    site: str | None = None,
    event_type: str | None = None,
    start_time: str = "1h ago",
    end_time: str = "now",
    limit: int = 50,
):
    ...
```

### Phase 3: Incidents + Live Device State

**Incidents = more Qdrant.** Reuse the same Qdrant instance, different collection:
- Collection: `incidents`
- Ingestion: post-mortems, resolved tickets, runbooks
- Tool: `search_incidents(query)` — nearly identical to `search_docs`, just different collection

**Live device state = Central API.** No vector DB, no log store. Direct API call.
- Client: `src/clients/central_client.py` — handles Central auth + API calls
- Tool: `get_device_status(device_id)` — returns current state, clients connected, health

**What you add:** new files in `tools/` and `clients/`. Same pattern every time.
**What you change in existing code:** nothing.

---

## Things to Plan For (gotchas)

1. **Ollama on CPU is slow for embeddings.** Thousands of pages might take 20-60 min to ingest. That's fine - it's one-time. If you have a GPU, turn it on in compose.

2. **Re-ingestion strategy.** When docs update, you need to re-chunk and re-embed. Plan for this early. Easy version: wipe collection, re-ingest everything. Harder: track file hashes, only redo changed files.

3. **Metadata schema consistency.** Decide on your metadata fields early and stick to them. Mixing `product: "CX"` and `product: "cx-switches"` will bite you during filtering.

4. **Backup Qdrant.** The `./qdrant_data` volume is everything. Back it up, or be ready to re-ingest.

5. **Embedding model lock-in.** Whatever model you pick for ingestion, you MUST use the same one for queries. Changing models = re-embed everything. Pick once, stick with it, unless you're ready to rebuild.

6. **Tool descriptions matter more than tool code.** The LLM decides when to call `search_docs` vs `query_logs` based on the tool description. Write good ones. "Search network documentation for configuration and how-to information" is better than "search docs."

---

## Rough Time Estimate

Phase 1, working evenings/weekends:
- Infra setup (Docker, Ollama model pull): 1-2 hours
- Basic ingestion script: 3-5 hours
- MCP tool + register with server: 2-3 hours
- Testing + tweaking chunking: 3-5 hours (this is where you'll spend the most time)
- **Total: probably a weekend or two**

Phases 2 and 3 each take about the same - most of the work is ingestion pipeline for the new data source, not the tool itself.

---

## Quick Decision Checklist Before You Start

- [ ] What docs are you ingesting first? (PDF? HTML scrape? Internal markdown?)
- [ ] Where does the MCP project live on your network? (Which box runs Docker?)
- [ ] Is your existing MCP server using a specific SDK (official MCP Python SDK, FastMCP, etc.)? Tool registration differs.
- [ ] Do you want ingestion as a one-shot script or a service that watches a folder? (Start with script.)

---

## Next Steps

1. Get Qdrant + Ollama running in Docker, verify dashboards
2. Pick 10-20 docs to test with (small sample first - don't ingest everything day one)
3. Build the ingestion script against the sample
4. Build the `search_docs` tool, register with MCP
5. Test end-to-end from your portal
6. THEN scale up to full doc set
7. Then plan Phase 2

Do the small-scale end-to-end first. Don't ingest 5,000 pages only to find your chunking strategy stinks.
