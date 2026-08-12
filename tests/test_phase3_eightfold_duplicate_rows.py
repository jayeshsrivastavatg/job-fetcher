from __future__ import annotations

import pytest

from job_fetcher.sources.eightfold_pcsx_exhaustive import EightfoldPcsxExhaustiveSource


def _company(company_id="morgan_stanley"):
    return {
        "id": company_id,
        "name": company_id,
        "career_url": "https://jobs.example.com/careers?domain=example.com&hl=en",
        "source": {
            "type": "eightfold",
            "entry_url": "https://jobs.example.com/careers?domain=example.com&hl=en",
        },
    }


def _row(job_id: int, location: str):
    return {
        "id": job_id,
        "name": f"Engineer {job_id}",
        "locations": [location],
        "standardizedLocations": [location],
        "positionUrl": f"/careers/job/{job_id}",
    }


def test_provider_count_can_include_repeated_rows_for_one_vacancy(monkeypatch):
    source = EightfoldPcsxExhaustiveSource()
    passes = {"number": 0}

    def fake_page(origin, domain, start):
        if start == 0:
            passes["number"] += 1
        pages = {
            0: ([_row(1, "New York, US"), _row(2, "Mumbai, India")], 4),
            2: ([_row(2, "Bengaluru, India"), _row(3, "London, UK")], 4),
        }
        return pages[start]

    monkeypatch.setattr(source, "_page", fake_page)
    rows, evidence = source.enumerate_rows(_company())

    assert set(rows) == {"1", "2", "3"}
    assert evidence["reported_count"] == 4
    assert evidence["provider_row_count"] == 4
    assert evidence["duplicate_row_occurrences"] == 1
    assert evidence["duplicate_id_count"] == 1
    assert evidence["passes"] == 3
    assert passes["number"] == 3
    assert evidence["board_mutated_during_fetch"] is False
    assert rows["2"]["locations"] == ["Mumbai, India", "Bengaluru, India"]


def test_microsoft_uses_half_page_overlap_to_close_offset_shift_holes(monkeypatch):
    source = EightfoldPcsxExhaustiveSource()
    source._full_passes = 2
    starts = []

    # Ten-row provider pages with a 5-row Microsoft stride. The second request
    # intentionally overlaps IDs 6-10 from the first request before moving on.
    all_rows = [_row(i, "India") for i in range(1, 16)]

    def fake_page(origin, domain, start):
        starts.append(start)
        return all_rows[start : start + 10], len(all_rows)

    monkeypatch.setattr(source, "_page", fake_page)
    rows, evidence = source.enumerate_rows(_company("microsoft"))

    assert set(rows) == {str(i) for i in range(1, 16)}
    assert evidence["page_stride"] == 5
    assert evidence["pagination_exhausted"] is True
    assert starts[:3] == [0, 5, 10]
    assert evidence["furthest_covered_offset"] >= 15


def test_multi_pass_union_recovers_job_hidden_by_live_offset_shift(monkeypatch):
    source = EightfoldPcsxExhaustiveSource()
    source._full_passes = 3
    snapshots = [
        {"1": _row(1, "US"), "2": _row(2, "India"), "3": _row(3, "UK")},
        {"2": _row(2, "India"), "3": _row(3, "UK"), "4": _row(4, "India")},
        {"1": _row(1, "US"), "2": _row(2, "India"), "3": _row(3, "UK"), "4": _row(4, "India")},
    ]
    index = {"value": 0}

    def fake_walk(origin, domain):
        rows = snapshots[index["value"]]
        index["value"] += 1
        return {
            "by_id": rows,
            "reported_count": len(rows),
            "raw_rows": len(rows),
            "unique_count": len(rows),
            "duplicate_ids": {},
            "duplicate_row_occurrences": 0,
            "pages_requested": 1,
            "furthest_covered_offset": len(rows),
            "page_stride": source.page_size,
        }

    monkeypatch.setattr(source, "_walk_exact_once", fake_walk)
    rows, evidence = source.enumerate_rows(_company())

    assert set(rows) == {"1", "2", "3", "4"}
    assert evidence["passes"] == 3
    assert evidence["board_mutated_during_fetch"] is True
    assert evidence["pass_evidence"][1]["new_ids_added"] == 1
    assert evidence["pass_evidence"][1]["prior_ids_not_seen_this_pass"] == 1


def test_continuously_mutating_board_returns_bounded_union_with_evidence(monkeypatch):
    source = EightfoldPcsxExhaustiveSource()
    source._full_passes = 3
    counter = {"value": 0}

    def fake_walk(origin, domain):
        counter["value"] += 1
        job_id = counter["value"]
        rows = {str(job_id): _row(job_id, "India")}
        return {
            "by_id": rows,
            "reported_count": 1,
            "raw_rows": 1,
            "unique_count": 1,
            "duplicate_ids": {},
            "duplicate_row_occurrences": 0,
            "pages_requested": 1,
            "furthest_covered_offset": 1,
            "page_stride": source.page_size,
        }

    monkeypatch.setattr(source, "_walk_exact_once", fake_walk)
    rows, evidence = source.enumerate_rows(_company())

    assert set(rows) == {"1", "2", "3"}
    assert evidence["passes"] == 3
    assert evidence["board_mutated_during_fetch"] is True
    assert [x["new_ids_added"] for x in evidence["pass_evidence"]] == [1, 1, 1]


def test_exhaustive_walker_fails_if_provider_row_space_is_not_consumed(monkeypatch):
    source = EightfoldPcsxExhaustiveSource()
    monkeypatch.setattr(
        source,
        "_page",
        lambda origin, domain, start: ([_row(1, "India")], 3) if start == 0 else ([], 3),
    )

    with pytest.raises(RuntimeError) as excinfo:
        source.enumerate_rows(_company())
    assert "premature_empty" in str(excinfo.value) or "row_space_incomplete" in str(excinfo.value)
