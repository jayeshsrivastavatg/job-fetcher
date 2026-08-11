import json
import sqlite3

from fastapi.testclient import TestClient


def _temp_db(monkeypatch, tmp_path):
    import job_fetcher.storage as storage
    import job_fetcher.run_manager as run_manager
    monkeypatch.setattr(storage, "DB", tmp_path / "jobs.db")
    run_manager._MANAGER = None
    return storage


def test_legacy_database_migrates_active_column_without_losing_jobs(monkeypatch, tmp_path):
    storage = _temp_db(monkeypatch, tmp_path)
    db = storage.DB
    conn = sqlite3.connect(db)
    conn.execute('''CREATE TABLE jobs (
      company_id TEXT NOT NULL, external_id TEXT NOT NULL, company_name TEXT NOT NULL,
      source_type TEXT NOT NULL, title TEXT NOT NULL, location TEXT, description TEXT,
      job_url TEXT, posted_at TEXT, first_seen_at TEXT NOT NULL, last_seen_at TEXT NOT NULL,
      raw_json TEXT, PRIMARY KEY(company_id, external_id))''')
    conn.execute("INSERT INTO jobs VALUES (?,?,?,?,?,?,?,?,?,?,?,?)", (
        "acme", "1", "Acme", "auto", "Engineer", "India", None,
        "https://example.test/1", None, "2026-08-10T00:00:00+00:00",
        "2026-08-10T00:00:00+00:00", None,
    ))
    conn.commit(); conn.close()

    store = storage.JobStore()
    try:
        cols = {r["name"] for r in store.conn.execute("PRAGMA table_info(jobs)")}
        assert "active" in cols
        assert store.active_total() == 1
    finally:
        store.close()


def test_incomplete_snapshot_never_deactivates_previous_jobs(monkeypatch, tmp_path):
    storage = _temp_db(monkeypatch, tmp_path)
    from job_fetcher.models import Job
    store = storage.JobStore()
    jobs = [Job("acme", "Acme", "auto", str(i), f"Engineer {i}", "India", None, f"https://x/{i}") for i in range(3)]
    try:
        store.upsert_snapshot("acme", jobs, complete=True)
        assert store.company_active_count("acme") == 3
        store.upsert_snapshot("acme", [], complete=False)
        assert store.company_active_count("acme") == 3
    finally:
        store.close()


def test_run_store_persists_progress(monkeypatch, tmp_path):
    storage = _temp_db(monkeypatch, tmp_path)
    rs = storage.RunStore()
    rid = rs.create_run("verify", total_companies=2, scope="all", settings={"fetch_workers": 2}, targets=["a", "b"])
    rs.mark_running(rid)
    rs.record_company_result(rid, {"id": "a", "name": "A", "rank": 1, "status": "healthy", "jobs_found": 10})
    run = rs.get_run(rid)
    assert run["status"] == "running"
    assert run["completed_companies"] == 1
    assert run["healthy"] == 1
    assert run["jobs_found"] == 10
    rs.finish(rid)
    assert rs.get_run(rid)["status"] == "completed"


def test_p0_pages_render(monkeypatch, tmp_path):
    storage = _temp_db(monkeypatch, tmp_path)
    from job_fetcher.web.app import app
    with TestClient(app) as client:
        expected = {
            "/": "Dashboard",
            "/companies": "Companies",
            "/companies/amazon": "Amazon",
            "/jobs": "Jobs",
            "/relevance": "Relevant Jobs",
            "/health": "Health",
            "/runs": "Runs",
            "/settings": "Settings",
        }
        for path, text in expected.items():
            response = client.get(path)
            assert response.status_code == 200, path
            assert text in response.text
            assert "Job Fetcher" in response.text


def test_run_detail_page_renders_persisted_company_result(monkeypatch, tmp_path):
    storage = _temp_db(monkeypatch, tmp_path)
    rs = storage.RunStore()
    rid = rs.create_run("fetch", total_companies=1, scope="company", settings={}, targets=["amazon"])
    rs.mark_running(rid)
    rs.record_company_result(rid, {
        "id": "amazon", "name": "Amazon", "rank": 1, "status": "healthy_with_fallback",
        "adapter": "AmazonSource", "configured_source": "amazon", "jobs_found": 12,
        "new_jobs": 3, "existing_jobs": 9, "browser_used": True,
    })
    rs.finish(rid)

    from job_fetcher.web.app import app
    with TestClient(app) as client:
        response = client.get(f"/runs/{rid}")
        assert response.status_code == 200
        assert "Amazon" in response.text
        assert "12" in response.text
        assert "Fallback" in response.text

def test_operation_manager_serializes_runs_and_persists_completion(monkeypatch, tmp_path):
    storage = _temp_db(monkeypatch, tmp_path)
    import job_fetcher.run_manager as rm

    gate = __import__('threading').Event()

    def fake_fetch(companies, max_workers=4, drop_threshold=.8, on_result=None):
        gate.wait(timeout=2)
        row = {
            "id": companies[0]["id"], "name": companies[0]["name"], "rank": companies[0].get("rank"),
            "status": "healthy", "adapter": "DummySource", "configured_source": "auto",
            "jobs_found": 2, "new_jobs": 2, "existing_jobs": 0, "browser_used": False,
        }
        on_result(row)
        return {"companies": [row]}

    monkeypatch.setattr(rm, "fetch_companies_detailed", fake_fetch)
    manager = rm.OperationManager()
    rid = manager.start_fetch(["amazon"])
    try:
        manager.start_verify(["amazon"])
        assert False, "expected RunConflict"
    except rm.RunConflict as exc:
        assert exc.run_id == rid
    gate.set()
    manager._threads[rid].join(timeout=3)
    run = storage.RunStore().get_run(rid)
    assert run["status"] == "completed"
    assert run["completed_companies"] == 1
    assert run["jobs_found"] == 2
