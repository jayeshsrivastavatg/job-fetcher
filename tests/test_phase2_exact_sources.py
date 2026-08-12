from pathlib import Path

import pytest
import yaml

from job_fetcher.service import classify_error
from job_fetcher.sources.factory import build_source
from job_fetcher.sources.phase2_exact import (
    AtlassianListingsApiSource,
    NaviOfficialCareersSource,
    UberJobsApiSource,
)


def _companies():
    root = Path(__file__).resolve().parents[1]
    data = yaml.safe_load((root / "config" / "companies.yaml").read_text(encoding="utf-8"))
    return {c["id"]: c for c in data["companies"]}


def test_phase2_factory_routes_to_exact_sources():
    by = _companies()
    assert isinstance(build_source(by["uber"]), UberJobsApiSource)
    assert isinstance(build_source(by["atlassian"]), AtlassianListingsApiSource)
    assert isinstance(build_source(by["navi"]), NaviOfficialCareersSource)


def _uber_row(job_id: str, title: str, location="Bengaluru, Karnataka, India"):
    return {
        "Id": job_id,
        "Title": title,
        "DisplayDate": "2026-08-12",
        "Description": f"<p>{title} role description</p>",
        "Locations": [{"Address": location, "City": "Bengaluru", "Country": "India"}],
    }


def test_uber_exhausts_every_page_and_keeps_every_id(monkeypatch):
    source = UberJobsApiSource()
    pages = {
        1: {"jobs": [_uber_row("1", "Software Engineer"), _uber_row("2", "Backend Engineer")], "totalPages": 2, "totalJobs": 3, "page": 1, "pageSize": 2},
        2: {"jobs": [_uber_row("3", "Staff Engineer")], "totalPages": 2, "totalJobs": 3, "page": 2, "pageSize": 2},
    }
    calls = []

    def fake_page(page):
        calls.append(page)
        return pages[page]

    monkeypatch.setattr(source, "_page", fake_page)
    jobs = source.fetch({"id": "uber", "name": "Uber"})
    assert calls == [1, 2, 1]
    assert {job.external_id for job in jobs} == {"1", "2", "3"}
    assert {job.job_url for job in jobs} == {
        "https://jobs.uber.com/en/jobs/1/",
        "https://jobs.uber.com/en/jobs/2/",
        "https://jobs.uber.com/en/jobs/3/",
    }
    assert all(job.description for job in jobs)


def test_uber_fails_closed_when_enumeration_is_partial(monkeypatch):
    source = UberJobsApiSource()
    pages = {
        1: {"jobs": [_uber_row("1", "Software Engineer")], "totalPages": 2, "totalJobs": 10, "page": 1, "pageSize": 100},
        2: {"jobs": [_uber_row("2", "Backend Engineer")], "totalPages": 2, "totalJobs": 10, "page": 2, "pageSize": 100},
    }
    monkeypatch.setattr(source, "_page", lambda page: pages[page])
    with pytest.raises(RuntimeError, match="uber_jobs_api_incomplete"):
        source.fetch({"id": "uber", "name": "Uber"})


def _atlas_row(portal_id: int, job_id: int, title: str):
    return {
        "portalJobPost": {
            "portalId": portal_id,
            "id": job_id,
            "updatedDate": "2026-08-12 01:00 PM",
            "portalUrl": f"https://portal-{portal_id}.icims.com/jobs/{job_id}/job",
        },
        "portalId": portal_id,
        "id": job_id,
        "title": title,
        "locations": ["Bengaluru - India", "Remote - Remote"],
        "overview": f"<p>{title} overview</p>",
        "responsibilities": "<p>Build software</p>",
        "qualifications": "<p>Engineering experience</p>",
    }


def test_atlassian_keeps_same_numeric_job_id_from_different_portals(monkeypatch):
    source = AtlassianListingsApiSource()
    rows = [
        _atlas_row(17, 25020, "Software Engineer"),
        _atlas_row(242, 25020, "Backend Engineer"),
    ]
    monkeypatch.setattr(source, "_rows", lambda: rows)
    jobs = source.fetch({"id": "atlassian", "name": "Atlassian"})
    assert {job.external_id for job in jobs} == {"17:25020", "242:25020"}
    assert len(jobs) == 2
    assert all(job.job_url == "https://www.atlassian.com/company/careers/details/25020" for job in jobs)


def test_atlassian_fails_when_board_changes_materially(monkeypatch):
    source = AtlassianListingsApiSource()
    snapshots = [
        [_atlas_row(1, 1, "Engineer 1"), _atlas_row(1, 2, "Engineer 2"), _atlas_row(1, 3, "Engineer 3")],
        [_atlas_row(1, 4, "Engineer 4"), _atlas_row(1, 5, "Engineer 5"), _atlas_row(1, 6, "Engineer 6")],
    ]
    monkeypatch.setattr(source, "_rows", lambda: snapshots.pop(0))
    with pytest.raises(RuntimeError, match="board_changed"):
        source.fetch({"id": "atlassian", "name": "Atlassian"})


def test_navi_is_explicitly_blocked_not_empty_or_fake_jobs():
    source = NaviOfficialCareersSource()
    with pytest.raises(RuntimeError) as excinfo:
        source.fetch({"id": "navi", "name": "Navi"})
    assert classify_error(excinfo.value) == "manual_or_approved_feed_required"
    assert "approved" in str(excinfo.value).lower()
