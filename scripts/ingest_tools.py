"""Embed FastMCP tool definitions into the router tools index.

Usage: uv run python scripts/ingest_tools.py                  # LanceDB (embedded, default)
       uv run python scripts/ingest_tools.py --backend redis  # legacy Redis Stack
       uv run python scripts/ingest_tools.py --products clearpass,mist

Reads the servers by direct module import (no subprocess) and walks the
`mcp._tool_manager._tools` registry. Each tool becomes one indexed row with:
  payload: {server, name, description, schema_json}
  vector:  embedding of "name\\ndescription\\nparam_names"
"""
import argparse
import hashlib
import importlib
import json
import os
import sys
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

SERVERS = [
    ("aruba-config", "mcp_servers.config"),
    ("aruba-monitoring", "mcp_servers.monitoring"),
    ("aruba-nac", "mcp_servers.nac"),
    ("aruba-ops", "mcp_servers.ops"),
    ("aruba-glp", "mcp_servers.glp"),
    ("aruba-rag", "mcp_servers.rag"),
]
OPTIONAL_SERVERS = {
    "clearpass": ("clearpass-core", "mcp_servers.clearpass"),
    "mist": ("mist-core", "mcp_servers.mist"),
    "apstra": ("apstra-core", "mcp_servers.apstra"),
    "aos8": ("aos8-core", "mcp_servers.aos8"),
    "edgeconnect": ("edgeconnect-core", "mcp_servers.edgeconnect"),
}


def _csv(value: str | None) -> list[str]:
    return [item.strip().lower() for item in (value or "").split(",") if item.strip()]


def _server_specs(products: str | None = None) -> list[tuple[str, str]]:
    """Return core server specs plus optional products requested by arg/env."""
    requested = _csv(products if products is not None else os.getenv("CENTRALMCP_PRODUCTS"))
    specs = list(SERVERS)
    if "all" in requested:
        requested = list(OPTIONAL_SERVERS)
    for product in requested:
        spec = OPTIONAL_SERVERS.get(product)
        if spec and spec not in specs:
            specs.append(spec)
    return specs


def _stable_id(server: str, tool: str) -> str:
    h = hashlib.sha1(f"{server}:{tool}".encode()).hexdigest()
    return str(uuid.UUID(h[:32]))


def _extract_tools(module_path: str) -> list[dict]:
    mod = importlib.import_module(module_path)
    manager = mod.mcp._tool_manager
    out = []
    for name, tool in manager._tools.items():
        schema = tool.parameters if isinstance(tool.parameters, dict) else {}
        params = list((schema.get("properties") or {}).keys())
        out.append({
            "name": name,
            "description": (tool.description or "").strip(),
            "schema": schema,
            "params": params,
        })
    return out


def _embed_text(t: dict) -> str:
    # Repeat the name — tool names carry most of the semantic signal but are
    # short relative to docstrings; duplication lifts name-match recall.
    name_words = t["name"].replace("_", " ")
    return (
        f"{t['name']}\n{name_words}\n{name_words}\n"
        f"{t['description']}\nparams: {', '.join(t['params'])}"
    )


def _collect(products: str | None = None) -> list[tuple[str, dict]]:
    out = []
    for server, module_path in _server_specs(products):
        tools = _extract_tools(module_path)
        print(f"  {server}: {len(tools)} tools")
        out.extend((server, t) for t in tools)
    return out


def main_lancedb(products: str | None = None) -> int:
    from pipeline.clients import lance_client
    from pipeline.clients.embed_client import EmbedClient

    pairs = _collect(products)
    embedder = EmbedClient()
    vectors = embedder.embed_document([_embed_text(t) for _, t in pairs])
    rows = [
        {
            "id": _stable_id(server, t["name"]),
            "server": server,
            "name": t["name"],
            "description": t["description"],
            "schema_json": json.dumps(t["schema"]),
            # FTS half of hybrid tool search runs over this column
            "fts_text": (f"{t['name'].replace('_', ' ')} {t['name']} "
                         f"{t['description']} {' '.join(t['params'])}"),
            "vector": vec,
        }
        for (server, t), vec in zip(pairs, vectors)
    ]
    db = lance_client.connect()
    lance_client.create_tools_table(db, rows)
    print(f"Ingested {len(rows)} tools into LanceDB '{lance_client.TOOLS_TABLE}'")
    return 0


def main_redis(products: str | None = None) -> int:
    from pipeline.clients.ollama_client import OllamaClient
    from pipeline.clients.redis_client import (
        TOOLS_INDEX,
        ensure_tools_index,
        get_client,
        upsert_tools,
    )

    ollama = OllamaClient()
    client = get_client()

    # Drop and recreate the index for a clean re-ingest
    try:
        client.ft(TOOLS_INDEX).dropindex(delete_documents=True)
        print(f"Dropped existing index '{TOOLS_INDEX}'")
    except Exception:
        pass
    ensure_tools_index(client)

    batch: list[dict] = []
    for server, t in _collect(products):
        vec = ollama.embed(_embed_text(t))
        batch.append({
            "id": _stable_id(server, t["name"]),
            "server": server,
            "name": t["name"],
            "description": t["description"],
            "schema_json": json.dumps(t["schema"]),
            "params": t["params"],
            "embedding": vec,
        })

    upsert_tools(client, batch)
    print(f"Ingested {len(batch)} tools into '{TOOLS_INDEX}'")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--backend", choices=("lancedb", "redis"), default="lancedb")
    ap.add_argument(
        "--products",
        default=None,
        help=(
            "Optional product catalog entries to include, comma-separated "
            "(clearpass,mist,apstra,aos8,edgeconnect,all). Defaults to CENTRALMCP_PRODUCTS."
        ),
    )
    args = ap.parse_args()
    return main_redis(args.products) if args.backend == "redis" else main_lancedb(args.products)


if __name__ == "__main__":
    sys.exit(main())
