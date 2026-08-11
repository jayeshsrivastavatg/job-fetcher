from __future__ import annotations

import argparse
import json
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

import requests

from job_fetcher.config import load_config
from job_fetcher.job_quality import plausible_job, prefer_usable_jobs, strong_job_detail_url, valid_http_url
from job_fetcher.service import jobs_used_browser
from job_fetcher.sources.factory import build_source


TRUSTED_CONFIGURED_SOURCES = {
    "greenhouse", "lever", "ashby", "smartrecruiters", "workday", "oracle", "eightfold",
    "successfactors", "kula", "apple", "meta", "amazon", "avature", "atlassian", "phenom",
    "goldman", "trakstar", "custom_api",
}
TRUSTED_RECORD_SOURCES = {
    "greenhouse", "lever", "ashby", "smartrecruiters", "workday", "oracle", "eightfold",
    "successfactors", "kula", "apple", "meta", "amazon", "amazon_json", "avature",
    "atlassian", "phenom", "goldman", "trakstar",
}
ACCESS_RESTRICTED = {401, 403, 429}


@dataclass
class UrlCheck:
    url: str
    status: int | None
    ok: bool
    access_restricted: bool
    error: str | None


def _sample_jobs(jobs: list[Any], n: int = 5) -> list[Any]:
    if len(jobs) <= n:
        return jobs
    positions = {0, len(jobs) - 1}
    for i in range(1, n - 1):
        positions.add(round(i * (len(jobs) - 1) / (n - 1)))
    return [jobs[i] for i in sorted(positions)[:n]]


def _check_url(url: str, timeout: float = 10.0) -> UrlCheck:
    if not valid_http_url(url):
        return UrlCheck(url, None, False, False, "invalid_url")
    try:
        response = requests.get(
            url,
            timeout=timeout,
            allow_redirects=True,
            stream=True,
            headers={"User-Agent": "Mozilla/5.0 JobFetcherSourceAudit/1.0"},
        )
        status = int(response.status_code)
        response.close()
        restricted = status in ACCESS_RESTRICTED
        return UrlCheck(url, status, 200 <= status < 400 or restricted, restricted, None if 200 <= status < 400 else f"HTTP {status}")
    except Exception as exc:
        return UrlCheck(url, None, False, False, f"{type(exc).__name__}: {exc}")


def _coverage_confidence(company: dict[str, Any], jobs: list[Any], adapter_name: str) -> tuple[str, list[str]]:
    configured = str((company.get("source") or {}).get("type") or "")
    record_sources = {str(getattr(j, "source_type", "") or "").lower() for j in jobs}
    reasons: list[str] = []

    if configured == "manual":
        return "restricted", ["manual/approved feed required"]

    if configured in TRUSTED_CONFIGURED_SOURCES and configured != "phenom":
        reasons.append(f"configured structured/dedicated adapter: {configured}")
        return "high", reasons

    if record_sources and all(
        any(source == trusted or source.startswith(trusted + "_") for trusted in TRUSTED_RECORD_SOURCES)
        for source in record_sources
    ):
        reasons.append("all returned records came from structured/dedicated source types")
        return "high", reasons

    if "Recovery" in adapter_name and record_sources:
        reasons.append("recovery adapter returned records, but completeness is not independently proven")
        return "medium", reasons

    if jobs_used_browser(jobs):
        reasons.append("browser/XHR extraction used; result may be partial unless provider pagination is known")
        return "unverified", reasons

    reasons.append("generic HTML/auto extraction; no independent total-count source")
    return "unverified", reasons


