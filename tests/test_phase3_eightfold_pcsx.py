from __future__ import annotations

from copy import deepcopy
from pathlib import Path

import pytest
import yaml

from job_fetcher.sources.eightfold_pcsx import EightfoldPcsxSource
from job_fetcher.sources.factory import build_source


def _companies():
    root = Path(__file__).resolve().parents[1]
    data = yaml.safe_load((root / "config" / "companies.yaml").read_text(encoding="utf-8"))
    return {c["id"]: c for c in data["companies"]}


def _company(cid="microsoft"):
    return {
        "id": cid,
        "name": cid,
        "career_url": f"https://jobs.example.com/careers?domain={cid}.com&hl=en",
        "source": {
            "type": "eightfold",
            "entry_url": f"https://jobs.example.com/careers?domain={cid}.com&hl=en",
        },
    }


def _row(job_id: int, title: str | None = None, location="United States, Washington, Redmond"):
    return {
        "id": job_id,
        "displayJobId": f"REQ-{job_id}",
        "name": title or f"Software Engineer {job_id}",
        "locations": [location],
        "standardizedLocations": ["Redmond, WA, US" if "India" not in location else "Bengaluru, KA, IN"],
        "postedTs": 1780000000,
        "positionUrl": f"/careers/job/{job_id}",
    }


def test_factory_routes_phase3_companies_to_direct_pcsx():
    by = _companies()
    for cid in ("microsoft", "twilio", "morgan_stanley"):
        assert isinstance(build_source(deepcopy(by[cid])), EightfoldPcsxSource)


def test_contract_uses_exact_branded_origin_and_domain():
    company = _company("microsoft")
    origin, domain, _ = EightfoldPcsxSource.contract(company)
    assert origin == "https://jobs.example.com"
    assert domain == "microsoft.com"


def test_public_pcs_page_size_is_the_provider_maximum():
    assert EightfoldPcsxSource.page_size == 10


def test_exhausts_offsets_and_preserves_every_position_id(monkeypatch):
    source = EightfoldPcsxSource()
    pages = {
        0: ([_row(1), _row(2)], 5),
        2: ([_row(3), _row(4)], 5),
        4: ([_row(5)], 5),
    }
    calls = []

    def fake_page(origin, domain, start):
        calls.append(start)
        return pages[start]

    monkeypatch.setattr(source, "_page", fake_page)
    rows, evidence = source.enumerate_rows(_company())
    assert set(rows) == {"1", "2", "3", "4", "5"}
    assert calls == [0, 2, 4]
    assert evidence["reported_count"] == 5
    assert evidence["unique_count"] == 5
    assert evidence["pagination_exhausted"] is True


def test_chases_count_growth_during_pagination(monkeypatch):
    source = EightfoldPcsxSource()
    pages = {
        0: ([_row(1), _row(2)], 4),
        2: ([_row(3), _row(4)], 6),
        4: ([_row(5), _row(6)], 6),
    }
    monkeypatch.setattr(source, "_page", lambda origin, domain, start: pages[start])
    rows, evidence = source.enumerate_rows(_company())
    assert set(rows) == {"1", "2", "3", "4", "5", "6"}
    assert evidence["reported_count"] == 6


def test_retries_with_union_when_offset_shift_creates_duplicate(monkeypatch):
    source = EightfoldPcsxSource()
    calls = []
    first_pass = {
        0: ([_row(1), _row(2)], 4),
        2: ([_row(2), _row(3)], 4),
    }
    second_pass = {
        0: ([_row(1), _row(2)], 4),
        2: ([_row(3), _row(4)], 4),
    }
    phase = {"second": False}

    def fake_page(origin, domain, start):
        calls.append(start)
        if start == 0 and calls.count(0) >= 2:
            phase["second"] = True
        return (second_pass if phase["second"] else first_pass)[start]

    monkeypatch.setattr(source, "_page", fake_page)
    rows, evidence = source.enumerate_rows(_company())
    assert set(rows) == {"1", "2", "3", "4"}
    assert evidence["reported_count"] == 4
    assert calls == [0, 2, 0, 2]


def test_fails_closed_if_second_pass_still_cannot_cover_reported_count(monkeypatch):
    source = EightfoldPcsxSource()
    pages = {
        0: ([_row(1), _row(2)], 4),
        2: ([_row(2), _row(3)], 4),
    }
    monkeypatch.setattr(source, "_page", lambda origin, domain, start: pages[start])
    with pytest.raises(RuntimeError, match="eightfold_pcsx_incomplete"):
        source.enumerate_rows(_company())


def test_india_job_is_hydrated_but_listing_survives_detail_failure(monkeypatch):
    source = EightfoldPcsxSource()
    india = _row(7, "Principal Software Engineer", "Bengaluru, Karnataka, India")
    monkeypatch.setattr(source, "enumerate_rows", lambda company: (
        {"7": india},
        {"origin": "https://jobs.example.com", "domain": "example.com", "reported_count": 1, "unique_count": 1, "pagination_exhausted": True, "pages_requested": 1},
    ))
    monkeypatch.setattr(source, "_detail", lambda origin, domain, position_id: {
        **india,
        "jobDescription": "<p>Build distributed systems.</p>",
        "publicUrl": "https://jobs.example.com/careers/job/7",
    })
    jobs = source.fetch(_company())
    assert len(jobs) == 1
    assert jobs[0].external_id == "7"
    assert jobs[0].description == "Build distributed systems."
    assert jobs[0].source_type == "eightfold_pcsx"
    assert jobs[0].raw["_pcsx_detail_hydrated"] is True

    monkeypatch.setattr(source, "_detail", lambda *args: None)
    jobs = source.fetch(_company())
    assert len(jobs) == 1
    assert jobs[0].external_id == "7"
    assert jobs[0].description is None
    assert jobs[0].raw["_pcsx_detail_hydrated"] is False
