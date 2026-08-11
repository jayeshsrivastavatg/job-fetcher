from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from job_fetcher.india_location import LOCATION_RULESET_VERSION, is_india_job
from job_fetcher.profile import load_profile
from job_fetcher.storage import ROOT, _connect

RUN_REPORTS_ROOT = ROOT / "reports" / "runs"
LATEST_REPORTS_ROOT = ROOT / "reports" / "latest"
DELIVERY_SCHEMA_VERSION = 3


def _json_text(payload: dict[str, Any]) -> str:
    return json.dumps(payload, indent=2, ensure_ascii=False) + "\n"


def _sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _target_location_ok(job: dict[str, Any], normalized_location: str | None, profile: dict[str, Any]) -> bool:
    target = str((profile.get("candidate") or {}).get("targetCountry") or "").strip().lower()
    if not target:
        return True
    if target == "india":
        # The frozen normalized location contains the result of the deterministic
        # relevance classifier, including India evidence that may originally have
        # come only from structured ATS metadata. Historical job snapshots do not
        # intentionally retain bulky raw ATS JSON, so preserve that already-frozen
        # positive signal before re-checking the display/JD text.
        if is_india_job(normalized_location):
            return True
        return is_india_job(
            job.get("location"),
            description=job.get("description"),
            raw=job.get("raw") or job.get("raw_json"),
        )
    return target in str(normalized_location or "").lower()


def _artifact_payload(text: str | None) -> dict[str, Any]:
    try:
        value = json.loads(text or "{}")
        return value if isinstance(value, dict) else {}
    except Exception:
        return {}


def _prior_delivery_exists(conn, run) -> bool:
    rows = conn.execute(
        '''SELECT ra.content_text
           FROM run_artifacts ra JOIN runs r ON r.id=ra.run_id
           WHERE ra.kind='ai_input' AND ra.run_id<>? AND r.run_type='fetch'
             AND (r.created_at < ? OR (r.created_at = ? AND r.id < ?))
           ORDER BY r.created_at,r.id''',
        (run["id"], run["created_at"], run["created_at"], run["id"]),
    ).fetchall()
    for row in rows:
        payload = _artifact_payload(row["content_text"])
        if payload.get("delivery_mode") in {"baseline", "incremental"}:
            return True
    return False


def _is_latest_artifact_run(conn, run_id: str) -> bool:
    row = conn.execute(
        '''SELECT ra.run_id FROM run_artifacts ra JOIN runs r ON r.id=ra.run_id
           WHERE ra.kind='ai_input' AND r.run_type='fetch'
           ORDER BY r.created_at DESC,r.id DESC LIMIT 1'''
    ).fetchone()
    return bool(row and row["run_id"] == run_id)


