from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from job_fetcher.profile import load_profile
from job_fetcher.storage import ROOT, _connect, _job_content_hash, utcnow

REPORTS_ROOT = ROOT / "reports"
RUN_REPORTS_ROOT = REPORTS_ROOT / "runs"
LATEST_REPORTS_ROOT = REPORTS_ROOT / "latest"
GIT_HISTORY_ROOT = ROOT / "run-history"

HISTORY_SCHEMA = '''
CREATE TABLE IF NOT EXISTS job_versions (
  company_id TEXT NOT NULL,
  external_id TEXT NOT NULL,
  content_hash TEXT NOT NULL,
  snapshot_json TEXT NOT NULL,
  created_at TEXT NOT NULL,
  PRIMARY KEY(company_id, external_id, content_hash)
);

CREATE TABLE IF NOT EXISTS run_job_snapshots (
  run_id TEXT NOT NULL,
  company_id TEXT NOT NULL,
  external_id TEXT NOT NULL,
  content_hash TEXT NOT NULL,
  observed INTEGER NOT NULL DEFAULT 1,
  event_type TEXT NOT NULL,
  company_name TEXT,
  title TEXT,
  location TEXT,
  posted_at TEXT,
  first_seen_at TEXT,
  last_seen_at TEXT,
  job_url TEXT,
  normalized_location TEXT,
  role_family TEXT,
  role_label TEXT,
  min_experience REAL,
  max_experience REAL,
  relevance_score REAL,
  relevance_status TEXT,
  is_relevant INTEGER,
  created_at TEXT NOT NULL,
  PRIMARY KEY(run_id, company_id, external_id),
  FOREIGN KEY(run_id) REFERENCES runs(id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_run_job_snapshots_run_event
  ON run_job_snapshots(run_id, event_type, is_relevant, relevance_score DESC);
CREATE INDEX IF NOT EXISTS idx_run_job_snapshots_job
  ON run_job_snapshots(company_id, external_id, run_id);

CREATE TABLE IF NOT EXISTS run_history_summary (
  run_id TEXT PRIMARY KEY,
  snapshot_jobs INTEGER NOT NULL DEFAULT 0,
  jobs_new INTEGER NOT NULL DEFAULT 0,
  jobs_changed INTEGER NOT NULL DEFAULT 0,
  jobs_unchanged INTEGER NOT NULL DEFAULT 0,
  jobs_closed INTEGER NOT NULL DEFAULT 0,
  new_relevant INTEGER NOT NULL DEFAULT 0,
  changed_relevant INTEGER NOT NULL DEFAULT 0,
  ai_input_count INTEGER NOT NULL DEFAULT 0,
  generated_at TEXT,
  artifact_error TEXT,
  FOREIGN KEY(run_id) REFERENCES runs(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS run_artifacts (
  run_id TEXT NOT NULL,
  kind TEXT NOT NULL,
  filename TEXT NOT NULL,
  content_text TEXT NOT NULL,
  sha256 TEXT NOT NULL,
  generated_at TEXT NOT NULL,
  downloaded_at TEXT,
  git_prepared_at TEXT,
  PRIMARY KEY(run_id, kind),
  FOREIGN KEY(run_id) REFERENCES runs(id) ON DELETE CASCADE
);
'''


def _sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _json_text(payload: dict[str, Any]) -> str:
    return json.dumps(payload, indent=2, ensure_ascii=False) + "\n"


def _job_snapshot_from_row(row) -> dict[str, Any]:
    d = dict(row)
    return {
        "company_id": d.get("company_id"),
        "external_id": d.get("external_id"),
        "company_name": d.get("company_name"),
        "source_type": d.get("source_type"),
        "title": d.get("title"),
        "location": d.get("location"),
        "description": d.get("description"),
        "job_url": d.get("job_url"),
        "posted_at": d.get("posted_at"),
        "first_seen_at": d.get("first_seen_at"),
        "last_seen_at": d.get("last_seen_at"),
        "active": bool(d.get("active")),
        "content_hash": d.get("content_hash"),
    }


