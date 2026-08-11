from __future__ import annotations

import json

from fastapi.testclient import TestClient


def _temp_runtime(monkeypatch, tmp_path):
    import job_fetcher.storage as storage
    import job_fetcher.run_history as run_history
    import job_fetcher.run_manager as run_manager

    monkeypatch.setattr(storage, "DB", tmp_path / "jobs.db")
    monkeypatch.setattr(run_history, "RUN_REPORTS_ROOT", tmp_path / "reports" / "runs")
    monkeypatch.setattr(run_history, "LATEST_REPORTS_ROOT", tmp_path / "reports" / "latest")
    monkeypatch.setattr(run_history, "GIT_HISTORY_ROOT", tmp_path / "run-history")
    run_manager._MANAGER = None
    return storage, run_history


def _job(external_id: str, title: str, description: str):
    from job_fetcher.models import Job

    return Job(
        "acme", "Acme", "test", external_id, title, "Bengaluru, India",
        description, f"https://jobs.example/{external_id}", "2026-08-10",
    )


def _build_finalized_run(monkeypatch, tmp_path):
    storage, run_history = _temp_runtime(monkeypatch, tmp_path)
    from job_fetcher.relevance_service import analyze_relevance

    relevant_v1 = "Requirements\n3+ years\nJava Spring Boot backend REST API SQL AWS"
    relevant_v2 = "Requirements\n3+ years\nJava Spring Boot backend microservices REST API SQL AWS Docker"

    jobs = storage.JobStore()
    try:
        jobs.upsert_snapshot("acme", [
            _job("a", "Java Backend Engineer", relevant_v1),
            _job("c", "Java Software Engineer", relevant_v1),
        ], complete=True)
    finally:
        jobs.close()

    runs = storage.RunStore()
    run_id = runs.create_run("fetch", total_companies=1, scope="all", settings={}, targets=["acme"])
    runs.mark_running(run_id)
    history = run_history.RunHistoryStore()
    before = history.capture_inventory(["acme"])

    current = [
        _job("a", "Java Backend Engineer", relevant_v2),
        _job("b", "Software Development Engineer II", relevant_v1),
    ]
    jobs = storage.JobStore()
    try:
        new, existing, deactivated = jobs.upsert_snapshot("acme", current, complete=True)
    finally:
        jobs.close()
    # The legacy JobStore `deactivated` value is a net active-count delta, so one
    # closure plus one new job nets to zero. RunHistory derives CLOSED exactly
    # from before-vs-returned membership instead of relying on that metric.
    assert (new, existing, deactivated) == (1, 1, 0)

    history.record_company_snapshot(
        run_id,
        before["acme"],
        {"id": "acme", "snapshot_complete": True},
        current,
    )
    analyze_relevance(recompute_all=True)
    summary = history.finalize_run(run_id)
    runs.finish(run_id)
    return storage, run_history, history, run_id, summary


def test_run_history_freezes_new_changed_closed_and_ai_file(monkeypatch, tmp_path):
    storage, _, history, run_id, summary = _build_finalized_run(monkeypatch, tmp_path)

    assert summary["snapshot_jobs"] == 2
    assert summary["jobs_new"] == 1
    assert summary["jobs_changed"] == 1
    assert summary["jobs_closed"] == 1
    assert summary["new_relevant"] == 1
    assert summary["changed_relevant"] == 1
    assert summary["ai_input_count"] == 2

    artifact = history.get_artifact(run_id, "ai_input")
    payload = json.loads(artifact["content_text"])
    assert {j["external_id"] for j in payload["jobs"]} == {"a", "b"}
    assert {j["event_type"] for j in payload["jobs"]} == {"new", "changed"}
    assert all("India" in j["normalized_location"] for j in payload["jobs"])
    original_text = artifact["content_text"]

    # Mutating the current/latest job later must not change the old run artifact.
    jobs = storage.JobStore()
    try:
        jobs.upsert_snapshot("acme", [
            _job("a", "Senior Java Backend Engineer", "Requirements\n4+ years\nJava Spring Boot backend Kubernetes"),
            _job("b", "Software Development Engineer II", "Requirements\n3+ years\nJava Spring Boot backend REST API SQL AWS"),
        ], complete=True)
    finally:
        jobs.close()
    assert history.finalize_run(run_id)["ai_input_count"] == 2
    assert history.get_artifact(run_id, "ai_input")["content_text"] == original_text


def test_run_history_records_exact_fetched_membership(monkeypatch, tmp_path):
    _, _, history, run_id, _ = _build_finalized_run(monkeypatch, tmp_path)

    all_rows = history.search_run_jobs(run_id, page_size=50)
    assert all_rows["total"] == 3  # two returned + one closed lifecycle event
    observed = [r for r in all_rows["rows"] if r["observed"]]
    assert {r["external_id"] for r in observed} == {"a", "b"}
    assert {r["event_type"] for r in observed} == {"new", "changed"}
    closed = [r for r in all_rows["rows"] if r["event_type"] == "closed"]
    assert [r["external_id"] for r in closed] == ["c"]


def test_run_history_ui_downloads_current_and_old_artifacts(monkeypatch, tmp_path):
    storage, _, history, run_id, _ = _build_finalized_run(monkeypatch, tmp_path)
    from job_fetcher.web import app

    with TestClient(app) as client:
        page = client.get(f"/history/runs/{run_id}")
        assert page.status_code == 200
        assert "Download AI Input" in page.text
        assert "Jobs in this run" in page.text

        download = client.get(f"/history/runs/{run_id}/download/ai-input")
        assert download.status_code == 200
        assert "attachment" in download.headers["content-disposition"]
        assert json.loads(download.text)["run_id"] == run_id

        latest = client.get("/history/latest/ai-input")
        assert latest.status_code == 200
        assert json.loads(latest.text)["run_id"] == run_id

        prepared = client.post(f"/history/runs/{run_id}/prepare-github", follow_redirects=False)
        assert prepared.status_code == 303

    assert history.get_artifact(run_id, "ai_input")["downloaded_at"] is not None
    prepared_dir = tmp_path / "run-history" / "2026" / "08"
    assert list(prepared_dir.glob(f"*_{run_id}/ai_input.json"))

    # A pre-feature/legacy run remains visible but truthfully has no frozen AI file.
    legacy = storage.RunStore().create_run("fetch", total_companies=1, scope="all", settings={}, targets=["acme"])
    storage.RunStore().finish(legacy)
    with TestClient(app) as client:
        legacy_page = client.get(f"/history/runs/{legacy}")
        assert legacy_page.status_code == 200
        assert "No immutable job snapshot" in legacy_page.text
        assert client.get(f"/history/runs/{legacy}/download/ai-input").status_code == 404
