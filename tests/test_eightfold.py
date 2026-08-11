import yaml
from pathlib import Path

from job_fetcher.sources.auto import AutoSource
from job_fetcher.sources.eightfold import EightfoldSource
from job_fetcher.models import Job


COMPANY = {
    "id": "twilio",
    "name": "Twilio",
    "career_url": "https://jobs.twilio.com/careers?domain=twilio.com&hl=en",
    "source": {
        "type": "eightfold",
        "entry_url": "https://jobs.twilio.com/careers?domain=twilio.com&hl=en",
        "provider_url": "https://twilio.eightfold.ai/careers?domain=twilio.com&hl=en",
        "tenant": "twilio",
        "canonical_base_url": "https://jobs.twilio.com",
    },
}


def test_parse_eightfold_provider_url():
    assert EightfoldSource.parse_eightfold_url("https://twilio.eightfold.ai/careers") == {
        "host": "twilio.eightfold.ai",
        "tenant": "twilio",
    }
    assert EightfoldSource.parse_eightfold_url("https://jobs.twilio.com/careers") is None


def test_provider_fallback_url_preserves_domain_query():
    url = EightfoldSource._provider_url(
        {"tenant": "twilio"},
        "https://jobs.twilio.com/careers?domain=twilio.com&hl=en",
    )
    assert url in {
        "https://twilio.eightfold.ai/careers?domain=twilio.com&hl=en",
        "https://twilio.eightfold.ai/careers?hl=en&domain=twilio.com",
    }


def test_total_job_count_extraction():
    html = "<html><body><div>173 jobs</div><div>1 job selected</div></body></html>"
    assert EightfoldSource.extract_total_jobs(html) == 173


def test_eightfold_position_payload_normalization():
    payload = {
        "positions": [{
            "positionId": "1099553745549",
            "positionDisplayId": "REQ-123",
            "name": "Tech Lead (L6)",
            "location": "Remote - India",
            "jobDescription": "Lead applied research engineering.",
            "datePosted": "2026-06-16",
        }]
    }
    jobs = EightfoldSource().extract_eightfold_payload(COMPANY, payload, COMPANY["career_url"])
    assert len(jobs) == 1
    job = jobs[0]
    assert job.source_type == "eightfold"
    assert job.external_id == "1099553745549"
    assert job.title == "Tech Lead (L6)"
    assert job.location == "Remote - India"
    assert job.job_url == "https://jobs.twilio.com/careers/job/1099553745549"
    assert job.posted_at == "2026-06-16"


def test_provider_url_is_canonicalized_to_twilio_domain():
    job = Job(
        company_id="twilio",
        company_name="Twilio",
        source_type="eightfold_browser_json",
        external_id="1099553745549",
        title="Tech Lead (L6)",
        location="Remote - India",
        description=None,
        job_url="https://twilio.eightfold.ai/careers/job/1099553745549-tech-lead-l6",
    )
    result = EightfoldSource()._canonicalize(COMPANY, [job])
    assert result[0].source_type == "eightfold"
    assert result[0].job_url == "https://jobs.twilio.com/careers/job/1099553745549"


def test_fetch_avoids_browser_when_static_page_is_complete(monkeypatch):
    static_jobs = [
        Job("twilio", "Twilio", "eightfold_html", "1", "Principal Engineer", "Remote - India", None,
            "https://jobs.twilio.com/careers/job/1"),
        Job("twilio", "Twilio", "eightfold_html", "2", "Staff Software Engineer", "Remote - India", None,
            "https://jobs.twilio.com/careers/job/2"),
    ]
    calls = []
    monkeypatch.setattr(EightfoldSource, "_fetch_static", lambda self, company, url: (static_jobs, 2))

    def fail_browser(self, company, url, expected_total=None):
        calls.append(url)
        raise AssertionError("browser should not run")

    monkeypatch.setattr(EightfoldSource, "_fetch_browser", fail_browser)
    result = EightfoldSource().fetch(COMPANY)
    assert len(result) == 2
    assert calls == []
    assert all(j.source_type == "eightfold" for j in result)


def test_fetch_uses_browser_when_static_list_is_partial(monkeypatch):
    monkeypatch.delenv("JOB_FETCHER_DISABLE_BROWSER", raising=False)
    static = [Job("twilio", "Twilio", "eightfold_html", "1", "Principal Engineer", "Remote - India", None,
                  "https://jobs.twilio.com/careers/job/1")]
    browser = [
        static[0],
        Job("twilio", "Twilio", "eightfold_browser_json", "2", "Staff Software Engineer", "Remote - India", None,
            "https://jobs.twilio.com/careers/job/2"),
    ]
    monkeypatch.setattr(EightfoldSource, "_fetch_static", lambda self, company, url: (static, 2))
    monkeypatch.setattr(EightfoldSource, "_fetch_browser", lambda self, company, url, expected_total=None: browser)
    result = EightfoldSource().fetch(COMPANY)
    assert len(result) == 2
    assert {j.external_id for j in result} == {"1", "2"}


def test_auto_source_delegates_eightfold_provider(monkeypatch):
    sentinel = [object()]
    monkeypatch.setattr(EightfoldSource, "fetch", lambda self, company: sentinel)
    result = AutoSource()._delegate(
        {"id": "acme", "name": "Acme", "career_url": "https://acme.test/careers", "source": {"type": "auto"}},
        "https://acme.eightfold.ai/careers",
    )
    assert result is sentinel


def test_twilio_config_is_explicit_eightfold():
    root = Path(__file__).resolve().parents[1]
    data = yaml.safe_load((root / "config" / "companies.yaml").read_text(encoding="utf-8"))
    twilio = next(c for c in data["companies"] if c["id"] == "twilio")
    assert twilio["enabled"] is True
    assert twilio["source"]["type"] == "eightfold"
    assert twilio["source"]["tenant"] == "twilio"
    assert twilio["source"]["canonical_base_url"] == "https://jobs.twilio.com"
