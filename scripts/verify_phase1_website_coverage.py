from __future__ import annotations

import argparse
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
from job_fetcher.sources.factory import build_source
from job_fetcher.sources.http_client import session, timeout_seconds

TARGET_IDS = ("snowflake", "servicenow", "cohesity")
SNOWFLAKE_JOB_RE = re.compile(r"/us/en/job/(?P<id>[^/?#]+)", re.I)
SERVICENOW_JOB_RE = re.compile(r"/jobs/(?P<id>\d{8,})/", re.I)
TOTAL_RE = re.compile(r"\bof\s+([\d,]+)\s+matching jobs\b", re.I)
CLOSED_RE = re.compile(r"\b(job has been closed|job is no longer available|position has been filled|no longer accepting applications)\b", re.I)


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


def _production_workday_ids(jobs):
    return {_clean(getattr(job, "external_id", None)) for job in jobs if _clean(getattr(job, "external_id", None))}


def _extract_anchor_jobs(page, *, kind):
    records = {}
    anchors = page.locator("a[href]")
    for index in range(anchors.count()):
        anchor = anchors.nth(index)
        try:
            href = urljoin(page.url, anchor.get_attribute("href") or "")
            title = _clean(anchor.inner_text(timeout=900))
        except Exception:
            continue
        if not href or not title:
            continue
        if "${" in href or "%7b" in href.lower() or title.casefold() in {"cookies settings", "privacy notice", "site terms"}:
            continue
        match = SNOWFLAKE_JOB_RE.search(urlparse(href).path) if kind == "snowflake" else SERVICENOW_JOB_RE.search(urlparse(href).path + "/")
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
        records[website_id] = {"website_id": website_id, "title": title, "url": href, "card_text": card_text}
    return records


def _fetch_servicenow_website():
    """Walk every employer-branded ServiceNow careers result page in Chromium."""
    base = "https://careers.servicenow.com/jobs/"
    records = {}
    totals = []
    page_fingerprints = set()
    max_pages = 80
    pages_visited = 0

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page(user_agent="Mozilla/5.0 (X11; Linux x86_64) Chrome/131 Safari/537.36")
        page.goto(base, wait_until="domcontentloaded", timeout=90000)
        page.wait_for_timeout(700)
        body = page.locator("body").inner_text(timeout=5000)
        match = TOTAL_RE.search(body)
        first_total = int(match.group(1).replace(",", "")) if match else None
        if first_total is not None:
            totals.append(first_total)
        expected_pages = math.ceil(first_total / 20) if first_total else max_pages

        for page_number in range(1, min(max_pages, expected_pages + 2) + 1):
            target = base if page_number == 1 else f"{base}?page={page_number}"
            if page.url != target:
                page.goto(target, wait_until="domcontentloaded", timeout=90000)
            page.wait_for_timeout(450)
            pages_visited += 1
            try:
                body = page.locator("body").inner_text(timeout=5000)
            except Exception:
                body = ""
            match = TOTAL_RE.search(body)
            if match:
                totals.append(int(match.group(1).replace(",", "")))
            page_records = _extract_anchor_jobs(page, kind="servicenow")
            fingerprint = tuple(sorted(page_records))
            if not fingerprint:
                break
            if fingerprint in page_fingerprints:
                break
            page_fingerprints.add(fingerprint)
            records.update(page_records)
            current_total = totals[-1] if totals else first_total
            if current_total is not None and len(records) >= current_total:
                break
        browser.close()

    before = totals[0] if totals else None
    after = totals[-1] if totals else None
    stable_total = before is None or after is None or before == after
    exhausted = bool(records) and after is not None and len(records) >= after and stable_total
    return {
        "records": list(records.values()),
        "website_total_before": before,
        "website_total_after": after,
        "pagination_exhausted": exhausted,
        "evidence": f"Chromium walked {pages_visited} employer result pages; collected {len(records)} exact /jobs/<posting-id>/ links",
    }


def _is_live_snowflake_detail(record):
    client = session()
    try:
        response = client.get(
            record["url"], timeout=timeout_seconds(), allow_redirects=True,
            headers={"User-Agent": "Mozilla/5.0 PersonalJobFetcherWebsiteAudit/0.2"},
        )
    except Exception:
        return True, "detail_validation_failed_open; conservatively treated as live"
    if response.status_code in {404, 410}:
        return False, f"detail_http_{response.status_code}"
    if response.status_code >= 400:
        return True, f"detail_http_{response.status_code}; conservatively treated as live"
    text = _clean(response.text[:200000])
    if CLOSED_RE.search(text):
        return False, "detail_page_explicitly_closed"
    return True, f"detail_http_{response.status_code}"


