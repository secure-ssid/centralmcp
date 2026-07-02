from langchain_text_splitters import RecursiveCharacterTextSplitter

CHUNK_SIZE = 800
CHUNK_OVERLAP = 100

_splitter = RecursiveCharacterTextSplitter(
    chunk_size=CHUNK_SIZE,
    chunk_overlap=CHUNK_OVERLAP,
    # "" is the character-level fallback: a run of text with no paragraph/
    # line/sentence/word break (a long URL, a base64 blob, a minified code
    # line) would otherwise emit as one unbounded chunk — silently truncated
    # past EmbedClient._MAX_CHARS when embedded, while the full untruncated
    # text is still stored and retrieved.
    separators=["\n\n", "\n", ". ", " ", ""],
)


def chunk_text(text: str) -> list[str]:
    return _splitter.split_text(text)
