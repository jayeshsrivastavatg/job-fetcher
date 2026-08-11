from __future__ import annotations

from urllib.parse import urlencode

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response

from job_fetcher.run_history import RunHistoryStore
from job_fetcher.storage import RunStore
from job_fetcher.web.app import TEMPLATES, _base_context

router = APIRouter()
history = RunHistoryStore()

# Existing dashboard/runs templates can query lightweight history metadata without
# changing their underlying FastAPI routes.
TEMPLATES.env.globals["run_history_summary"] = history.summary
TEMPLATES.env.globals["run_ai_artifact"] = lambda run_id: history.get_artifact(str(run_id), "ai_input")


def _optional_score(value: str) -> float | None:
    raw = (value or "").strip()
    if not raw:
        return None
    try:
        score = float(raw)
    except ValueError as exc:
        raise HTTPException(400, "min_score must be a number") from exc
    if not 0 <= score <= 100:
        raise HTTPException(400, "min_score must be between 0 and 100")
    return score


@router.get("/history/runs/{run_id}", response_class=HTMLResponse)
def history_run_detail(
    request: Request,
    run_id: str,
    q: str = "",
    company: str = "",
    event: str = "",
    relevant: str = "all",
    min_score: str = "",
    page: int = 1,
):
    run = RunStore().get_run(run_id)
    if not run:
        raise HTTPException(404, "Run not found")
    if run["run_type"] != "fetch":
        return RedirectResponse(f"/runs/{run_id}", status_code=307)

    min_score_value = _optional_score(min_score)
    if event not in {"", "new", "changed", "unchanged", "closed"}:
        event = ""
    if relevant not in {"all", "yes", "no"}:
        relevant = "all"
    result = history.search_run_jobs(
        run_id,
        query=q,
        company_id=company,
        event_type=event,
        relevant=relevant,
        min_score=min_score_value,
        page=page,
        page_size=50,
    )
    summary = history.summary(run_id)
    artifact = history.get_artifact(run_id, "ai_input")
    companies = history.companies_for_run(run_id)
    params = {
        "q": q, "company": company, "event": event,
        "relevant": relevant if relevant != "all" else "",
        "min_score": min_score_value if min_score_value is not None else "",
    }
    params = {k: v for k, v in params.items() if v not in (None, "")}
    prev_url = f"/history/runs/{run_id}?" + urlencode({**params, "page": max(1, result["page"] - 1)})
    next_url = f"/history/runs/{run_id}?" + urlencode({**params, "page": result["page"] + 1})
    return TEMPLATES.TemplateResponse(request, "run_history_detail.html", _base_context(
        request,
        run=run,
        summary=summary,
        artifact=artifact,
        jobs=result["rows"],
        result=result,
        companies=companies,
        filters={
            "q": q, "company": company, "event": event,
            "relevant": relevant, "min_score": min_score_value,
        },
        prev_url=prev_url,
        next_url=next_url,
    ))


@router.get("/history/job", response_class=HTMLResponse)
def historical_job_detail(request: Request, run_id: str, company: str, external_id: str):
    run = RunStore().get_run(run_id)
    if not run:
        raise HTTPException(404, "Run not found")
    job = history.historical_job(run_id, company, external_id)
    if not job:
        raise HTTPException(404, "Historical job snapshot not found")
    return TEMPLATES.TemplateResponse(request, "run_job_detail.html", _base_context(
        request, run=run, job=job,
    ))


def _download(run_id: str, kind: str) -> Response:
    artifact = history.get_artifact(run_id, kind)
    if not artifact:
        raise HTTPException(404, "This run predates immutable artifacts or artifact generation failed")
    history.mark_downloaded(run_id, kind)
    filename = f"job-fetcher-{kind.replace('_','-')}-{run_id}.json"
    return Response(
        content=artifact["content_text"],
        media_type="application/json",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.get("/history/runs/{run_id}/download/ai-input")
def download_ai_input(run_id: str):
    return _download(run_id, "ai_input")


@router.get("/history/runs/{run_id}/download/manifest")
def download_manifest(run_id: str):
    return _download(run_id, "manifest")


@router.get("/history/latest/ai-input")
def download_latest_ai_input():
    run_id = history.latest_artifact_run()
    if not run_id:
        raise HTTPException(404, "No finalized AI-input artifact exists yet")
    return _download(run_id, "ai_input")


@router.post("/history/runs/{run_id}/prepare-github")
def prepare_github_copy(run_id: str):
    run = RunStore().get_run(run_id)
    if not run:
        raise HTTPException(404, "Run not found")
    try:
        history.prepare_git_copy(run_id)
    except FileNotFoundError as exc:
        raise HTTPException(404, str(exc)) from exc
    return RedirectResponse(f"/history/runs/{run_id}?git_prepared=1", status_code=303)
