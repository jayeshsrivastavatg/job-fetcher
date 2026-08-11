from __future__ import annotations

import json
import hashlib
import sqlite3
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
DB = ROOT / "data" / "jobs.db"

SCHEMA = '''
CREATE TABLE IF NOT EXISTS jobs (
  company_id TEXT NOT NULL,
  external_id TEXT NOT NULL,
  company_name TEXT NOT NULL,
  source_type TEXT NOT NULL,
  title TEXT NOT NULL,
  location TEXT,
  description TEXT,
  job_url TEXT,
  posted_at TEXT,
  first_seen_at TEXT NOT NULL,
  last_seen_at TEXT NOT NULL,
  raw_json TEXT,
  active INTEGER NOT NULL DEFAULT 1,
  content_hash TEXT,
  content_changed_at TEXT,
  last_change_type TEXT NOT NULL DEFAULT 'baseline',
  PRIMARY KEY(company_id, external_id)
);
CREATE INDEX IF NOT EXISTS idx_jobs_company ON jobs(company_id);
CREATE INDEX IF NOT EXISTS idx_jobs_first_seen ON jobs(first_seen_at);

CREATE TABLE IF NOT EXISTS runs (
  id TEXT PRIMARY KEY,
  run_type TEXT NOT NULL,
  scope TEXT NOT NULL DEFAULT 'all',
  status TEXT NOT NULL,
  started_at TEXT,
  finished_at TEXT,
  created_at TEXT NOT NULL,
  total_companies INTEGER NOT NULL DEFAULT 0,
  completed_companies INTEGER NOT NULL DEFAULT 0,
  healthy INTEGER NOT NULL DEFAULT 0,
  fallback INTEGER NOT NULL DEFAULT 0,
  suspicious INTEGER NOT NULL DEFAULT 0,
  failed INTEGER NOT NULL DEFAULT 0,
  jobs_found INTEGER NOT NULL DEFAULT 0,
  jobs_new INTEGER NOT NULL DEFAULT 0,
  jobs_updated INTEGER NOT NULL DEFAULT 0,
  error TEXT,
  settings_json TEXT,
  target_json TEXT
);
CREATE INDEX IF NOT EXISTS idx_runs_type_created ON runs(run_type, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_runs_status ON runs(status);

CREATE TABLE IF NOT EXISTS run_company_results (
  run_id TEXT NOT NULL,
  company_id TEXT NOT NULL,
  company_name TEXT NOT NULL,
  rank INTEGER,
  status TEXT NOT NULL,
  adapter TEXT,
  configured_source TEXT,
  jobs_found INTEGER NOT NULL DEFAULT 0,
  new_jobs INTEGER NOT NULL DEFAULT 0,
  existing_jobs INTEGER NOT NULL DEFAULT 0,
  previous_jobs_found INTEGER,
  count_change_pct REAL,
  browser_used INTEGER NOT NULL DEFAULT 0,
  failure_category TEXT,
  error TEXT,
  quality_ratio REAL,
  duration_seconds REAL,
  payload_json TEXT,
  created_at TEXT NOT NULL,
  PRIMARY KEY(run_id, company_id),
  FOREIGN KEY(run_id) REFERENCES runs(id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_run_results_company ON run_company_results(company_id, created_at DESC);

CREATE TABLE IF NOT EXISTS job_relevance_analysis (
  company_id TEXT NOT NULL,
  external_id TEXT NOT NULL,
  source_hash TEXT NOT NULL,
  analyzed_at TEXT NOT NULL,
  change_type TEXT NOT NULL,
  role_family TEXT,
  role_label TEXT,
  normalized_location TEXT,
  min_experience REAL,
  max_experience REAL,
  experience_text TEXT,
  role_score REAL NOT NULL DEFAULT 0,
  experience_score REAL NOT NULL DEFAULT 0,
  primary_skill_score REAL NOT NULL DEFAULT 0,
  supporting_score REAL NOT NULL DEFAULT 0,
  relevance_score REAL NOT NULL DEFAULT 0,
  relevance_status TEXT NOT NULL,
  is_relevant INTEGER NOT NULL DEFAULT 0,
  hard_filtered INTEGER NOT NULL DEFAULT 0,
  filter_reason TEXT,
  matched_primary_json TEXT,
  matched_supporting_json TEXT,
  score_breakdown_json TEXT,
  duplicate_of_company_id TEXT,
  duplicate_of_external_id TEXT,
  PRIMARY KEY(company_id, external_id),
  FOREIGN KEY(company_id, external_id) REFERENCES jobs(company_id, external_id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_relevance_status ON job_relevance_analysis(relevance_status, relevance_score DESC);
CREATE INDEX IF NOT EXISTS idx_relevance_flag ON job_relevance_analysis(is_relevant, relevance_score DESC);
CREATE INDEX IF NOT EXISTS idx_relevance_family ON job_relevance_analysis(role_family, relevance_score DESC);
'''


def utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


def _connect() -> sqlite3.Connection:
    DB.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute("PRAGMA busy_timeout=30000")
    conn.executescript(SCHEMA)
    # Step 8 databases do not yet have lifecycle state. Keep migration small and
    # idempotent so users can point the UI at an existing jobs.db safely.
    cols = {r["name"] for r in conn.execute("PRAGMA table_info(jobs)")}
    if "active" not in cols:
        conn.execute("ALTER TABLE jobs ADD COLUMN active INTEGER NOT NULL DEFAULT 1")
    if "content_hash" not in cols:
        conn.execute("ALTER TABLE jobs ADD COLUMN content_hash TEXT")
    if "content_changed_at" not in cols:
        conn.execute("ALTER TABLE jobs ADD COLUMN content_changed_at TEXT")
    if "last_change_type" not in cols:
        conn.execute("ALTER TABLE jobs ADD COLUMN last_change_type TEXT NOT NULL DEFAULT 'baseline'")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_jobs_active ON jobs(active)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_jobs_change_type ON jobs(last_change_type, content_changed_at)")

    # One-time compatibility migration from the Step 10 pre-GitHub schema.
    # The old table is read only to preserve previously computed relevance data;
    # all current code writes to job_relevance_analysis.
    legacy_exists = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='job_candidate_analysis'"
    ).fetchone()
    if legacy_exists:
        current_count = conn.execute("SELECT COUNT(*) FROM job_relevance_analysis").fetchone()[0]
        if current_count == 0:
            conn.execute(
                """INSERT OR IGNORE INTO job_relevance_analysis (
                   company_id,external_id,source_hash,analyzed_at,change_type,role_family,role_label,
                   normalized_location,min_experience,max_experience,experience_text,role_score,
                   experience_score,primary_skill_score,supporting_score,relevance_score,relevance_status,
                   is_relevant,hard_filtered,filter_reason,matched_primary_json,matched_supporting_json,
                   score_breakdown_json,duplicate_of_company_id,duplicate_of_external_id
                   )
                   SELECT company_id,external_id,source_hash,analyzed_at,change_type,role_family,role_label,
                   normalized_location,min_experience,max_experience,experience_text,role_score,
                   experience_score,primary_skill_score,supporting_score,local_score,candidate_status,
                   ai_candidate,hard_filtered,filter_reason,matched_primary_json,matched_supporting_json,
                   score_breakdown_json,duplicate_of_company_id,duplicate_of_external_id
                   FROM job_candidate_analysis"""
            )
    conn.commit()
    return conn


def _job_content_hash(title: str | None, location: str | None, description: str | None) -> str:
    payload = "\n".join([str(title or "").strip(), str(location or "").strip(), str(description or "").strip()])
    return hashlib.sha256(payload.encode("utf-8", errors="ignore")).hexdigest()


def _change_for_existing(row, new_hash: str) -> tuple[str, str | None]:
    if not row:
        return "new", utcnow()
    old_hash = row["content_hash"] if "content_hash" in row.keys() else None
    if not old_hash:
        old_hash = _job_content_hash(row["title"], row["location"], row["description"])
    if old_hash != new_hash:
        return "changed", utcnow()
    return "unchanged", row["content_changed_at"] if "content_changed_at" in row.keys() else None


