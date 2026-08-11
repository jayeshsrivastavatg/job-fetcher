from __future__ import annotations

import argparse
import json
from pathlib import Path

from job_fetcher.config import (
    SUPPORTED_SOURCES,
    find_company,
    load_config,
    make_source,
    next_rank,
    save_config,
    slugify,
    validate_config,
)
from job_fetcher.service import fetch_companies, fetch_companies_detailed, probe_company
from job_fetcher.health import verify_all
from job_fetcher.storage import JobStore
from job_fetcher.relevance_service import analyze_relevance, relevance_stats, export_relevance
from job_fetcher.profile import load_profile, validate_profile


def die(msg):
    raise SystemExit(msg)


def list_companies(a):
    rows = load_config()["companies"]
    if a.enabled_only:
        rows = [c for c in rows if c.get("enabled", True)]
    for c in sorted(rows, key=lambda x: (x.get("rank", 10**9), x["id"])):
        print(
            f"{str(c.get('rank','-')):>3}  {c['id']:<28} "
            f"{'ENABLED' if c.get('enabled', True) else 'disabled':<8} "
            f"{c['source']['type']:<14} {c['name']}"
        )


def _source_from_args(a, existing=None):
    source_type = a.source or ((existing or {}).get("source") or {}).get("type") or "auto"
    existing_src = dict((existing or {}).get("source") or {}) if ((existing or {}).get("source") or {}).get("type") == source_type else {}
    values = {
        "entry_url": getattr(a, "entry_url", None),
        "board_token": getattr(a, "board_token", None),
        "site": getattr(a, "site", None),
        "board_name": getattr(a, "board_name", None),
        "company_identifier": getattr(a, "company_identifier", None),
        "host": getattr(a, "host", None),
        "tenant": getattr(a, "tenant", None),
        "workday_site": getattr(a, "workday_site", None),
        "site_number": getattr(a, "site_number", None),
        "locale": getattr(a, "locale", None),
    }
    if source_type == "workday" and values.pop("workday_site", None):
        values["site"] = getattr(a, "workday_site")
    src = {**existing_src, "type": source_type}
    for k, v in values.items():
        if v is not None:
            src[k] = v
    raw_json = getattr(a, "source_config_json", None)
    if raw_json:
        try:
            extra = json.loads(raw_json)
        except json.JSONDecodeError as e:
            die(f"Invalid --source-config-json: {e}")
        if not isinstance(extra, dict):
            die("--source-config-json must be a JSON object")
        src.update(extra)
        src["type"] = source_type
    return src


def add_company(a):
    data = load_config()
    cid = a.id or slugify(a.name)
    if find_company(data, cid):
        die(f"Company id already exists: {cid}")
    src = _source_from_args(a)
    if src["type"] == "auto":
        src.setdefault("entry_url", a.career_url)
    row = {
        "id": cid,
        "rank": a.rank or next_rank(data),
        "name": a.name,
        "enabled": not a.disabled,
        "career_url": a.career_url,
        "source": src,
        "research": {"status": "user_added", "provider_hint": a.provider_hint or src["type"]},
    }
    candidate = {**data, "companies": [*data["companies"], row]}
    errors = validate_config(candidate)
    if errors:
        die("Cannot add company:\n" + "\n".join(f"- {e}" for e in errors))
    save_config(candidate)
    print(f"Added {cid} ({'enabled' if row['enabled'] else 'disabled'}) rank={row['rank']}")


def update_company(a):
    data = load_config()
    c = find_company(data, a.company_id)
    if not c:
        die("Unknown company id")
    if a.name is not None:
        c["name"] = a.name
    if a.career_url is not None:
        c["career_url"] = a.career_url
    if a.rank is not None:
        c["rank"] = a.rank
    if a.enable:
        c["enabled"] = True
    if a.disable:
        c["enabled"] = False
    if any(getattr(a, k, None) is not None for k in (
        "source", "entry_url", "board_token", "site", "board_name", "company_identifier", "host", "tenant", "workday_site", "site_number", "locale", "source_config_json"
    )):
        c["source"] = _source_from_args(a, c)
    if c["source"].get("type") == "auto":
        c["source"].setdefault("entry_url", c["career_url"])
        if a.career_url is not None and a.entry_url is None:
            c["source"]["entry_url"] = a.career_url
    errors = validate_config(data)
    if errors:
        die("Cannot update company:\n" + "\n".join(f"- {e}" for e in errors))
    save_config(data)
    print(f"Updated {a.company_id}")