def ensure_delivery_artifact(run_id: str) -> dict[str, Any] | None:
    """Upgrade/finalize one frozen run into the App-2 delivery contract.

    The first deliverable is a baseline containing every relevant India job observed
    in that frozen run. Later deliverables are incremental and only contain NEW or
    CHANGED relevant India jobs. Existing artifacts are regenerated from frozen run
    snapshots when the delivery/location ruleset changes, so no refetch is required.
    """
    profile = load_profile()
    with _connect() as conn:
        run = conn.execute("SELECT * FROM runs WHERE id=?", (run_id,)).fetchone()
        artifact = conn.execute(
            "SELECT * FROM run_artifacts WHERE run_id=? AND kind='ai_input'", (run_id,)
        ).fetchone()
        if not run or not artifact:
            return None

        existing_payload = _artifact_payload(artifact["content_text"])
        if (
            existing_payload.get("schema_version", 0) >= DELIVERY_SCHEMA_VERSION
            and existing_payload.get("delivery_mode") in {"baseline", "incremental"}
            and existing_payload.get("location_ruleset") == LOCATION_RULESET_VERSION
        ):
            return dict(artifact)

        mode = "incremental" if _prior_delivery_exists(conn, run) else "baseline"
        rows = conn.execute(
            '''SELECT s.*,v.snapshot_json
               FROM run_job_snapshots s
               JOIN job_versions v ON v.company_id=s.company_id
                 AND v.external_id=s.external_id AND v.content_hash=s.content_hash
               WHERE s.run_id=?
               ORDER BY COALESCE(s.relevance_score,-1) DESC,s.company_name,s.title''',
            (run_id,),
        ).fetchall()
        summary_row = conn.execute(
            "SELECT * FROM run_history_summary WHERE run_id=?", (run_id,)
        ).fetchone()

        baseline_relevant = 0
        new_relevant = 0
        changed_relevant = 0
        ai_jobs: list[dict[str, Any]] = []
        for row in rows:
            job = json.loads(row["snapshot_json"])
            relevant = bool(row["is_relevant"])
            target_location = _target_location_ok(job, row["normalized_location"], profile)
            observed = bool(row["observed"])
            event_type = str(row["event_type"])
            eligible_baseline = observed and relevant and target_location
            if eligible_baseline:
                baseline_relevant += 1
            if event_type == "new" and relevant and target_location:
                new_relevant += 1
            if event_type == "changed" and relevant and target_location:
                changed_relevant += 1

            include = eligible_baseline if mode == "baseline" else (
                event_type in {"new", "changed"} and relevant and target_location
            )
            if not include:
                continue

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

        base_summary = dict(summary_row) if summary_row else {}
        summary_payload = {
            "snapshot_jobs": int(base_summary.get("snapshot_jobs") or sum(bool(r["observed"]) for r in rows)),
            "jobs_new": int(base_summary.get("jobs_new") or 0),
            "jobs_changed": int(base_summary.get("jobs_changed") or 0),
            "jobs_unchanged": int(base_summary.get("jobs_unchanged") or 0),
            "jobs_closed": int(base_summary.get("jobs_closed") or 0),
            "baseline_relevant": baseline_relevant,
            "new_relevant": new_relevant,
            "changed_relevant": changed_relevant,
            "ai_input_count": len(ai_jobs),
        }
        generated_at = artifact["generated_at"]
        target_country = (profile.get("candidate") or {}).get("targetCountry")
        ai_payload = {
            "schema_version": DELIVERY_SCHEMA_VERSION,
            "delivery_mode": mode,
            "location_ruleset": LOCATION_RULESET_VERSION,
            "run_id": run_id,
            "generated_at": generated_at,
            "target_country": target_country,
            "summary": summary_payload,
            "jobs": ai_jobs,
        }
        ai_text = _json_text(ai_payload)
        ai_sha = _sha256(ai_text)
        manifest_payload = {
            "schema_version": DELIVERY_SCHEMA_VERSION,
            "delivery_mode": mode,
            "location_ruleset": LOCATION_RULESET_VERSION,
            "run_id": run_id,
            "run_created_at": run["created_at"],
            "run_started_at": run["started_at"],
            "generated_at": generated_at,
            "target_country": target_country,
            **summary_payload,
            "ai_input_filename": "ai_input.json",
            "ai_input_sha256": ai_sha,
        }
        manifest_text = _json_text(manifest_payload)
        manifest_sha = _sha256(manifest_text)

        conn.execute(
            '''UPDATE run_artifacts
               SET content_text=?,sha256=?,downloaded_at=NULL,git_prepared_at=NULL
               WHERE run_id=? AND kind='ai_input' ''',
            (ai_text, ai_sha, run_id),
        )
        manifest = conn.execute(
            "SELECT 1 FROM run_artifacts WHERE run_id=? AND kind='manifest'", (run_id,)
        ).fetchone()
        if manifest:
            conn.execute(
                '''UPDATE run_artifacts
                   SET content_text=?,sha256=?,downloaded_at=NULL,git_prepared_at=NULL
                   WHERE run_id=? AND kind='manifest' ''',
                (manifest_text, manifest_sha, run_id),
            )
        else:
            conn.execute(
                '''INSERT INTO run_artifacts(run_id,kind,filename,content_text,sha256,generated_at)
                   VALUES (?,?,?,?,?,?)''',
                (run_id, "manifest", "manifest.json", manifest_text, manifest_sha, generated_at),
            )
        conn.execute(
            '''UPDATE run_history_summary
               SET new_relevant=?,changed_relevant=?,ai_input_count=?
               WHERE run_id=?''',
            (new_relevant, changed_relevant, len(ai_jobs), run_id),
        )
        latest = _is_latest_artifact_run(conn, run_id)

    run_dir = RUN_REPORTS_ROOT / run_id
    run_dir.mkdir(parents=True, exist_ok=True)
    (run_dir / "ai_input.json").write_text(ai_text, encoding="utf-8")
    (run_dir / "manifest.json").write_text(manifest_text, encoding="utf-8")
    if latest:
        LATEST_REPORTS_ROOT.mkdir(parents=True, exist_ok=True)
        (LATEST_REPORTS_ROOT / "ai_input.json").write_text(ai_text, encoding="utf-8")
        (LATEST_REPORTS_ROOT / "manifest.json").write_text(manifest_text, encoding="utf-8")

    with _connect() as conn:
        row = conn.execute(
            "SELECT * FROM run_artifacts WHERE run_id=? AND kind='ai_input'", (run_id,)
        ).fetchone()
    return dict(row) if row else None


def ensure_all_delivery_artifacts() -> None:
    """Migrate artifacts in chronological order so only the first is baseline."""
    with _connect() as conn:
        rows = conn.execute(
            '''SELECT ra.run_id,ra.content_text FROM run_artifacts ra JOIN runs r ON r.id=ra.run_id
               WHERE ra.kind='ai_input' AND r.run_type='fetch'
               ORDER BY r.created_at,r.id'''
        ).fetchall()
    for row in rows:
        payload = _artifact_payload(row["content_text"])
        if (
            payload.get("schema_version", 0) < DELIVERY_SCHEMA_VERSION
            or payload.get("delivery_mode") not in {"baseline", "incremental"}
            or payload.get("location_ruleset") != LOCATION_RULESET_VERSION
        ):
            ensure_delivery_artifact(str(row["run_id"]))