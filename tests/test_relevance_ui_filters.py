from __future__ import annotations

from fastapi.testclient import TestClient


def _temp_db(monkeypatch, tmp_path):
    import job_fetcher.storage as storage
    import job_fetcher.run_manager as run_manager
    monkeypatch.setattr(storage, "DB", tmp_path / "jobs.db")
    run_manager._MANAGER = None
    return storage


def test_relevance_page_accepts_blank_min_score(monkeypatch, tmp_path):
    _temp_db(monkeypatch, tmp_path)
    from job_fetcher.web.app import app

    with TestClient(app) as client:
        response = client.get("/relevance", params={"min_score": ""})

    assert response.status_code == 200
    assert "Relevant Jobs" in response.text
    assert "Posted since" in response.text
    assert "First found since" in response.text


def test_relevance_store_filters_by_posted_and_first_seen_dates(monkeypatch, tmp_path):
    storage = _temp_db(monkeypatch, tmp_path)
    from job_fetcher.models import Job
    from job_fetcher.relevance_service import analyze_relevance

    description = "Requirements\n3+ years\nJava Spring Boot backend REST SQL AWS"
    store = storage.JobStore()
    try:
        store.upsert_snapshot("acme", [
            Job("acme", "Acme", "test", "old", "Java Backend Engineer", "India",
                description, "https://x/old", "2026-08-01"),
            Job("acme", "Acme", "test", "new", "Java Backend Engineer II", "India",
                description, "https://x/new", "2026-08-11"),
        ], complete=True)
        store.conn.execute(
            "UPDATE jobs SET first_seen_at=? WHERE company_id='acme' AND external_id='old'",
            ("2026-08-01T09:00:00+00:00",),
        )
        store.conn.execute(
            "UPDATE jobs SET first_seen_at=? WHERE company_id='acme' AND external_id='new'",
            ("2026-08-11T09:00:00+00:00",),
        )
        store.conn.commit()
    finally:
        store.close()

    analyze_relevance()
    from job_fetcher.relevance_query import RelevanceStore
    relevance = RelevanceStore()

    posted = relevance.search(posted_since="2026-08-10", page_size=20)["rows"]
    found = relevance.search(first_seen_since="2026-08-10", page_size=20)["rows"]

    assert [row["external_id"] for row in posted] == ["new"]
    assert [row["external_id"] for row in found] == ["new"]