def enabled(a, value):
    data = load_config()
    c = find_company(data, a.company_id)
    if not c:
        die("Unknown company id")
    c["enabled"] = value
    save_config(data)
    print(f"{a.company_id}: {'enabled' if value else 'disabled'}")


def remove(a):
    data = load_config()
    before = len(data["companies"])
    data["companies"] = [c for c in data["companies"] if c["id"] != a.company_id]
    if len(data["companies"]) == before:
        die("Unknown company id")
    save_config(data)
    print("Removed from config; historical DB rows kept")


def fetch(a):
    companies = [
        c for c in load_config()["companies"]
        if c.get("enabled", True) and (not a.company or c["id"] == a.company)
    ]
    if not companies:
        return print("No enabled companies selected")
    r = fetch_companies_detailed(companies, max_workers=a.workers)
    for x in r["companies"]:
        if x["status"] in {"healthy", "healthy_with_fallback"}:
            print(f"OK   {x['name']}: fetched={x['jobs_found']} new={x['new_jobs']} existing={x['existing_jobs']} status={x['status']}")
        else:
            print(f"WARN {x['name']}: status={x['status']} jobs={x['jobs_found']} [{x.get('failure_category') or '-'}] {x.get('error') or ''}")
    if a.report:
        p = Path(a.report)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(r, indent=2, ensure_ascii=False), encoding="utf-8")
        print(f"Report: {p}")
    try:
        relevance_result = analyze_relevance(recompute_all=False)
        print(
            f"Relevance: analyzed_this_run={relevance_result['analyzed_this_run']} "
            f"relevant_jobs={relevance_result.get('relevant_jobs', 0)} "
            f"new_changed={relevance_result.get('new_changed', 0)}"
        )
    except Exception as exc:
        print(f"Relevance analysis failed (fetch data was still saved): {type(exc).__name__}: {exc}")



def probe(a):
    c = find_company(load_config(), a.company_id)
    if not c:
        die("Unknown company id")
    result = probe_company(c, browser=not a.no_browser)
    print(json.dumps(result, indent=2, ensure_ascii=False))
    if result["status"] != "success":
        raise SystemExit(2)



def verify_all_cmd(a):
    data = load_config()
    payload = verify_all(
        data["companies"],
        max_workers=a.workers,
        output_dir=a.output_dir,
        browser=not a.no_browser,
        drop_threshold=a.drop_threshold,
        validate_detail=not a.skip_detail_check,
        detail_timeout=a.detail_timeout,
    )
    sm = payload["summary"]
    print("=" * 68)
    print("JOB FETCHER HEALTH CHECK")
    print("=" * 68)
    print(f"Configured:            {sm['configured']}")
    print(f"Enabled / verified:    {sm['enabled']} / {sm['verified']}")
    print(f"Healthy:               {sm.get('healthy', 0)}")
    print(f"Healthy with fallback: {sm.get('healthy_with_fallback', 0)}")
    print(f"Suspicious:            {sm.get('suspicious', 0)}")
    print(f"Failed:                {sm.get('failed', 0)}")
    print(f"Jobs detected:         {sm.get('total_jobs_detected', 0)}")
    if sm.get("environment_blocked"):
        print("WARNING: Most failures are DNS-related; this run appears environment-blocked.")

    bad = [r for r in payload["companies"] if r["status"] in {"failed", "suspicious"}]
    if bad:
        print("\nNeeds attention:")
        for r in bad:
            print(f"- {r['name']}: {r['status']} [{r.get('failure_category') or '-'}] jobs={r['jobs_found']} {r.get('error') or ''}")
    print(f"\nJSON: {Path(a.output_dir) / 'company_health.json'}")
    print(f"CSV:  {Path(a.output_dir) / 'company_health.csv'}")
    if bad and not a.no_fail_exit:
        raise SystemExit(2)


def validate(_):
    data = load_config()
    errors = validate_config(data)
    print(f"companies={len(data['companies'])} enabled={sum(c.get('enabled', True) for c in data['companies'])} errors={len(errors)}")
    for e in errors:
        print("ERROR", e)
    if errors:
        raise SystemExit(1)


