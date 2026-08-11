from __future__ import annotations

import os
import socket
from concurrent.futures import ThreadPoolExecutor, as_completed

import requests

from job_fetcher.sources.factory import build_source
from job_fetcher.storage import JobStore


def classify_error(e):
    text = f"{type(e).__name__}: {e}"
    low = text.lower()
    if isinstance(e, (requests.exceptions.ConnectionError, socket.gaierror)) or any(x in low for x in (
        "nameresolutionerror", "name or service not known", "temporary failure in name resolution", "failed to resolve"
    )):
        return "network_dns"
    if isinstance(e, requests.exceptions.Timeout) or "timeout" in low or "timed out" in low:
        return "network_timeout"
    if "401" in low or "authentication required" in low:
        return "authentication_required"
    if "403" in low or "access denied" in low:
        return "http_403_or_waf"
    if "429" in low:
        return "rate_limited"
    if any(x in low for x in ("captcha", "anti_bot", "verify you are human", "bot challenge")):
        return "anti_bot_or_captcha"
    if "automation_disallowed_or_unavailable" in low:
        return "manual_or_approved_feed_required"
    if "browser_fallback_disabled" in low:
        return "needs_browser_or_provider_adapter"
    if "no_jobs_detected" in low:
        return "unsupported_or_empty_layout"
    if "playwright" in low and ("executable" in low or "browser" in low):
        return "browser_runtime_missing"
    return "fetch_or_parse_error"



def _discovered_endpoints(jobs):
    endpoints = set()
    for job in jobs or []:
        raw = job.raw if isinstance(job.raw, dict) else {}
        url = raw.get("_source_response_url")
        if url:
            endpoints.add(str(url))
    return sorted(endpoints)[:50]


def _fetch_one(c):
    try:
        jobs = build_source(c).fetch(c)
        return c, jobs, None
    except Exception as e:
        return c, None, e


def probe_company(company, browser=True):
    """Fetch one company without writing to the DB; useful after adding/updating config."""
    old = os.environ.get("JOB_FETCHER_DISABLE_BROWSER")
    if not browser:
        os.environ["JOB_FETCHER_DISABLE_BROWSER"] = "1"
    try:
        c, jobs, e = _fetch_one(company)
        if e is None:
            return {
                "id": c["id"], "name": c["name"], "career_url": c["career_url"],
                "status": "success", "jobs_detected": len(jobs), "category": None, "error": None,
                "source_types": sorted({j.source_type for j in jobs}),
                "discovered_endpoints": _discovered_endpoints(jobs),
            }
        return {
            "id": c.get("id"), "name": c.get("name"), "career_url": c.get("career_url"),
            "status": "failed", "jobs_detected": 0, "category": classify_error(e),
            "error": f"{type(e).__name__}: {e}", "source_types": [],
        }
    finally:
        if old is None:
            os.environ.pop("JOB_FETCHER_DISABLE_BROWSER", None)
        else:
            os.environ["JOB_FETCHER_DISABLE_BROWSER"] = old


def fetch_companies(companies, max_workers=4):
    companies = [c for c in companies if c.get("enabled", True)]
    result = {"success": [], "failed": []}
    if not companies:
        return result

    # HTTP/native work can be concurrent. Browser fallback has its own smaller semaphore,
    # so high --workers values do not spawn an unsafe number of Chromium instances.
    workers = max(1, min(int(max_workers or 1), len(companies)))
    fetched = []
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = [pool.submit(_fetch_one, c) for c in companies]
        for fut in as_completed(futures):
            fetched.append(fut.result())

    store = JobStore()
    for c, jobs, e in fetched:
        if e is None:
            new, old = store.upsert_many(jobs)
            result["success"].append({
                "id": c["id"], "name": c["name"], "fetched": len(jobs), "new": new, "existing": old,
                "source_types": sorted({j.source_type for j in jobs}),
                "discovered_endpoints": _discovered_endpoints(jobs),
            })
        else:
            result["failed"].append({
                "id": c.get("id", "?"), "name": c.get("name", c.get("id", "?")),
                "category": classify_error(e), "error": f"{type(e).__name__}: {e}",
            })
    rank = {c["id"]: c.get("rank", 10**9) for c in companies}
    result["success"].sort(key=lambda x: rank.get(x["id"], 10**9))
    result["failed"].sort(key=lambda x: rank.get(x["id"], 10**9))
    return result


def jobs_used_browser(jobs) -> bool:
    for job in jobs or []:
        if str(getattr(job, "source_type", "")).startswith("browser_"):
            return True
        raw = job.raw if isinstance(getattr(job, "raw", None), dict) else {}
        if raw.get("_fetch_via_browser") or raw.get("_source_response_url"):
            return True
    return False


