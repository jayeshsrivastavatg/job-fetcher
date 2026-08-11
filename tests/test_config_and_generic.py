from pathlib import Path

from job_fetcher.config import load_config, save_config, slugify, validate_config
from job_fetcher.sources.auto import AutoSource
from job_fetcher.sources.generic_extract import extract_embedded_json, extract_html_links

COMPANY = {"id": "acme", "name": "Acme"}


def test_slugify_and_atomic_config_roundtrip(tmp_path: Path):
    path = tmp_path / "companies.yaml"
    data = {"companies": [{
        "id": "new_co", "rank": 1, "name": "New Co", "enabled": False,
        "career_url": "https://example.com/jobs",
        "source": {"type": "auto", "entry_url": "https://example.com/jobs"},
    }]}
    save_config(data, path)
    assert load_config(path) == data
    assert slugify("New Co! India") == "new_co_india"
    assert validate_config(data) == []


def test_validate_rejects_duplicate_rank_and_bad_source():
    data = {"companies": [
        {"id":"a","rank":1,"name":"A","career_url":"https://a.test/jobs","source":{"type":"auto"}},
        {"id":"b","rank":1,"name":"B","career_url":"https://b.test/jobs","source":{"type":"mystery"}},
    ]}
    errors = validate_config(data)
    assert any("duplicate rank" in e for e in errors)
    assert any("unsupported source" in e for e in errors)


def test_apply_now_card_infers_heading_as_title():
    html = '''
    <div class="job-card">
      <h3>Software Development Engineer 2</h3>
      <div>Gurgaon, India</div>
      <a href="/jobs/123">Apply Now</a>
    </div>
    '''
    jobs = extract_html_links(COMPANY, html, "https://example.com/careers")
    assert len(jobs) == 1
    assert jobs[0].title == "Software Development Engineer 2"
    assert jobs[0].job_url == "https://example.com/jobs/123"


def test_embedded_next_data_json():
    html = '''<script id="__NEXT_DATA__" type="application/json">{
      "props":{"jobs":[{"jobId":"42","jobTitle":"Backend Engineer","location":"India","url":"/jobs/42"}]}
    }</script>'''
    jobs = extract_embedded_json(COMPANY, html, "https://example.com")
    assert len(jobs) == 1
    assert jobs[0].external_id == "42"


def test_hidden_ats_url_discovery():
    html = '<script>window.cfg={"board":"https://jobs.lever.co/acme"}</script>'
    assert AutoSource._find_ats_links(html, "https://acme.test/careers") == ["https://jobs.lever.co/acme"]


def test_next_page_detection():
    html = '<a href="/jobs/25/?q=">Next</a>'
    assert AutoSource._find_next_page(html, "https://example.com/jobs/") == "https://example.com/jobs/25/?q="


def test_fetch_companies_skips_disabled(monkeypatch):
    from job_fetcher import service

    called = []

    def fake_fetch(c):
        called.append(c["id"])
        return c, [], None

    class DummyStore:
        def upsert_many(self, jobs):
            return 0, 0

    monkeypatch.setattr(service, "_fetch_one", fake_fetch)
    monkeypatch.setattr(service, "JobStore", DummyStore)
    rows = [
        {"id": "enabled", "name": "Enabled", "enabled": True, "rank": 1},
        {"id": "disabled", "name": "Disabled", "enabled": False, "rank": 2},
    ]
    result = service.fetch_companies(rows, max_workers=2)
    assert called == ["enabled"]
    assert [r["id"] for r in result["success"]] == ["enabled"]
