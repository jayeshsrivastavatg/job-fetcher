from pathlib import Path

import yaml

from job_fetcher.models import Job
from job_fetcher.sources.auto import AutoSource
from job_fetcher.sources.avature import AvatureSource
from job_fetcher.sources.factory import build_source
from job_fetcher.sources.lever import LeverSource
from job_fetcher.sources.oracle import OracleSource


def _config():
    root = Path(__file__).resolve().parents[1]
    return yaml.safe_load((root / "config" / "companies.yaml").read_text(encoding="utf-8"))


def test_oracle_public_search_parser_extracts_job_and_india_location():
    company = {"id": "oracle_oci", "name": "Oracle Cloud (OCI)"}
    html = '''
    <article class="job-card">
      <h3><a href="/en/sites/jobsearch/job/336251/">Software Developer 3</a></h3>
      <div>Locations BENGALURU, KARNATAKA, India Trending OCI Security and Compliance</div>
    </article>
    '''
    jobs = OracleSource.parse_public_search_page(
        company, html, "https://careers.oracle.com/en/sites/jobsearch/jobs"
    )
    assert len(jobs) == 1
    assert jobs[0].source_type == "oracle"
    assert jobs[0].external_id == "336251"
    assert jobs[0].title == "Software Developer 3"
    assert jobs[0].location == "BENGALURU, KARNATAKA, India"
    assert jobs[0].job_url == "https://careers.oracle.com/en/sites/jobsearch/job/336251/"


def test_oracle_public_search_next_page():
    html = '<a aria-label="Next page" href="/en/sites/jobsearch/jobs?page=2">›</a>'
    assert OracleSource._find_public_next(
        html, "https://careers.oracle.com/en/sites/jobsearch/jobs?page=1"
    ).endswith("page=2")


def test_avature_job_id_supports_ibm_path_and_query_forms():
    assert AvatureSource.job_id_from_url(
        "https://careers.ibm.com/en_US/careers/JobDetail/Senior-Software-Engineer/124640"
    ) == "124640"
    assert AvatureSource.job_id_from_url(
        "https://careers.ibm.com/en_IN/careers/JobDetail?jobId=124618&source=WEB_Search_INDIA"
    ) == "124618"


def test_avature_page_parser_extracts_ibm_job():
    company = {"id": "ibm_software_labs", "name": "IBM Software Labs"}
    html = '''
    <div class="job-card">
      <h3><a href="https://careers.ibm.com/en_US/careers/JobDetail/Senior-Software-Engineer/124640">Senior Software Engineer</a></h3>
      <div>Bangalore, Karnataka, India Professional</div>
    </div>
    '''
    jobs = AvatureSource.parse_page(company, html, "https://www.ibm.com/in-en/careers/search")
    assert len(jobs) == 1
    assert jobs[0].source_type == "avature"
    assert jobs[0].external_id == "124640"
    assert jobs[0].title == "Senior Software Engineer"
    assert jobs[0].location == "Bangalore, Karnataka, India"


def test_avature_browser_payload_can_synthesize_canonical_ibm_url():
    company = {"id": "ibm_software_labs", "name": "IBM Software Labs"}
    raw = Job(
        company_id=company["id"], company_name=company["name"], source_type="browser_json",
        external_id="126243", title="Software Engineer - Confluent", location="Bangalore, India",
        description=None, job_url=None, posted_at=None, raw={"jobId": "126243"},
    )
    result = AvatureSource.normalize_browser_jobs(
        company, [raw], {"canonical_base_url": "https://careers.ibm.com", "locale": "en_US"}
    )
    assert len(result) == 1
    assert result[0].source_type == "avature"
    assert result[0].job_url == "https://careers.ibm.com/en_US/careers/JobDetail?jobId=126243"


def test_zerodha_zero_openings_is_healthy_empty_state():
    html = "<html><body><p>There are no job openings currently. Check back later.</p></body></html>"
    assert AutoSource._is_empty(html) is True


