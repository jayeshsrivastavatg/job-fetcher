from __future__ import annotations

import argparse
import json
import math
import re
import time
from collections import Counter, defaultdict
from copy import deepcopy
from pathlib import Path
from urllib.parse import urljoin, urlparse

from bs4 import BeautifulSoup
from playwright.sync_api import sync_playwright

from job_fetcher.config import load_config
from job_fetcher.job_quality import prefer_usable_jobs
from job_fetcher.sources.factory import build_source
from job_fetcher.sources.http_client import session, timeout_seconds
from job_fetcher.sources.phenom import PhenomSource
from job_fetcher.sources.playwright_auto import PlaywrightAutoSource

TARGET_IDS = ("snowflake", "servicenow", "cohesity")
SNOWFLAKE_JOB_RE = re.compile(r"/us/en/job/(?P<id>[^/?#]+)", re.I)
SERVICENOW_JOB_RE = re.compile(r"/jobs/(?P<id>\d{8,})/", re.I)
COHESITY_JOB_RE = re.compile(r"[?&]gh_jid=(?P<id>[0-9a-f]{16,})", re.I)
TOTAL_RE = re.compile(r"\bof\s+([\d,]+)\s+matching jobs\b", re.I)


def _clean(value) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()


def _norm(value) -> str:
    value = _clean(value).casefold()
    value = re.sub(r"[^a-z0-9]+", " ", value)
    return re.sub(r"\s+", " ", value).strip()


def _production_jobs(company: dict):
    candidate = deepcopy(company)
    adapter = build_source(candidate)
    jobs = list(prefer_usable_jobs(adapter.fetch(candidate)) or [])
    return type(adapter).__name__, jobs


def _job_payload(job):
    return {
        "external_id": _clean(getattr(job, "external_id", None)),
        "title": _clean(getattr(job, "title", None)),
        "location": _clean(getattr(job, "location", None)),
        "url": _clean(getattr(job, "job_url", None)),
        "source_type": _clean(getattr(job, "source_type", None)),
    }


def _production_numeric_ids(jobs):
    ids = set()
    for job in jobs:
        for value in (getattr(job, "external_id", None), getattr(job, "job_url", None)):
            match = re.search(r"(\d{8,})", str(value or ""))
            if match:
                ids.add(match.group(1))
                break
    return ids


def _fetch_servicenow_website():
    """Enumerate every vacancy linked by ServiceNow's employer careers site.

    This deliberately does not call SmartRecruiters. It walks the public
    careers.servicenow.com pagination and captures the stable posting id embedded
    in each employer-branded detail URL.
    """
    client = session()
    base = "https://careers.servicenow.com/jobs/"
    website = {}
    first_total = None
    last_total = None
    seen_page_fingerprints = set()
    page_number = 1
    max_pages = 80

    while page_number <= max_pages:
        response = client.get(
            base,
            params={"page": page_number},
            timeout=timeout_seconds(),
            headers={"User-Agent": "Mozilla/5.0 PersonalJobFetcherWebsiteAudit/0.1"},
        )
        response.raise_for_status()
        soup = BeautifulSoup(response.text, "html.parser")
        text = soup.get_text(" ", strip=True)
        total_match = TOTAL_RE.search(text)
        if total_match:
            current_total = int(total_match.group(1).replace(",", ""))
            first_total = current_total if first_total is None else first_total
            last_total = current_total

        page_ids = []
        for anchor in soup.select("a[href]"):
            href = urljoin(response.url, anchor.get("href") or "")
            match = SERVICENOW_JOB_RE.search(urlparse(href).path + "/")
            if not match:
                continue
            job_id = match.group("id")
            title = _clean(anchor.get_text(" ", strip=True))
            if not title:
                continue
            website[job_id] = {"website_id": job_id, "title": title, "url": href}
            page_ids.append(job_id)

        fingerprint = tuple(page_ids)
        if not page_ids or fingerprint in seen_page_fingerprints:
            break
        seen_page_fingerprints.add(fingerprint)

        if last_total is not None and len(website) >= last_total:
            break
        if last_total is not None and page_number >= math.ceil(last_total / 20) + 2:
            break
        page_number += 1
        time.sleep(0.08)

    # A count change during enumeration means the board was mutating. We still
    # report the exact set we observed, but do not label the website snapshot
    # stable until a rerun sees matching boundary totals.
    return {
        "records": list(website.values()),
        "website_total_before": first_total,
        "website_total_after": last_total,
        "pagination_exhausted": bool(website) and (
            last_total is None or len(website) >= last_total or page_number < max_pages
        ),
        "evidence": f"walked employer HTML pages 1..{page_number}; stable posting ids from /jobs/<id>/",
    }


