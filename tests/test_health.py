import json
from pathlib import Path

from job_fetcher.health import verify_all
from job_fetcher.models import Job


def company(cid="acme", enabled=True):
    return {
        "id": cid, "rank": 1, "name": cid.title(), "enabled": enabled,
        "career_url": f"https://{cid}.example/jobs",
        "source": {"type": "auto", "entry_url": f"https://{cid}.example/jobs"},
    }


class DummySource:
    def __init__(self, jobs=None, exc=None):
        self.jobs = jobs or []
        self.exc = exc
    def fetch(self, c):
        if self.exc:
            raise self.exc
        return self.jobs


def test_verify_all_zero_jobs_is_failure(monkeypatch, tmp_path):
    monkeypatch.setattr("job_fetcher.health.build_source", lambda c: DummySource([]))
    result = verify_all([company()], output_dir=tmp_path, validate_detail=False)
    row = result["companies"][0]
    assert row["status"] == "failed"
    assert row["failure_category"] == "zero_jobs_detected"
    assert (tmp_path / "company_health.json").exists()
    assert (tmp_path / "company_health.csv").exists()


def test_verify_all_detects_browser_fallback(monkeypatch, tmp_path):
    j = Job("acme", "Acme", "auto", "1", "Backend Engineer", "India", None,
            "https://acme.example/jobs/1", raw={"_fetch_via_browser": True})
    monkeypatch.setattr("job_fetcher.health.build_source", lambda c: DummySource([j]))
    result = verify_all([company()], output_dir=tmp_path, validate_detail=False)
    row = result["companies"][0]
    assert row["status"] == "healthy_with_fallback"
    assert row["browser_used"] is True


def test_verify_all_detects_large_drop_from_previous_run(monkeypatch, tmp_path):
    baseline = {
        "companies": [{"id": "acme", "jobs_found": 100}]
    }
    (tmp_path / "company_health.json").write_text(json.dumps(baseline))
    jobs = [Job("acme", "Acme", "auto", str(i), f"Engineer {i}", "India", None,
                f"https://acme.example/jobs/{i}") for i in range(10)]
    monkeypatch.setattr("job_fetcher.health.build_source", lambda c: DummySource(jobs))
    result = verify_all([company()], output_dir=tmp_path, validate_detail=False, drop_threshold=0.80)
    row = result["companies"][0]
    assert row["status"] == "suspicious"
    assert row["failure_category"] == "large_job_count_drop"
    assert row["previous_jobs_found"] == 100
    assert list((tmp_path / "history").glob("company_health_*.json"))


def test_verify_all_skips_disabled(monkeypatch, tmp_path):
    monkeypatch.setattr("job_fetcher.health.build_source", lambda c: DummySource([]))
    result = verify_all([company("on", True), company("off", False)], output_dir=tmp_path, validate_detail=False)
    assert result["summary"]["configured"] == 2
    assert result["summary"]["enabled"] == 1
    assert result["summary"]["disabled"] == 1
    assert [r["id"] for r in result["companies"]] == ["on"]


def test_verify_all_classifies_fetch_exception(monkeypatch, tmp_path):
    import requests
    monkeypatch.setattr("job_fetcher.health.build_source", lambda c: DummySource(exc=requests.Timeout("boom")))
    result = verify_all([company()], output_dir=tmp_path, validate_detail=False)
    row = result["companies"][0]
    assert row["status"] == "failed"
    assert row["failure_category"] == "network_timeout"

def test_environment_blocked_run_does_not_erase_good_baseline(monkeypatch, tmp_path):
    baseline = {"companies": [{"id": "acme", "jobs_found": 80}]}
    (tmp_path / "company_health_baseline.json").write_text(json.dumps(baseline))

    import requests
    exc = requests.ConnectionError("NameResolutionError: failed to resolve")
    monkeypatch.setattr("job_fetcher.health.build_source", lambda c: DummySource(exc=exc))
    result = verify_all([company()], output_dir=tmp_path, validate_detail=False)
    assert result["summary"]["environment_blocked"] is True

    persisted = json.loads((tmp_path / "company_health_baseline.json").read_text())
    assert persisted["companies"] == [{"id": "acme", "jobs_found": 80}]

def test_verify_all_allows_explicit_known_empty_board(monkeypatch, tmp_path):
    c = company()
    c["source"]["allow_zero_jobs"] = True
    monkeypatch.setattr("job_fetcher.health.build_source", lambda c: DummySource([]))
    result = verify_all([c], output_dir=tmp_path, validate_detail=False)
    row = result["companies"][0]
    assert row["status"] == "healthy"
    assert row["jobs_found"] == 0
    assert row["failure_category"] is None
