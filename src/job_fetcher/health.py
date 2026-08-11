from __future__ import annotations

import csv
import json
import os
import re
import shutil
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import requests

from job_fetcher.job_quality import prefer_usable_jobs, valid_http_url
from job_fetcher.service import _discovered_endpoints, classify_error
from job_fetcher.sources.factory import build_source
from job_fetcher.sources.http_client import session

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_REPORT_DIR = ROOT / "reports"


@dataclass
class HealthRow:
    id: str
    name: str
    rank: int | None
    career_url: str
    adapter: str
    configured_source: str
    status: str
    failure_category: str | None
    error: str | None
    jobs_found: int
    previous_jobs_found: int | None
    count_change_pct: float | None
    browser_used: bool
    discovered_endpoints: list[str]
    source_types: list[str]
    titles_valid: int
    urls_valid: int
    quality_ratio: float
    sample_job_url: str | None
    sample_detail_reachable: bool | None
    sample_detail_status_code: int | None
    sample_detail_error: str | None
    duration_seconds: float

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _browser_used(jobs) -> bool:
    for job in jobs or []:
        if str(getattr(job, "source_type", "")).startswith("browser_"):
            return True
        raw = job.raw if isinstance(getattr(job, "raw", None), dict) else {}
        if raw.get("_fetch_via_browser"):
            return True
        if raw.get("_source_response_url"):
            # XHR URLs are produced by the browser network-capture fallback in
            # this project unless a provider explicitly supplies them.
            return True
    return False


def _sample_detail(jobs, timeout: float) -> tuple[str | None, bool | None, int | None, str | None]:
    urls = [j.job_url for j in jobs if valid_http_url(getattr(j, "job_url", None))]
    if not urls:
        return None, None, None, "no_valid_job_url"

    url = urls[0]
    try:
        # GET is more compatible than HEAD across ATS/CDN/WAF setups. Keep the
        # body small because this is only an existence check.
        r = session().get(url, timeout=timeout, allow_redirects=True, stream=True)
        code = int(getattr(r, "status_code", 0) or 0)
        ok = 200 <= code < 400
        r.close()
        return url, ok, code, None if ok else f"HTTP {code}"
    except Exception as exc:
        text = f"{type(exc).__name__}: {exc}"
        # urllib3/requests retry exhaustion often hides the final HTTP status in
        # the exception text instead of exposing a Response. Preserve 401/403/429
        # so health classification can distinguish an access block from a dead URL.
        wrapped_code = None
        match = re.search(r"\b(401|403|429)\b", text)
        if match:
            wrapped_code = int(match.group(1))
        return url, False, wrapped_code, text


def _previous_counts(path: Path) -> dict[str, int]:
    if not path.exists():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    rows = payload.get("companies") if isinstance(payload, dict) else None
    if not isinstance(rows, list):
        return {}
    out = {}
    for row in rows:
        if isinstance(row, dict) and row.get("id") and isinstance(row.get("jobs_found"), int):
            out[str(row["id"])] = int(row["jobs_found"])
    return out


