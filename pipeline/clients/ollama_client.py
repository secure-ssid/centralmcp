import time
import httpx

OLLAMA_URL = "http://localhost:11434"
EMBEDDING_MODEL = "nomic-embed-text"
# nomic-embed-text context window; truncate to stay safe
_MAX_CHARS = 6000


class OllamaClient:
    def __init__(self, url: str = OLLAMA_URL, model: str = EMBEDDING_MODEL):
        self.url = url
        self.model = model
        self._client = httpx.Client(timeout=60.0)

    def embed(self, text: str) -> list[float]:
        text = text[:_MAX_CHARS]
        for attempt in range(3):
            try:
                resp = self._client.post(
                    f"{self.url}/api/embeddings",
                    json={"model": self.model, "prompt": text},
                )
                resp.raise_for_status()
                return resp.json()["embedding"]
            except httpx.HTTPStatusError as e:
                if e.response.status_code == 500 and attempt < 2:
                    time.sleep(2 ** attempt)
                    continue
                raise
        raise RuntimeError("Ollama embed failed after 3 attempts")

    def embed_batch(self, texts: list[str]) -> list[list[float]]:
        return [self.embed(t) for t in texts]

    def close(self):
        self._client.close()

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.close()
