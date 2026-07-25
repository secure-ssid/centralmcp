from __future__ import annotations

from ingestion import ingest_docs
from pipeline.clients import lance_client


def _row(doc_id: str, text: str, content_hash: str) -> dict:
    return {
        "id": doc_id,
        "text": text,
        "source": "docs",
        "doc_type": "guide",
        "file_path": "docs/example.md",
        "chunk_index": 0,
        "content_hash": content_hash,
        "vector": [0.0, 0.0],
    }


def test_content_hash_is_stable_and_changes_with_text():
    assert ingest_docs.content_hash("same") == ingest_docs.content_hash("same")
    assert ingest_docs.content_hash("same") != ingest_docs.content_hash("changed")


def test_lancedb_merge_and_delete_support_incremental_rows(tmp_path):
    db = lance_client.connect(tmp_path)
    first_id = "11111111-1111-1111-1111-111111111111"
    second_id = "22222222-2222-2222-2222-222222222222"
    lance_client.create_docs_table(db, [_row(first_id, "old", "old")])

    lance_client.merge_docs_rows(
        db,
        [
            _row(first_id, "updated", "new"),
            _row(second_id, "inserted", "inserted"),
        ],
    )

    metadata = {row["id"]: row for row in lance_client.docs_metadata(db)}
    assert metadata[first_id]["content_hash"] == "new"
    assert second_id in metadata

    lance_client.delete_docs_ids(db, [first_id])

    assert lance_client.doc_count(db) == 1
    assert lance_client.docs_metadata(db)[0]["id"] == second_id


def test_invalid_incremental_delete_id_is_rejected(tmp_path):
    db = lance_client.connect(tmp_path)
    lance_client.create_docs_table(
        db,
        [_row("11111111-1111-1111-1111-111111111111", "old", "old")],
    )

    try:
        lance_client.delete_docs_ids(db, ["not-a-safe-id"])
    except ValueError as exc:
        assert "invalid document id" in str(exc)
    else:
        raise AssertionError("invalid ID was accepted")