def stats(_):
    rows = JobStore().counts()
    total = 0
    for r in rows:
        print(f"{r['company_name']:<35} {r['n']:>6}")
        total += r["n"]
    print(f"{'TOTAL':<35} {total:>6}")


def export(a):
    rows = [dict(r) for r in JobStore().all()]
    p = Path(a.output)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(rows, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"Exported {len(rows)} jobs to {p}")



def daily_scan_cmd(a):
    companies = [c for c in load_config()["companies"] if c.get("enabled", True)]
    if not companies:
        die("No enabled companies configured")
    fetch_result = fetch_companies_detailed(companies, max_workers=a.workers)
    output_dir = Path(a.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / "daily_fetch.json").write_text(json.dumps(fetch_result, indent=2, ensure_ascii=False), encoding="utf-8")

    analysis = analyze_relevance(recompute_all=False)
    csv_path = export_relevance(output_dir / "relevant_jobs.csv", format="csv")
    json_path = export_relevance(output_dir / "relevant_jobs.json", format="json")

    failed = sum(1 for r in fetch_result["companies"] if r["status"] == "failed")
    suspicious = sum(1 for r in fetch_result["companies"] if r["status"] == "suspicious")
    print("=" * 68)
    print("JOB SCAN")
    print("=" * 68)
    print(f"Companies attempted:     {len(fetch_result['companies'])}")
    print(f"Fetch failed/suspicious: {failed} / {suspicious}")
    print(f"Active jobs:             {analysis.get('active_jobs', 0)}")
    print(f"New / changed jobs:      {analysis.get('new_changed', 0)}")
    print(f"High priority:           {analysis.get('statuses', {}).get('high', 0)}")
    print(f"Good relevance:          {analysis.get('statuses', {}).get('good', 0)}")
    print(f"Possible relevance:      {analysis.get('statuses', {}).get('possible', 0)}")
    print(f"Low priority:            {analysis.get('statuses', {}).get('low', 0)}")
    print(f"Filtered:                {analysis.get('statuses', {}).get('filtered', 0)}")
    print(f"Relevant jobs:           {analysis.get('relevant_jobs', 0)}")
    print(f"Relevant NEW/CHANGED:    {analysis.get('relevant_new_changed', 0)}")
    print(f"Relevant CSV:            {csv_path}")
    print(f"Relevant JSON:           {json_path}")


def analyze_relevance_cmd(a):
    result = analyze_relevance(recompute_all=a.all)
    print(json.dumps(result, indent=2, ensure_ascii=False))


def relevance_stats_cmd(_):
    print(json.dumps(relevance_stats(), indent=2, ensure_ascii=False))


def export_relevance_cmd(a):
    path = export_relevance(a.output, format=a.format, relevant_only=a.relevant_only, min_score=a.min_score)
    print(f"Relevance export: {path}")


def validate_profile_cmd(_):
    profile = load_profile()
    errors = validate_profile(profile)
    print(f"profile_version={profile.get('version')} errors={len(errors)}")
    for error in errors:
        print("ERROR", error)
    if errors:
        raise SystemExit(1)


def add_source_args(q, allow_none=True):
    q.add_argument("--source", choices=sorted(SUPPORTED_SOURCES), default=None if allow_none else "auto")
    q.add_argument("--entry-url")
    q.add_argument("--board-token", help="Greenhouse board token")
    q.add_argument("--site", help="Lever site slug")
    q.add_argument("--board-name", help="Ashby board name")
    q.add_argument("--company-identifier", help="SmartRecruiters company identifier")
    q.add_argument("--host", help="ATS host (Workday or Oracle Candidate Experience)")
    q.add_argument("--tenant", help="Workday tenant")
    q.add_argument("--workday-site", help="Workday site slug")
    q.add_argument("--site-number", help="Oracle Candidate Experience site, e.g. CX_1001")
    q.add_argument("--locale", help="Provider locale, e.g. en or en-US")
    q.add_argument(
        "--source-config-json",
        help="Advanced: JSON object merged into source config (selectors, API field_mapping, headers, etc.)",
    )


