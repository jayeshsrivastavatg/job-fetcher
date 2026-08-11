from __future__ import annotations

from job_fetcher.relevance_service import _change_type


def test_ruleset_hash_change_does_not_turn_unchanged_fetch_into_changed_event():
    row = {"last_change_type": "unchanged"}
    previous = {"source_hash": "old-ruleset-hash"}
    assert _change_type(row, previous, "new-ruleset-hash") == "unchanged"


def test_real_fetch_change_is_preserved_during_reanalysis():
    row = {"last_change_type": "changed"}
    previous = {"source_hash": "old-ruleset-hash"}
    assert _change_type(row, previous, "new-ruleset-hash") == "changed"
