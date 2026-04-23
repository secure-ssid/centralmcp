from qdrant_client import QdrantClient
from qdrant_client.models import Distance, VectorParams

QDRANT_URL = "http://localhost:6333"
DOCS_COLLECTION = "network_docs"
EMBEDDING_DIMS = 768  # nomic-embed-text


def get_client(url: str = QDRANT_URL) -> QdrantClient:
    return QdrantClient(url=url)


def ensure_collection(
    client: QdrantClient,
    name: str = DOCS_COLLECTION,
    dims: int = EMBEDDING_DIMS,
):
    existing = {c.name for c in client.get_collections().collections}
    if name not in existing:
        client.create_collection(
            collection_name=name,
            vectors_config=VectorParams(size=dims, distance=Distance.COSINE),
        )
        print(f"Created collection '{name}'")
    else:
        print(f"Collection '{name}' already exists")
