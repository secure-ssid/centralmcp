"""Regression test: chunker must bound chunk size even with no textual separator."""

from __future__ import annotations

from ingestion.chunking import CHUNK_SIZE, chunk_text


def test_unbroken_text_is_still_split_to_chunk_size():
    # A long run with no \n\n, \n, ". ", or " " (a URL, base64 blob, minified
    # code) previously produced one unbounded chunk instead of falling back
    # to character-level splitting.
    chunks = chunk_text("A" * (CHUNK_SIZE * 4))

    assert len(chunks) > 1
    assert all(len(c) <= CHUNK_SIZE for c in chunks)


def test_normal_prose_chunking_is_unaffected():
    text = ("This is a normal paragraph of prose text that splits cleanly. " * 20 + "\n\n") * 3

    chunks = chunk_text(text)

    assert len(chunks) > 1
    assert all(len(c) <= CHUNK_SIZE for c in chunks)