def verify_company(company, previous_count: int | None, drop_threshold: float, validate_detail: bool, detail_timeout: float) -> HealthRow:
    started = time.perf_counter()
    adapter_obj = build_source(company)
    adapter = type(adapter_obj).__name__
    jobs = None
    error = None
    category = None
    try:
        jobs = prefer_usable_jobs(adapter_obj.fetch(company))
    except Exception as exc:
        error = f"{type(exc).__name__}: {exc}"
        category = classify_error(exc)

    elapsed = round(time.perf_counter() - started, 3)
    if error is not None:
        return HealthRow(
            id=company["id"], name=company["name"], rank=company.get("rank"),
            career_url=company.get("career_url", ""), adapter=adapter,
            configured_source=(company.get("source") or {}).get("type", "auto"),
            status="failed", failure_category=category, error=error,
            jobs_found=0, previous_jobs_found=previous_count,
            count_change_pct=(-100.0 if previous_count else None), browser_used=False,
            discovered_endpoints=[], source_types=[], titles_valid=0, urls_valid=0,
            quality_ratio=0.0, sample_job_url=None, sample_detail_reachable=None,
            sample_detail_status_code=None, sample_detail_error=None,
            duration_seconds=elapsed,
        )

    jobs = list(jobs or [])
    n = len(jobs)
    titles_valid = sum(bool((getattr(j, "title", None) or "").strip()) for j in jobs)
    urls_valid = sum(valid_http_url(getattr(j, "job_url", None)) for j in jobs)
    quality_ratio = round((min(titles_valid, urls_valid) / n) if n else 0.0, 4)
    browser_used = _browser_used(jobs)
    endpoints = _discovered_endpoints(jobs)
    source_types = sorted({str(getattr(j, "source_type", "")) for j in jobs if getattr(j, "source_type", None)})

    change_pct = None
    if previous_count is not None and previous_count > 0:
        change_pct = round(((n - previous_count) / previous_count) * 100.0, 2)

    sample_url = sample_ok = sample_code = sample_error = None
    if validate_detail and n:
        sample_url, sample_ok, sample_code, sample_error = _sample_detail(jobs, detail_timeout)

    status = "healthy_with_fallback" if browser_used else "healthy"
    category = None
    error = None

    allow_zero = bool((company.get("source") or {}).get("allow_zero_jobs"))
    if n == 0 and not allow_zero:
        status = "failed"
        category = "zero_jobs_detected"
        error = "Source completed without an exception but produced zero jobs"
    elif n == 0 and allow_zero:
        status = "healthy"
        category = None
        error = None
    elif quality_ratio < 0.90:
        status = "suspicious"
        category = "low_job_record_quality"
        error = f"Only {quality_ratio:.0%} of records contain both a title and valid http(s) job URL"
    elif previous_count is not None and previous_count > 0 and n < previous_count * (1.0 - drop_threshold):
        status = "suspicious"
        category = "large_job_count_drop"
        error = f"Job count dropped from {previous_count} to {n} ({change_pct}%)"
    elif validate_detail and sample_ok is False and sample_code in {401, 403, 429}:
        # The listing data is structurally usable, but the one-off validator was
        # denied by the site's WAF/rate limit. This is not evidence that the job
        # URL is malformed, so distinguish it from a genuinely dead detail link.
        status = "healthy_with_fallback"
        category = "sample_detail_access_restricted"
        error = f"HTTP {sample_code}; listing accepted, direct validation was blocked"
    elif validate_detail and sample_ok is False:
        status = "suspicious"
        category = "sample_job_detail_unreachable"
        error = sample_error

    return HealthRow(
        id=company["id"], name=company["name"], rank=company.get("rank"),
        career_url=company.get("career_url", ""), adapter=adapter,
        configured_source=(company.get("source") or {}).get("type", "auto"),
        status=status, failure_category=category, error=error,
        jobs_found=n, previous_jobs_found=previous_count, count_change_pct=change_pct,
        browser_used=browser_used, discovered_endpoints=endpoints, source_types=source_types,
        titles_valid=titles_valid, urls_valid=urls_valid, quality_ratio=quality_ratio,
        sample_job_url=sample_url, sample_detail_reachable=sample_ok,
        sample_detail_status_code=sample_code, sample_detail_error=sample_error,
        duration_seconds=elapsed,
    )


def _summary(rows: list[HealthRow], configured: int, enabled: int, disabled: int) -> dict[str, Any]:
    counts = {k: 0 for k in ("healthy", "healthy_with_fallback", "suspicious", "failed")}
    categories: dict[str, int] = {}
    total_jobs = 0
    for row in rows:
        counts[row.status] = counts.get(row.status, 0) + 1
        total_jobs += row.jobs_found
        if row.failure_category:
            categories[row.failure_category] = categories.get(row.failure_category, 0) + 1

    network_dns = categories.get("network_dns", 0)
    environment_blocked = bool(rows) and network_dns >= max(1, int(len(rows) * 0.8))
    return {
        "configured": configured,
        "enabled": enabled,
        "disabled": disabled,
        "verified": len(rows),
        **counts,
        "total_jobs_detected": total_jobs,
        "failure_categories": dict(sorted(categories.items())),
        "environment_blocked": environment_blocked,
    }


