from __future__ import annotations

from pathlib import Path

from job_fetcher.models import Job


def _company(source_type="greenhouse", enabled=True):
    source = {"type": source_type}
    if source_type == "greenhouse":
        source["board_token"] = "acme"
    return {
        "id": "acme",
        "rank": 1,
        "name": "Acme",
        "enabled": enabled,
        "career_url": "https://acme.example/jobs",
        "source": source,
        "research": {"provider_hint": source_type},
    }


def _jobs(n=2):
    return [
        Job(
            "acme", "Acme", "greenhouse", str(i),
            f"Software Engineer {i}", "Bengaluru, India", "Backend Java role",
            f"https://acme.example/jobs/{i}", "2026-08-01", {},
        )
        for i in range(1, n + 1)
    ]


def test_certifies_when_authoritative_count_matches(monkeypatch):
    import job_fetcher.certification as module

    class Source:
        def fetch(self, company):
            return _jobs(2)

    monkeypatch.setattr(module, "build_source", lambda company: Source())
    monkeypatch.setattr(
        module, "_provider_expected_count",
        lambda company, source_types: {
            "provider": "greenhouse", "expected_count": 2, "status": "verified",
            "evidence": "provider total", "duration_seconds": 0.01,
        },
    )
    monkeypatch.setattr(
        module, "_check_details",
        lambda jobs, sample_size, timeout: [
            {"title": jobs[0].title, "url": jobs[0].job_url, "status": "verified", "http_status": 200, "evidence": "title"}
        ],
    )

    result = module.audit_company(_company(), sample_size=1)
    assert result["verdict"] == "CERTIFIED"
    assert result["jobs_found"] == 2
    assert result["expected_count"] == 2
    assert result["completeness_pct"] == 100.0


def test_marks_partial_when_provider_count_is_larger(monkeypatch):
    import job_fetcher.certification as module

    class Source:
        def fetch(self, company):
            return _jobs(2)

    monkeypatch.setattr(module, "build_source", lambda company: Source())
    monkeypatch.setattr(
        module, "_provider_expected_count",
        lambda company, source_types: {
            "provider": "greenhouse", "expected_count": 5, "status": "verified",
            "evidence": "provider total", "duration_seconds": 0.01,
        },
    )
    monkeypatch.setattr(module, "_check_details", lambda jobs, sample_size, timeout: [])

    result = module.audit_company(_company())
    assert result["verdict"] == "PARTIAL"
    assert result["failure_category"] == "incomplete_fetch"
    assert result["completeness_pct"] == 40.0


def test_navigation_only_records_are_invalid(monkeypatch):
    import job_fetcher.certification as module

    fake = Job(
        "acme", "Acme", "generic_html", "x", "Products", None, None,
        "https://acme.example/careers/products", None, {},
    )

    class Source:
        def fetch(self, company):
            return [fake]

    monkeypatch.setattr(module, "build_source", lambda company: Source())
    monkeypatch.setattr(
        module, "_provider_expected_count",
        lambda company, source_types: {
            "provider": "auto", "expected_count": None, "status": "unavailable",
            "evidence": "no probe", "duration_seconds": 0.01,
        },
    )
    monkeypatch.setattr(module, "_check_details", lambda jobs, sample_size, timeout: [])

    result = module.audit_company(_company("auto"))
    assert result["verdict"] == "INVALID"
    assert result["raw_records"] == 1
    assert result["jobs_found"] == 0
    assert result["rejected_non_job_records"] == 1


def test_unknown_completeness_is_never_called_certified(monkeypatch):
    import job_fetcher.certification as module

    class Source:
        def fetch(self, company):
            return _jobs(2)

    monkeypatch.setattr(module, "build_source", lambda company: Source())
    monkeypatch.setattr(
        module, "_provider_expected_count",
        lambda company, source_types: {
            "provider": "auto", "expected_count": None, "status": "unavailable",
            "evidence": "no independent count", "duration_seconds": 0.01,
        },
    )
    monkeypatch.setattr(module, "_check_details", lambda jobs, sample_size, timeout: [])

    result = module.audit_company(_company("auto"))
    assert result["verdict"] == "UNVERIFIED"
    assert result["failure_category"] == "completeness_unverified"


def test_disabled_manual_company_is_accounted_for_without_fetch(monkeypatch):
    import job_fetcher.certification as module

    company = _company("manual", enabled=False)
    company["source"]["reason"] = "automation prohibited"
    monkeypatch.setattr(module, "build_source", lambda company: (_ for _ in ()).throw(AssertionError("must not fetch")))

    result = module.audit_company(company)
    assert result["verdict"] == "BLOCKED"
    assert "prohibited" in result["error"]


def test_merge_reports_preserves_all_companies(tmp_path: Path):
    import json
    import job_fetcher.certification as module

    one = tmp_path / "shard-0"
    two = tmp_path / "shard-1"
    one.mkdir(); two.mkdir()
    (one / "company_certification.json").write_text(json.dumps({"companies": [{"id": "a", "rank": 1, "name": "A", "enabled": True, "verdict": "CERTIFIED", "jobs_found": 2}]}))
    (two / "company_certification.json").write_text(json.dumps({"companies": [{"id": "b", "rank": 2, "name": "B", "enabled": True, "verdict": "FAILED", "jobs_found": 0}]}))

    output = tmp_path / "merged"
    payload = module.merge_reports(tmp_path, output)
    assert [row["id"] for row in payload["companies"]] == ["a", "b"]
    assert payload["summary"]["companies"] == 2
    assert (output / "company_certification.csv").exists()
    assert (output / "company_certification.md").exists()
