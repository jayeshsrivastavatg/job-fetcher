from __future__ import annotations

from job_fetcher.models import Job


def _job(eid, title="Software Engineer", url=None, location="Bengaluru, India", raw=None):
    return Job("acme", "Acme", "test", eid, title, location, None, url, None, raw or {})


def test_prefer_usable_jobs_removes_incomplete_fragments_when_good_records_exist():
    from job_fetcher.job_quality import prefer_usable_jobs

    jobs = [
        _job("1", url="https://example.com/jobs/1"),
        _job("2", url=None),
        _job("https://example.com/jobs/3", url=None),
    ]
    out = prefer_usable_jobs(jobs)
    assert {j.external_id for j in out} == {"1", "https://example.com/jobs/3"}
    assert all(j.job_url and j.job_url.startswith("https://") for j in out)


def test_prefer_usable_jobs_does_not_turn_all_incomplete_result_into_zero():
    from job_fetcher.job_quality import prefer_usable_jobs

    jobs = [_job("1", url=None), _job("2", url=None)]
    assert len(prefer_usable_jobs(jobs)) == 2


def test_oracle_browser_json_gets_canonical_public_job_url():
    from job_fetcher.sources.generic_extract import extract_jobs_from_json

    company = {"id": "oracle_oci", "name": "Oracle Cloud (OCI)"}
    payload = {
        "items": [{
            "Title": "Principal Member of Technical Staff",
            "Id": 310123,
            "PrimaryLocation": "BENGALURU, KARNATAKA, India",
            "ShortDescriptionStr": "Build distributed cloud infrastructure.",
            "PostedDate": "2026-08-10",
        }]
    }
    jobs = extract_jobs_from_json(
        company,
        payload,
        "https://careers.oracle.com/en/sites/jobsearch/jobs",
        "recovery_browser_json",
    )
    assert len(jobs) == 1
    assert jobs[0].external_id == "310123"
    assert jobs[0].job_url == "https://careers.oracle.com/en/sites/jobsearch/job/310123"
    assert "India" in (jobs[0].location or "")


def test_oraclecloud_browser_json_gets_candidate_experience_url():
    from job_fetcher.sources.generic_extract import extract_jobs_from_json

    company = {"id": "jpmorgan_chase", "name": "JPMorgan Chase (Tech)"}
    payload = {"Title": "Software Engineer", "Id": "300123", "PrimaryLocation": "Bengaluru, India"}
    jobs = extract_jobs_from_json(
        company,
        payload,
        "https://jpmc.fa.oraclecloud.com/hcmUI/CandidateExperience/en/sites/CX_1001/jobs",
    )
    assert len(jobs) == 1
    assert jobs[0].job_url.endswith("/hcmUI/CandidateExperience/en/sites/CX_1001/job/300123")


def test_swiggy_requisition_json_gets_stable_public_spa_url():
    from job_fetcher.sources.generic_extract import extract_jobs_from_json

    company = {"id": "swiggy", "name": "Swiggy"}
    payload = {
        "title": "Software Dev Engineer II",
        "reqId": 25453,
        "location": [{"City": "Bangalore"}],
        "Department": "Technology",
    }
    jobs = extract_jobs_from_json(company, payload, "https://careers.swiggy.com/list.html")
    assert len(jobs) == 1
    assert jobs[0].external_id == "25453"
    assert jobs[0].job_url == "https://careers.swiggy.com/#/careers?reqid=25453"


def test_direct_provider_recovery_is_registered_for_known_quality_problems():
    from job_fetcher.sources.recovery import RECOVERY_PLANS

    assert RECOVERY_PLANS["twilio"][0]["kind"] == "greenhouse"
    assert RECOVERY_PLANS["twilio"][0]["board_token"] == "twilio"
    assert RECOVERY_PLANS["inmobi"][0]["board_token"] == "inmobi"
    assert RECOVERY_PLANS["elastic"][0]["board_token"] == "elastic"
    assert RECOVERY_PLANS["hackerrank"][0]["board_token"] == "hackerrank"
    assert RECOVERY_PLANS["chargebee"][0]["kind"] == "successfactors"
    assert "rippling" in RECOVERY_PLANS
    assert "druva" in RECOVERY_PLANS
    assert "oracle_oci" in RECOVERY_PLANS
    assert "jpmorgan_chase" in RECOVERY_PLANS


def test_oracle_recovery_wrapper_preserves_oracle_type_contract():
    from job_fetcher.sources.factory import build_source
    from job_fetcher.sources.oracle import OracleSource

    company = {
        "id": "oracle_oci",
        "name": "Oracle Cloud (OCI)",
        "career_url": "https://careers.oracle.com/en/sites/jobsearch/jobs",
        "source": {
            "type": "oracle",
            "mode": "public_search",
            "entry_url": "https://careers.oracle.com/en/sites/jobsearch/jobs",
            "host": "careers.oracle.com",
            "site_number": "jobsearch",
        },
    }
    assert isinstance(build_source(company), OracleSource)


def test_blocked_sample_detail_is_fallback_not_suspicious(monkeypatch):
    import job_fetcher.health as health

    class Source:
        def fetch(self, company):
            return [_job("1", url="https://example.com/jobs/1")]

    monkeypatch.setattr(health, "build_source", lambda company: Source())
    monkeypatch.setattr(
        health,
        "_sample_detail",
        lambda jobs, timeout: (jobs[0].job_url, False, 403, "HTTP 403"),
    )

    company = {
        "id": "acme",
        "name": "Acme",
        "rank": 1,
        "career_url": "https://example.com/jobs",
        "source": {"type": "auto"},
    }
    row = health.verify_company(company, previous_count=1, drop_threshold=0.8, validate_detail=True, detail_timeout=1)
    assert row.status == "healthy_with_fallback"
    assert row.failure_category == "sample_detail_access_restricted"
    assert row.quality_ratio == 1.0