def fetch_companies_detailed(
    companies,
    max_workers=4,
    *,
    drop_threshold: float = 0.80,
    on_result=None,
    on_snapshot=None,
):
    """Fetch enabled companies with lifecycle-safe snapshot semantics.

    Unlike the legacy CLI helper, this method emits one normalized result per
    company and will not deactivate historical jobs when a run looks incomplete
    (zero jobs or a >drop_threshold count collapse).

    `on_snapshot`, when supplied, receives `(result_row, jobs)` after the jobs have
    been persisted. It exists so run-history code can record the exact set returned
    by the provider without bloating the normal run result payload with thousands
    of job IDs.
    """
    import time
    from job_fetcher.sources.factory import build_source

    companies = [c for c in companies if c.get("enabled", True)]
    result = {"companies": []}
    if not companies:
        return result

    def worker(c):
        started = time.perf_counter()
        adapter_obj = None
        try:
            adapter_obj = build_source(c)
            jobs = list(adapter_obj.fetch(c) or [])
            return c, jobs, None, round(time.perf_counter() - started, 3), type(adapter_obj).__name__
        except Exception as exc:
            adapter = type(adapter_obj).__name__ if adapter_obj is not None else (c.get("source") or {}).get("type", "unknown")
            return c, None, exc, round(time.perf_counter() - started, 3), adapter

    workers = max(1, min(int(max_workers or 1), len(companies)))
    store = JobStore()
    try:
        with ThreadPoolExecutor(max_workers=workers) as pool:
            futures = {pool.submit(worker, c): c for c in companies}
            for future in as_completed(futures):
                c, jobs, exc, duration, adapter = future.result()
                configured_source = (c.get("source") or {}).get("type", "auto")
                if exc is not None:
                    row = {
                        "id": c.get("id"), "name": c.get("name"), "rank": c.get("rank"),
                        "status": "failed", "adapter": adapter, "configured_source": configured_source,
                        "jobs_found": 0, "new_jobs": 0, "existing_jobs": 0,
                        "previous_jobs_found": store.company_active_count(c.get("id")),
                        "count_change_pct": None, "browser_used": False,
                        "failure_category": classify_error(exc), "error": f"{type(exc).__name__}: {exc}",
                        "source_types": [], "discovered_endpoints": [], "duration_seconds": duration,
                        "snapshot_complete": False,
                    }
                else:
                    jobs = list(jobs or [])
                    previous = store.company_active_count(c["id"])
                    n = len(jobs)
                    allow_zero = bool((c.get("source") or {}).get("allow_zero_jobs"))
                    change_pct = round(((n - previous) / previous) * 100.0, 2) if previous > 0 else None
                    suspicious_drop = previous > 0 and n < previous * (1.0 - drop_threshold)
                    browser_used = jobs_used_browser(jobs)
                    complete = (n > 0 and not suspicious_drop) or (n == 0 and allow_zero)
                    if n == 0 and allow_zero:
                        status = "healthy"
                        category = error = None
                    elif n == 0:
                        status = "failed"
                        category = "zero_jobs_detected"
                        error = "Source completed without an exception but produced zero jobs"
                    elif suspicious_drop:
                        status = "suspicious"
                        category = "large_job_count_drop"
                        error = f"Job count dropped from {previous} to {n} ({change_pct}%)"
                    else:
                        status = "healthy_with_fallback" if browser_used else "healthy"
                        category = error = None
                    new, existing, deactivated = store.upsert_snapshot(c["id"], jobs, complete=complete)
                    row = {
                        "id": c["id"], "name": c["name"], "rank": c.get("rank"),
                        "status": status, "adapter": adapter, "configured_source": configured_source,
                        "jobs_found": n, "new_jobs": new, "existing_jobs": existing,
                        "deactivated_jobs": deactivated, "snapshot_complete": complete,
                        "previous_jobs_found": previous, "count_change_pct": change_pct,
                        "browser_used": browser_used, "failure_category": category, "error": error,
                        "source_types": sorted({j.source_type for j in jobs}),
                        "discovered_endpoints": _discovered_endpoints(jobs), "duration_seconds": duration,
                    }
                result["companies"].append(row)
                if on_result is not None:
                    on_result(dict(row))
                if on_snapshot is not None:
                    on_snapshot(dict(row), list(jobs or []) if exc is None else [])
    finally:
        store.close()

    rank = {c["id"]: c.get("rank", 10**9) for c in companies}
    result["companies"].sort(key=lambda x: rank.get(x["id"], 10**9))
    return result