def _fetch_snowflake_website():
    """Enumerate Snowflake's official Phenom careers search by its public offset."""
    base = "https://careers.snowflake.com/us/en/search-results"
    records = {}
    fingerprints = set()
    pages_visited = 0
    consecutive_empty = 0
    step = 10
    max_offset = 1200

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page(user_agent="Mozilla/5.0 (X11; Linux x86_64) Chrome/131 Safari/537.36")
        for offset in range(0, max_offset + 1, step):
            page.goto(f"{base}?from={offset}&s=1", wait_until="domcontentloaded", timeout=90000)
            page.wait_for_timeout(750)
            try:
                page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
                page.wait_for_timeout(350)
            except Exception:
                pass
            pages_visited += 1
            page_records = _extract_anchor_jobs(page, kind="snowflake")
            fingerprint = tuple(sorted(page_records))
            new_ids = set(page_records) - set(records)
            if page_records and fingerprint not in fingerprints:
                fingerprints.add(fingerprint)
                records.update(page_records)
            if not page_records or not new_ids:
                consecutive_empty += 1
            else:
                consecutive_empty = 0
            if consecutive_empty >= 2:
                break
        browser.close()

    return {
        "records": list(records.values()),
        "website_total_before": None,
        "website_total_after": None,
        "pagination_exhausted": consecutive_empty >= 2,
        "evidence": f"Chromium walked Phenom offsets in steps of {step} across {pages_visited} pages until two consecutive offsets added no jobs; collected {len(records)} concrete employer detail links",
    }


def _fetch_cohesity_website():
    """Discover and exhaust Cohesity's Workday feed from employer-site network traffic."""
    entry = "https://careers.cohesity.com/open-positions/"
    discovered = None
    discovered_body = None

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(user_agent="Mozilla/5.0 (X11; Linux x86_64) Chrome/131 Safari/537.36", locale="en-US")
        page = context.new_page()

        def on_response(resp):
            nonlocal discovered, discovered_body
            try:
                url = resp.url
                ct = (resp.headers.get("content-type") or "").lower()
                if "/wday/cxs/" not in url or "json" not in ct:
                    return
                payload = resp.json()
                if not isinstance(payload, dict) or "jobPostings" not in payload:
                    return
                if discovered is None:
                    discovered = url
                    try:
                        discovered_body = resp.request.post_data_json
                    except Exception:
                        discovered_body = None
            except Exception:
                pass

        page.on("response", on_response)
        page.goto(entry, wait_until="domcontentloaded", timeout=90000)
        page.wait_for_timeout(2500)
        for _ in range(8):
            try:
                page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
                page.wait_for_timeout(500)
            except Exception:
                break
        browser.close()

    if not discovered:
        return {
            "records": [], "website_total_before": None, "website_total_after": None,
            "pagination_exhausted": False,
            "evidence": "official Cohesity page did not expose a Workday CXS network response in this run",
        }

    client = session()
    limit = 20
    offset = 0
    total_before = None
    total_after = None
    records = {}
    seen_pages = set()
    parsed = urlparse(discovered)
    origin = f"{parsed.scheme}://{parsed.netloc}"
    headers = {
        "Accept": "application/json", "Content-Type": "application/json", "Origin": origin,
        "Referer": entry, "User-Agent": "Mozilla/5.0 PersonalJobFetcherWebsiteAudit/0.2",
    }

    for _ in range(400):
        body = dict(discovered_body or {})
        body["appliedFacets"] = body.get("appliedFacets") or {}
        body["searchText"] = body.get("searchText") or ""
        body["limit"] = limit
        body["offset"] = offset
        response = client.post(discovered, json=body, headers=headers, timeout=timeout_seconds())
        response.raise_for_status()
        data = response.json()
        items = data.get("jobPostings") or []
        total = int(data.get("total") or 0)
        if total_before is None:
            total_before = total
        total_after = total
        fingerprint = tuple(_clean(x.get("externalPath")) for x in items)
        if fingerprint in seen_pages and fingerprint:
            break
        if fingerprint:
            seen_pages.add(fingerprint)
        for item in items:
            bullets = item.get("bulletFields") or []
            req = _clean(bullets[0] if isinstance(bullets, list) and bullets else None)
            path = _clean(item.get("externalPath"))
            key = req or path
            if not key:
                continue
            records[key] = {
                "website_id": key, "title": _clean(item.get("title")), "url": urljoin(origin, path),
                "card_text": _clean(item.get("locationsText")),
            }
        offset += len(items)
        if not items or (total and offset >= total):
            break
        time.sleep(0.12)

    stable = total_before is None or total_after is None or total_before == total_after
    exhausted = stable and total_after is not None and len(records) >= total_after
    return {
        "records": list(records.values()),
        "website_total_before": total_before,
        "website_total_after": total_after,
        "pagination_exhausted": exhausted,
        "evidence": f"official employer page exposed {discovered}; replayed discovered Workday pagination to {len(records)} unique requisitions",
    }


def _match_by_title_and_context(website_records, production_jobs):
    candidates = defaultdict(list)
    for index, job in enumerate(production_jobs):
        candidates[_norm(getattr(job, "title", None))].append(index)
    unused = set(range(len(production_jobs)))
    matched, missing = [], []
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
                if loc and (loc in context or context in loc or all(token in context for token in loc.split()[:2])):
                    chosen = i
                    break
        if chosen is None:
            chosen = possible[0]
        unused.remove(chosen)
        matched.append({"website": record, "production": _job_payload(production_jobs[chosen])})
    return matched, missing


