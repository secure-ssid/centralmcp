"""Unit tests for specs_index.lookup() — the engine behind the lookup_api tool.

Builds a tiny OpenAPI spec fixture into a temp SQLite DB, so tests run without
the real data/specs.sqlite (gitignored) or any network.

Test bar:
- _query_stems drops question scaffolding, keeps domain terms, folds plurals,
  and expands hyphenated tokens into token + components
- exact enum hit: field-like term -> authoritative enum list, ranked first
- endpoint trim: device-firmware-upgrade resolves to the /device-firmware path
- relevance threshold: an off-corpus query returns [] (caller falls back to
  search_docs) instead of plausible-but-wrong rows
- exact-hit corroboration: a lone field-name collision on a multi-term query
  is dropped (the MVRP "registration" regression)
- missing DB raises FileNotFoundError with build instructions
"""

from __future__ import annotations

import json

import pytest

from pipeline.clients import specs_index


FIXTURE_SPECS = {
    "cda-auth-profile.json": {
        "info": {"title": "CDA Auth Profile"},
        "servers": [{"url": "https://example.test/cda"}],
        "paths": {
            "/auth-profiles/{name}": {
                "patch": {"summary": "Update auth profile",
                          "description": "Update an existing CDA auth profile."},
            },
        },
        "components": {"schemas": {
            "CdaAuthProfile": {
                "description": "CDA authentication profile.",
                "properties": {
                    "auth-type": {
                        "type": "string",
                        "description": "Authentication type for the profile.",
                        "enum": ["MPSK", "EAP", "CAPTIVE_PORTAL", "MAB"],
                    },
                },
            },
        }},
    },
    "firmware-management.json": {
        "info": {"title": "Firmware Management"},
        "servers": [{"url": "https://example.test/config"}],
        "paths": {
            "/device-firmware": {
                "post": {"summary": "Create device firmware settings",
                         "description": "Configure device firmware for a scope."},
                "patch": {"summary": "Update device firmware settings",
                          "description": "Update device firmware for a scope."},
            },
        },
        "components": {"schemas": {}},
    },
    "interface-ethernet.json": {
        "info": {"title": "Interface Ethernet"},
        "servers": [{"url": "https://example.test/config"}],
        "paths": {},
        "components": {"schemas": {
            "MvrpInterfaceConfig": {
                "description": "MVRP interface settings.",
                "properties": {
                    "registration": {
                        "type": "string",
                        "description": "MVRP registrar state machine control.",
                        "enum": ["NORMAL", "FIXED", "FORBIDDEN"],
                    },
                },
            },
        }},
    },
}


@pytest.fixture
def db(tmp_path):
    specs_dir = tmp_path / "specs"
    specs_dir.mkdir()
    for fname, spec in FIXTURE_SPECS.items():
        (specs_dir / fname).write_text(json.dumps(spec))
    db_path = tmp_path / "specs.sqlite"
    counts = specs_index.build(specs_dir=specs_dir, db_path=db_path)
    assert counts["specs"] == 3 and counts["endpoints"] == 3
    return db_path


# ---------------------------------------------------------------------------
# _query_groups
# ---------------------------------------------------------------------------


class TestQueryGroups:
    def test_drops_scaffolding_keeps_domain_terms(self):
        groups = specs_index._query_groups(
            "What are the valid auth-type enum values for an auth profile?"
        )
        flat = [s for g in groups for s in g]
        assert "auth-type" in flat
        assert "profile" in flat
        # scaffolding and generic API words are gone
        for noise in ("what", "the", "valid", "enum", "values", "for"):
            assert noise not in flat

    def test_hyphen_token_is_one_group_with_components(self):
        groups = specs_index._query_groups("device-firmware-upgrade endpoint")
        # one concept -> ONE group (components must not corroborate each other)
        assert len(groups) == 1
        assert groups[0][0] == "device-firmware-upgrade"
        assert {"device", "firmware", "upgrade"} <= set(groups[0])

    def test_plural_fold_is_prefix_safe(self):
        flat = [s for g in specs_index._query_groups("passpoint profiles") for s in g]
        assert "profile" in flat  # "profiles" folded, still a prefix of original

    def test_irregular_plural_keeps_both_spellings(self):
        # neither "policy" nor "policie" alone covers the other as a prefix
        groups = specs_index._query_groups("authorization policies")
        policy_group = next(g for g in groups if any(s.startswith("polic") for s in g))
        assert "policy" in policy_group and "policie" in policy_group

    def test_no_duplicate_groups_and_no_short_or_numeric(self):
        groups = specs_index._query_groups("802.1X dot1x dot1x profile profile")
        flat = [s for g in groups for s in g]
        assert flat.count("dot1x") == 1
        assert all(len(s) >= 3 and not s.isdigit() for s in flat)

    def test_stopwords_checked_before_stemming(self):
        # Regression: "does" stemmed to "doe" BEFORE the stopword check and
        # survived as a junk concept group polluting FTS and the threshold.
        groups = specs_index._query_groups("What does the opmode field accept?")
        flat = [s for g in groups for s in g]
        assert "doe" not in flat and "does" not in flat
        assert flat == ["opmode"]  # only the real concept survives

    def test_domain_synonyms_join_the_same_group(self):
        # Regression: users say "SSID", the specs say "wlan"/"essid" — the
        # synonym must corroborate within ONE group, not add a new concept.
        groups = specs_index._query_groups("ssid opmode")
        assert len(groups) == 2
        ssid_group = next(g for g in groups if "ssid" in g)
        assert "wlan" in ssid_group and "essid" in ssid_group


