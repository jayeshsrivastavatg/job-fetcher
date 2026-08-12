from __future__ import annotations

import json
import math
import re
import time
from collections import defaultdict
from copy import deepcopy
from pathlib import Path
from urllib.parse import urljoin, urlparse

from playwright.sync_api import sync_playwright

from job_fetcher.config import load_config
from job_fetcher.job_quality import prefer_usable_jobs
from job_fetcher.sources.cohesity import ENDPOINT as COHESITY_ENDPOINT, flatten_job_data
from job_fetcher.sources.factory import build_source
from job_fetcher.sources.http_client import session, timeout_seconds

SERVICENOW_BASE = "https://careers.servicenow.com/jobs/"
SERVICENOW_JOB_RE = re.compile(r"/jobs/(?P<id>\d{8,})/", re.I)
SERVICENOW_TOTAL_RE = re.compile(r"\bof\s+([\d,]+)\s+matching jobs\b", re.I)
SNOWFLAKE_BASE = "https://careers.snowflake.com/us/en/search-results"
SNOWFLAKE_JOB_RE = re.compile(r"/us/en/job/(?P<id>[^/?#]+)", re.I)
CLOSED_RE = re.compile(
    r"\b(job has been closed|job is no longer available|position has been filled|no longer accepting applications)\b",
    re.I,
)


def clean(value):
    return re.sub(r"\s+", " ", str(value or "")).strip()


def norm(value):
    return re.sub(r"\s+", " ", re.sub(r"[^a-z0-9]+", " ", clean(value).casefold())).strip()


def app_jobs(company):
    candidate = deepcopy(company)
    source = build_source(candidate)
    jobs = list(prefer_usable_jobs(source.fetch(candidate)) or [])
    return type(source).__name__, jobs


def numeric_id(job):
    for value in (getattr(job, "external_id", None), getattr(job, "job_url", None)):
        m = re.search(r"(\d{8,})", str(value or ""))
        if m:
            return m.group(1)
    return None


def servicenow_page(page):
    body = page.locator("body").inner_text(timeout=10000)
    total_match = SERVICENOW_TOTAL_RE.search(body)
    total = int(total_match.group(1).replace(",", "")) if total_match else None
    records = {}
    anchors = page.locator("a[href]")
    for index in range(anchors.count()):
        a = anchors.nth(index)
        try:
            href = urljoin(page.url, a.get_attribute("href") or "")
            title = clean(a.inner_text(timeout=700))
        except Exception:
            continue
        match = SERVICENOW_JOB_RE.search(urlparse(href).path + "/")
        if match and title:
            records[match.group("id")] = {"id": match.group("id"), "title": title, "url": href}
    return total, records


def enumerate_servicenow():
    records = {}
    totals = []
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(user_agent="Mozilla/5.0 (X11; Linux x86_64) Chrome/131 Safari/537.36", locale="en-US")
        page = context.new_page()
        page.goto(f"{SERVICENOW_BASE}?page=1&pagesize=20&audit={int(time.time())}", wait_until="domcontentloaded", timeout=90000)
        page.wait_for_timeout(500)
        total, first = servicenow_page(page)
        context.close()
        if total is None:
            browser.close()
            return {}, None, None, False
        totals.append(total)
        records.update(first)
        expected_pages = math.ceil(total / 20)

        # Fresh browser context per page avoids sticky SPA pagination and caching.
        for n in range(2, min(expected_pages + 3, 80) + 1):
            best = {}
            page_total = None
            for attempt in range(3):
                context = browser.new_context(user_agent="Mozilla/5.0 (X11; Linux x86_64) Chrome/131 Safari/537.36", locale="en-US")
                page = context.new_page()
                page.goto(
                    f"{SERVICENOW_BASE}?page={n}&pagesize=20&audit={int(time.time()*1000)}-{attempt}",
                    wait_until="domcontentloaded", timeout=90000,
                )
                page.wait_for_timeout(450 + attempt * 200)
                page_total, current = servicenow_page(page)
                context.close()
                if len(current) > len(best):
                    best = current
                if set(current) - set(records):
                    break
            if page_total is not None:
                totals.append(page_total)
            records.update(best)
        browser.close()

    before = totals[0] if totals else None
    after = totals[-1] if totals else None
    stable = before is not None and before == after
    exhausted = stable and len(records) >= after
    return records, before, after, exhausted


def verify_servicenow(company):
    website, before, after, exhausted = enumerate_servicenow()
    # Fetch the app after website enumeration so jobs published during the crawl
    # have a chance to appear in the production output.
    adapter, jobs = app_jobs(company)
    ids = {jid for job in jobs if (jid := numeric_id(job))}
    missing = sorted(set(website) - ids)
    return {
        "company": "ServiceNow", "adapter": adapter, "website_count": len(website),
        "website_total_before": before, "website_total_after": after,
        "app_count": len(jobs), "missing_count": len(missing),
        "missing": [website[jid] for jid in missing], "enumeration_exhausted": exhausted,
        "passed": exhausted and not missing,
        "match": "exact ServiceNow employer posting id",
    }


def cohesity_snapshot():
    r = session().get(
        COHESITY_ENDPOINT, timeout=timeout_seconds(),
        headers={"Accept": "application/json", "User-Agent": "PersonalJobFetcherAudit/0.4"},
    )
    r.raise_for_status()
    rows = flatten_job_data(r.json())
    records = {}
    for row in rows:
        req = clean(row.get("req_id")) or clean(row.get("JobID"))
        if req:
            records[req] = {"id": req, "title": clean(row.get("title")), "location": clean(row.get("primaryLocation"))}
    return records


