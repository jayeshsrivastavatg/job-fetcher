from __future__ import annotations

import math
import re
import time
from copy import deepcopy
from urllib.parse import urljoin, urlparse

from playwright.sync_api import sync_playwright

from job_fetcher.config import load_config
from job_fetcher.job_quality import prefer_usable_jobs
from job_fetcher.sources.factory import build_source

TOTAL_RE = re.compile(r"\bof\s+([\d,]+)\s+matching jobs\b", re.I)
JOB_RE = re.compile(r"/jobs/(?P<id>\d{8,})/", re.I)
BASE = "https://careers.servicenow.com/jobs/"


def production_ids(company):
    c = deepcopy(company)
    source = build_source(c)
    jobs = list(prefer_usable_jobs(source.fetch(c)) or [])
    ids = set()
    for job in jobs:
        for value in (getattr(job, "external_id", None), getattr(job, "job_url", None)):
            m = re.search(r"(\d{8,})", str(value or ""))
            if m:
                ids.add(m.group(1)); break
    return type(source).__name__, jobs, ids


def parse_page(page):
    body = page.locator("body").inner_text(timeout=10000)
    m = TOTAL_RE.search(body)
    total = int(m.group(1).replace(",", "")) if m else None
    records = {}
    anchors = page.locator("a[href]")
    for i in range(anchors.count()):
        a = anchors.nth(i)
        try:
            href = urljoin(page.url, a.get_attribute("href") or "")
            title = re.sub(r"\s+", " ", a.inner_text(timeout=800)).strip()
        except Exception:
            continue
        m = JOB_RE.search(urlparse(href).path + "/")
        if m and title:
            records[m.group("id")] = (title, href)
    return total, records


def main():
    company = next(c for c in load_config()["companies"] if c["id"] == "servicenow")
    adapter, jobs, app_ids = production_ids(company)
    print(f"APP adapter={adapter} jobs={len(jobs)} ids={len(app_ids)}", flush=True)

    all_records = {}
    totals = []
    pages = []
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        # First page establishes the display total/page count.
        context = browser.new_context(user_agent="Mozilla/5.0 (X11; Linux x86_64) Chrome/131 Safari/537.36", locale="en-US")
        page = context.new_page()
        page.goto(f"{BASE}?page=1&pagesize=20&audit={int(time.time())}", wait_until="domcontentloaded", timeout=90000)
        page.wait_for_timeout(500)
        total, recs = parse_page(page)
        totals.append(total)
        all_records.update(recs)
        pages.append((1, total, len(recs), len(all_records)))
        context.close()
        expected_pages = math.ceil((total or len(recs)) / 20)

        # Use a fresh browser context for every page to defeat sticky pagination,
        # stale SPA state and per-context response caching. Do not stop on a
        # repeated page; later pages may still be valid.
        for n in range(2, min(expected_pages + 3, 80) + 1):
            best = {}
            page_total = None
            for attempt in range(3):
                context = browser.new_context(user_agent="Mozilla/5.0 (X11; Linux x86_64) Chrome/131 Safari/537.36", locale="en-US")
                page = context.new_page()
                url = f"{BASE}?page={n}&pagesize=20&audit={int(time.time()*1000)}-{attempt}"
                page.goto(url, wait_until="domcontentloaded", timeout=90000)
                page.wait_for_timeout(450 + attempt * 250)
                page_total, recs = parse_page(page)
                context.close()
                if len(recs) > len(best):
                    best = recs
                # Accept a page if it contributes any previously unseen job IDs.
                if set(recs) - set(all_records):
                    break
                time.sleep(0.35)
            if page_total is not None:
                totals.append(page_total)
            all_records.update(best)
            pages.append((n, page_total, len(best), len(all_records)))
            print(f"PAGE {n} total={page_total} page_jobs={len(best)} unique_so_far={len(all_records)}", flush=True)
        browser.close()

    website_ids = set(all_records)
    missing = website_ids - app_ids
    print(f"WEBSITE unique={len(website_ids)} totals_first_last={totals[0] if totals else None}/{totals[-1] if totals else None}")
    print(f"COVERAGE website_missing_from_app={len(missing)} app_extras={len(app_ids-website_ids)}")
    for jid in sorted(missing):
        print("MISSING", jid, all_records[jid][0], all_records[jid][1])

    stable = bool(totals) and totals[0] == totals[-1]
    exhaustive = bool(totals) and len(website_ids) >= totals[-1]
    print(f"PROOF stable_total={stable} enumeration_exhausted={exhaustive}")
    if missing or not exhaustive:
        raise SystemExit(2)
    if not stable:
        raise SystemExit(3)


if __name__ == "__main__":
    main()
