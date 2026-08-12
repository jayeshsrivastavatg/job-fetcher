from __future__ import annotations

from job_fetcher.sources.eightfold_pcsx_exhaustive import EightfoldPcsxExhaustiveSource


def _company():
    return {
        "id": "morgan_stanley",
        "name": "Morgan Stanley",
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

    # The provider reports 4 result rows, but ID 2 appears twice. This is three
    # unique vacancies, not one missing vacancy.
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
    assert evidence["passes"] == 2
    assert passes["number"] == 2
    assert rows["2"]["locations"] == ["Mumbai, India", "Bengaluru, India"]


def test_exhaustive_walker_fails_if_provider_row_space_is_not_consumed(monkeypatch):
    source = EightfoldPcsxExhaustiveSource()
    monkeypatch.setattr(
        source,
        "_page",
        lambda origin, domain, start: ([_row(1, "India")], 3) if start == 0 else ([], 3),
    )

    try:
        source.enumerate_rows(_company())
    except RuntimeError as exc:
        assert "premature_empty" in str(exc) or "row_space_incomplete" in str(exc)
    else:
        raise AssertionError("expected incomplete provider row space to fail closed")