def verify_cohesity(company):
    before = cohesity_snapshot()
    adapter, jobs = app_jobs(company)
    app_ids = {clean(getattr(job, "external_id", None)) for job in jobs if clean(getattr(job, "external_id", None))}
    after = cohesity_snapshot()
    stable = set(before) == set(after)
    authoritative = after if stable else before
    missing = sorted(set(authoritative) - app_ids)
    return {
        "company": "Cohesity", "adapter": adapter, "website_count": len(authoritative),
        "website_total_before": len(before), "website_total_after": len(after),
        "app_count": len(jobs), "missing_count": len(missing),
        "missing": [authoritative[jid] for jid in missing], "enumeration_exhausted": True,
        "passed": stable and not missing,
        "match": "exact req_id from Cohesity first-party careers JSON",
    }


def extract_snowflake_links(page):
    records = {}
    anchors = page.locator("a[href]")
    for index in range(anchors.count()):
        a = anchors.nth(index)
        try:
            href = urljoin(page.url, a.get_attribute("href") or "")
            title = clean(a.inner_text(timeout=700))
        except Exception:
            continue
        if "${" in href or "%7b" in href.lower() or not title:
            continue
        match = SNOWFLAKE_JOB_RE.search(urlparse(href).path)
        if not match:
            continue
        card_text = ""
        try:
            card_text = clean(a.evaluate(
                """a => { let n=a,b=a.innerText||''; for(let i=0;i<5&&n&&n.parentElement;i++){n=n.parentElement;const t=(n.innerText||'').trim();if(t.length>=b.length&&t.length<=900)b=t;} return b;}"""
            ))
        except Exception:
            pass
        jid = match.group("id")
        records[jid] = {"id": jid, "title": title, "url": href, "card_text": card_text}
    return records


def enumerate_snowflake():
    records = {}
    consecutive_no_new = 0
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page(user_agent="Mozilla/5.0 (X11; Linux x86_64) Chrome/131 Safari/537.36")
        for offset in range(0, 1201, 10):
            page.goto(f"{SNOWFLAKE_BASE}?from={offset}&s=1", wait_until="domcontentloaded", timeout=90000)
            page.wait_for_timeout(700)
            try:
                page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
                page.wait_for_timeout(300)
            except Exception:
                pass
            current = extract_snowflake_links(page)
            new_ids = set(current) - set(records)
            records.update(current)
            consecutive_no_new = consecutive_no_new + 1 if not new_ids else 0
            if consecutive_no_new >= 2:
                break
        browser.close()
    return records, consecutive_no_new >= 2


def snowflake_live(record):
    try:
        r = session().get(record["url"], timeout=timeout_seconds(), allow_redirects=True, headers={"User-Agent": "Mozilla/5.0"})
    except Exception:
        return True, "detail check failed; treated as live"
    if r.status_code in {404, 410}:
        return False, f"HTTP {r.status_code}"
    if r.status_code >= 400:
        return True, f"HTTP {r.status_code}; treated as live"
    if CLOSED_RE.search(clean(r.text[:200000])):
        return False, "explicit closed marker"
    return True, f"HTTP {r.status_code}"


def match_snowflake(website, jobs):
    candidates = defaultdict(list)
    for i, job in enumerate(jobs):
        candidates[norm(getattr(job, "title", None))].append(i)
    unused = set(range(len(jobs)))
    missing = []
    ignored = []
    for record in website.values():
        possible = [i for i in candidates.get(norm(record["title"]), []) if i in unused]
        chosen = None
        context = norm(record.get("card_text"))
        if possible and context:
            for i in possible:
                loc = norm(getattr(jobs[i], "location", None))
                if loc and (loc in context or context in loc):
                    chosen = i
                    break
        if chosen is None and possible:
            chosen = possible[0]
        if chosen is not None:
            unused.remove(chosen)
            continue
        live, reason = snowflake_live(record)
        if live:
            item = dict(record); item["detail_check"] = reason; missing.append(item)
        else:
            item = dict(record); item["ignored_reason"] = reason; ignored.append(item)
    return missing, ignored


def verify_snowflake(company):
    website, exhausted = enumerate_snowflake()
    adapter, jobs = app_jobs(company)
    missing, ignored = match_snowflake(website, jobs)
    return {
        "company": "Snowflake", "adapter": adapter, "website_count": len(website),
        "app_count": len(jobs), "missing_count": len(missing), "missing": missing,
        "ignored_stale_count": len(ignored), "ignored_stale": ignored,
        "enumeration_exhausted": exhausted, "passed": exhausted and not missing,
        "match": "one-to-one title/location; unmatched website links individually checked for closure",
    }


def main():
    companies = {c["id"]: c for c in load_config().get("companies", [])}
    rows = [
        verify_snowflake(companies["snowflake"]),
        verify_servicenow(companies["servicenow"]),
        verify_cohesity(companies["cohesity"]),
    ]
    for row in rows:
        print(
            f"{row['company']}: passed={row['passed']} website={row['website_count']} app={row['app_count']} "
            f"missing={row['missing_count']} exhausted={row['enumeration_exhausted']}", flush=True,
        )
        for missing in row.get("missing", [])[:20]:
            print("  MISSING", json.dumps(missing, ensure_ascii=False), flush=True)
        for ignored in row.get("ignored_stale", [])[:10]:
            print("  IGNORED_STALE", json.dumps(ignored, ensure_ascii=False), flush=True)

    payload = {
        "checked_at": time.time(),
        "rule": "every current official employer careers vacancy must be present in production output; production extras are allowed",
        "all_passed": all(row["passed"] for row in rows),
        "companies": rows,
    }
    out = Path("reports/phase1-exact-production.json")
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    if not payload["all_passed"]:
        raise SystemExit(2)


if __name__ == "__main__":
    main()
