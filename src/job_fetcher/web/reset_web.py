from __future__ import annotations

import shutil
from urllib.parse import parse_qs

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from job_fetcher.run_history import GIT_HISTORY_ROOT, LATEST_REPORTS_ROOT, RUN_REPORTS_ROOT
from job_fetcher.storage import ROOT, RunStore, _connect
from job_fetcher.web.app import TEMPLATES, _base_context


router = APIRouter()
RESET_PHRASE = "DELETE ALL JOBS"

# Delete child tables before their parents so the reset remains safe with
# foreign-key enforcement enabled. The legacy analysis table is included for
# older local databases that were upgraded in place.
_RESET_TABLES = (
    "run_artifacts",
    "run_history_summary",
    "run_job_snapshots",
    "job_versions",
    "run_company_results",
    "job_candidate_analysis",
    "job_relevance_analysis",
    "runs",
    "jobs",
)


def _table_exists(conn, table: str) -> bool:
    return conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)
    ).fetchone() is not None


def _reset_counts() -> dict[str, int]:
    with _connect() as conn:
        def count(table: str) -> int:
            if not _table_exists(conn, table):
                return 0
            return int(conn.execute(f'SELECT COUNT(*) FROM "{table}"').fetchone()[0])

        return {
            "jobs": count("jobs"),
            "relevance": count("job_relevance_analysis"),
            "runs": count("runs"),
            "snapshots": count("run_job_snapshots"),
            "artifacts": count("run_artifacts"),
        }


def _remove_generated_job_artifacts() -> None:
    # These locations are generated from the database and can be rebuilt. Do not
    # touch config/, data/profile.json, company configuration, settings, or health
    # reports that are independent of the job inventory.
    for path in (RUN_REPORTS_ROOT, LATEST_REPORTS_ROOT, GIT_HISTORY_ROOT, ROOT / "reports" / "daily"):
        if path.exists():
            shutil.rmtree(path, ignore_errors=True)

    for path in (ROOT / "reports" / "relevant_jobs.csv", ROOT / "reports" / "relevant_jobs.json"):
        try:
            path.unlink()
        except FileNotFoundError:
            pass


def _clear_all_job_data() -> dict[str, int]:
    counts = _reset_counts()
    with _connect() as conn:
        existing = {
            str(row[0])
            for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
        }
        for table in _RESET_TABLES:
            if table in existing:
                conn.execute(f'DELETE FROM "{table}"')
    _remove_generated_job_artifacts()
    return counts


def _confirmation_value(body: bytes) -> str:
    try:
        parsed = parse_qs(body.decode("utf-8"), keep_blank_values=True)
    except UnicodeDecodeError:
        return ""
    return str((parsed.get("confirmation") or [""])[0]).strip()


@router.get("/jobs/reset", response_class=HTMLResponse)
def reset_jobs_page(request: Request):
    return TEMPLATES.TemplateResponse(
        request,
        "reset_jobs.html",
        _base_context(
            request,
            reset_phrase=RESET_PHRASE,
            reset_counts=_reset_counts(),
            reset_error=None,
        ),
    )


@router.post("/jobs/reset", response_class=HTMLResponse)
async def reset_jobs(request: Request):
    # Never clear state while a fetch/verification worker may still be writing it.
    active = RunStore().active_run()
    if active:
        return TEMPLATES.TemplateResponse(
            request,
            "reset_jobs.html",
            _base_context(
                request,
                reset_phrase=RESET_PHRASE,
                reset_counts=_reset_counts(),
                reset_error="A fetch or verification run is still active. Wait for it to finish before resetting job data.",
            ),
            status_code=409,
        )

    confirmation = _confirmation_value(await request.body())
    if confirmation != RESET_PHRASE:
        return TEMPLATES.TemplateResponse(
            request,
            "reset_jobs.html",
            _base_context(
                request,
                reset_phrase=RESET_PHRASE,
                reset_counts=_reset_counts(),
                reset_error=f'Type {RESET_PHRASE} exactly to confirm the reset.',
            ),
            status_code=400,
        )

    cleared = _clear_all_job_data()
    total = int(cleared.get("jobs", 0))
    return RedirectResponse(f"/jobs?reset=1&cleared={total}", status_code=303)