class JobStore:
    def __init__(self):
        self.conn = _connect()

    def close(self):
        self.conn.close()

    def upsert_many(self, jobs):
        """Backward-compatible upsert used by the CLI fetch path."""
        now = utcnow()
        new = old = 0
        with self.conn:
            for j in jobs:
                eid = j.stable_external_id()
                exists = self.conn.execute(
                    "SELECT content_hash,content_changed_at,title,location,description FROM jobs WHERE company_id=? AND external_id=?",
                    (j.company_id, eid),
                ).fetchone()
                raw = json.dumps(j.raw, ensure_ascii=False) if j.raw is not None else None
                content_hash = _job_content_hash(j.title, j.location, j.description)
                change_type, changed_at = _change_for_existing(exists, content_hash)
                if exists:
                    self.conn.execute(
                        '''UPDATE jobs SET company_name=?,source_type=?,title=?,location=?,description=?,
                           job_url=?,posted_at=?,last_seen_at=?,raw_json=?,active=1,content_hash=?,
                           content_changed_at=?,last_change_type=?
                           WHERE company_id=? AND external_id=?''',
                        (j.company_name, j.source_type, j.title, j.location, j.description,
                         j.job_url, j.posted_at, now, raw, content_hash, changed_at, change_type, j.company_id, eid),
                    )
                    old += 1
                else:
                    self.conn.execute(
                        '''INSERT INTO jobs
                           (company_id,external_id,company_name,source_type,title,location,description,
                            job_url,posted_at,first_seen_at,last_seen_at,raw_json,active,content_hash,content_changed_at,last_change_type)
                           VALUES (?,?,?,?,?,?,?,?,?,?,?,?,1,?,?,?)''',
                        (j.company_id, eid, j.company_name, j.source_type, j.title, j.location,
                         j.description, j.job_url, j.posted_at, now, now, raw, content_hash, now, "new"),
                    )
                    new += 1
        return new, old

    def upsert_snapshot(self, company_id: str, jobs, *, complete: bool) -> tuple[int, int, int]:
        """Upsert a single-company snapshot and optionally deactivate missing jobs.

        `complete=False` is the safety valve for zero/very-low/otherwise suspicious
        fetches: no previously active job is deactivated when completeness is in
        doubt.
        """
        jobs = list(jobs or [])
        now = utcnow()
        new = old = 0
        before_active = self.company_active_count(company_id)
        with self.conn:
            if complete:
                self.conn.execute("UPDATE jobs SET active=0 WHERE company_id=?", (company_id,))
            for j in jobs:
                eid = j.stable_external_id()
                exists = self.conn.execute(
                    "SELECT content_hash,content_changed_at,title,location,description FROM jobs WHERE company_id=? AND external_id=?",
                    (j.company_id, eid),
                ).fetchone()
                raw = json.dumps(j.raw, ensure_ascii=False) if j.raw is not None else None
                content_hash = _job_content_hash(j.title, j.location, j.description)
                change_type, changed_at = _change_for_existing(exists, content_hash)
                if exists:
                    self.conn.execute(
                        '''UPDATE jobs SET company_name=?,source_type=?,title=?,location=?,description=?,
                           job_url=?,posted_at=?,last_seen_at=?,raw_json=?,active=1,content_hash=?,
                           content_changed_at=?,last_change_type=?
                           WHERE company_id=? AND external_id=?''',
                        (j.company_name, j.source_type, j.title, j.location, j.description,
                         j.job_url, j.posted_at, now, raw, content_hash, changed_at, change_type, j.company_id, eid),
                    )
                    old += 1
                else:
                    self.conn.execute(
                        '''INSERT INTO jobs
                           (company_id,external_id,company_name,source_type,title,location,description,
                            job_url,posted_at,first_seen_at,last_seen_at,raw_json,active,content_hash,content_changed_at,last_change_type)
                           VALUES (?,?,?,?,?,?,?,?,?,?,?,?,1,?,?,?)''',
                        (j.company_id, eid, j.company_name, j.source_type, j.title, j.location,
                         j.description, j.job_url, j.posted_at, now, now, raw, content_hash, now, "new"),
                    )
                    new += 1
        after_active = self.company_active_count(company_id)
        deactivated = max(0, before_active - after_active) if complete else 0
        return new, old, deactivated

    def counts(self, active_only: bool = False):
        where = "WHERE active=1" if active_only else ""
        return self.conn.execute(
            f"SELECT company_name, COUNT(*) n FROM jobs {where} GROUP BY company_name ORDER BY company_name"
        ).fetchall()

    def all(self, active_only: bool = False):
        where = "WHERE active=1" if active_only else ""
        return self.conn.execute(
            f"SELECT * FROM jobs {where} ORDER BY company_name,title"
        ).fetchall()

    def active_total(self) -> int:
        return int(self.conn.execute("SELECT COUNT(*) FROM jobs WHERE active=1").fetchone()[0])

    def total(self) -> int:
        return int(self.conn.execute("SELECT COUNT(*) FROM jobs").fetchone()[0])

    def company_active_count(self, company_id: str) -> int:
        return int(self.conn.execute(
            "SELECT COUNT(*) FROM jobs WHERE company_id=? AND active=1", (company_id,)
        ).fetchone()[0])

    def company_counts(self) -> dict[str, dict[str, int]]:
        rows = self.conn.execute(
            '''SELECT company_id, COUNT(*) total,
                      SUM(CASE WHEN active=1 THEN 1 ELSE 0 END) active
               FROM jobs GROUP BY company_id'''
        ).fetchall()
        return {r["company_id"]: {"total": int(r["total"]), "active": int(r["active"] or 0)} for r in rows}

    def company_jobs(self, company_id: str, limit: int = 10, active_only: bool = True):
        where = "AND active=1" if active_only else ""
        return self.conn.execute(
            f'''SELECT * FROM jobs WHERE company_id=? {where}
                ORDER BY COALESCE(posted_at,'' ) DESC, first_seen_at DESC, title LIMIT ?''',
            (company_id, int(limit)),
        ).fetchall()

    def get_job(self, company_id: str, external_id: str):
        return self.conn.execute(
            "SELECT * FROM jobs WHERE company_id=? AND external_id=?",
            (company_id, external_id),
        ).fetchone()

    def distinct_locations(self, limit: int = 250):
        return [r[0] for r in self.conn.execute(
            '''SELECT location FROM jobs
               WHERE active=1 AND location IS NOT NULL AND TRIM(location)<>''
               GROUP BY location ORDER BY COUNT(*) DESC, location LIMIT ?''',
            (int(limit),),
        ).fetchall()]

    def search_jobs(
        self, *, query: str = "", company_id: str = "", location: str = "",
        active: str = "active", posted_since: str = "", first_seen_since: str = "",
        page: int = 1, page_size: int = 50,
    ) -> dict[str, Any]:
        clauses: list[str] = []
        args: list[Any] = []
        if query:
            like = f"%{query.strip()}%"
            clauses.append("(title LIKE ? OR description LIKE ? OR company_name LIKE ? OR location LIKE ?)")
            args.extend([like, like, like, like])
        if company_id:
            clauses.append("company_id=?")
            args.append(company_id)
        if location:
            clauses.append("location LIKE ?")
            args.append(f"%{location.strip()}%")
        if active == "active":
            clauses.append("active=1")
        elif active == "inactive":
            clauses.append("active=0")
        if posted_since:
            clauses.append("date(posted_at) >= date(?)")
            args.append(posted_since)
        if first_seen_since:
            clauses.append("datetime(first_seen_at) >= datetime(?)")
            args.append(first_seen_since)
        where = (" WHERE " + " AND ".join(clauses)) if clauses else ""
        total = int(self.conn.execute(f"SELECT COUNT(*) FROM jobs{where}", args).fetchone()[0])
        page = max(1, int(page))
        page_size = max(1, min(200, int(page_size)))
        offset = (page - 1) * page_size
        rows = self.conn.execute(
            f'''SELECT * FROM jobs{where}
                ORDER BY active DESC, COALESCE(posted_at,'') DESC, first_seen_at DESC, company_name, title
                LIMIT ? OFFSET ?''',
            [*args, page_size, offset],
        ).fetchall()
        return {"rows": rows, "total": total, "page": page, "page_size": page_size,
                "pages": max(1, (total + page_size - 1) // page_size)}


class RunStore:
    ACTIVE_STATUSES = {"queued", "running"}

    def __init__(self):
        # Initialize/migrate schema once; individual methods use short-lived
        # connections so FastAPI request threads and background workers never
        # share a sqlite connection object.
        conn = _connect()
        conn.close()

    def create_run(self, run_type: str, *, total_companies: int, scope: str,
                   settings: dict[str, Any] | None = None,
                   targets: list[str] | None = None) -> str:
        run_id = uuid.uuid4().hex[:12]
        with _connect() as conn:
            conn.execute(
                '''INSERT INTO runs
                   (id,run_type,scope,status,created_at,total_companies,settings_json,target_json)
                   VALUES (?,?,?,?,?,?,?,?)''',
                (run_id, run_type, scope, "queued", utcnow(), int(total_companies),
                 json.dumps(settings or {}), json.dumps(targets or [])),
            )
        return run_id

    def mark_running(self, run_id: str):
        with _connect() as conn:
            conn.execute("UPDATE runs SET status='running', started_at=? WHERE id=?", (utcnow(), run_id))

    def record_company_result(self, run_id: str, row: dict[str, Any]):
        payload = dict(row)
        with _connect() as conn:
            conn.execute(
                '''INSERT OR REPLACE INTO run_company_results
                   (run_id,company_id,company_name,rank,status,adapter,configured_source,jobs_found,
                    new_jobs,existing_jobs,previous_jobs_found,count_change_pct,browser_used,
                    failure_category,error,quality_ratio,duration_seconds,payload_json,created_at)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)''',
                (
                    run_id, str(row.get("id") or row.get("company_id")),
                    str(row.get("name") or row.get("company_name") or row.get("id")),
                    row.get("rank"), str(row.get("status") or "failed"), row.get("adapter"),
                    row.get("configured_source"), int(row.get("jobs_found", row.get("fetched", 0)) or 0),
                    int(row.get("new_jobs", row.get("new", 0)) or 0),
                    int(row.get("existing_jobs", row.get("existing", 0)) or 0),
                    row.get("previous_jobs_found"), row.get("count_change_pct"),
                    1 if row.get("browser_used") else 0, row.get("failure_category") or row.get("category"),
                    row.get("error"), row.get("quality_ratio"), row.get("duration_seconds"),
                    json.dumps(payload, ensure_ascii=False), utcnow(),
                ),
            )
            self._recompute(conn, run_id)

    @staticmethod
    def _recompute(conn: sqlite3.Connection, run_id: str):
        rows = conn.execute(
            "SELECT status,jobs_found,new_jobs,existing_jobs FROM run_company_results WHERE run_id=?",
            (run_id,),
        ).fetchall()
        healthy = sum(r["status"] == "healthy" for r in rows)
        fallback = sum(r["status"] == "healthy_with_fallback" for r in rows)
        suspicious = sum(r["status"] == "suspicious" for r in rows)
        failed = sum(r["status"] == "failed" for r in rows)
        conn.execute(
            '''UPDATE runs SET completed_companies=?,healthy=?,fallback=?,suspicious=?,failed=?,
               jobs_found=?,jobs_new=?,jobs_updated=? WHERE id=?''',
            (len(rows), healthy, fallback, suspicious, failed,
             sum(int(r["jobs_found"] or 0) for r in rows),
             sum(int(r["new_jobs"] or 0) for r in rows),
             sum(int(r["existing_jobs"] or 0) for r in rows), run_id),
        )

    def finish(self, run_id: str, *, error: str | None = None):
        with _connect() as conn:
            status = "failed" if error else "completed"
            conn.execute(
                "UPDATE runs SET status=?,finished_at=?,error=? WHERE id=?",
                (status, utcnow(), error, run_id),
            )

    def interrupt_stale_runs(self):
        with _connect() as conn:
            conn.execute(
                "UPDATE runs SET status='interrupted',finished_at=?,error=COALESCE(error,'Server restarted during run') WHERE status IN ('queued','running')",
                (utcnow(),),
            )

    def active_run(self):
        with _connect() as conn:
            return conn.execute(
                "SELECT * FROM runs WHERE status IN ('queued','running') ORDER BY created_at DESC LIMIT 1"
            ).fetchone()

    def get_run(self, run_id: str) -> dict[str, Any] | None:
        with _connect() as conn:
            run = conn.execute("SELECT * FROM runs WHERE id=?", (run_id,)).fetchone()
            if not run:
                return None
            results = conn.execute(
                '''SELECT * FROM run_company_results WHERE run_id=?
                   ORDER BY COALESCE(rank,999999), company_name''',
                (run_id,),
            ).fetchall()
        out = dict(run)
        out["settings"] = json.loads(out.pop("settings_json") or "{}")
        out["targets"] = json.loads(out.pop("target_json") or "[]")
        parsed = []
        for r in results:
            d = dict(r)
            try:
                payload = json.loads(d.pop("payload_json") or "{}")
            except Exception:
                payload = {}
            d["payload"] = payload
            parsed.append(d)
        out["results"] = parsed
        return out

    def list_runs(self, run_type: str | None = None, limit: int = 100):
        with _connect() as conn:
            if run_type:
                rows = conn.execute(
                    "SELECT * FROM runs WHERE run_type=? ORDER BY created_at DESC LIMIT ?",
                    (run_type, int(limit)),
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT * FROM runs ORDER BY created_at DESC LIMIT ?", (int(limit),)
                ).fetchall()
        return rows

    def latest_run(self, run_type: str):
        with _connect() as conn:
            return conn.execute(
                "SELECT * FROM runs WHERE run_type=? ORDER BY created_at DESC LIMIT 1", (run_type,)
            ).fetchone()

    def company_history(self, company_id: str, run_type: str = "verify", limit: int = 20):
        with _connect() as conn:
            return conn.execute(
                '''SELECT r.id run_id,r.run_type,r.created_at run_created_at,r.started_at,r.finished_at,
                          rcr.*
                   FROM run_company_results rcr JOIN runs r ON r.id=rcr.run_id
                   WHERE rcr.company_id=? AND r.run_type=?
                   ORDER BY r.created_at DESC LIMIT ?''',
                (company_id, run_type, int(limit)),
            ).fetchall()


    def latest_results_by_company(self, run_type: str) -> dict[str, dict[str, Any]]:
        with _connect() as conn:
            rows = conn.execute(
                '''SELECT rcr.*, r.created_at run_created_at, r.finished_at run_finished_at
                   FROM run_company_results rcr
                   JOIN runs r ON r.id=rcr.run_id
                   WHERE r.run_type=? AND r.created_at=(
                       SELECT MAX(r2.created_at) FROM run_company_results rr2
                       JOIN runs r2 ON r2.id=rr2.run_id
                       WHERE rr2.company_id=rcr.company_id AND r2.run_type=?
                   )''',
                (run_type, run_type),
            ).fetchall()
        return {r["company_id"]: dict(r) for r in rows}

    def latest_company_result(self, company_id: str, run_type: str = "verify"):
        rows = self.company_history(company_id, run_type=run_type, limit=1)
        return rows[0] if rows else None

    def latest_good_verification_count(self, company_id: str) -> int | None:
        with _connect() as conn:
            row = conn.execute(
                '''SELECT rcr.jobs_found FROM run_company_results rcr
                   JOIN runs r ON r.id=rcr.run_id
                   WHERE rcr.company_id=? AND r.run_type='verify'
                     AND rcr.jobs_found>0 AND rcr.status IN ('healthy','healthy_with_fallback','suspicious')
                   ORDER BY r.created_at DESC LIMIT 1''',
                (company_id,),
            ).fetchone()
        return int(row[0]) if row else None


class RelevanceStore:
    def __init__(self):
        conn = _connect()
        conn.close()

    def get(self, company_id: str, external_id: str) -> dict[str, Any] | None:
        with _connect() as conn:
            row = conn.execute(
                "SELECT * FROM job_relevance_analysis WHERE company_id=? AND external_id=?",
                (company_id, external_id),
            ).fetchone()
        return dict(row) if row else None

    def upsert(self, company_id: str, external_id: str, source_hash: str, change_type: str, result: dict[str, Any]):
        with _connect() as conn:
            conn.execute(
                '''INSERT INTO job_relevance_analysis (
                   company_id,external_id,source_hash,analyzed_at,change_type,role_family,role_label,
                   normalized_location,min_experience,max_experience,experience_text,role_score,
                   experience_score,primary_skill_score,supporting_score,relevance_score,relevance_status,
                   is_relevant,hard_filtered,filter_reason,matched_primary_json,matched_supporting_json,
                   score_breakdown_json,duplicate_of_company_id,duplicate_of_external_id
                   ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                   ON CONFLICT(company_id,external_id) DO UPDATE SET
                   source_hash=excluded.source_hash,analyzed_at=excluded.analyzed_at,change_type=excluded.change_type,
                   role_family=excluded.role_family,role_label=excluded.role_label,normalized_location=excluded.normalized_location,
                   min_experience=excluded.min_experience,max_experience=excluded.max_experience,
                   experience_text=excluded.experience_text,role_score=excluded.role_score,
                   experience_score=excluded.experience_score,primary_skill_score=excluded.primary_skill_score,
                   supporting_score=excluded.supporting_score,relevance_score=excluded.relevance_score,
                   relevance_status=excluded.relevance_status,is_relevant=excluded.is_relevant,
                   hard_filtered=excluded.hard_filtered,filter_reason=excluded.filter_reason,
                   matched_primary_json=excluded.matched_primary_json,matched_supporting_json=excluded.matched_supporting_json,
                   score_breakdown_json=excluded.score_breakdown_json,duplicate_of_company_id=NULL,duplicate_of_external_id=NULL''',
                (
                    company_id, external_id, source_hash, utcnow(), change_type,
                    result.get("role_family"), result.get("role_label"), result.get("normalized_location"),
                    result.get("min_experience"), result.get("max_experience"), result.get("experience_text"),
                    float(result.get("role_score") or 0), float(result.get("experience_score") or 0),
                    float(result.get("primary_skill_score") or 0), float(result.get("supporting_score") or 0),
                    float(result.get("relevance_score") or 0), result.get("relevance_status") or "filtered",
                    1 if result.get("is_relevant") else 0, 1 if result.get("hard_filtered") else 0,
                    result.get("filter_reason"), json.dumps(result.get("matched_primary") or [], ensure_ascii=False),
                    json.dumps(result.get("matched_supporting") or [], ensure_ascii=False),
                    json.dumps(result.get("score_breakdown") or {}, ensure_ascii=False), None, None,
                ),
            )

    def update_change_type(self, company_id: str, external_id: str, change_type: str):
        with _connect() as conn:
            conn.execute(
                "UPDATE job_relevance_analysis SET change_type=? WHERE company_id=? AND external_id=?",
                (change_type, company_id, external_id),
            )

    def reset_duplicate_markers(self, scoring: dict[str, Any] | None = None):
        scoring = scoring or {}
        high = float(scoring.get("highPriorityScore", 80))
        good = float(scoring.get("goodCandidateScore", 65))
        relevant_min = float(scoring.get("relevantMinScore", 50))
        low = float(scoring.get("lowPriorityMinScore", 35))
        with _connect() as conn:
            rows = conn.execute(
                "SELECT company_id,external_id,relevance_score,hard_filtered,filter_reason FROM job_relevance_analysis WHERE duplicate_of_external_id IS NOT NULL"
            ).fetchall()
            # Reconstruct the normal score bucket for rows that were only marked duplicate.
            for row in rows:
                if row["hard_filtered"]:
                    status = "filtered"
                    relevant = 0
                else:
                    score = float(row["relevance_score"] or 0)
                    # Defaults are only a fallback; relevance_service will recalc profile-driven status on next analysis.
                    if score >= high: status, relevant = "high", 1
                    elif score >= good: status, relevant = "good", 1
                    elif score >= relevant_min: status, relevant = "possible", 1
                    elif score >= low: status, relevant = "low", 0
                    else: status, relevant = "filtered", 0
                conn.execute(
                    '''UPDATE job_relevance_analysis SET duplicate_of_company_id=NULL,duplicate_of_external_id=NULL,
                       relevance_status=?,is_relevant=?,filter_reason=CASE WHEN filter_reason LIKE 'near_duplicate:%' THEN NULL ELSE filter_reason END
                       WHERE company_id=? AND external_id=?''',
                    (status, relevant, row["company_id"], row["external_id"]),
                )

    def mark_duplicate(self, company_id: str, external_id: str, canonical_company_id: str, canonical_external_id: str):
        with _connect() as conn:
            conn.execute(
                '''UPDATE job_relevance_analysis SET relevance_status='duplicate',is_relevant=0,
                   filter_reason=?,duplicate_of_company_id=?,duplicate_of_external_id=?
                   WHERE company_id=? AND external_id=?''',
                (f"near_duplicate:{canonical_company_id}/{canonical_external_id}", canonical_company_id,
                 canonical_external_id, company_id, external_id),
            )

    def rows_for_dedup(self):
        with _connect() as conn:
            return conn.execute(
                '''SELECT j.*, a.relevance_score,a.relevance_status,a.hard_filtered,a.is_relevant
                   FROM jobs j JOIN job_relevance_analysis a
                   ON a.company_id=j.company_id AND a.external_id=j.external_id
                   WHERE j.active=1 AND a.hard_filtered=0
                   ORDER BY j.company_id,a.relevance_score DESC,j.first_seen_at ASC'''
            ).fetchall()

    def stats(self) -> dict[str, Any]:
        with _connect() as conn:
            total_active = int(conn.execute("SELECT COUNT(*) FROM jobs WHERE active=1").fetchone()[0])
            analyzed = int(conn.execute(
                '''SELECT COUNT(*) FROM job_relevance_analysis a JOIN jobs j
                   ON j.company_id=a.company_id AND j.external_id=a.external_id WHERE j.active=1'''
            ).fetchone()[0])
            rows = conn.execute(
                '''SELECT a.relevance_status,COUNT(*) n FROM job_relevance_analysis a JOIN jobs j
                   ON j.company_id=a.company_id AND j.external_id=a.external_id
                   WHERE j.active=1 GROUP BY a.relevance_status'''
            ).fetchall()
            relevant_count = int(conn.execute(
                '''SELECT COUNT(*) FROM job_relevance_analysis a JOIN jobs j
                   ON j.company_id=a.company_id AND j.external_id=a.external_id
                   WHERE j.active=1 AND a.is_relevant=1'''
            ).fetchone()[0])
            relevant_new_changed = int(conn.execute(
                '''SELECT COUNT(*) FROM job_relevance_analysis a JOIN jobs j
                   ON j.company_id=a.company_id AND j.external_id=a.external_id
                   WHERE j.active=1 AND a.is_relevant=1 AND a.change_type IN ('new','changed')'''
            ).fetchone()[0])
            new_changed = int(conn.execute(
                '''SELECT COUNT(*) FROM job_relevance_analysis a JOIN jobs j
                   ON j.company_id=a.company_id AND j.external_id=a.external_id
                   WHERE j.active=1 AND a.change_type IN ('new','changed')'''
            ).fetchone()[0])
            filter_rows = conn.execute(
                '''SELECT COALESCE(a.filter_reason,'unknown') reason,COUNT(*) n
                   FROM job_relevance_analysis a JOIN jobs j
                   ON j.company_id=a.company_id AND j.external_id=a.external_id
                   WHERE j.active=1 AND a.relevance_status='filtered'
                   GROUP BY COALESCE(a.filter_reason,'unknown') ORDER BY n DESC'''
            ).fetchall()
        counts = {r["relevance_status"]: int(r["n"]) for r in rows}
        filter_reasons = {r["reason"]: int(r["n"]) for r in filter_rows}
        return {
            "active_jobs": total_active,
            "analyzed": analyzed,
            "pending": max(0, total_active-analyzed),
            "relevant_jobs": relevant_count,
            "relevant_new_changed": relevant_new_changed,
            "new_changed": new_changed,
            "statuses": counts,
            "filter_reasons": filter_reasons,
        }

    def pending_jobs(self, recompute_all: bool = False):
        with _connect() as conn:
            if recompute_all:
                return conn.execute("SELECT * FROM jobs WHERE active=1 ORDER BY company_name,title").fetchall()
            return conn.execute(
                '''SELECT j.* FROM jobs j LEFT JOIN job_relevance_analysis a
                   ON a.company_id=j.company_id AND a.external_id=j.external_id
                   WHERE j.active=1 AND (a.company_id IS NULL OR a.source_hash IS NULL)
                   ORDER BY j.company_name,j.title'''
            ).fetchall()

    def active_jobs_with_analysis(self):
        with _connect() as conn:
            return conn.execute(
                '''SELECT j.*,a.source_hash,a.change_type,a.role_family,a.role_label,a.normalized_location,
                          a.min_experience,a.max_experience,a.experience_text,a.role_score,a.experience_score,
                          a.primary_skill_score,a.supporting_score,a.relevance_score,a.relevance_status,a.is_relevant,
                          a.hard_filtered,a.filter_reason,a.matched_primary_json,a.matched_supporting_json,
                          a.score_breakdown_json,a.duplicate_of_company_id,a.duplicate_of_external_id,a.analyzed_at
                   FROM jobs j LEFT JOIN job_relevance_analysis a
                   ON a.company_id=j.company_id AND a.external_id=j.external_id
                   WHERE j.active=1 ORDER BY COALESCE(a.relevance_score,-1) DESC,j.company_name,j.title'''
            ).fetchall()

    def search(
        self, *, query: str = "", company_id: str = "", status: str = "", family: str = "",
        change_type: str = "", relevant_only: bool = False, min_score: float | None = None,
        page: int = 1, page_size: int = 50,
    ) -> dict[str, Any]:
        clauses = ["j.active=1", "a.company_id IS NOT NULL"]
        args: list[Any] = []
        if query:
            like = f"%{query.strip()}%"
            clauses.append("(j.title LIKE ? OR j.description LIKE ? OR j.company_name LIKE ? OR j.location LIKE ?)")
            args.extend([like, like, like, like])
        if company_id:
            clauses.append("j.company_id=?"); args.append(company_id)
        if status:
            clauses.append("a.relevance_status=?"); args.append(status)
        if family:
            clauses.append("a.role_family=?"); args.append(family)
        if change_type:
            clauses.append("a.change_type=?"); args.append(change_type)
        if relevant_only:
            clauses.append("a.is_relevant=1")
        if min_score is not None:
            clauses.append("a.relevance_score>=?"); args.append(float(min_score))
        where = " WHERE " + " AND ".join(clauses)
        with _connect() as conn:
            total = int(conn.execute(
                f'''SELECT COUNT(*) FROM jobs j JOIN job_relevance_analysis a
                    ON a.company_id=j.company_id AND a.external_id=j.external_id {where}''', args
            ).fetchone()[0])
            page = max(1, int(page)); page_size = max(1, min(200, int(page_size)))
            offset = (page-1)*page_size
            rows = conn.execute(
                f'''SELECT j.*,a.change_type,a.role_family,a.role_label,a.normalized_location,a.min_experience,
                           a.max_experience,a.relevance_score,a.relevance_status,a.is_relevant,a.filter_reason,
                           a.role_score,a.experience_score,a.primary_skill_score,a.supporting_score,
                           a.matched_primary_json,a.matched_supporting_json,a.score_breakdown_json,
                           a.duplicate_of_company_id,a.duplicate_of_external_id,a.analyzed_at
                    FROM jobs j JOIN job_relevance_analysis a
                    ON a.company_id=j.company_id AND a.external_id=j.external_id {where}
                    ORDER BY a.is_relevant DESC,a.relevance_score DESC,j.company_name,j.title LIMIT ? OFFSET ?''',
                [*args, page_size, offset],
            ).fetchall()
        return {"rows": rows, "total": total, "page": page, "page_size": page_size,
                "pages": max(1, (total+page_size-1)//page_size)}

    def analysis_for_job(self, company_id: str, external_id: str) -> dict[str, Any] | None:
        row = self.get(company_id, external_id)
        if not row:
            return None
        for key in ("matched_primary_json", "matched_supporting_json", "score_breakdown_json"):
            raw = row.pop(key, None)
            try:
                row[key.removesuffix("_json")] = json.loads(raw or ("{}" if key == "score_breakdown_json" else "[]"))
            except Exception:
                row[key.removesuffix("_json")] = {} if key == "score_breakdown_json" else []
        return row
