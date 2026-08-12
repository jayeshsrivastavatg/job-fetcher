from job_fetcher.models import Job
from job_fetcher.sources.cohesity import CohesitySource, flatten_job_data
from job_fetcher.sources.factory import build_source
from job_fetcher.sources.servicenow import ServiceNowSource
import job_fetcher.sources.servicenow as servicenow_module


def _company(company_id):
    return {
        "id": company_id,
        "name": company_id,
        "career_url": "https://example.com/careers",
        "source": {"type": "auto", "entry_url": "https://example.com/careers"},
    }


def test_factory_uses_exact_coverage_sources():
    assert isinstance(build_source(_company("servicenow")), ServiceNowSource)
    assert isinstance(build_source(_company("cohesity")), CohesitySource)


def test_cohesity_flattens_every_department_record():
    payload = {
        "job_data": {
            "Engineering": [{"req_id": "R1", "title": "Engineer"}],
            "Sales": [{"req_id": "R2", "title": "Sales Executive"}],
        }
    }
    rows = flatten_job_data(payload)
    assert {row["req_id"] for row in rows} == {"R1", "R2"}
    assert {row["careerSiteDept"] for row in rows} == {"Engineering", "Sales"}


def test_servicenow_supplements_provider_when_official_site_has_missing_job(monkeypatch):
    company = _company("servicenow")
    provider_job = Job(
        "servicenow", "ServiceNow", "smartrecruiters", "744000000000001",
        "Software Engineer", "Bangalore", None,
        "https://careers.servicenow.com/jobs/744000000000001/software-engineer/",
    )
    website = {
        "744000000000001": {
            "id": "744000000000001", "title": "Software Engineer",
            "url": "https://careers.servicenow.com/jobs/744000000000001/software-engineer/",
        },
        "744000000000002": {
            "id": "744000000000002", "title": "Product Manager",
            "url": "https://careers.servicenow.com/jobs/744000000000002/product-manager/",
        },
    }
    supplement = Job(
        "servicenow", "ServiceNow", "servicenow_official", "744000000000002",
        "Product Manager", "Santa Clara", "description",
        website["744000000000002"]["url"],
    )

    monkeypatch.setattr(servicenow_module.SmartRecruitersSource, "fetch", lambda self, c: [provider_job])
    monkeypatch.setattr(ServiceNowSource, "_enumerate_official_site", lambda self: (website, 2))
    monkeypatch.setattr(ServiceNowSource, "_official_total", lambda self: 2)
    monkeypatch.setattr(ServiceNowSource, "_fetch_official_detail", lambda self, c, r: supplement)

    jobs = ServiceNowSource().fetch(company)
    ids = {job.external_id for job in jobs}
    assert ids == {"744000000000001", "744000000000002"}


def test_servicenow_fails_closed_when_official_site_enumeration_is_partial(monkeypatch):
    company = _company("servicenow")
    monkeypatch.setattr(servicenow_module.SmartRecruitersSource, "fetch", lambda self, c: [])
    monkeypatch.setattr(
        ServiceNowSource,
        "_enumerate_official_site",
        lambda self: ({"744000000000001": {"id": "744000000000001", "title": "Engineer", "url": "https://x/jobs/744000000000001/x/"}}, 2),
    )

    import pytest
    with pytest.raises(RuntimeError, match="servicenow_website_incomplete_pagination"):
        ServiceNowSource().fetch(company)