def _browser_collect(url: str, *, kind: str, max_pages: int = 40, max_scrolls: int = 12, load_more_clicks: int = 40):
    records = {}
    visited = set()
    body_totals = []
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(
            user_agent="Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/131 Safari/537.36",
            locale="en-US",
        )
        page = context.new_page()
        page.goto(url, wait_until="domcontentloaded", timeout=90000)
        for page_index in range(max_pages):
            page.wait_for_timeout(900)
            PlaywrightAutoSource._scroll_until_stable(page, max_scrolls, 3)
            PlaywrightAutoSource._click_load_more(page, load_more_clicks)
            PlaywrightAutoSource._scroll_until_stable(page, max_scrolls, 3)

            try:
                body = page.locator("body").inner_text(timeout=5000)
            except Exception:
                body = ""
            for match in TOTAL_RE.finditer(body):
                body_totals.append(int(match.group(1).replace(",", "")))

            anchors = page.locator("a[href]")
            count = anchors.count()
            for index in range(count):
                anchor = anchors.nth(index)
                try:
                    href = urljoin(page.url, anchor.get_attribute("href") or "")
                    title = _clean(anchor.inner_text(timeout=1000))
                except Exception:
                    continue
                if not href or not title:
                    continue

                if kind == "snowflake":
                    match = SNOWFLAKE_JOB_RE.search(urlparse(href).path)
                else:
                    match = COHESITY_JOB_RE.search(href)
                if not match:
                    continue
                website_id = match.group("id")

                card_text = ""
                try:
                    card_text = _clean(anchor.evaluate(
                        """a => {
                          let n = a;
                          let best = a.innerText || '';
                          for (let i = 0; i < 5 && n && n.parentElement; i++) {
                            n = n.parentElement;
                            const t = (n.innerText || '').trim();
                            if (t.length >= best.length && t.length <= 900) best = t;
                          }
                          return best;
                        }"""
                    ))
                except Exception:
                    pass
                records[website_id] = {
                    "website_id": website_id,
                    "title": title,
                    "url": href,
                    "card_text": card_text,
                }

            if page_index >= max_pages - 1:
                break
            before = len(records)
            if not PlaywrightAutoSource._go_next(page, 90000, visited):
                break
            visited.add(page.url)
            page.wait_for_timeout(700)
            if len(records) == before and page.url in visited:
                # _go_next may keep the same SPA URL; allow one content cycle, then
                # the next loop's record growth/pagination controls determine stop.
                pass
        browser.close()

    total = body_totals[-1] if body_totals else None
    return {
        "records": list(records.values()),
        "website_total_before": body_totals[0] if body_totals else None,
        "website_total_after": total,
        "pagination_exhausted": len(records) > 0 and (total is None or len(records) >= total),
        "evidence": f"rendered employer site with Chromium; exhausted scroll/load-more/Next; captured {len(records)} concrete vacancy links",
    }


def _fetch_snowflake_website(company: dict):
    # First collect exact employer-branded detail links from the rendered Phenom
    # site. Then independently run the configured Phenom source (not the production
    # Ashby override) and merge only concrete careers.snowflake.com job URLs. This
    # gives us a stronger website inventory while keeping it independent from the
    # app's production Ashby adapter.
    rendered = _browser_collect(
        "https://careers.snowflake.com/us/en/search-results",
        kind="snowflake",
        max_pages=35,
        max_scrolls=12,
        load_more_clicks=40,
    )
    website = {row["website_id"]: row for row in rendered["records"]}
    phenom_company = deepcopy(company)
    phenom_company["source"] = deepcopy(company.get("source") or {})
    phenom_company["source"]["type"] = "phenom"
    try:
        jobs = PhenomSource().fetch(phenom_company)
    except Exception:
        jobs = []
    for job in jobs:
        href = _clean(getattr(job, "job_url", None))
        match = SNOWFLAKE_JOB_RE.search(urlparse(href).path) if href else None
        if not match or "careers.snowflake.com" not in urlparse(href).netloc.lower():
            continue
        website_id = match.group("id")
        website.setdefault(website_id, {
            "website_id": website_id,
            "title": _clean(getattr(job, "title", None)),
            "url": href,
            "card_text": _clean(getattr(job, "location", None)),
        })
    rendered["records"] = list(website.values())
    rendered["evidence"] += f"; merged concrete official Phenom-source links={len(website)}"
    return rendered


def _fetch_cohesity_website():
    return _browser_collect(
        "https://careers.cohesity.com/open-positions/",
        kind="cohesity",
        max_pages=35,
        max_scrolls=18,
        load_more_clicks=80,
    )


