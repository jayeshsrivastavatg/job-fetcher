from __future__ import annotations

from copy import deepcopy

from job_fetcher.config import load_config
from job_fetcher.job_quality import prefer_usable_jobs
from job_fetcher.sources.factory import build_source
from job_fetcher.sources.http_client import session, timeout_seconds


API = "https://api.rippling.com/platform/api/ats/v1/board/rippling/jobs"


def is_india(row):
    value = row.get("workLocation") if isinstance(row, dict) else None
    if isinstance(value, dict):
        value = value.get("label")
    low = str(value or "").casefold()
    return any(token in low for token in ("india", "bangalore", "bengaluru", "hyderabad"))


def snapshot():
    response = session().get(API, timeout=timeout_seconds(), headers={"Accept": "application/json"})
    response.raise_for_status()
    rows = response.json()
    if not isinstance(rows, list):
        raise RuntimeError("hosted board API did not return a list")
    urls = {
        str(row.get("url")).split("?", 1)[0].rstrip("/")
        for row in rows
        if isinstance(row, dict) and row.get("url") and is_india(row)
    }
    return urls, len(rows)


def main():
    company = next(c for c in load_config().get("companies", []) if c.get("id") == "rippling")
    before, global_before = snapshot()
    source = build_source(deepcopy(company))
    jobs = list(prefer_usable_jobs(source.fetch(deepcopy(company))) or [])
    production = {
        str(job.job_url).split("?", 1)[0].rstrip("/")
        for job in jobs
        if job.job_url
    }
    after, global_after = snapshot()
    stable = before & after
    allowed = before | after
    missing = stable - production
    extras = production - allowed
    print({
        "adapter": type(source).__name__,
        "global_before": global_before,
        "global_after": global_after,
        "india_before": len(before),
        "india_after": len(after),
        "stable_india": len(stable),
        "production": len(production),
        "missing": len(missing),
        "extras": len(extras),
    }, flush=True)
    if not stable or missing or extras:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
