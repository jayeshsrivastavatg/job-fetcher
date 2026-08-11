from pathlib import Path

import yaml

from job_fetcher.models import Job
from job_fetcher.sources.atlassian import AtlassianSource
from job_fetcher.sources.auto import AutoSource
from job_fetcher.sources.factory import build_source
from job_fetcher.sources.generic_extract import extract_html_links
from job_fetcher.sources.goldman import GoldmanSource
from job_fetcher.sources.manual import ManualSource
from job_fetcher.sources.phenom import PhenomSource
from job_fetcher.sources.trakstar import TrakstarSource


def _config():
    root = Path(__file__).resolve().parents[1]
    return yaml.safe_load((root / "config" / "companies.yaml").read_text(encoding="utf-8"))


def test_atlassian_parser_keeps_only_stable_detail_roles():
    company = {"id": "atlassian", "name": "Atlassian"}
    html = '''
    <div class="job-card">
      <h3>Senior Software Engineer</h3>
      <span>Engineering | Bengaluru, India | Remote, India</span>
      <a href="/company/careers/details/25693">View role</a>
    </div>
    <a href="/company/careers/all-jobs">Browse jobs</a>
    '''
    jobs = AtlassianSource.parse_listing(company, html, "https://www.atlassian.com/company/careers/all-jobs")
    assert len(jobs) == 1
    assert jobs[0].external_id == "25693"
    assert jobs[0].title == "Senior Software Engineer"
    assert jobs[0].source_type == "atlassian"


def test_goldman_parser_extracts_higher_role():
    company = {"id": "goldman_sachs", "name": "Goldman Sachs (Tech)"}
    html = '''
    <article>
      <h2>Compliance Engineering - Associate - Bengaluru</h2>
      <div>Bengaluru, Karnataka, India</div>
      <a href="/roles/178636">View job</a>
    </article>
    '''
    jobs = GoldmanSource.parse_listing(company, html, "https://higher.gs.com/results")
    assert len(jobs) == 1
    assert jobs[0].external_id == "178636"
    assert jobs[0].job_url == "https://higher.gs.com/roles/178636"
    assert jobs[0].source_type == "goldman"


def test_trakstar_parser_extracts_dream11_job():
    company = {"id": "dream11", "name": "Dream11"}
    html = '''
    <div class="job">
      <a href="/jobs/fk0hol7/">Node JS Developer / Lead</a>
      <span>Mumbai, Maharashtra, India Full-time</span>
    </div>
    '''
    jobs = TrakstarSource.parse_listing(company, html, "https://dream11.hire.trakstar.com/")
    assert len(jobs) == 1
    assert jobs[0].external_id == "fk0hol7"
    assert jobs[0].title == "Node JS Developer / Lead"
    assert jobs[0].source_type == "trakstar"


def test_phenom_normalizes_snowflake_detail_url():
    company = {"id": "snowflake", "name": "Snowflake", "career_url": "https://careers.snowflake.com"}
    raw = Job(
        company_id="snowflake", company_name="Snowflake", source_type="browser_html",
        external_id="SNCOUSABC123", title="Software Engineer", location="Bengaluru, India",
        description=None,
        job_url="https://careers.snowflake.com/us/en/job/SNCOUSABC123/Software-Engineer",
        posted_at=None, raw={},
    )
    jobs = PhenomSource._normalize(company, [raw], {"canonical_base_url": "https://careers.snowflake.com"})
    assert len(jobs) == 1
    assert jobs[0].source_type == "phenom"
    assert jobs[0].external_id == "SNCOUSABC123"


def test_urban_jobdetail_links_are_generic_job_links():
    company = {"id": "urban_company", "name": "Urban Company"}
    html = '<a href="/jobDetail?id=9af0656f-fad3-421a-802e-caf1591f8a4f">Software Engineer II</a>'
    jobs = extract_html_links(company, html, "https://careers.urbancompany.com/jobs")
    assert len(jobs) == 1
    assert "jobDetail?id=" in jobs[0].job_url


def test_auto_403_escalates_to_browser(monkeypatch):
    monkeypatch.delenv("JOB_FETCHER_DISABLE_BROWSER", raising=False)
    company = {
        "id": "rippling", "name": "Rippling",
        "career_url": "https://www.rippling.com/careers/open-roles",
        "source": {"type": "auto", "entry_url": "https://www.rippling.com/careers/open-roles"},
    }

    class R:
        status_code = 403
        url = company["career_url"]
        text = "Access denied"

    class C:
        def get(self, *args, **kwargs):
            return R()

    expected = [Job("rippling", "Rippling", "browser_html", "1", "Software Engineer II", "Bangalore, India", None, "https://example/jobs/1")]
    monkeypatch.setattr("job_fetcher.sources.auto.session", lambda: C())
    monkeypatch.setattr("job_fetcher.sources.auto.PlaywrightAutoSource.fetch", lambda self, c: expected)
    assert AutoSource().fetch(company) == expected


def test_step7_company_routing_and_policy_state():
    data = _config()
    by = {c["id"]: c for c in data["companies"]}

    assert isinstance(build_source(by["atlassian"]), AtlassianSource)
    assert isinstance(build_source(by["snowflake"]), PhenomSource)
    assert isinstance(build_source(by["goldman_sachs"]), GoldmanSource)
    assert isinstance(build_source(by["dream11"]), TrakstarSource)

    assert by["linkedin"]["enabled"] is False
    assert isinstance(build_source(by["linkedin"]), ManualSource)

    assert by["rippling"]["source"]["type"] == "auto"
    assert "open-roles" in by["rippling"]["source"]["entry_url"]
    assert "list.html" in by["swiggy"]["source"]["entry_url"]
    assert by["urban_company"]["source"]["browser_max_pages"] >= 10


def test_probe_surfaces_public_xhr_endpoint(monkeypatch):
    from job_fetcher.service import probe_company
    company = {
        "id": "swiggy", "name": "Swiggy", "career_url": "https://careers.swiggy.com/",
        "source": {"type": "auto", "entry_url": "https://careers.swiggy.com/list.html"},
    }
    job = Job(
        "swiggy", "Swiggy", "browser_json", "23631", "Software Development Engineer II",
        "Bengaluru, India", None, "https://careers.swiggy.com/job/23631", None,
        {"_source_response_url": "https://careers.swiggy.com/api/jobs?page=1"},
    )
    monkeypatch.setattr("job_fetcher.service._fetch_one", lambda c: (c, [job], None))
    result = probe_company(company)
    assert result["discovered_endpoints"] == ["https://careers.swiggy.com/api/jobs?page=1"]