# ---------------------------------------------------------------------------
# lookup
# ---------------------------------------------------------------------------


class TestLookup:
    def test_exact_enum_hit_ranked_first_with_full_enum_list(self, db):
        hits = specs_index.lookup(
            "What are the valid auth-type values for a CDA auth profile?", db_path=db
        )
        assert hits, "expected an exact enum hit"
        top = hits[0]
        assert top["kind"] == "enum"
        assert "cda-auth-profile.json" in top["file_path"]
        for value in ("MPSK", "EAP", "CAPTIVE_PORTAL", "MAB"):
            assert value in top["text"]

    def test_endpoint_trim_resolves_hyphenated_token(self, db):
        # No /device-firmware-upgrade path exists; one right-trim finds /device-firmware
        hits = specs_index.lookup(
            "Is there a device-firmware-upgrade endpoint?", db_path=db
        )
        assert hits
        assert any(h["kind"] == "endpoint" and "/device-firmware" in h["text"] for h in hits)
        assert any("firmware-management.json" in h["file_path"] for h in hits)

    def test_off_corpus_query_returns_empty_not_noise(self, db):
        # Nothing about BGP route reflectors in the fixture -> honest empty
        hits = specs_index.lookup(
            "How do I configure a BGP route reflector cluster identifier?", db_path=db
        )
        assert hits == []

    def test_field_name_collision_needs_corroboration(self, db):
        # "registration" matches the MVRP field name, but nothing else in the
        # query corroborates it -> must NOT surface the MVRP enums
        hits = specs_index.lookup(
            "What URL and method updates a CNAC MAC registration?", db_path=db
        )
        assert all("MvrpInterfaceConfig" not in h["file_path"] for h in hits)

    def test_results_shape_matches_search_docs_contract(self, db):
        hits = specs_index.lookup("auth-type for the CDA auth profile", db_path=db)
        for h in hits:
            assert set(h) == {"text", "source", "file_path", "kind", "score"}
            assert h["source"] == "openapi_specs"
            assert h["file_path"].startswith("openapi_specs/")
            assert "#" in h["file_path"]

    def test_top_k_caps_results(self, db):
        hits = specs_index.lookup("cda auth profile firmware device", top_k=2, db_path=db)
        assert len(hits) <= 2

    def test_hyphen_components_do_not_self_corroborate(self, db):
        # Regression: "auth-type" used to expand to [auth-type, auth] and count
        # twice, letting an off-corpus query return confident enum hits instead
        # of [] (which would suppress the search_docs fallback).
        hits = specs_index.lookup(
            "auth-type quantum teleportation flux capacitor", db_path=db
        )
        assert hits == []

    def test_missing_db_raises_with_build_instructions(self, tmp_path):
        with pytest.raises(FileNotFoundError, match="--build"):
            specs_index.lookup("anything", db_path=tmp_path / "nope.sqlite")

    def test_corrupt_db_raises_filenotfound_not_sqlite_error(self, tmp_path):
        # The MCP tool only catches FileNotFoundError — sqlite errors from a
        # present-but-corrupt file must be converted, not leak to the transport.
        bad = tmp_path / "specs.sqlite"
        bad.write_bytes(b"this is not a sqlite database, not even close!!")
        with pytest.raises(FileNotFoundError, match="--build"):
            specs_index.lookup("auth-type enum", db_path=bad)

    def test_schemaless_db_raises_filenotfound(self, tmp_path):
        # An interrupted --build leaves a present-but-empty DB (build() unlinks
        # then recreates); lookup during that window must stay graceful.
        import sqlite3
        empty = tmp_path / "specs.sqlite"
        sqlite3.connect(empty).close()  # creates a 0-byte file
        with pytest.raises(FileNotFoundError, match="--build"):
            specs_index.lookup("firmware compliance", db_path=empty)
