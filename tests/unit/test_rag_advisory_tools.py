from __future__ import annotations

from mcp_servers import rag


def test_lookup_advisory_forwards_structured_filters(monkeypatch):
    captured = {}

    def fake_lookup(**kwargs):
        captured.update(kwargs)
        return [{"advisory_id": "HPESBNW04987"}]

    monkeypatch.setattr(rag.advisory_index, "lookup_advisories", fake_lookup)

    result = rag.lookup_advisory(
        product="AP-635",
        cve="CVE-2025-12345",
        min_severity="high",
        limit=500,
    )

    assert result == [{"advisory_id": "HPESBNW04987"}]
    assert captured == {
        "product": "AP-635",
        "cve": "CVE-2025-12345",
        "advisory_id": None,
        "min_severity": "high",
        "limit": 200,
    }


def test_check_product_lifecycle_clamps_limit(monkeypatch):
    captured = {}

    def fake_lookup(product, *, limit):
        captured.update(product=product, limit=limit)
        return [{"notice_id": "123"}]

    monkeypatch.setattr(rag.advisory_index, "lookup_lifecycle", fake_lookup)

    result = rag.check_product_lifecycle("AP-635", limit=-1)

    assert result == [{"notice_id": "123"}]
    assert captured == {"product": "AP-635", "limit": 1}


def test_structured_tools_return_missing_index_error(monkeypatch):
    def missing(*args, **kwargs):
        raise FileNotFoundError("rebuild the index")

    monkeypatch.setattr(rag.advisory_index, "lookup_advisories", missing)
    monkeypatch.setattr(rag.advisory_index, "lookup_lifecycle", missing)

    assert rag.lookup_advisory(cve="CVE-2025-12345") == [
        {"error": "rebuild the index"}
    ]
    assert rag.check_product_lifecycle("AP-635") == [
        {"error": "rebuild the index"}
    ]
