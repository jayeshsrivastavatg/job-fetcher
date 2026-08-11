from __future__ import annotations

import json
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from urllib.parse import quote, urlencode

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from job_fetcher.company_admin import create_company, set_company_enabled, source_config_json, source_schema, update_company
from job_fetcher.config import find_company, load_config
from job_fetcher.run_manager import RunConflict, get_manager
from job_fetcher.settings import load_settings, save_settings
from job_fetcher.storage import JobStore, RunStore, RelevanceStore, ROOT
from job_fetcher.relevance_service import analyze_relevance, relevance_stats
from job_fetcher.profile import load_profile

WEB_ROOT = Path(__file__).resolve().parent
TEMPLATES = Jinja2Templates(directory=str(WEB_ROOT / "templates"))
TEMPLATES.env.filters["urlquote"] = lambda v: quote(str(v), safe="")
TEMPLATES.env.filters["jsonpretty"] = lambda v: json.dumps(v, indent=2, ensure_ascii=False)

STATUS_ORDER = {
    "failed": 0,
    "suspicious": 1,
    "healthy_with_fallback": 2,
    "healthy": 3,
    "disabled": 4,
    "never_verified": 5,
}


def _now_utc() -> datetime:
    return datetime.now(timezone.utc)


def _date_since(days: int | None) -> str:
    if not days:
        return ""
    return (_now_utc() - timedelta(days=days)).date().isoformat()


def _optional_float(value: str | None, field_name: str) -> float | None:
    raw = (value or "").strip()
    if not raw:
        return None
    try:
        return float(raw)
    except ValueError as exc:
        raise HTTPException(400, f"{field_name} must be a number") from exc


def _optional_iso_date(value: str | None, field_name: str) -> str:
    raw = (value or "").strip()
    if not raw:
        return ""
    try:
        datetime.strptime(raw, "%Y-%m-%d")
    except ValueError as exc:
        raise HTTPException(400, f"{field_name} must use YYYY-MM-DD") from exc
    return raw


def _result_dict(row):
    if not row:
        return None
    d = dict(row)
    raw = d.pop("payload_json", None)
    try:
        d["payload"] = json.loads(raw or "{}")
    except Exception:
        d["payload"] = {}
    return d


def _base_context(request: Request, **extra):
    run_store = RunStore()
    active = run_store.active_run()
    ctx = {
        "request": request,
        "active_run": dict(active) if active else None,
        "path": request.url.path,
        "source_schema": source_schema(),
    }
    ctx.update(extra)
    return ctx


def _company_state():
    companies = sorted(load_config()["companies"], key=lambda c: (c.get("rank", 10**9), c["name"]))
    job_store = JobStore()
    try:
        counts = job_store.company_counts()
    finally:
        job_store.close()
    run_store = RunStore()
    health = run_store.latest_results_by_company("verify")
    fetch = run_store.latest_results_by_company("fetch")
    rows = []
    for c in companies:
        enabled = c.get("enabled", True)
        h = health.get(c["id"])
        f = fetch.get(c["id"])
        if not enabled:
            health_status = "disabled"
        elif h:
            health_status = h["status"]
        else:
            health_status = "never_verified"
        rows.append({
            **c,
            "resolved_health": health_status,
            "health_result": h,
            "fetch_result": f,
            "active_jobs": counts.get(c["id"], {}).get("active", 0),
            "total_jobs": counts.get(c["id"], {}).get("total", 0),
        })
    return rows


@asynccontextmanager
async def lifespan(app: FastAPI):
    get_manager().startup()
    yield


app = FastAPI(title="Job Fetcher", lifespan=lifespan)
app.mount("/static", StaticFiles(directory=str(WEB_ROOT / "static")), name="static")