def _write_csv(path: Path, rows: list[HealthRow]):
    path.parent.mkdir(parents=True, exist_ok=True)
    fields = [
        "rank", "id", "name", "status", "adapter", "configured_source", "jobs_found",
        "previous_jobs_found", "count_change_pct", "browser_used", "quality_ratio",
        "titles_valid", "urls_valid", "sample_detail_reachable", "sample_detail_status_code",
        "failure_category", "error", "career_url", "sample_job_url", "discovered_endpoints",
        "source_types", "duration_seconds",
    ]
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fields)
        writer.writeheader()
        for row in rows:
            d = row.to_dict()
            d["discovered_endpoints"] = " | ".join(d["discovered_endpoints"])
            d["source_types"] = " | ".join(d["source_types"])
            writer.writerow({k: d.get(k) for k in fields})


def verify_all(companies: list[dict], *, max_workers: int = 4, output_dir: str | Path = DEFAULT_REPORT_DIR,
               browser: bool = True, drop_threshold: float = 0.80, validate_detail: bool = True,
               detail_timeout: float = 15.0, on_result=None, previous_counts: dict[str, int] | None = None,
               write_reports: bool = True) -> dict[str, Any]:
    """Verify all enabled companies without writing jobs to SQLite.

    A prior reports/company_health.json is used as the count baseline. The new
    report is archived under reports/history before becoming the next baseline.
    """
    configured = len(companies)
    enabled_rows = [c for c in companies if c.get("enabled", True)]
    disabled = configured - len(enabled_rows)
    out_dir = Path(output_dir)
    if not out_dir.is_absolute():
        out_dir = ROOT / out_dir
    out_dir.mkdir(parents=True, exist_ok=True)
    json_path = out_dir / "company_health.json"
    csv_path = out_dir / "company_health.csv"
    baseline_path = out_dir / "company_health_baseline.json"
    # Prefer a durable last-good baseline. For users upgrading from an earlier
    # version, fall back to the previous health report once.
    previous = dict(previous_counts) if previous_counts is not None else (_previous_counts(baseline_path) or _previous_counts(json_path))

    old_disable = os.environ.get("JOB_FETCHER_DISABLE_BROWSER")
    if not browser:
        os.environ["JOB_FETCHER_DISABLE_BROWSER"] = "1"

    rows: list[HealthRow] = []
    workers = max(1, min(int(max_workers or 1), max(1, len(enabled_rows))))
    try:
        with ThreadPoolExecutor(max_workers=workers) as pool:
            futures = {
                pool.submit(
                    verify_company, c, previous.get(c["id"]), drop_threshold,
                    validate_detail, detail_timeout,
                ): c for c in enabled_rows
            }
            for future in as_completed(futures):
                row = future.result()
                rows.append(row)
                if on_result is not None:
                    on_result(row.to_dict())
    finally:
        if not browser:
            if old_disable is None:
                os.environ.pop("JOB_FETCHER_DISABLE_BROWSER", None)
            else:
                os.environ["JOB_FETCHER_DISABLE_BROWSER"] = old_disable

    rank = {c["id"]: c.get("rank", 10**9) for c in enabled_rows}
    rows.sort(key=lambda x: rank.get(x.id, 10**9))
    generated = datetime.now(timezone.utc).isoformat()
    payload = {
        "generated_at": generated,
        "settings": {
            "browser_enabled": browser,
            "workers": workers,
            "drop_threshold": drop_threshold,
            "validate_sample_detail": validate_detail,
            "detail_timeout_seconds": detail_timeout,
        },
        "summary": _summary(rows, configured, len(enabled_rows), disabled),
        "companies": [r.to_dict() for r in rows],
    }

    if write_reports:
        # Preserve the prior baseline before replacing it.
        if json_path.exists():
            hist = out_dir / "history"
            hist.mkdir(parents=True, exist_ok=True)
            stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
            shutil.copy2(json_path, hist / f"company_health_{stamp}.json")

        json_path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
        _write_csv(csv_path, rows)

        # Keep the last successful non-zero count per company. A DNS outage, WAF
        # incident or parser failure must not erase the last known-good baseline.
        baseline_counts = dict(previous)
        if not payload["summary"].get("environment_blocked"):
            for row in rows:
                if row.jobs_found > 0 and row.status in {"healthy", "healthy_with_fallback", "suspicious"}:
                    baseline_counts[row.id] = row.jobs_found
        baseline_payload = {
            "generated_at": generated,
            "companies": [
                {"id": cid, "jobs_found": count}
                for cid, count in sorted(baseline_counts.items())
            ],
        }
        baseline_path.write_text(json.dumps(baseline_payload, indent=2), encoding="utf-8")
    return payload