def test_step5_company_routing():
    data = _config()
    by = {c["id"]: c for c in data["companies"]}

    assert by["oracle_oci"]["source"]["type"] == "oracle"
    assert by["oracle_oci"]["source"]["mode"] == "public_search"
    assert "AttributeChar15%7COCI" in by["oracle_oci"]["source"]["entry_url"]
    assert isinstance(build_source(by["oracle_oci"]), OracleSource)

    assert by["cred"]["source"] == {"type": "lever", "site": "cred"}
    assert isinstance(build_source(by["cred"]), LeverSource)

    assert by["ibm_software_labs"]["source"]["type"] == "avature"
    assert isinstance(build_source(by["ibm_software_labs"]), AvatureSource)

    assert by["zerodha"]["source"]["type"] == "auto"
    assert by["zerodha"]["enabled"] is True


def test_oracle_public_fetch_uses_static_listing_without_browser(monkeypatch):
    company = {
        "id": "oracle_oci", "name": "Oracle Cloud (OCI)",
        "career_url": "https://www.oracle.com/in/careers/opportunities/oracle-cloud-infrastructure/",
        "source": {
            "type": "oracle", "mode": "public_search",
            "entry_url": "https://careers.oracle.com/en/sites/jobsearch/jobs?selectedFlexFieldsFacets=OCI",
            "host": "careers.oracle.com", "site_number": "jobsearch", "locale": "en",
        },
    }
    html = '<a href="/en/sites/jobsearch/job/336251/">Software Developer 3</a><div>BENGALURU, KARNATAKA, India</div>'

    class R:
        url = company["source"]["entry_url"]
        text = html
        def raise_for_status(self): pass
    class C:
        def get(self, *args, **kwargs): return R()

    monkeypatch.setattr("job_fetcher.sources.oracle.session", lambda: C())
    monkeypatch.setattr(
        "job_fetcher.sources.oracle.PlaywrightAutoSource.fetch",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("browser should not run")),
    )
    jobs = OracleSource().fetch(company)
    assert len(jobs) == 1 and jobs[0].external_id == "336251"


def test_avature_fetch_uses_static_jobdetail_without_browser(monkeypatch):
    company = {
        "id": "ibm_software_labs", "name": "IBM Software Labs",
        "career_url": "https://www.ibm.com/in-en/careers/search",
        "source": {
            "type": "avature", "entry_url": "https://www.ibm.com/in-en/careers/search",
            "canonical_base_url": "https://careers.ibm.com", "locale": "en_US",
        },
    }
    html = '<a href="https://careers.ibm.com/en_US/careers/JobDetail/Software-Engineer/126243">Software Engineer</a>'

    class R:
        url = company["source"]["entry_url"]
        text = html
        def raise_for_status(self): pass
    class C:
        def get(self, *args, **kwargs): return R()

    monkeypatch.setattr("job_fetcher.sources.avature.session", lambda: C())
    monkeypatch.setattr(
        "job_fetcher.sources.avature.PlaywrightAutoSource.fetch",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("browser should not run")),
    )
    jobs = AvatureSource().fetch(company)
    assert len(jobs) == 1 and jobs[0].external_id == "126243"


def test_zerodha_fetch_returns_zero_without_browser_when_official_page_is_empty(monkeypatch):
    company = {
        "id": "zerodha", "name": "Zerodha", "career_url": "https://careers.zerodha.com/",
        "source": {"type": "auto", "entry_url": "https://careers.zerodha.com/"},
    }

    class R:
        url = "https://careers.zerodha.com/"
        text = "<html><body>There are no job openings currently. Check back later.</body></html>"
        def raise_for_status(self): pass
    class C:
        def get(self, *args, **kwargs): return R()

    monkeypatch.setattr("job_fetcher.sources.auto.session", lambda: C())
    monkeypatch.setattr(
        "job_fetcher.sources.auto.PlaywrightAutoSource.fetch",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("browser should not run")),
    )
    assert AutoSource().fetch(company) == []