def _match_by_title_and_context(website_records, production_jobs):
    """One-to-one coverage match for sites whose display id differs from ATS id.

    Exact normalized title is mandatory. When the employer card exposes location
    text and multiple production postings share a title, prefer the candidate whose
    normalized location appears in the card context. Remaining identical-title
    duplicates are matched one-to-one, so multiplicity is preserved rather than a
    simple set-of-titles comparison.
    """
    candidates = defaultdict(list)
    for index, job in enumerate(production_jobs):
        candidates[_norm(getattr(job, "title", None))].append(index)
    unused = set(range(len(production_jobs)))
    matched = []
    missing = []

    for record in website_records:
        key = _norm(record.get("title"))
        possible = [i for i in candidates.get(key, []) if i in unused]
        if not possible:
            missing.append(record)
            continue
        context = _norm(record.get("card_text"))
        chosen = None
        if context:
            for i in possible:
                loc = _norm(getattr(production_jobs[i], "location", None))
                if loc and (loc in context or all(token in context for token in loc.split()[:2])):
                    chosen = i
                    break
        if chosen is None:
            chosen = possible[0]
        unused.remove(chosen)
        matched.append({
            "website": record,
            "production": _job_payload(production_jobs[chosen]),
        })
    return matched, missing


def _verify_company(company: dict):
    adapter, production = _production_jobs(company)
    cid = company["id"]

    if cid == "servicenow":
        website = _fetch_servicenow_website()
        app_ids = _production_numeric_ids(production)
        missing = [row for row in website["records"] if row["website_id"] not in app_ids]
        matched = len(website["records"]) - len(missing)
        match_basis = "exact employer detail id == SmartRecruiters posting id"
    elif cid == "snowflake":
        website = _fetch_snowflake_website(company)
        pairs, missing = _match_by_title_and_context(website["records"], production)
        matched = len(pairs)
        match_basis = "one-to-one normalized title; location/card context used for duplicate titles"
    elif cid == "cohesity":
        website = _fetch_cohesity_website()
        pairs, missing = _match_by_title_and_context(website["records"], production)
        matched = len(pairs)
        match_basis = "one-to-one normalized title; location/card context used for duplicate titles"
    else:
        raise ValueError(cid)

    stable_boundary = (
        website.get("website_total_before") is None
        or website.get("website_total_after") is None
        or website.get("website_total_before") == website.get("website_total_after")
    )
    covered = len(missing) == 0
    status = "WEBSITE_COVERED" if covered else "MISSING_WEBSITE_JOBS"
    if not website.get("pagination_exhausted"):
        status = "WEBSITE_ENUMERATION_UNPROVEN"
    if not stable_boundary:
        status = "WEBSITE_CHANGED_DURING_CHECK"

    return {
        "id": cid,
        "name": company.get("name"),
        "status": status,
        "adapter": adapter,
        "production_count": len(production),
        "website_count": len(website["records"]),
        "website_total_before": website.get("website_total_before"),
        "website_total_after": website.get("website_total_after"),
        "pagination_exhausted": website.get("pagination_exhausted"),
        "website_jobs_matched": matched,
        "website_jobs_missing": len(missing),
        "match_basis": match_basis,
        "website_evidence": website.get("evidence"),
        "missing": missing,
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", default="reports/phase1-website-coverage.json")
    args = parser.parse_args()

    companies = {c["id"]: c for c in load_config().get("companies", [])}
    rows = []
    for cid in TARGET_IDS:
        print(f"Verifying {cid}: official website inventory -> production adapter", flush=True)
        row = _verify_company(companies[cid])
        rows.append(row)
        print(
            f"  {row['status']} website={row['website_count']} app={row['production_count']} "
            f"matched={row['website_jobs_matched']} missing={row['website_jobs_missing']}",
            flush=True,
        )
        if row["missing"]:
            for miss in row["missing"][:20]:
                print(f"    MISSING {miss.get('title')} :: {miss.get('url')}", flush=True)

    payload = {
        "checked_at_epoch": time.time(),
        "rule": "every vacancy enumerated from the official employer careers website must be present in the app output; app extras are allowed",
        "companies": rows,
        "all_covered": all(row["status"] == "WEBSITE_COVERED" for row in rows),
    }
    path = Path(args.output)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")

    md = path.with_suffix(".md")
    lines = [
        "# Phase 1 website coverage verification",
        "",
        "Rule: every vacancy enumerated from the official employer careers website must be present in the app output. Extra app jobs are allowed.",
        "",
        "| Company | Status | Website | App | Matched | Missing | Enumeration | Match basis |",
        "|---|---|---:|---:|---:|---:|---|---|",
    ]
    for row in rows:
        lines.append(
            f"| {row['name']} | **{row['status']}** | {row['website_count']} | {row['production_count']} | "
            f"{row['website_jobs_matched']} | {row['website_jobs_missing']} | "
            f"{'exhausted' if row['pagination_exhausted'] else 'unproven'} | {row['match_basis']} |"
        )
    for row in rows:
        if row["missing"]:
            lines.extend(["", f"## Missing from app: {row['name']}"])
            for miss in row["missing"]:
                lines.append(f"- `{miss.get('website_id')}` — {miss.get('title')} — {miss.get('url')}")
    md.write_text("\n".join(lines) + "\n", encoding="utf-8")

    if not payload["all_covered"]:
        raise SystemExit(2)


if __name__ == "__main__":
    main()