class RunHistoryStore:
    """Immutable per-run job membership, versions, relevance snapshots and artifacts.

    `jobs` remains the mutable/latest inventory. This store records which exact
    content version was observed in a fetch run, plus the relevance result that
    existed when the run was finalized. Historical AI files are persisted both
    in SQLite and as convenient files under reports/runs/.
    """

    def __init__(self):
        with _connect() as conn:
            conn.executescript(HISTORY_SCHEMA)

    @staticmethod
    def _ensure_version(conn, row) -> tuple[str, dict[str, Any]]:
        snapshot = _job_snapshot_from_row(row)
        content_hash = str(snapshot.get("content_hash") or _job_content_hash(
            snapshot.get("title"), snapshot.get("location"), snapshot.get("description")
        ))
        snapshot["content_hash"] = content_hash
        conn.execute(
            '''INSERT OR IGNORE INTO job_versions
               (company_id,external_id,content_hash,snapshot_json,created_at)
               VALUES (?,?,?,?,?)''',
            (
                snapshot["company_id"], snapshot["external_id"], content_hash,
                json.dumps(snapshot, ensure_ascii=False), utcnow(),
            ),
        )
        return content_hash, snapshot

    def capture_inventory(self, company_ids: list[str]) -> dict[str, dict[str, dict[str, Any]]]:
        """Capture active pre-run state and ensure every current content version exists."""
        before: dict[str, dict[str, dict[str, Any]]] = {}
        if not company_ids:
            return before
        with _connect() as conn:
            for company_id in company_ids:
                rows = conn.execute(
                    "SELECT * FROM jobs WHERE company_id=? AND active=1", (company_id,)
                ).fetchall()
                company_before: dict[str, dict[str, Any]] = {}
                for row in rows:
                    content_hash, snapshot = self._ensure_version(conn, row)
                    company_before[str(row["external_id"])] = {
                        "content_hash": content_hash,
                        "snapshot": snapshot,
                    }
                before[company_id] = company_before
        return before

    def record_company_snapshot(
        self,
        run_id: str,
        before_company: dict[str, dict[str, Any]],
        result: dict[str, Any],
        jobs: list[Any],
    ) -> None:
        """Record exact jobs returned by one company and derive NEW/CHANGED/CLOSED."""
        company_id = str(result.get("id") or "")
        if not company_id:
            return
        returned_ids = {str(job.stable_external_id()) for job in (jobs or [])}
        with _connect() as conn:
            rows = conn.execute("SELECT * FROM jobs WHERE company_id=?", (company_id,)).fetchall()
            current = {str(r["external_id"]): r for r in rows}

            for external_id in sorted(returned_ids):
                row = current.get(external_id)
                if row is None:
                    continue
                content_hash, snapshot = self._ensure_version(conn, row)
                previous = before_company.get(external_id)
                if previous is None:
                    event_type = "new"
                elif str(previous.get("content_hash")) != content_hash:
                    event_type = "changed"
                else:
                    event_type = "unchanged"
                self._insert_snapshot(
                    conn, run_id, event_type, observed=True,
                    snapshot=snapshot, content_hash=content_hash,
                )

            if bool(result.get("snapshot_complete")):
                for external_id in sorted(set(before_company) - returned_ids):
                    previous = before_company[external_id]
                    snapshot = dict(previous.get("snapshot") or {})
                    if not snapshot:
                        continue
                    self._insert_snapshot(
                        conn, run_id, "closed", observed=False,
                        snapshot=snapshot, content_hash=str(previous["content_hash"]),
                    )

    @staticmethod
    def _insert_snapshot(
        conn,
        run_id: str,
        event_type: str,
        *,
        observed: bool,
        snapshot: dict[str, Any],
        content_hash: str,
    ) -> None:
        conn.execute(
            '''INSERT OR REPLACE INTO run_job_snapshots
               (run_id,company_id,external_id,content_hash,observed,event_type,company_name,title,
                location,posted_at,first_seen_at,last_seen_at,job_url,created_at)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)''',
            (
                run_id, snapshot.get("company_id"), snapshot.get("external_id"), content_hash,
                1 if observed else 0, event_type, snapshot.get("company_name"), snapshot.get("title"),
                snapshot.get("location"), snapshot.get("posted_at"), snapshot.get("first_seen_at"),
                snapshot.get("last_seen_at"), snapshot.get("job_url"), utcnow(),
            ),
        )

    def mark_artifact_error(self, run_id: str, error: str) -> None:
        with _connect() as conn:
            conn.execute(
                '''INSERT INTO run_history_summary(run_id,artifact_error)
                   VALUES (?,?) ON CONFLICT(run_id) DO UPDATE SET artifact_error=excluded.artifact_error''',
                (run_id, error),
            )

    @staticmethod
    def _target_location_ok(normalized_location: str | None, profile: dict[str, Any]) -> bool:
        target = str((profile.get("candidate") or {}).get("targetCountry") or "").strip().lower()
        if not target:
            return True
        return target in str(normalized_location or "").lower()

    def finalize_run(self, run_id: str) -> dict[str, Any]:
        """Freeze relevance for all observed jobs and create immutable AI artifacts."""
        existing_artifact = self.get_artifact(run_id, "ai_input")
        existing_summary = self.summary(run_id)
        if existing_artifact and existing_summary:
            return existing_summary

        profile = load_profile()
        generated_at = utcnow()
        with _connect() as conn:
            snapshots = conn.execute(
                '''SELECT s.*,a.normalized_location,a.role_family,a.role_label,a.min_experience,
                          a.max_experience,a.relevance_score,a.relevance_status,a.is_relevant
                   FROM run_job_snapshots s
                   LEFT JOIN job_relevance_analysis a
                     ON a.company_id=s.company_id AND a.external_id=s.external_id
                   WHERE s.run_id=?''',
                (run_id,),
            ).fetchall()
            for row in snapshots:
                conn.execute(
                    '''UPDATE run_job_snapshots SET normalized_location=?,role_family=?,role_label=?,
                       min_experience=?,max_experience=?,relevance_score=?,relevance_status=?,is_relevant=?
                       WHERE run_id=? AND company_id=? AND external_id=?''',
                    (
                        row["normalized_location"], row["role_family"], row["role_label"],
                        row["min_experience"], row["max_experience"], row["relevance_score"],
                        row["relevance_status"], row["is_relevant"], run_id,
                        row["company_id"], row["external_id"],
                    ),
                )

            rows = conn.execute(
                '''SELECT s.*,v.snapshot_json FROM run_job_snapshots s
                   JOIN job_versions v ON v.company_id=s.company_id
                     AND v.external_id=s.external_id AND v.content_hash=s.content_hash
                   WHERE s.run_id=?
                   ORDER BY COALESCE(s.relevance_score,-1) DESC,s.company_name,s.title''',
                (run_id,),
            ).fetchall()
            run = conn.execute("SELECT * FROM runs WHERE id=?", (run_id,)).fetchone()

        counts = {k: 0 for k in ("new", "changed", "unchanged", "closed")}
        ai_jobs: list[dict[str, Any]] = []
        new_relevant = changed_relevant = 0
        for row in rows:
            event_type = str(row["event_type"])
            counts[event_type] = counts.get(event_type, 0) + 1
            relevant = bool(row["is_relevant"])
            target_location = self._target_location_ok(row["normalized_location"], profile)
            if event_type == "new" and relevant and target_location:
                new_relevant += 1
            if event_type == "changed" and relevant and target_location:
                changed_relevant += 1
            if event_type not in {"new", "changed"} or not relevant or not target_location:
                continue
            job = json.loads(row["snapshot_json"])
            job.update({
                "event_type": event_type,
                "normalized_location": row["normalized_location"],
                "role_family": row["role_family"],
                "role_label": row["role_label"],
                "min_experience": row["min_experience"],
                "max_experience": row["max_experience"],
                "relevance_score": row["relevance_score"],
                "relevance_status": row["relevance_status"],
                "is_relevant": True,
            })
            ai_jobs.append(job)

        summary_payload = {
            "snapshot_jobs": sum(1 for r in rows if bool(r["observed"])),
            "jobs_new": counts.get("new", 0),
            "jobs_changed": counts.get("changed", 0),
            "jobs_unchanged": counts.get("unchanged", 0),
            "jobs_closed": counts.get("closed", 0),
            "new_relevant": new_relevant,
            "changed_relevant": changed_relevant,
            "ai_input_count": len(ai_jobs),
        }
        ai_payload = {
            "schema_version": 1,
            "run_id": run_id,
            "generated_at": generated_at,
            "target_country": (profile.get("candidate") or {}).get("targetCountry"),
            "summary": summary_payload,
            "jobs": ai_jobs,
        }
        ai_text = _json_text(ai_payload)
        ai_sha = _sha256(ai_text)
        manifest_payload = {
            "schema_version": 1,
            "run_id": run_id,
            "run_created_at": run["created_at"] if run else None,
            "run_started_at": run["started_at"] if run else None,
            "generated_at": generated_at,
            **summary_payload,
            "ai_input_filename": "ai_input.json",
            "ai_input_sha256": ai_sha,
        }
        manifest_text = _json_text(manifest_payload)

        with _connect() as conn:
            conn.execute(
                '''INSERT INTO run_history_summary
                   (run_id,snapshot_jobs,jobs_new,jobs_changed,jobs_unchanged,jobs_closed,
                    new_relevant,changed_relevant,ai_input_count,generated_at,artifact_error)
                   VALUES (?,?,?,?,?,?,?,?,?,?,NULL)
                   ON CONFLICT(run_id) DO UPDATE SET
                     snapshot_jobs=excluded.snapshot_jobs,jobs_new=excluded.jobs_new,
                     jobs_changed=excluded.jobs_changed,jobs_unchanged=excluded.jobs_unchanged,
                     jobs_closed=excluded.jobs_closed,new_relevant=excluded.new_relevant,
                     changed_relevant=excluded.changed_relevant,ai_input_count=excluded.ai_input_count,
                     generated_at=excluded.generated_at,artifact_error=NULL''',
                (
                    run_id, summary_payload["snapshot_jobs"], summary_payload["jobs_new"],
                    summary_payload["jobs_changed"], summary_payload["jobs_unchanged"],
                    summary_payload["jobs_closed"], summary_payload["new_relevant"],
                    summary_payload["changed_relevant"], summary_payload["ai_input_count"], generated_at,
                ),
            )
            self._insert_artifact(conn, run_id, "ai_input", "ai_input.json", ai_text, generated_at)
            self._insert_artifact(conn, run_id, "manifest", "manifest.json", manifest_text, generated_at)

        self._write_runtime_files(run_id, ai_text, manifest_text)
        return self.summary(run_id) or summary_payload

    @staticmethod
    def _insert_artifact(conn, run_id: str, kind: str, filename: str, content: str, generated_at: str) -> None:
        existing = conn.execute(
            "SELECT sha256 FROM run_artifacts WHERE run_id=? AND kind=?", (run_id, kind)
        ).fetchone()
        digest = _sha256(content)
        if existing:
            if existing["sha256"] != digest:
                raise RuntimeError(f"Historical artifact differs for run {run_id}: {kind}")
            return
        conn.execute(
            '''INSERT INTO run_artifacts(run_id,kind,filename,content_text,sha256,generated_at)
               VALUES (?,?,?,?,?,?)''',
            (run_id, kind, filename, content, digest, generated_at),
        )

    @staticmethod
    def _write_runtime_files(run_id: str, ai_text: str, manifest_text: str) -> None:
        run_dir = RUN_REPORTS_ROOT / run_id
        run_dir.mkdir(parents=True, exist_ok=True)
        (run_dir / "ai_input.json").write_text(ai_text, encoding="utf-8")
        (run_dir / "manifest.json").write_text(manifest_text, encoding="utf-8")
        LATEST_REPORTS_ROOT.mkdir(parents=True, exist_ok=True)
        (LATEST_REPORTS_ROOT / "ai_input.json").write_text(ai_text, encoding="utf-8")
        (LATEST_REPORTS_ROOT / "manifest.json").write_text(manifest_text, encoding="utf-8")

    def summary(self, run_id: str) -> dict[str, Any] | None:
        with _connect() as conn:
            row = conn.execute("SELECT * FROM run_history_summary WHERE run_id=?", (run_id,)).fetchone()
        return dict(row) if row else None

    def get_artifact(self, run_id: str, kind: str = "ai_input") -> dict[str, Any] | None:
        with _connect() as conn:
            row = conn.execute(
                "SELECT * FROM run_artifacts WHERE run_id=? AND kind=?", (run_id, kind)
            ).fetchone()
        return dict(row) if row else None

    def mark_downloaded(self, run_id: str, kind: str = "ai_input") -> None:
        with _connect() as conn:
            conn.execute(
                "UPDATE run_artifacts SET downloaded_at=COALESCE(downloaded_at,?) WHERE run_id=? AND kind=?",
                (utcnow(), run_id, kind),
            )

    def search_run_jobs(
        self,
        run_id: str,
        *,
        query: str = "",
        company_id: str = "",
        event_type: str = "",
        relevant: str = "all",
        min_score: float | None = None,
        page: int = 1,
        page_size: int = 50,
    ) -> dict[str, Any]:
        clauses = ["s.run_id=?"]
        args: list[Any] = [run_id]
        if query:
            like = f"%{query.strip()}%"
            clauses.append("(s.title LIKE ? OR s.company_name LIKE ? OR s.location LIKE ?)")
            args.extend([like, like, like])
        if company_id:
            clauses.append("s.company_id=?"); args.append(company_id)
        if event_type:
            clauses.append("s.event_type=?"); args.append(event_type)
        if relevant == "yes":
            clauses.append("s.is_relevant=1")
        elif relevant == "no":
            clauses.append("COALESCE(s.is_relevant,0)=0")
        if min_score is not None:
            clauses.append("COALESCE(s.relevance_score,0)>=?"); args.append(float(min_score))
        where = " WHERE " + " AND ".join(clauses)
        page = max(1, int(page)); page_size = max(1, min(200, int(page_size)))
        offset = (page - 1) * page_size
        with _connect() as conn:
            total = int(conn.execute(f"SELECT COUNT(*) FROM run_job_snapshots s{where}", args).fetchone()[0])
            rows = conn.execute(
                f'''SELECT s.* FROM run_job_snapshots s{where}
                    ORDER BY CASE s.event_type WHEN 'new' THEN 0 WHEN 'changed' THEN 1
                             WHEN 'closed' THEN 2 ELSE 3 END,
                             COALESCE(s.relevance_score,-1) DESC,s.company_name,s.title
                    LIMIT ? OFFSET ?''',
                [*args, page_size, offset],
            ).fetchall()
        return {
            "rows": [dict(r) for r in rows], "total": total, "page": page, "page_size": page_size,
            "pages": max(1, (total + page_size - 1) // page_size),
        }

    def historical_job(self, run_id: str, company_id: str, external_id: str) -> dict[str, Any] | None:
        with _connect() as conn:
            row = conn.execute(
                '''SELECT s.*,v.snapshot_json FROM run_job_snapshots s
                   JOIN job_versions v ON v.company_id=s.company_id AND v.external_id=s.external_id
                     AND v.content_hash=s.content_hash
                   WHERE s.run_id=? AND s.company_id=? AND s.external_id=?''',
                (run_id, company_id, external_id),
            ).fetchone()
        if not row:
            return None
        result = json.loads(row["snapshot_json"])
        result.update({k: row[k] for k in (
            "run_id", "event_type", "observed", "normalized_location", "role_family", "role_label",
            "min_experience", "max_experience", "relevance_score", "relevance_status", "is_relevant",
        )})
        return result

    def companies_for_run(self, run_id: str) -> list[dict[str, str]]:
        with _connect() as conn:
            rows = conn.execute(
                '''SELECT company_id,MAX(company_name) company_name FROM run_job_snapshots
                   WHERE run_id=? GROUP BY company_id ORDER BY company_name''', (run_id,)
            ).fetchall()
        return [dict(r) for r in rows]

    def latest_artifact_run(self) -> str | None:
        with _connect() as conn:
            row = conn.execute(
                '''SELECT r.id FROM runs r JOIN run_artifacts a ON a.run_id=r.id AND a.kind='ai_input'
                   WHERE r.run_type='fetch' ORDER BY r.created_at DESC LIMIT 1'''
            ).fetchone()
        return str(row[0]) if row else None

    def prepare_git_copy(self, run_id: str) -> Path:
        ai = self.get_artifact(run_id, "ai_input")
        manifest = self.get_artifact(run_id, "manifest")
        if not ai or not manifest:
            raise FileNotFoundError(f"No finalized artifacts for run {run_id}")
        with _connect() as conn:
            run = conn.execute("SELECT created_at FROM runs WHERE id=?", (run_id,)).fetchone()
        date = str(run["created_at"] if run else utcnow())[:10]
        year, month = date[:4], date[5:7]
        target = GIT_HISTORY_ROOT / year / month / f"{date}_{run_id}"
        target.mkdir(parents=True, exist_ok=True)
        (target / "ai_input.json").write_text(ai["content_text"], encoding="utf-8")
        (target / "manifest.json").write_text(manifest["content_text"], encoding="utf-8")
        with _connect() as conn:
            conn.execute(
                "UPDATE run_artifacts SET git_prepared_at=COALESCE(git_prepared_at,?) WHERE run_id=?",
                (utcnow(), run_id),
            )
        return target