@app.get("/", response_class=HTMLResponse)
def dashboard(request: Request):
    companies = _company_state()
    enabled = [c for c in companies if c.get("enabled", True)]
    job_store = JobStore()
    try:
        active_jobs = job_store.active_total()
    finally:
        job_store.close()
    run_store = RunStore()
    latest_fetch = run_store.latest_run("fetch")
    latest_verify = run_store.latest_run("verify")
    health_counts = {k: 0 for k in ("healthy", "healthy_with_fallback", "suspicious", "failed", "never_verified")}
    for c in enabled:
        health_counts[c["resolved_health"]] = health_counts.get(c["resolved_health"], 0) + 1
    issues = [c for c in enabled if c["resolved_health"] in {"failed", "suspicious", "healthy_with_fallback"}]
    issues.sort(key=lambda c: (STATUS_ORDER.get(c["resolved_health"], 99), c.get("rank", 10**9)))
    return TEMPLATES.TemplateResponse(request, "dashboard.html", _base_context(
        request,
        companies_count=len(companies),
        enabled_count=len(enabled),
        active_jobs=active_jobs,
        relevance_summary=relevance_stats(),
        health_counts=health_counts,
        issues=issues[:8],
        latest_fetch=dict(latest_fetch) if latest_fetch else None,
        latest_verify=dict(latest_verify) if latest_verify else None,
    ))


@app.get("/companies", response_class=HTMLResponse)
def companies_page(request: Request, q: str = "", enabled: str = "all", health: str = "all", source: str = "all"):
    rows = _company_state()
    ql = q.strip().lower()
    if ql:
        rows = [c for c in rows if ql in c["name"].lower() or ql in c["id"].lower()]
    if enabled == "enabled":
        rows = [c for c in rows if c.get("enabled", True)]
    elif enabled == "disabled":
        rows = [c for c in rows if not c.get("enabled", True)]
    if health != "all":
        rows = [c for c in rows if c["resolved_health"] == health]
    if source != "all":
        rows = [c for c in rows if (c.get("source") or {}).get("type") == source]
    all_rows = _company_state()
    sources = sorted({(c.get("source") or {}).get("type", "auto") for c in all_rows})
    return TEMPLATES.TemplateResponse(request, "companies.html", _base_context(
        request,
        companies=rows,
        total_companies=len(all_rows),
        enabled_count=sum(c.get("enabled", True) for c in all_rows),
        sources=sources,
        filters={"q": q, "enabled": enabled, "health": health, "source": source},
    ))


@app.get("/companies/{company_id}", response_class=HTMLResponse)
def company_detail(request: Request, company_id: str):
    data = load_config()
    company = find_company(data, company_id)
    if not company:
        raise HTTPException(404, "Company not found")
    run_store = RunStore()
    latest_health = run_store.latest_company_result(company_id, "verify")
    latest_fetch = run_store.latest_company_result(company_id, "fetch")
    health_history = run_store.company_history(company_id, "verify", limit=12)
    job_store = JobStore()
    try:
        jobs = job_store.company_jobs(company_id, limit=12, active_only=True)
        active_jobs = job_store.company_active_count(company_id)
    finally:
        job_store.close()
    return TEMPLATES.TemplateResponse(request, "company_detail.html", _base_context(
        request,
        company=company,
        latest_health=_result_dict(latest_health),
        latest_fetch=_result_dict(latest_fetch),
        health_history=[_result_dict(r) for r in health_history],
        jobs=[dict(r) for r in jobs],
        active_jobs=active_jobs,
        source_config_json=source_config_json(company),
    ))


@app.get("/jobs", response_class=HTMLResponse)
def jobs_page(
    request: Request,
    q: str = "",
    company: str = "",
    location: str = "",
    status: str = "active",
    posted_days: int | None = None,
    first_seen_days: int | None = None,
    page: int = 1,
):
    job_store = JobStore()
    try:
        result = job_store.search_jobs(
            query=q,
            company_id=company,
            location=location,
            active=status,
            posted_since=_date_since(posted_days),
            first_seen_since=_date_since(first_seen_days),
            page=page,
            page_size=50,
        )
        locations = job_store.distinct_locations()
    finally:
        job_store.close()
    companies = sorted(load_config()["companies"], key=lambda c: c["name"])
    base_params = {
        "q": q, "company": company, "location": location, "status": status,
        "posted_days": posted_days or "", "first_seen_days": first_seen_days or "",
    }
    base_params = {k: v for k, v in base_params.items() if v not in (None, "")}
    prev_url = "/jobs?" + urlencode({**base_params, "page": max(1, result["page"] - 1)})
    next_url = "/jobs?" + urlencode({**base_params, "page": result["page"] + 1})
    return TEMPLATES.TemplateResponse(request, "jobs.html", _base_context(
        request,
        jobs=[dict(r) for r in result["rows"]],
        result=result,
        companies=companies,
        locations=locations,
        filters={
            "q": q, "company": company, "location": location, "status": status,
            "posted_days": posted_days, "first_seen_days": first_seen_days,
        },
        prev_url=prev_url, next_url=next_url,
    ))