def main():
    p = argparse.ArgumentParser(description="Configurable multi-company career-site job fetcher")
    s = p.add_subparsers(required=True)

    q = s.add_parser("companies", help="List configured companies")
    q.add_argument("--enabled-only", action="store_true")
    q.set_defaults(fn=list_companies)

    q = s.add_parser("add-company", help="Add a company; auto source needs only name + career URL")
    q.add_argument("--id", help="Stable id; generated from name if omitted")
    q.add_argument("--name", required=True)
    q.add_argument("--career-url", required=True)
    q.add_argument("--rank", type=int)
    q.add_argument("--disabled", action="store_true")
    q.add_argument("--provider-hint")
    add_source_args(q, allow_none=False)
    q.set_defaults(fn=add_company)

    q = s.add_parser("update-company", help="Change name/url/rank/source without deleting history")
    q.add_argument("company_id")
    q.add_argument("--name")
    q.add_argument("--career-url")
    q.add_argument("--rank", type=int)
    mx = q.add_mutually_exclusive_group()
    mx.add_argument("--enable", action="store_true")
    mx.add_argument("--disable", action="store_true")
    add_source_args(q, allow_none=True)
    q.set_defaults(fn=update_company)

    q = s.add_parser("enable-company")
    q.add_argument("company_id")
    q.set_defaults(fn=lambda a: enabled(a, True))

    q = s.add_parser("disable-company")
    q.add_argument("company_id")
    q.set_defaults(fn=lambda a: enabled(a, False))

    q = s.add_parser("remove-company")
    q.add_argument("company_id")
    q.set_defaults(fn=remove)

    q = s.add_parser("probe-company", help="Fetch one company without writing to SQLite")
    q.add_argument("company_id")
    q.add_argument("--no-browser", action="store_true")
    q.set_defaults(fn=probe)

    q = s.add_parser("fetch")
    q.add_argument("--company")
    q.add_argument("--report", default="logs/fetch-report.json")
    q.add_argument("--workers", type=int, default=4)
    q.set_defaults(fn=fetch)

    q = s.add_parser("verify-all", help="Health-check every enabled company without writing jobs to SQLite")
    q.add_argument("--workers", type=int, default=4)
    q.add_argument("--output-dir", default="reports")
    q.add_argument("--no-browser", action="store_true", help="Disable Playwright/browser fallbacks")
    q.add_argument("--skip-detail-check", action="store_true", help="Do not open one sample job-detail URL per company")
    q.add_argument("--detail-timeout", type=float, default=15.0)
    q.add_argument("--drop-threshold", type=float, default=0.80, help="Flag when job count falls by more than this fraction vs previous run")
    q.add_argument("--no-fail-exit", action="store_true", help="Always exit 0 even when companies are failed/suspicious")
    q.set_defaults(fn=verify_all_cmd)

    q = s.add_parser("scan", help="Daily job-discovery pipeline: fetch, detect NEW/CHANGED, score/filter, export")
    q.add_argument("--workers", type=int, default=4)
    q.add_argument("--output-dir", default="reports/daily")
    q.set_defaults(fn=daily_scan_cmd)

    q = s.add_parser("analyze-relevance", help="Run deterministic role/experience/skill relevance scoring on active jobs")
    q.add_argument("--all", action="store_true", help="Recompute even when the JD hash is unchanged")
    q.set_defaults(fn=analyze_relevance_cmd)

    q = s.add_parser("relevance-stats", help="Show deterministic relevance-pipeline counts")
    q.set_defaults(fn=relevance_stats_cmd)

    q = s.add_parser("export-relevance", help="Export scored jobs as JSON or CSV")
    q.add_argument("--output", default="reports/relevant_jobs.csv")
    q.add_argument("--format", choices=["json", "csv"])
    q.add_argument("--relevant-only", action="store_true", help="Only export jobs meeting the relevance threshold")
    q.add_argument("--min-score", type=float)
    q.set_defaults(fn=export_relevance_cmd)

    q = s.add_parser("validate-profile")
    q.set_defaults(fn=validate_profile_cmd)

    q = s.add_parser("validate-config")
    q.set_defaults(fn=validate)

    q = s.add_parser("stats")
    q.set_defaults(fn=stats)

    q = s.add_parser("export")
    q.add_argument("--output", default="data/jobs.json")
    q.set_defaults(fn=export)

    a = p.parse_args()
    a.fn(a)


if __name__ == "__main__":
    main()
