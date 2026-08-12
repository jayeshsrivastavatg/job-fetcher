from __future__ import annotations

import json
import re
import sys
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path

from bs4 import BeautifulSoup

from job_fetcher.config import load_config
from job_fetcher.job_quality import prefer_usable_jobs
from job_fetcher.service import classify_error
from job_fetcher.sources.factory import build_source
from job_fetcher.sources.http_client import session, timeout_seconds
from job_fetcher.sources.phase2_exact import AtlassianListingsApiSource


def utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


def clean(value) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()


def uber_official_snapshot() -> dict:
    endpoint = "https://jobs.uber.com/api/jobs/search/"
    page_size = 100
    first = session().get(endpoint, params={"page": 1, "pagesize": page_size}, timeout=timeout_seconds(), headers={"Accept": "application/json"})
    first.raise_for_status()
    payload = first.json()
    total_pages = int(payload.get("totalPages") or 0)
    total_jobs = int(payload.get("totalJobs") or 0)
    rows = list(payload.get("jobs") or [])
    page_counts = [len(rows)]
    for page in range(2, total_pages + 1):
        response = session().get(endpoint, params={"page": page, "pagesize": page_size}, timeout=timeout_seconds(), headers={"Accept": "application/json"})
        response.raise_for_status()
        data = response.json()
        if int(data.get("page") or page) != page:
            raise RuntimeError(f"uber_official_unexpected_page:{page}")
        rows.extend(data.get("jobs") or [])
        page_counts.append(len(data.get("jobs") or []))
    by_id = {clean(row.get("Id")): row for row in rows if isinstance(row, dict) and clean(row.get("Id"))}
    if len(by_id) < max(0, total_jobs - 2):
        raise RuntimeError(f"uber_official_incomplete:{len(by_id)}/{total_jobs}")
    return {"source": endpoint, "total_reported": total_jobs, "total_pages": total_pages, "page_counts": page_counts, "ids": set(by_id), "rows": by_id}


def atlassian_official_snapshot() -> dict:
    endpoint = "https://www.atlassian.com/endpoint/careers/listings"
    response = session().get(endpoint, timeout=timeout_seconds(), headers={"Accept": "application/json"})
    response.raise_for_status()
    payload = response.json()
    if not isinstance(payload, list):
        raise RuntimeError("atlassian_official_invalid_payload")
    rows = [row for row in payload if isinstance(row, dict)]
    unique = AtlassianListingsApiSource.unique_rows(rows)
    return {
        "source": endpoint,
        "raw_rows": len(rows),
        "unique_vacancies": len(unique),
        "duplicate_ui_rows": len(rows) - len(unique),
        "ids": set(unique),
        "rows": unique,
    }


def app_snapshot(company: dict) -> dict:
    source = build_source(deepcopy(company))
    jobs = list(prefer_usable_jobs(source.fetch(deepcopy(company))) or [])
    ids = {clean(getattr(job, "external_id", None)) for job in jobs if clean(getattr(job, "external_id", None))}
    return {"adapter": type(source).__name__, "count": len(jobs), "ids": ids, "jobs": jobs}


def detail_evidence(job) -> dict:
    return {
        "id": clean(getattr(job, "external_id", None)),
        "title": clean(getattr(job, "title", None)),
        "location": clean(getattr(job, "location", None)),
        "url": clean(getattr(job, "job_url", None)),
        "has_description": bool(clean(getattr(job, "description", None))),
    }


def verify_uber(company: dict) -> dict:
    before = uber_official_snapshot()
    app = app_snapshot(company)
    after = uber_official_snapshot()
    stable = before["ids"] & after["ids"]
    missing = stable - app["ids"]
    invalid_urls = [detail_evidence(job) for job in app["jobs"] if not re.fullmatch(r"https://jobs\.uber\.com/en/jobs/\d+/", clean(getattr(job, "job_url", None)))]
    invalid_ids = [detail_evidence(job) for job in app["jobs"] if not clean(getattr(job, "external_id", None)).isdigit()]
    passed = not missing and not invalid_urls and not invalid_ids
    return {
        "company": "Uber", "verdict": "CERTIFIED" if passed else "FAILED", "passed": passed,
        "official_source": before["source"], "official_before": len(before["ids"]), "official_after": len(after["ids"]),
        "stable_current_jobs_checked": len(stable), "app_jobs": app["count"], "missing_count": len(missing),
        "missing_ids": sorted(missing)[:100], "extra_count": len(app["ids"] - stable),
        "pagination_exhausted": len(before["page_counts"]) == before["total_pages"], "page_counts": before["page_counts"],
        "invalid_url_records": invalid_urls[:20], "invalid_id_records": invalid_ids[:20], "adapter": app["adapter"],
        "sample": [detail_evidence(job) for job in app["jobs"][:5]],
    }