def _verify_once(company: dict):
    adapter, production = _production_jobs(company)
    cid = company["id"]
    ignored_stale = []

    if cid == "servicenow":
        website = _fetch_servicenow_website()
        app_ids = _production_numeric_ids(production)
        missing = [row for row in website["records"] if row["website_id"] not in app_ids]
        matched = len(website["records"]) - len(missing)
        match_basis = "exact employer detail posting ID == SmartRecruiters posting ID"
    elif cid == "snowflake":
        website = _fetch_snowflake_website()
        pairs, missing = _match_by_title_and_context(website["records"], production)
        live_missing = []
        for record in missing:
            live, reason = _is_live_snowflake_detail(record)
            record = dict(record)
            if live:
                record["detail_validation"] = reason
                live_missing.append(record)
            else:
                record["ignored_reason"] = reason
                ignored_stale.append(record)
        missing = live_missing
        matched = len(website["records"]) - len(missing) - len(ignored_stale)
        match_basis = "one-to-one normalized title/location against Ashby; unmatched official links are detail-validated so stale 404/410/closed pages do not count"
    elif cid == "cohesity":
        website = _fetch_cohesity_website()
        app_ids = _production_workday_ids(production)
        missing = [row for row in website["records"] if row["website_id"] not in app_ids]
        matched = len(website["records"]) - len(missing)
        match_basis = "exact Workday requisition ID from the official website's own discovered CXS feed"
    else:
        raise ValueError(cid)

    stable_boundary = (
        website.get("website_total_before") is None or website.get("website_total_after") is None
        or website.get("website_total_before") == website.get("website_total_after")
    )
    status = "WEBSITE_COVERED" if not missing else "MISSING_WEBSITE_JOBS"
    if not website.get("pagination_exhausted"):
        status = "WEBSITE_ENUMERATION_UNPROVEN"
    if not stable_boundary:
        status = "WEBSITE_CHANGED_DURING_CHECK"

    return {
        "id": cid, "name": company.get("name"), "status": status, "adapter": adapter,
        "production_count": len(production), "website_count": len(website["records"]),
        "website_total_before": website.get("website_total_before"), "website_total_after": website.get("website_total_after"),
        "pagination_exhausted": website.get("pagination_exhausted"), "website_jobs_matched": matched,
        "website_jobs_missing": len(missing), "ignored_stale_or_template": len(ignored_stale),
        "match_basis": match_basis, "website_evidence": website.get("evidence"), "missing": missing, "ignored": ignored_stale,
    }


def _verify_company(company: dict):
    row = _verify_once(company)
    if row["status"] == "WEBSITE_CHANGED_DURING_CHECK":
        time.sleep(1.0)
        row = _verify_once(company)
    return row


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
            f"  {row['status']} website={row['website_count']} app={row['production_count']} matched={row['website_jobs_matched']} "
            f"missing={row['website_jobs_missing']} ignored_stale={row['ignored_stale_or_template']}", flush=True,
        )
        print(f"    evidence: {row['website_evidence']}", flush=True)
        for miss in row["missing"][:20]:
            print(f"    MISSING {miss.get('website_id')} :: {miss.get('title')} :: {miss.get('url')}", flush=True)
        for ignored in row["ignored"][:10]:
            print(f"    IGNORED_STALE {ignored.get('title')} :: {ignored.get('ignored_reason')}", flush=True)

    payload = {
        "checked_at_epoch": time.time(),
        "rule": "every current vacancy enumerated from the official employer careers website must be present in the app output; app extras are allowed",
        "companies": rows, "all_covered": all(row["status"] == "WEBSITE_COVERED" for row in rows),
    }
    path = Path(args.output)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")

    md = path.with_suffix(".md")
    lines = [
        "# Phase 1 website coverage verification", "",
        "Rule: every current vacancy enumerated from the official employer careers website must be present in the app output. Extra app jobs are allowed.", "",
        "| Company | Status | Website | App | Matched | Missing | Stale ignored | Enumeration | Match basis |",
        "|---|---|---:|---:|---:|---:|---:|---|---|",
    ]
    for row in rows:
        lines.append(
            f"| {row['name']} | **{row['status']}** | {row['website_count']} | {row['production_count']} | {row['website_jobs_matched']} | "
            f"{row['website_jobs_missing']} | {row['ignored_stale_or_template']} | {'exhausted' if row['pagination_exhausted'] else 'unproven'} | {row['match_basis']} |"
        )
    for row in rows:
        if row["missing"]:
            lines.extend(["", f"## Missing from app: {row['name']}"])
            for miss in row["missing"]:
                lines.append(f"- `{miss.get('website_id')}` — {miss.get('title')} — {miss.get('url')}")
        if row["ignored"]:
            lines.extend(["", f"## Ignored stale/template links: {row['name']}"])
            for ignored in row["ignored"]:
                lines.append(f"- {ignored.get('title')} — {ignored.get('ignored_reason')} — {ignored.get('url')}")
    md.write_text("\n".join(lines) + "\n", encoding="utf-8")

    if not payload["all_covered"]:
        raise SystemExit(2)


if __name__ == "__main__":
    main()
