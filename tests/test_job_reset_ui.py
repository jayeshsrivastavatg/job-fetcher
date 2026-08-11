from __future__ import annotations

from fastapi.testclient import TestClient


def _temp_runtime(monkeypatch, tmp_path):
    import job_fetcher.storage as storage
    import job_fetcher.run_manager as run_manager
    import job_fetcher.web.reset_web as reset_web

    monkeypatch.setattr(storage, "DB", tmp_path / "jobs.db")
    run_manager._MANAGER = None

    monkeypatch.setattr(reset_web, "RUN_REPORTS_ROOT", tmp_path / "reports" / "runs")
    monkeypatch.setattr(reset_web, "LATEST_REPORTS_ROOT", tmp_path / "reports" / "latest")
    monkeypatch.setattr(reset_web, "GIT_HISTORY_ROOT", tmp_path / "run-history")
    # reset_web.ROOT is imported from storage by value, so patch it separately for
    # standard daily/relevance artifact cleanup in this isolated test runtime.
    monkeypatch.setattr(reset_web, "ROOT", tmp_path)
    return storage, reset_web


def _seed_job(storage):
    from job_fetcher.models import Job

    job = Job(
        "acme", "Acme", "test", "1", "Java Backend Engineer", "Bengaluru, India",
        "Requirements\n3+ years\nJava Spring Boot backend REST SQL AWS", "https://x/1",
    )
    store = storage.JobStore()
    try:
        store.upsert_snapshot("acme", [job], complete=True)
    finally:
        store.close()


def test_reset_page_requires_typed_and_final_confirmation(monkeypatch, tmp_path):
    storage, reset_web = _temp_runtime(monkeypatch, tmp_path)
    _seed_job(storage)

    # Seed old run metadata and generated files so the reset proves it is a full
    # clean slate, not merely DELETE FROM jobs.
    runs = storage.RunStore()
    old_run = runs.create_run("fetch", total_companies=1, scope="all", settings={}, targets=["acme"])
    runs.mark_running(old_run)
    runs.finish(old_run)

    for path in (
        reset_web.RUN_REPORTS_ROOT,
        reset_web.LATEST_REPORTS_ROOT,
        reset_web.GIT_HISTORY_ROOT,
        tmp_path / "reports" / "daily",
    ):
        path.mkdir(parents=True, exist_ok=True)
        (path / "old.json").write_text("{}", encoding="utf-8")
    (tmp_path / "reports" / "relevant_jobs.csv").write_text("old", encoding="utf-8")

    from job_fetcher.web.app import app

    with TestClient(app) as client:
        jobs_page = client.get("/jobs")
        assert jobs_page.status_code == 200
        assert "Clear all job data" in jobs_page.text

        confirmation_page = client.get("/jobs/reset")
        assert confirmation_page.status_code == 200
        assert "DELETE ALL JOBS" in confirmation_page.text
        assert "Permanently clear all collected job data" in confirmation_page.text

        bad = client.post("/jobs/reset", data={"confirmation": "DELETE JOBS"})
        assert bad.status_code == 400
        store = storage.JobStore()
        try:
            assert store.total() == 1
        finally:
            store.close()

        good = client.post(
            "/jobs/reset",
            data={"confirmation": "DELETE ALL JOBS"},
            follow_redirects=False,
        )
        assert good.status_code == 303
        assert good.headers["location"].startswith("/jobs?reset=1&cleared=1")

    store = storage.JobStore()
    try:
        assert store.total() == 0
        assert store.active_total() == 0
    finally:
        store.close()
    assert list(storage.RunStore().list_runs()) == []
    assert storage.RelevanceStore().get("acme", "1") is None

    assert not reset_web.RUN_REPORTS_ROOT.exists()
    assert not reset_web.LATEST_REPORTS_ROOT.exists()
    assert not reset_web.GIT_HISTORY_ROOT.exists()
    assert not (tmp_path / "reports" / "daily").exists()
    assert not (tmp_path / "reports" / "relevant_jobs.csv").exists()


def test_reset_is_blocked_while_a_run_is_active(monkeypatch, tmp_path):
    storage, _ = _temp_runtime(monkeypatch, tmp_path)
    _seed_job(storage)

    from job_fetcher.web.app import app

    with TestClient(app) as client:
        runs = storage.RunStore()
        active_run = runs.create_run("fetch", total_companies=1, scope="all", settings={}, targets=["acme"])

        response = client.post("/jobs/reset", data={"confirmation": "DELETE ALL JOBS"})
        assert response.status_code == 409
        assert "still active" in response.text

        store = storage.JobStore()
        try:
            assert store.total() == 1
        finally:
            store.close()

        runs.finish(active_run)
