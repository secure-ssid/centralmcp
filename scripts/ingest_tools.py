"""Embed all FastMCP tool definitions from the 6 Aruba servers into Qdrant.

Usage: uv run python scripts/ingest_tools.py

Reads the servers by direct module import (no subprocess) and walks the
`mcp._tool_manager._tools` registry. Each tool becomes one Qdrant point with:
  payload: {server, name, description, schema, signature}
  vector:  embedding of "name\\ndescription\\nparam_names"
"""
import hashlib
import importlib
import json
import sys
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from qdrant_client.models import Distance, PointStruct, VectorParams

from pipeline.clients.ollama_client import OllamaClient
from pipeline.clients.qdrant_client import EMBEDDING_DIMS, QDRANT_URL, get_client

TOOLS_COLLECTION = "aruba_tools"

SERVERS = [
    ("aruba-config", "mcp_servers.config"),
    ("aruba-monitoring", "mcp_servers.monitoring"),
    ("aruba-nac", "mcp_servers.nac"),
    ("aruba-ops", "mcp_servers.ops"),
    ("aruba-glp", "mcp_servers.glp"),
    ("aruba-rag", "mcp_servers.rag"),
]


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


def main() -> int:
    ollama = OllamaClient()
    qc = get_client()

    existing = {c.name for c in qc.get_collections().collections}
    if TOOLS_COLLECTION in existing:
        qc.delete_collection(TOOLS_COLLECTION)
    qc.create_collection(
        collection_name=TOOLS_COLLECTION,
        vectors_config=VectorParams(size=EMBEDDING_DIMS, distance=Distance.COSINE),
    )
    print(f"Created collection '{TOOLS_COLLECTION}'")

    points: list[PointStruct] = []
    total = 0
    for server, module_path in SERVERS:
        tools = _extract_tools(module_path)
        print(f"  {server}: {len(tools)} tools")
        for t in tools:
            vec = ollama.embed(_embed_text(t))
            points.append(PointStruct(
                id=_stable_id(server, t["name"]),
                vector=vec,
                payload={
                    "server": server,
                    "name": t["name"],
                    "description": t["description"],
                    "schema": json.dumps(t["schema"]),
                    "params": t["params"],
                },
            ))
        total += len(tools)

    for i in range(0, len(points), 64):
        qc.upsert(collection_name=TOOLS_COLLECTION, points=points[i:i + 64])
    print(f"Ingested {total} tools into '{TOOLS_COLLECTION}' @ {QDRANT_URL}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