@app.get("/jobs/detail", response_class=HTMLResponse)
def job_detail(request: Request, company: str, external_id: str):
    job_store = JobStore()
    try:
        row = job_store.get_job(company, external_id)
    finally:
        job_store.close()
    if not row:
        raise HTTPException(404, "Job not found")
    analysis = RelevanceStore().analysis_for_job(company, external_id)
    return TEMPLATES.TemplateResponse(request, "job_detail.html", _base_context(request, job=dict(row), analysis=analysis))


@app.get("/candidates")
def legacy_candidates_redirect():
    return RedirectResponse("/relevance", status_code=307)


@app.get("/relevance", response_class=HTMLResponse)
def relevance_page(
    request: Request, q: str = "", company: str = "", status: str = "", family: str = "",
    change_type: str = "", relevant_only: bool = False, min_score: str = "",
    posted_since: str = "", first_seen_since: str = "", page: int = 1,
):
    # HTML forms submit empty number/date fields as empty strings. Parse them here
    # instead of asking FastAPI to coerce them before the route runs; otherwise a
    # harmless blank min-score field produces a 422 response.
    min_score_value = _optional_float(min_score, "min_score")
    posted_since_value = _optional_iso_date(posted_since, "posted_since")
    first_seen_since_value = _optional_iso_date(first_seen_since, "first_seen_since")

    store = RelevanceStore()
    result = store.search(
        query=q, company_id=company, status=status, family=family, change_type=change_type,
        relevant_only=relevant_only, min_score=min_score_value,
        posted_since=posted_since_value, first_seen_since=first_seen_since_value,
        page=page, page_size=50,
    )
    stats = store.stats()
    profile = load_profile()
    companies = sorted(load_config()["companies"], key=lambda c: c["name"])
    families = [(x.get("id"), x.get("label", x.get("id"))) for x in profile.get("roleFamilies") or []]
    families.append(("software_engineering_general", "General Software Engineering"))
    params = {
        "q": q, "company": company, "status": status, "family": family, "change_type": change_type,
        "relevant_only": "true" if relevant_only else "",
        "min_score": min_score_value if min_score_value is not None else "",
        "posted_since": posted_since_value, "first_seen_since": first_seen_since_value,
    }
    params = {k:v for k,v in params.items() if v not in (None, "", False)}
    prev_url = "/relevance?" + urlencode({**params, "page": max(1, result["page"]-1)})
    next_url = "/relevance?" + urlencode({**params, "page": result["page"]+1})
    return TEMPLATES.TemplateResponse(request, "relevance.html", _base_context(
        request, jobs=[dict(r) for r in result["rows"]], result=result, stats=stats,
        companies=companies, families=families, profile=profile,
        filters={
            "q":q,"company":company,"status":status,"family":family,"change_type":change_type,
            "relevant_only":relevant_only,"min_score":min_score_value,
            "posted_since":posted_since_value,"first_seen_since":first_seen_since_value,
        },
        prev_url=prev_url,next_url=next_url,
    ))


@app.get("/health", response_class=HTMLResponse)
def health_page(request: Request):
    rows = _company_state()
    enabled_rows = [r for r in rows if r.get("enabled", True)]
    counts = {k: 0 for k in ("healthy", "healthy_with_fallback", "suspicious", "failed", "never_verified")}
    for r in enabled_rows:
        counts[r["resolved_health"]] = counts.get(r["resolved_health"], 0) + 1
    ordered = sorted(enabled_rows, key=lambda r: (STATUS_ORDER.get(r["resolved_health"], 99), r.get("rank", 10**9)))
    return TEMPLATES.TemplateResponse(request, "health.html", _base_context(
        request, companies=ordered, health_counts=counts,
    ))


@app.get("/runs", response_class=HTMLResponse)
def runs_page(request: Request, kind: str = "fetch"):
    if kind not in {"fetch", "verify"}:
        kind = "fetch"
    run_store = RunStore()
    rows = [dict(r) for r in run_store.list_runs(kind, limit=100)]
    return TEMPLATES.TemplateResponse(request, "runs.html", _base_context(request, runs=rows, kind=kind))


