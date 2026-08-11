from __future__ import annotations

from job_fetcher.matching import parse_experience, score_job
from job_fetcher.profile import load_profile


def _profile():
    return load_profile()


def test_experience_parser_handles_plus_and_ranges():
    a = parse_experience("Required Qualifications\n3+ years of software development experience")
    assert a.min_years == 3
    assert a.max_years is None
    b = parse_experience("Requirements\n4-7 years experience building services")
    assert b.min_years == 4
    assert b.max_years == 7


def test_java_react_fullstack_is_high_priority():
    r = score_job({
        "title": "Full Stack Engineer",
        "location": "Hyderabad, India",
        "description": "Requirements\n4-7 years\nJava Spring Boot React TypeScript REST APIs PostgreSQL AWS",
    }, _profile())
    assert r.role_family == "java_react_fullstack"
    assert r.relevance_score >= 80
    assert r.relevance_status == "high"
    assert r.is_relevant is True


def test_node_backend_is_valid_target():
    r = score_job({
        "title": "Backend Engineer - Node.js",
        "location": "India",
        "description": "Requirements\n2+ years\nNode.js TypeScript JavaScript REST API PostgreSQL AWS",
    }, _profile())
    assert r.role_family == "node_backend"
    assert r.relevance_score >= 65
    assert r.is_relevant is True


def test_six_year_requirement_is_stretch_not_rejection():
    r = score_job({
        "title": "Software Engineer II - Backend",
        "location": "Bangalore",
        "description": "Minimum Qualifications\n6+ years\nJava Spring Boot backend REST SQL",
    }, _profile())
    assert r.experience_score == 12
    assert r.hard_filtered is False
    assert r.is_relevant is True


def test_eight_year_requirement_is_hard_filtered():
    r = score_job({
        "title": "Software Engineer - Backend",
        "location": "Bangalore",
        "description": "Minimum Qualifications\n8+ years\nJava Spring Boot backend REST SQL",
    }, _profile())
    assert r.hard_filtered is True
    assert r.relevance_status == "filtered"
    assert "requires_8" in (r.filter_reason or "")


def test_ios_is_filtered_even_with_matching_experience():
    r = score_job({
        "title": "Senior iOS Engineer",
        "location": "Bangalore",
        "description": "Requirements\n5+ years\nSwift SwiftUI iOS SDK",
    }, _profile())
    assert r.experience_score == 20
    assert r.hard_filtered is True
    assert r.is_relevant is False


def test_resume_backed_supporting_skills_contribute_points():
    r = score_job({
        "title": "Java Backend Engineer",
        "location": "Noida",
        "description": "Requirements\n3+ years\nJava Spring Boot REST PostgreSQL AWS Docker CI/CD observability",
    }, _profile())
    assert r.supporting_score >= 8
    names = {x["name"] for x in r.matched_supporting}
    assert "AWS" in names
    assert "SQL / PostgreSQL" in names


def test_foreign_only_location_is_filtered():
    r = score_job({
        "title": "Java Backend Engineer",
        "location": "New York, United States",
        "description": "Requirements\n3+ years\nJava Spring Boot backend REST SQL",
    }, _profile())
    assert r.hard_filtered is True
    assert r.filter_reason == "location_outside_target"


def _temp_db(monkeypatch, tmp_path):
    import job_fetcher.storage as storage
    monkeypatch.setattr(storage, "DB", tmp_path / "jobs.db")
    return storage


def test_new_changed_unchanged_is_driven_by_fetch_snapshot(monkeypatch, tmp_path):
    storage = _temp_db(monkeypatch, tmp_path)
    from job_fetcher.models import Job
    from job_fetcher.relevance_service import analyze_relevance

    store = storage.JobStore()
    try:
        job = Job("acme", "Acme", "test", "1", "Java Backend Engineer", "Bangalore, India",
                  "Requirements\n3+ years\nJava Spring Boot backend REST SQL", "https://x/1")
        store.upsert_snapshot("acme", [job], complete=True)
    finally:
        store.close()

    first = analyze_relevance()
    assert first["analyzed_this_run"] == 1
    row = storage.RelevanceStore().get("acme", "1")
    assert row["change_type"] == "new"

    # Re-analyzing without another fetch does not erase the NEW snapshot state.
    second = analyze_relevance()
    assert second["analyzed_this_run"] == 0
    assert storage.RelevanceStore().get("acme", "1")["change_type"] == "new"

    # A subsequent successful fetch of identical content marks it unchanged.
    store = storage.JobStore()
    try:
        store.upsert_snapshot("acme", [job], complete=True)
    finally:
        store.close()
    third = analyze_relevance()
    assert third["analyzed_this_run"] == 0
    assert storage.RelevanceStore().get("acme", "1")["change_type"] == "unchanged"

    # JD edits are detected by content hash and force a fresh score.
    changed = Job("acme", "Acme", "test", "1", "Java Backend Engineer", "Bangalore, India",
                  "Requirements\n3+ years\nJava Spring Boot backend REST SQL AWS Docker", "https://x/1")
    store = storage.JobStore()
    try:
        store.upsert_snapshot("acme", [changed], complete=True)
    finally:
        store.close()
    fourth = analyze_relevance()
    assert fourth["analyzed_this_run"] == 1
    assert storage.RelevanceStore().get("acme", "1")["change_type"] == "changed"


