from __future__ import annotations

from copy import deepcopy

from job_fetcher.config import load_config
from job_fetcher.job_quality import prefer_usable_jobs
from job_fetcher.sources.apple import AppleSource
from job_fetcher.sources.factory import build_source
from job_fetcher.sources.goldman import GoldmanSource
from job_fetcher.sources.http_client import session, timeout_seconds
from job_fetcher.sources.meta import MetaSource


def app_ids(company):
    source = build_source(deepcopy(company))
    jobs = list(prefer_usable_jobs(source.fetch(deepcopy(company))) or [])
    ids = {str(job.external_id) for job in jobs if job.external_id}
    return type(source).__name__, jobs, ids


def apple_snapshot(company):
    src = company.get("source") or {}
    entry = src.get("entry_url") or company["career_url"]
    max_pages = max(1, int(src.get("max_pages") or 50))
    ids = set()
    exhausted = False
    for page in range(1, max_pages + 1):
        url = AppleSource.with_page(entry, page)
        response = session().get(url, timeout=timeout_seconds(), allow_redirects=True)
        response.raise_for_status()
        jobs = AppleSource.parse_search_page(company, response.text, response.url)
        new = {str(job.external_id) for job in jobs if job.external_id} - ids
        ids.update(str(job.external_id) for job in jobs if job.external_id)
        if not jobs or (page > 1 and not new):
            exhausted = True
            break
    return ids, exhausted


def meta_snapshot(company):
    src = company.get("source") or {}
    offices = src.get("offices") or MetaSource.DEFAULT_OFFICES
    max_pages = max(1, int(src.get("max_pages") or 40))
    ids = set()
    exhausted = True
    for office in offices:
        office_ids = set()
        stopped = False
        for page in range(1, max_pages + 1):
            url = MetaSource.search_url(src.get("entry_url") or company["career_url"], office, page)
            response = session().get(url, timeout=timeout_seconds(), allow_redirects=True)
            response.raise_for_status()
            jobs = MetaSource.parse_search_page(company, response.text, response.url, default_location=office)
            current = {str(job.external_id) for job in jobs if job.external_id}
            new = current - office_ids
            office_ids.update(current)
            if not jobs or (page > 1 and not new):
                stopped = True
                break
        exhausted = exhausted and stopped
        ids.update(office_ids)
    return ids, exhausted


def goldman_snapshot(company):
    src = company.get("source") or {}
    entry = src.get("entry_url") or "https://higher.gs.com/results"
    response = session().get(entry, timeout=timeout_seconds(), allow_redirects=True)
    response.raise_for_status()
    jobs = GoldmanSource.parse_listing(company, response.text, response.url)
    return {str(job.external_id) for job in jobs if job.external_id}, True


def verify(company, snapshot):
    before, exhausted_before = snapshot(company)
    adapter, jobs, production = app_ids(company)
    after, exhausted_after = snapshot(company)
    stable = before & after
    missing = stable - production
    passed = exhausted_before and exhausted_after and not missing and len(production) == len(jobs)
    row = {
        "id": company["id"], "adapter": adapter, "official_before": len(before),
        "official_after": len(after), "stable_checked": len(stable), "production": len(jobs),
        "missing": len(missing), "exhausted_before": exhausted_before,
        "exhausted_after": exhausted_after, "passed": passed,
    }
    print(row, flush=True)
    return passed


def main():
    companies = {c["id"]: c for c in load_config().get("companies", [])}
    checks = [
        verify(companies["apple"], apple_snapshot),
        verify(companies["meta"], meta_snapshot),
        verify(companies["goldman_sachs"], goldman_snapshot),
    ]
    if not all(checks):
        raise SystemExit(1)


if __name__ == "__main__":
    main()
