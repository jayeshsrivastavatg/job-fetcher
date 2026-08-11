from __future__ import annotations

import argparse
import csv
import json
import re
import time
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from job_fetcher.config import load_config
from job_fetcher.job_quality import plausible_job, prefer_usable_jobs, valid_http_url
from job_fetcher.service import classify_error
from job_fetcher.sources.factory import build_source
from job_fetcher.sources.http_client import session, timeout_seconds


SCHEMA_VERSION = 1
ACCESS_RESTRICTED = {401, 403, 429}
DEAD_DETAIL_CODES = {404, 410}
STRUCTURED_TYPES = {"greenhouse", "lever", "ashby", "workday", "smartrecruiters"}
PROVIDER_OVERRIDE_BOARDS = {
    "snowflake": ("ashby", "snowflake"),
    "confluent": ("ashby", "confluent"),
}


def utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


def _clean(value) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()


def _job_key(job) -> str:
    eid = _clean(getattr(job, "external_id", None))
    if eid:
        return f"id:{eid}"
    url = _clean(getattr(job, "job_url", None))
    if url:
        return f"url:{url}"
    return f"title:{_clean(getattr(job, 'title', None)).casefold()}|{_clean(getattr(job, 'location', None)).casefold()}"


def _job_record(job) -> dict:
    return {
        "external_id": getattr(job, "external_id", None),
        "title": getattr(job, "title", None),
        "location": getattr(job, "location", None),
        "job_url": getattr(job, "job_url", None),
        "posted_at": getattr(job, "posted_at", None),
        "source_type": getattr(job, "source_type", None),
        "has_description": bool(_clean(getattr(job, "description", None))),
    }