def test_near_duplicates_are_suppressed_from_relevant_results(monkeypatch, tmp_path):
    storage = _temp_db(monkeypatch, tmp_path)
    from job_fetcher.models import Job
    from job_fetcher.relevance_service import analyze_relevance

    description = "Requirements\n3+ years\nJava Spring Boot React TypeScript REST APIs PostgreSQL AWS"
    jobs = [
        Job("acme", "Acme", "test", "a", "Full Stack Engineer", "Bangalore", description, "https://x/a"),
        Job("acme", "Acme", "test", "b", "Full Stack Engineer", "Bengaluru", description, "https://x/b"),
    ]
    store = storage.JobStore()
    try:
        store.upsert_snapshot("acme", jobs, complete=True)
    finally:
        store.close()
    result = analyze_relevance()
    assert result["duplicates_marked"] == 1
    rows = storage.RelevanceStore().search(page_size=10)["rows"]
    statuses = sorted(r["relevance_status"] for r in rows)
    assert "duplicate" in statuses
    assert sum(int(r["is_relevant"]) for r in rows) == 1


def test_relevance_export_contains_full_jd_and_only_relevant_jobs(monkeypatch, tmp_path):
    storage = _temp_db(monkeypatch, tmp_path)
    from job_fetcher.models import Job
    from job_fetcher.relevance_service import analyze_relevance, export_relevance
    import json

    jobs = [
        Job("acme", "Acme", "test", "good", "Full Stack Engineer", "India",
            "Requirements\n4-7 years\nJava Spring Boot React TypeScript REST APIs PostgreSQL AWS", "https://x/good"),
        Job("acme", "Acme", "test", "bad", "Senior iOS Engineer", "India",
            "Requirements\n5+ years\nSwift SwiftUI", "https://x/bad"),
    ]
    store = storage.JobStore()
    try:
        store.upsert_snapshot("acme", jobs, complete=True)
    finally:
        store.close()
    analyze_relevance()
    out = export_relevance(tmp_path / "relevant.json", format="json", relevant_only=True)
    payload = json.loads(out.read_text(encoding="utf-8"))
    assert len(payload) == 1
    assert payload[0]["external_id"] == "good"
    assert "Java Spring Boot" in payload[0]["description"]
    assert payload[0]["is_relevant"] == 1


def test_step10_relevance_table_migrates_without_losing_scores(monkeypatch, tmp_path):
    storage = _temp_db(monkeypatch, tmp_path)
    import sqlite3
    db = storage.DB
    conn = sqlite3.connect(db)
    conn.executescript('''
    CREATE TABLE jobs (
      company_id TEXT NOT NULL, external_id TEXT NOT NULL, company_name TEXT NOT NULL,
      source_type TEXT NOT NULL, title TEXT NOT NULL, location TEXT, description TEXT,
      job_url TEXT, posted_at TEXT, first_seen_at TEXT NOT NULL, last_seen_at TEXT NOT NULL,
      raw_json TEXT, active INTEGER NOT NULL DEFAULT 1, content_hash TEXT,
      content_changed_at TEXT, last_change_type TEXT NOT NULL DEFAULT 'baseline',
      PRIMARY KEY(company_id, external_id)
    );
    CREATE TABLE job_candidate_analysis (
      company_id TEXT NOT NULL, external_id TEXT NOT NULL, source_hash TEXT NOT NULL,
      analyzed_at TEXT NOT NULL, change_type TEXT NOT NULL, role_family TEXT, role_label TEXT,
      normalized_location TEXT, min_experience REAL, max_experience REAL, experience_text TEXT,
      role_score REAL NOT NULL DEFAULT 0, experience_score REAL NOT NULL DEFAULT 0,
      primary_skill_score REAL NOT NULL DEFAULT 0, supporting_score REAL NOT NULL DEFAULT 0,
      local_score REAL NOT NULL DEFAULT 0, candidate_status TEXT NOT NULL,
      ai_candidate INTEGER NOT NULL DEFAULT 0, hard_filtered INTEGER NOT NULL DEFAULT 0,
      filter_reason TEXT, matched_primary_json TEXT, matched_supporting_json TEXT,
      score_breakdown_json TEXT, duplicate_of_company_id TEXT, duplicate_of_external_id TEXT,
      PRIMARY KEY(company_id, external_id)
    );
    ''')
    conn.execute("INSERT INTO jobs VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", (
        "acme", "1", "Acme", "test", "Java Backend Engineer", "India",
        "Java Spring Boot REST SQL", "https://x/1", None, "2026-08-10", "2026-08-11",
        None, 1, "hash", "2026-08-10", "new",
    ))
    conn.execute("INSERT INTO job_candidate_analysis VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", (
        "acme", "1", "hash", "2026-08-11", "new", "java_backend", "Java Backend",
        "India", 3, None, "3+ years", 35, 20, 15, 8, 78, "good", 1, 0, None,
        "[]", "[]", "{}", None, None,
    ))
    conn.commit(); conn.close()

    row = storage.RelevanceStore().get("acme", "1")
    assert row is not None
    assert row["relevance_score"] == 78
    assert row["relevance_status"] == "good"
    assert row["is_relevant"] == 1


def test_legacy_profile_threshold_key_is_normalized(tmp_path):
    import json
    from job_fetcher.profile import load_profile
    profile = _profile()
    profile["scoring"]["aiCandidateMinScore"] = profile["scoring"].pop("relevantMinScore")
    path = tmp_path / "profile.json"
    path.write_text(json.dumps(profile), encoding="utf-8")
    loaded = load_profile(path)
    assert loaded["scoring"]["relevantMinScore"] == 50