@app.get("/runs/{run_id}", response_class=HTMLResponse)
def run_detail(request: Request, run_id: str):
    run = RunStore().get_run(run_id)
    if not run:
        raise HTTPException(404, "Run not found")
    return TEMPLATES.TemplateResponse(request, "run_detail.html", _base_context(request, run=run))


@app.get("/settings", response_class=HTMLResponse)
def settings_page(request: Request):
    return TEMPLATES.TemplateResponse(request, "settings.html", _base_context(request, settings=load_settings()))


@app.get("/api/runs/active")
def api_active_run():
    active = RunStore().active_run()
    return {"active": dict(active) if active else None}


@app.get("/api/runs/{run_id}")
def api_run(run_id: str):
    run = RunStore().get_run(run_id)
    if not run:
        raise HTTPException(404, "Run not found")
    return run


@app.post("/api/runs/fetch")
async def api_start_fetch(request: Request):
    payload = await request.json()
    company_ids = payload.get("company_ids") or None
    try:
        run_id = get_manager().start_fetch(company_ids)
    except RunConflict as exc:
        return JSONResponse({"error": str(exc), "run_id": exc.run_id}, status_code=409)
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    return {"run_id": run_id}


@app.post("/api/runs/verify")
async def api_start_verify(request: Request):
    payload = await request.json()
    company_ids = payload.get("company_ids") or None
    try:
        run_id = get_manager().start_verify(company_ids)
    except RunConflict as exc:
        return JSONResponse({"error": str(exc), "run_id": exc.run_id}, status_code=409)
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    return {"run_id": run_id}


@app.post("/api/companies")
async def api_create_company(request: Request):
    payload = await request.json()
    try:
        company = create_company(
            name=payload.get("name", ""),
            career_url=payload.get("career_url", ""),
            source_type=payload.get("source_type") or "auto",
            source_config=payload.get("source_config") or {},
            enabled=payload.get("enabled", True),
            company_id=payload.get("id") or None,
        )
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    run_id = None
    conflict_run_id = None
    if payload.get("verify", True) and company.get("enabled", True):
        try:
            run_id = get_manager().start_verify([company["id"]])
        except RunConflict as exc:
            conflict_run_id = exc.run_id
    return {"company": company, "run_id": run_id, "conflict_run_id": conflict_run_id}


@app.patch("/api/companies/{company_id}")
async def api_update_company(company_id: str, request: Request):
    payload = await request.json()
    try:
        row = update_company(
            company_id,
            name=payload.get("name") if "name" in payload else None,
            career_url=payload.get("career_url") if "career_url" in payload else None,
            source_type=payload.get("source_type") if "source_type" in payload else None,
            source_config=payload.get("source_config") if "source_config" in payload else None,
            enabled=payload.get("enabled") if "enabled" in payload else None,
            rank=payload.get("rank") if "rank" in payload else None,
        )
    except KeyError:
        raise HTTPException(404, "Company not found")
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    return {"company": row}


@app.post("/api/companies/{company_id}/enabled")
async def api_set_enabled(company_id: str, request: Request):
    payload = await request.json()
    try:
        row = set_company_enabled(company_id, bool(payload.get("enabled")))
    except KeyError:
        raise HTTPException(404, "Company not found")
    return {"company": row}


@app.post("/api/relevance/analyze")
async def api_analyze_relevance(request: Request):
    payload = await request.json()
    try:
        result = analyze_relevance(recompute_all=bool(payload.get("all", False)))
    except Exception as exc:
        raise HTTPException(400, f"{type(exc).__name__}: {exc}")
    return result


@app.put("/api/settings")
async def api_settings(request: Request):
    payload = await request.json()
    try:
        settings = save_settings(payload)
    except (ValueError, TypeError) as exc:
        raise HTTPException(400, str(exc))
    return {"settings": settings}


@app.get("/api/source-schema")
def api_source_schema():
    return source_schema()


@app.get("/favicon.ico", include_in_schema=False)
def favicon():
    return RedirectResponse(url="/static/favicon.svg", status_code=307)