def verify_atlassian(company: dict) -> dict:
    before = atlassian_official_snapshot()
    app = app_snapshot(company)
    after = atlassian_official_snapshot()
    stable = before["ids"] & after["ids"]
    missing = stable - app["ids"]
    invalid_urls = [detail_evidence(job) for job in app["jobs"] if not re.fullmatch(r"https://www\.atlassian\.com/company/careers/details/\d+", clean(getattr(job, "job_url", None)))]
    invalid_ids = [detail_evidence(job) for job in app["jobs"] if not re.fullmatch(r"[^:]+:\d+", clean(getattr(job, "external_id", None)))]
    passed = not missing and not invalid_urls and not invalid_ids
    return {
        "company": "Atlassian", "verdict": "CERTIFIED" if passed else "FAILED", "passed": passed,
        "official_source": before["source"], "official_raw_rows_before": before["raw_rows"],
        "official_unique_before": len(before["ids"]), "official_unique_after": len(after["ids"]),
        "duplicate_ui_rows_before": before["duplicate_ui_rows"], "stable_current_jobs_checked": len(stable),
        "app_jobs": app["count"], "missing_count": len(missing), "missing_ids": sorted(missing)[:100],
        "extra_count": len(app["ids"] - stable), "pagination_exhausted": True,
        "invalid_url_records": invalid_urls[:20], "invalid_id_records": invalid_ids[:20], "adapter": app["adapter"],
        "sample": [detail_evidence(job) for job in app["jobs"][:5]],
    }


def freshteam_zero_jobs() -> dict:
    url = "https://navi.freshteam.com/jobs"
    response = session().get(url, timeout=timeout_seconds(), allow_redirects=True)
    response.raise_for_status()
    soup = BeautifulSoup(response.text, "html.parser")
    og = soup.find("meta", attrs={"property": "og:description"})
    description = clean(og.get("content") if og else "")
    body = clean(soup.get_text(" ", strip=True))
    zero = bool(re.search(r"#\s*0\s+Jobs", description, re.I)) or "No jobs found" in body
    return {"url": url, "status": response.status_code, "description": description, "zero_jobs": zero}


def verify_navi(company: dict) -> dict:
    source = build_source(deepcopy(company))
    blocked_category = None
    blocked_error = None
    emitted_jobs = None
    try:
        emitted_jobs = list(source.fetch(deepcopy(company)) or [])
    except Exception as exc:
        blocked_category = classify_error(exc)
        blocked_error = f"{type(exc).__name__}: {exc}"

    branded_url = "https://navi.com/careers"
    branded = session().get(branded_url, timeout=timeout_seconds(), allow_redirects=True)
    restricted = int(branded.status_code) in {401, 403, 429}
    freshteam = freshteam_zero_jobs()
    passed = emitted_jobs is None and blocked_category == "manual_or_approved_feed_required" and restricted and freshteam["zero_jobs"]
    return {
        "company": "Navi", "verdict": "BLOCKED" if passed else "FAILED", "passed": passed,
        "adapter": type(source).__name__, "official_branded_url": branded_url,
        "official_branded_http_status": branded.status_code, "official_access_restricted": restricted,
        "old_public_board": freshteam, "production_error_category": blocked_category, "production_error": blocked_error,
        "production_emitted_jobs": None if emitted_jobs is None else len(emitted_jobs),
        "reason": "No approved enumerable first-party vacancy feed can currently be certified; fail closed instead of publishing guessed careers-page links.",
    }


def main() -> int:
    companies = {c["id"]: c for c in load_config().get("companies", [])}
    rows = []
    for cid, verifier in (("uber", verify_uber), ("atlassian", verify_atlassian), ("navi", verify_navi)):
        row = verifier(companies[cid])
        rows.append(row)
        print(f"{row['company']}: verdict={row['verdict']} passed={row['passed']} missing={row.get('missing_count', 0)} app={row.get('app_jobs', row.get('production_emitted_jobs'))}", flush=True)
        for missing in row.get("missing_ids", [])[:20]:
            print(f"  MISSING {missing}", flush=True)

    payload = {
        "generated_at": utcnow(),
        "rule": "Every current vacancy from the official employer careers inventory must exist in production output. Extras are allowed. Repeated rows for the same stable requisition are one vacancy. If no approved enumerable source exists, fail closed and report BLOCKED.",
        "companies": rows,
        "passed": all(row["passed"] for row in rows),
    }
    out = Path("reports/phase2-exact-production.json")
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    return 0 if payload["passed"] else 1


if __name__ == "__main__":
    sys.exit(main())