def audit_company(company: dict[str, Any], sample_urls: int = 5) -> dict[str, Any]:
    started = time.perf_counter()
    configured = str((company.get("source") or {}).get("type") or "")
    if not company.get("enabled", True):
        return {
            "id": company.get("id"), "name": company.get("name"), "rank": company.get("rank"),
            "enabled": False, "configured_source": configured, "verdict": "disabled",
        }

    adapter = None
    try:
        adapter = build_source(company)
        raw_jobs = list(adapter.fetch(company) or [])
        jobs = list(prefer_usable_jobs(raw_jobs) or [])
    except Exception as exc:
        return {
            "id": company.get("id"), "name": company.get("name"), "rank": company.get("rank"),
            "enabled": True, "configured_source": configured,
            "adapter": type(adapter).__name__ if adapter else None,
            "verdict": "failed", "jobs": 0,
            "error": f"{type(exc).__name__}: {exc}",
            "duration_seconds": round(time.perf_counter() - started, 3),
        }

    adapter_name = type(adapter).__name__
    invalid = [j for j in jobs if not plausible_job(j)]
    valid_urls = [j for j in jobs if valid_http_url(getattr(j, "job_url", None))]
    strong_urls = [j for j in jobs if strong_job_detail_url(getattr(j, "job_url", None))]
    locations = [j for j in jobs if str(getattr(j, "location", "") or "").strip()]
    descriptions = [j for j in jobs if str(getattr(j, "description", "") or "").strip()]
    source_types = sorted({str(getattr(j, "source_type", "") or "") for j in jobs})

    checks: list[UrlCheck] = []
    for job in _sample_jobs(valid_urls, sample_urls):
        checks.append(_check_url(str(job.job_url)))
    broken_checks = [c for c in checks if not c.ok]

    confidence, coverage_reasons = _coverage_confidence(company, jobs, adapter_name)
    allow_zero = bool((company.get("source") or {}).get("allow_zero_jobs"))

    reasons: list[str] = []
    if not jobs and not allow_zero:
        reasons.append("zero jobs returned")
    if invalid:
        reasons.append(f"{len(invalid)} implausible records survived normalization")
    if jobs and len(valid_urls) / len(jobs) < 0.95:
        reasons.append(f"only {len(valid_urls)}/{len(jobs)} records have valid URLs")
    if jobs and len(strong_urls) / len(jobs) < 0.80 and configured in {"auto", "phenom"}:
        reasons.append(f"only {len(strong_urls)}/{len(jobs)} URLs look like concrete vacancy details")
    if broken_checks:
        reasons.append(f"{len(broken_checks)}/{len(checks)} sampled detail URLs were unreachable")
    if confidence == "unverified":
        reasons.append("completeness is not independently verified")

    if not jobs and allow_zero:
        verdict = "verified_empty"
    elif not jobs or invalid or (jobs and len(valid_urls) / len(jobs) < 0.95) or broken_checks:
        verdict = "failed_quality"
    elif confidence == "unverified":
        verdict = "needs_dedicated_adapter"
    elif confidence == "medium":
        verdict = "needs_completeness_review"
    else:
        verdict = "verified"

    return {
        "id": company.get("id"), "name": company.get("name"), "rank": company.get("rank"),
        "enabled": True, "configured_source": configured, "adapter": adapter_name,
        "verdict": verdict, "jobs": len(jobs), "browser_used": jobs_used_browser(jobs),
        "source_types": source_types,
        "quality": {
            "valid_url_ratio": round(len(valid_urls) / len(jobs), 4) if jobs else 0.0,
            "strong_detail_url_ratio": round(len(strong_urls) / len(jobs), 4) if jobs else 0.0,
            "location_ratio": round(len(locations) / len(jobs), 4) if jobs else 0.0,
            "description_ratio": round(len(descriptions) / len(jobs), 4) if jobs else 0.0,
            "implausible_records": len(invalid),
        },
        "coverage_confidence": confidence,
        "coverage_reasons": coverage_reasons,
        "sample_url_checks": [asdict(c) for c in checks],
        "sample_jobs": [
            {
                "external_id": getattr(j, "external_id", None),
                "title": getattr(j, "title", None),
                "location": getattr(j, "location", None),
                "job_url": getattr(j, "job_url", None),
                "source_type": getattr(j, "source_type", None),
            }
            for j in _sample_jobs(jobs, 8)
        ],
        "reasons": reasons,
        "duration_seconds": round(time.perf_counter() - started, 3),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Live audit every configured career source")
    parser.add_argument("--shard-index", type=int, default=0)
    parser.add_argument("--shard-count", type=int, default=1)
    parser.add_argument("--output", required=True)
    parser.add_argument("--sample-urls", type=int, default=3)
    args = parser.parse_args()

    companies = sorted(load_config().get("companies", []), key=lambda c: int(c.get("rank") or 10**9))
    selected = [c for i, c in enumerate(companies) if i % args.shard_count == args.shard_index]
    rows = []
    for company in selected:
        print(f"AUDIT {company.get('rank'):>3} {company.get('name')}", flush=True)
        row = audit_company(company, sample_urls=args.sample_urls)
        rows.append(row)
        print(f"  -> {row.get('verdict')} jobs={row.get('jobs', '-')}", flush=True)

    payload = {
        "schema_version": 1,
        "shard_index": args.shard_index,
        "shard_count": args.shard_count,
        "companies": rows,
    }
    out = Path(args.output)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