def _sample_jobs(jobs, size: int) -> list:
    rows = list(jobs or [])
    if size <= 0 or not rows:
        return []
    if len(rows) <= size:
        return rows
    if size == 1:
        return [rows[len(rows) // 2]]
    indexes = sorted({round(i * (len(rows) - 1) / (size - 1)) for i in range(size)})
    return [rows[i] for i in indexes]


def _title_evidence(title: str, body: str) -> bool:
    title_norm = re.sub(r"[^a-z0-9]+", " ", (title or "").casefold()).strip()
    body_norm = re.sub(r"[^a-z0-9]+", " ", (body or "").casefold())
    if not title_norm:
        return False
    if title_norm in body_norm:
        return True
    tokens = [t for t in title_norm.split() if len(t) >= 4 and t not in {"senior", "junior", "staff", "lead"}]
    if not tokens:
        return False
    required = 1 if len(tokens) == 1 else min(2, len(tokens))
    return sum(token in body_norm for token in tokens) >= required


def _wrapped_http_code(exc: Exception) -> int | None:
    m = re.search(r"\b(401|403|404|410|429)\b", f"{type(exc).__name__}: {exc}")
    return int(m.group(1)) if m else None


def _check_one_detail(job, timeout: float) -> dict:
    url = _clean(getattr(job, "job_url", None))
    title = _clean(getattr(job, "title", None))
    base = {"title": title, "url": url, "status": "not_checked", "http_status": None, "evidence": None}
    if not valid_http_url(url):
        return {**base, "status": "invalid_url", "evidence": "job_url is not http(s)"}
    try:
        response = session().get(url, timeout=timeout, allow_redirects=True)
        code = int(response.status_code)
        if code in ACCESS_RESTRICTED:
            response.close()
            return {**base, "status": "access_restricted", "http_status": code, "evidence": "detail validator blocked"}
        if code in DEAD_DETAIL_CODES:
            response.close()
            return {**base, "status": "dead", "http_status": code, "evidence": "detail URL is gone"}
        if not 200 <= code < 400:
            response.close()
            return {**base, "status": "http_error", "http_status": code, "evidence": f"HTTP {code}"}
        content_type = (response.headers.get("content-type") or "").lower()
        text = response.text[:750_000] if ("text" in content_type or "html" in content_type or not content_type) else ""
        response.close()
        if not text:
            return {**base, "status": "reachable_unconfirmed", "http_status": code, "evidence": content_type or "non-text response"}
        low = text.casefold()
        if "jobposting" in low or _title_evidence(title, text):
            return {**base, "status": "verified", "http_status": code, "evidence": "title/job posting evidence on detail page"}
        return {**base, "status": "reachable_unconfirmed", "http_status": code, "evidence": "page reachable but title not found in server HTML"}
    except Exception as exc:
        code = _wrapped_http_code(exc)
        if code in ACCESS_RESTRICTED:
            return {**base, "status": "access_restricted", "http_status": code, "evidence": f"{type(exc).__name__}: {exc}"}
        if code in DEAD_DETAIL_CODES:
            return {**base, "status": "dead", "http_status": code, "evidence": f"{type(exc).__name__}: {exc}"}
        return {**base, "status": "check_error", "evidence": f"{type(exc).__name__}: {exc}"}


def _check_details(jobs, sample_size: int, timeout: float) -> list[dict]:
    return [_check_one_detail(job, timeout) for job in _sample_jobs(jobs, sample_size)]


def _greenhouse_count(token: str) -> tuple[int, str]:
    r = session().get(
        f"https://boards-api.greenhouse.io/v1/boards/{token}/jobs",
        params={"content": "false"}, timeout=timeout_seconds(),
        headers={"User-Agent": "PersonalJobFetcher/0.1"},
    )
    r.raise_for_status()
    return len(r.json().get("jobs", [])), "greenhouse board API returned the complete jobs array"


def _lever_count(site: str) -> tuple[int, str]:
    r = session().get(
        f"https://api.lever.co/v0/postings/{site}", params={"mode": "json"},
        timeout=timeout_seconds(), headers={"User-Agent": "PersonalJobFetcher/0.1"},
    )
    r.raise_for_status()
    payload = r.json()
    return len(payload if isinstance(payload, list) else []), "Lever postings API returned the complete postings array"


def _ashby_count(board: str) -> tuple[int, str]:
    r = session().get(
        f"https://api.ashbyhq.com/posting-api/job-board/{board}",
        params={"includeCompensation": "false"}, timeout=timeout_seconds(),
        headers={"User-Agent": "PersonalJobFetcher/0.1"},
    )
    r.raise_for_status()
    return len(r.json().get("jobs", [])), "Ashby public job-board API returned the complete jobs array"


def _workday_count(src: dict) -> tuple[int, str]:
    host, tenant, site = src["host"], src["tenant"], src["site"]
    r = session().post(
        f"https://{host}/wday/cxs/{tenant}/{site}/jobs",
        json={"appliedFacets": {}, "limit": 1, "offset": 0, "searchText": ""},
        timeout=timeout_seconds(), headers={"Content-Type": "application/json"},
    )
    r.raise_for_status()
    data = r.json()
    return int(data.get("total") or 0), "Workday API reported total for the configured board"


def _smartrecruiters_count(ident: str) -> tuple[int, str]:
    r = session().get(
        f"https://api.smartrecruiters.com/v1/companies/{ident}/postings",
        params={"limit": 1, "offset": 0}, timeout=timeout_seconds(),
    )
    r.raise_for_status()
    return int(r.json().get("totalFound") or 0), "SmartRecruiters API reported totalFound"


def _amazon_count(company: dict) -> tuple[int, str]:
    src = company.get("source") or {}
    entry = src.get("entry_url") or company.get("career_url") or ""
    query = parse_qs(urlparse(entry).query)
    params = {
        "base_query": (query.get("base_query") or ["Software Development"])[0],
        "country": (query.get("country") or ["IND"])[0] or "IND",
        "offset": 0,
        "result_limit": 1,
        "sort": (query.get("sort") or ["relevant"])[0],
    }
    for key in ("loc_query", "city", "region", "county", "radius", "job_category"):
        if query.get(key):
            params[key] = query[key][0]
    r = session().get(
        "https://www.amazon.jobs/en/search.json", params=params,
        timeout=timeout_seconds(), headers={"Accept": "application/json", "User-Agent": "PersonalJobFetcher/0.1"},
    )
    r.raise_for_status()
    return int(r.json().get("hits") or 0), "Amazon public search API reported hits for the configured search scope"


def _provider_expected_count(company: dict, source_types: set[str]) -> dict:
    src = company.get("source") or {}
    source_type = str(src.get("type") or "")
    cid = str(company.get("id") or "")
    started = time.perf_counter()
    try:
        if source_type == "greenhouse" and src.get("board_token"):
            count, evidence = _greenhouse_count(str(src["board_token"]))
            provider = "greenhouse"
        elif source_type == "lever" and src.get("site"):
            count, evidence = _lever_count(str(src["site"]))
            provider = "lever"
        elif source_type == "ashby" and src.get("board_name"):
            count, evidence = _ashby_count(str(src["board_name"]))
            provider = "ashby"
        elif source_type == "workday" and all(src.get(k) for k in ("host", "tenant", "site")):
            count, evidence = _workday_count(src)
            provider = "workday"
        elif source_type == "smartrecruiters" and src.get("company_identifier"):
            count, evidence = _smartrecruiters_count(str(src["company_identifier"]))
            provider = "smartrecruiters"
        elif cid in PROVIDER_OVERRIDE_BOARDS:
            provider, board = PROVIDER_OVERRIDE_BOARDS[cid]
            count, evidence = _ashby_count(board)
        elif cid == "amazon":
            provider = "amazon"
            count, evidence = _amazon_count(company)
        else:
            return {
                "provider": source_type or (sorted(source_types)[0] if len(source_types) == 1 else None),
                "expected_count": None,
                "status": "unavailable",
                "evidence": "no independent/exhaustive count probe is registered for this source",
                "duration_seconds": round(time.perf_counter() - started, 3),
            }
        return {
            "provider": provider,
            "expected_count": int(count),
            "status": "verified",
            "evidence": evidence,
            "duration_seconds": round(time.perf_counter() - started, 3),
        }
    except Exception as exc:
        return {
            "provider": source_type or None,
            "expected_count": None,
            "status": "probe_failed",
            "evidence": f"{type(exc).__name__}: {exc}",
            "duration_seconds": round(time.perf_counter() - started, 3),
        }


def _recommendation(verdict: str, company: dict, adapter: str, rejected_non_job: int) -> str:
    source_type = str((company.get("source") or {}).get("type") or "")
    if verdict == "CERTIFIED":
        return "none; keep the source under drift monitoring"
    if verdict == "BLOCKED":
        return "use a company-approved API/feed or manual import; do not bypass the restriction"
    if verdict == "PARTIAL":
        return "fix pagination/filtering until fetched unique jobs equals the provider-reported total"
    if verdict == "INVALID":
        return "replace heuristic extraction with a provider/company-specific adapter before trusting this source"
    if verdict == "FAILED":
        return "repair the provider route or create a dedicated adapter; preserve the last certified snapshot"
    if source_type == "auto" or "Auto" in adapter or "Recovery" in adapter or rejected_non_job:
        return "identify the authoritative ATS/API and replace generic extraction with a source contract/dedicated adapter"
    return "add an independent completeness probe for this provider before marking it certified"


def audit_company(company: dict, *, sample_size: int = 3, detail_timeout: float = 10.0) -> dict:
    started = time.perf_counter()
    cid = str(company.get("id") or "")
    base = {
        "id": cid,
        "rank": company.get("rank"),
        "name": company.get("name"),
        "enabled": bool(company.get("enabled", True)),
        "career_url": company.get("career_url"),
        "configured_source": (company.get("source") or {}).get("type"),
        "provider_hint": (company.get("research") or {}).get("provider_hint"),
        "audited_at": utcnow(),
    }

    if not company.get("enabled", True):
        reason = _clean((company.get("source") or {}).get("reason"))
        verdict = "BLOCKED" if (company.get("source") or {}).get("type") == "manual" else "DISABLED"
        return {
            **base, "verdict": verdict, "adapter": None, "raw_records": 0, "jobs_found": 0,
            "unique_jobs": 0, "expected_count": None, "completeness_pct": None,
            "rejected_non_job_records": 0, "duplicate_records": 0, "valid_url_ratio": None,
            "stable_id_ratio": None, "description_ratio": None, "location_ratio": None,
            "detail_checks": [], "source_types": [], "count_probe": None, "jobs": [],
            "failure_category": "manual_or_disabled", "error": reason or "company is disabled in config",
            "recommended_action": _recommendation(verdict, company, "", 0),
            "duration_seconds": round(time.perf_counter() - started, 3),
        }

    adapter_obj = None
    try:
        adapter_obj = build_source(company)
        raw_jobs = list(adapter_obj.fetch(company) or [])
    except Exception as exc:
        category = classify_error(exc)
        verdict = "BLOCKED" if category in {
            "manual_or_approved_feed_required", "authentication_required", "anti_bot_or_captcha",
            "http_403_or_waf", "rate_limited",
        } else "FAILED"
        adapter = type(adapter_obj).__name__ if adapter_obj is not None else None
        return {
            **base, "verdict": verdict, "adapter": adapter, "raw_records": 0, "jobs_found": 0,
            "unique_jobs": 0, "expected_count": None, "completeness_pct": None,
            "rejected_non_job_records": 0, "duplicate_records": 0, "valid_url_ratio": None,
            "stable_id_ratio": None, "description_ratio": None, "location_ratio": None,
            "detail_checks": [], "source_types": [], "count_probe": None, "jobs": [],
            "failure_category": category, "error": f"{type(exc).__name__}: {exc}",
            "recommended_action": _recommendation(verdict, company, adapter or "", 0),
            "duration_seconds": round(time.perf_counter() - started, 3),
        }

    adapter = type(adapter_obj).__name__
    plausible_raw = [job for job in raw_jobs if plausible_job(job)]
    rejected_non_job = max(0, len(raw_jobs) - len(plausible_raw))
    jobs = list(prefer_usable_jobs(raw_jobs) or [])
    unique_keys = {_job_key(job) for job in jobs}
    duplicate_records = max(0, len(jobs) - len(unique_keys))
    n = len(jobs)
    source_types = {str(getattr(job, "source_type", "") or "") for job in jobs if getattr(job, "source_type", None)}
    valid_urls = sum(valid_http_url(getattr(job, "job_url", None)) for job in jobs)
    stable_ids = sum(bool(_clean(getattr(job, "external_id", None))) for job in jobs)
    descriptions = sum(bool(_clean(getattr(job, "description", None))) for job in jobs)
    locations = sum(bool(_clean(getattr(job, "location", None))) for job in jobs)
    count_probe = _provider_expected_count(company, source_types)
    expected = count_probe.get("expected_count")
    detail_checks = _check_details(jobs, sample_size, detail_timeout)
    detail_dead = sum(check.get("status") in {"dead", "invalid_url"} for check in detail_checks)
    detail_verified = sum(check.get("status") == "verified" for check in detail_checks)

    unique_n = len(unique_keys)
    valid_url_ratio = round(valid_urls / n, 4) if n else None
    stable_id_ratio = round(stable_ids / n, 4) if n else None
    description_ratio = round(descriptions / n, 4) if n else None
    location_ratio = round(locations / n, 4) if n else None
    completeness_pct = round((unique_n / expected) * 100.0, 2) if isinstance(expected, int) and expected > 0 else (100.0 if expected == unique_n == 0 else None)

    hard_invalid = bool(n and (valid_url_ratio or 0) < 0.95) or detail_dead > 0
    if n == 0:
        if expected == 0 and count_probe.get("status") == "verified":
            verdict = "CERTIFIED"
        elif raw_jobs:
            verdict = "INVALID"
        else:
            verdict = "EMPTY_UNVERIFIED"
    elif hard_invalid:
        verdict = "INVALID"
    elif isinstance(expected, int):
        if unique_n < expected:
            verdict = "PARTIAL"
        elif unique_n > expected:
            verdict = "INVALID"
        else:
            verdict = "CERTIFIED"
    else:
        # Valid-looking records without an independent/exhaustive count are useful,
        # but explicitly not certified. This is the key distinction missing from
        # the old Healthy/Fallback statuses.
        verdict = "UNVERIFIED"

    jobs_payload = [_job_record(job) for job in jobs]
    failure_category = None
    error = None
    if verdict == "PARTIAL":
        failure_category = "incomplete_fetch"
        error = f"provider expected {expected} unique jobs but adapter returned {unique_n}"
    elif verdict == "INVALID":
        failure_category = "invalid_job_records"
        error = f"invalid/dead URLs={detail_dead}; valid_url_ratio={valid_url_ratio}; rejected_non_job={rejected_non_job}"
    elif verdict in {"UNVERIFIED", "EMPTY_UNVERIFIED"}:
        failure_category = "completeness_unverified"
        error = count_probe.get("evidence")

    return {
        **base,
        "verdict": verdict,
        "adapter": adapter,
        "raw_records": len(raw_jobs),
        "jobs_found": n,
        "unique_jobs": unique_n,
        "expected_count": expected,
        "completeness_pct": completeness_pct,
        "rejected_non_job_records": rejected_non_job,
        "duplicate_records": duplicate_records,
        "valid_url_ratio": valid_url_ratio,
        "stable_id_ratio": stable_id_ratio,
        "description_ratio": description_ratio,
        "location_ratio": location_ratio,
        "detail_verified": detail_verified,
        "detail_checks": detail_checks,
        "source_types": sorted(source_types),
        "count_probe": count_probe,
        "jobs": jobs_payload,
        "failure_category": failure_category,
        "error": error,
        "recommended_action": _recommendation(verdict, company, adapter, rejected_non_job),
        "duration_seconds": round(time.perf_counter() - started, 3),
    }


def _summary(companies: list[dict]) -> dict:
    counts = Counter(row.get("verdict") or "UNKNOWN" for row in companies)
    return {
        "companies": len(companies),
        "attempted": sum(row.get("enabled") for row in companies),
        "total_jobs_found": sum(int(row.get("jobs_found") or 0) for row in companies),
        "verdicts": dict(sorted(counts.items())),
        "certified": counts.get("CERTIFIED", 0),
        "needs_attention": sum(counts.get(v, 0) for v in ("PARTIAL", "INVALID", "FAILED", "BLOCKED")),
        "needs_certification": sum(counts.get(v, 0) for v in ("UNVERIFIED", "EMPTY_UNVERIFIED")),
    }


def _write_report(rows: list[dict], output_dir: Path, *, metadata: dict | None = None) -> dict:
    output_dir.mkdir(parents=True, exist_ok=True)
    rows = sorted(rows, key=lambda x: (x.get("rank") if isinstance(x.get("rank"), int) else 10**9, x.get("id") or ""))
    payload = {
        "schema_version": SCHEMA_VERSION,
        "generated_at": utcnow(),
        "metadata": metadata or {},
        "summary": _summary(rows),
        "companies": rows,
    }
    json_path = output_dir / "company_certification.json"
    json_path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")

    csv_path = output_dir / "company_certification.csv"
    fields = [
        "rank", "id", "name", "enabled", "verdict", "configured_source", "provider_hint", "adapter",
        "jobs_found", "unique_jobs", "expected_count", "completeness_pct", "raw_records",
        "rejected_non_job_records", "valid_url_ratio", "stable_id_ratio", "description_ratio", "location_ratio",
        "detail_verified", "failure_category", "error", "recommended_action", "duration_seconds",
    ]
    with csv_path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)

    md_path = output_dir / "company_certification.md"
    lines = [
        "# Company source certification",
        "",
        f"Generated: `{payload['generated_at']}`",
        "",
        f"Certified: **{payload['summary']['certified']}** / {payload['summary']['companies']} configured companies",
        "",
        "| # | Company | Verdict | Jobs | Expected | Complete | Adapter | Action |",
        "|---:|---|---|---:|---:|---:|---|---|",
    ]
    for row in rows:
        lines.append(
            "| {rank} | {name} | **{verdict}** | {jobs} | {expected} | {complete} | `{adapter}` | {action} |".format(
                rank=row.get("rank") or "-",
                name=str(row.get("name") or row.get("id") or "").replace("|", "/"),
                verdict=row.get("verdict") or "-",
                jobs=row.get("jobs_found") if row.get("jobs_found") is not None else "-",
                expected=row.get("expected_count") if row.get("expected_count") is not None else "?",
                complete=(f"{row.get('completeness_pct')}%" if row.get("completeness_pct") is not None else "?"),
                adapter=row.get("adapter") or "-",
                action=str(row.get("recommended_action") or "").replace("|", "/"),
            )
        )
    md_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return payload


def audit(*, shard_index: int, shard_count: int, output_dir: Path, sample_size: int, detail_timeout: float) -> dict:
    if shard_count < 1 or not 0 <= shard_index < shard_count:
        raise ValueError("shard-index must be in [0, shard-count)")
    companies = sorted(
        load_config().get("companies", []),
        key=lambda x: (x.get("rank") if isinstance(x.get("rank"), int) else 10**9, x.get("id") or ""),
    )
    selected = [company for index, company in enumerate(companies) if index % shard_count == shard_index]
    rows = []
    for position, company in enumerate(selected, 1):
        print(
            f"[{position}/{len(selected)}] audit {company.get('id')} ({company.get('name')})...",
            flush=True,
        )
        row = audit_company(company, sample_size=sample_size, detail_timeout=detail_timeout)
        rows.append(row)
        print(
            f"  -> {row['verdict']} jobs={row.get('jobs_found')} expected={row.get('expected_count')} "
            f"adapter={row.get('adapter')}",
            flush=True,
        )
    return _write_report(
        rows, output_dir,
        metadata={"shard_index": shard_index, "shard_count": shard_count, "sample_size": sample_size},
    )


def merge_reports(input_dir: Path, output_dir: Path) -> dict:
    files = sorted(input_dir.rglob("company_certification.json"))
    if not files:
        raise FileNotFoundError(f"no company_certification.json files under {input_dir}")
    by_id: dict[str, dict] = {}
    for path in files:
        payload = json.loads(path.read_text(encoding="utf-8"))
        for row in payload.get("companies", []):
            cid = str(row.get("id") or "")
            if cid:
                by_id[cid] = row
    return _write_report(
        list(by_id.values()), output_dir,
        metadata={"merged_shards": len(files), "source_files": [str(path) for path in files]},
    )


def main() -> None:
    parser = argparse.ArgumentParser(description="Live source certification for every configured company")
    sub = parser.add_subparsers(dest="command", required=True)

    q = sub.add_parser("audit", help="fetch and certify one deterministic shard of configured companies")
    q.add_argument("--shard-index", type=int, default=0)
    q.add_argument("--shard-count", type=int, default=1)
    q.add_argument("--output-dir", type=Path, default=Path("reports/certification"))
    q.add_argument("--sample-size", type=int, default=3)
    q.add_argument("--detail-timeout", type=float, default=10.0)

    q = sub.add_parser("merge", help="merge shard certification JSON reports")
    q.add_argument("--input-dir", type=Path, required=True)
    q.add_argument("--output-dir", type=Path, default=Path("reports/certification"))

    args = parser.parse_args()
    if args.command == "audit":
        payload = audit(
            shard_index=args.shard_index,
            shard_count=args.shard_count,
            output_dir=args.output_dir,
            sample_size=args.sample_size,
            detail_timeout=args.detail_timeout,
        )
    else:
        payload = merge_reports(args.input_dir, args.output_dir)

    summary = payload["summary"]
    print("=" * 72)
    print("COMPANY SOURCE CERTIFICATION")
    print("=" * 72)
    print(f"Companies:          {summary['companies']}")
    print(f"Certified:          {summary['certified']}")
    print(f"Needs attention:    {summary['needs_attention']}")
    print(f"Needs certification:{summary['needs_certification']}")
    print(f"Jobs inspected:     {summary['total_jobs_found']}")


if __name__ == "__main__":
    main()
