from __future__ import annotations

import json


def _job(external_id: str, description: str):
    from job_fetcher.models import Job

    return Job(
        "acme", "Acme", "test", external_id, "Java Backend Engineer",
        "Bengaluru, India", description, f"https://jobs.example/{external_id}", "2026-08-10",
    )


def _temp_runtime(monkeypatch, tmp_path):
    import job_fetcher.storage as storage
    import job_fetcher.run_history as run_history
    import job_fetcher.delivery as delivery

    monkeypatch.setattr(storage, "DB", tmp_path / "jobs.db")
    monkeypatch.setattr(run_history, "RUN_REPORTS_ROOT", tmp_path / "reports" / "runs")
    monkeypatch.setattr(run_history, "LATEST_REPORTS_ROOT", tmp_path / "reports" / "latest")
    monkeypatch.setattr(delivery, "RUN_REPORTS_ROOT", tmp_path / "reports" / "runs")
    monkeypatch.setattr(delivery, "LATEST_REPORTS_ROOT", tmp_path / "reports" / "latest")
    return storage, run_history, delivery


def _finalize_fetch(storage, run_history, jobs_now):
    from job_fetcher.relevance_service import analyze_relevance

    history = run_history.RunHistoryStore()
    before = history.capture_inventory(["acme"])
    runs = storage.RunStore()
    run_id = runs.create_run("fetch", total_companies=1, scope="all", settings={}, targets=["acme"])
    runs.mark_running(run_id)
    store = storage.JobStore()
    try:
        store.upsert_snapshot("acme", jobs_now, complete=True)
    finally:
        store.close()
    history.record_company_snapshot(
        run_id, before.get("acme", {}), {"id": "acme", "snapshot_complete": True}, jobs_now,
    )
    analyze_relevance(recompute_all=True)
    history.finalize_run(run_id)
    runs.finish(run_id)
    return run_id, history


def test_first_ai_handoff_is_baseline_then_incremental(monkeypatch, tmp_path):
    storage, run_history, delivery = _temp_runtime(monkeypatch, tmp_path)
    desc_v1 = "Requirements\n3+ years\nJava Spring Boot backend REST API SQL AWS"
    desc_v2 = "Requirements\n3+ years\nJava Spring Boot backend microservices REST API SQL AWS Docker"

    # Existing inventory predates App 2. The first post-feature fetch observes the
    # same job unchanged, so the old v1 incremental artifact would incorrectly be empty.
    store = storage.JobStore()
    try:
        store.upsert_snapshot("acme", [_job("a", desc_v1)], complete=True)
    finally:
        store.close()

    first_run, history = _finalize_fetch(storage, run_history, [_job("a", desc_v1)])
    old_payload = json.loads(history.get_artifact(first_run, "ai_input")["content_text"])
    assert old_payload["schema_version"] == 1
    assert old_payload["jobs"] == []

    delivery.ensure_delivery_artifact(first_run)
    baseline = json.loads(history.get_artifact(first_run, "ai_input")["content_text"])
    assert baseline["schema_version"] == delivery.DELIVERY_SCHEMA_VERSION
    assert baseline["delivery_mode"] == "baseline"
    assert baseline["location_ruleset"]
    assert baseline["summary"]["baseline_relevant"] == 1
    assert baseline["summary"]["ai_input_count"] == 1
    assert [j["external_id"] for j in baseline["jobs"]] == ["a"]

    # A later run is incremental and only sends the changed relevant job.
    second_run, history = _finalize_fetch(storage, run_history, [_job("a", desc_v2)])
    delivery.ensure_delivery_artifact(second_run)
    incremental = json.loads(history.get_artifact(second_run, "ai_input")["content_text"])
    assert incremental["schema_version"] == delivery.DELIVERY_SCHEMA_VERSION
    assert incremental["delivery_mode"] == "incremental"
    assert incremental["summary"]["changed_relevant"] == 1
    assert incremental["summary"]["ai_input_count"] == 1
    assert incremental["jobs"][0]["external_id"] == "a"
    assert incremental["jobs"][0]["event_type"] == "changed"


def test_migration_upgrades_existing_v1_runs_in_order(monkeypatch, tmp_path):
    storage, run_history, delivery = _temp_runtime(monkeypatch, tmp_path)
    desc = "Requirements\n3+ years\nJava Spring Boot backend REST API SQL AWS"

    store = storage.JobStore()
    try:
        store.upsert_snapshot("acme", [_job("a", desc)], complete=True)
    finally:
        store.close()

    first_run, history = _finalize_fetch(storage, run_history, [_job("a", desc)])
    second_run, history = _finalize_fetch(storage, run_history, [_job("a", desc)])
    delivery.ensure_all_delivery_artifacts()

    first = json.loads(history.get_artifact(first_run, "ai_input")["content_text"])
    second = json.loads(history.get_artifact(second_run, "ai_input")["content_text"])
    assert first["schema_version"] == delivery.DELIVERY_SCHEMA_VERSION
    assert second["schema_version"] == delivery.DELIVERY_SCHEMA_VERSION
    assert first["delivery_mode"] == "baseline"
    assert first["summary"]["ai_input_count"] == 1
    assert second["delivery_mode"] == "incremental"
    assert second["summary"]["ai_input_count"] == 0