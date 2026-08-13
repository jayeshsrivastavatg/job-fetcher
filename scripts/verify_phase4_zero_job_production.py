from __future__ import annotations

import argparse
import json
import math
import re
from copy import deepcopy
from pathlib import Path
from urllib.parse import urlparse

import requests
from playwright.sync_api import sync_playwright

from job_fetcher.config import find_company, load_config
from job_fetcher.job_quality import prefer_usable_jobs
from job_fetcher.sources.factory import build_source


LOWES_URL = "https://talent.lowes.com/in/en/search-results"
LOWES_ID_RE = re.compile(r"/in/en/job/(?P<id>JR-[^/?#]+)(?:/|$)", re.I)
SWIGGY_API = "https://swiggy.mynexthire.com/employer/careers/reqlist/get"
SWIGGY_PAYLOAD = {"source": "careers", "code": "", "filterByBuId": -1}
INDIA_RE = re.compile(
    r"\b(?:india|bengaluru|bangalore|gurugram|gurgaon|hyderabad|pune|chennai|noida|mumbai|delhi)\b",
    re.I,
)
FORCE_INDIA_COMPANIES = {"lowes_india", "swiggy"}


def _description_ok(value) -> bool:
    text = re.sub(r"\s+", " ", str(value or "")).strip()
    return len(text) >= 120


def _location_ok(value) -> bool:
    return bool(re.sub(r"\s+", " ", str(value or "")).strip())


def _production(company_id: str) -> dict:
    company = deepcopy(find_company(load_config(), company_id))
    if not company:
        raise RuntimeError(f"unknown_company:{company_id}")
    source = build_source(company)
    raw = list(source.fetch(company) or [])
    usable = list(prefer_usable_jobs(raw) or [])
    ids = {str(j.external_id).strip() for j in usable if str(j.external_id or "").strip()}
    force_india = company_id in FORCE_INDIA_COMPANIES
    india = list(usable) if force_india else [
        j for j in usable if INDIA_RE.search(str(j.location or ""))
    ]
    india_full = [j for j in india if _description_ok(j.description)]
    india_with_location = [j for j in india if _location_ok(j.location)]
    return {
        "raw_count": len(raw),
        "jobs": len(usable),
        "ids": sorted(ids),
        "source_type": type(source).__name__,
        "force_india_scope": force_india,
        "india_jobs": len(india),
        "india_jobs_with_full_description": len(india_full),
        "india_jobs_with_location": len(india_with_location),
        "india_missing_description_ids": sorted(
            str(j.external_id or "") for j in india if not _description_ok(j.description)
        ),
        "india_missing_location_ids": sorted(
            str(j.external_id or "") for j in india if not _location_ok(j.location)
        ),
        "sample": [
            {
                "id": j.external_id,
                "title": j.title,
                "location": j.location,
                "url": j.job_url,
                "description_chars": len(re.sub(r"\s+", " ", str(j.description or "")).strip()),
            }
            for j in usable[:10]
        ],
    }


def _greenhouse_snapshot(board_token: str) -> dict:
    r = requests.get(
        f"https://boards-api.greenhouse.io/v1/boards/{board_token}/jobs",
        params={"content": "true"},
        timeout=45,
        headers={"User-Agent": "Phase4ExactVerifier/1.0"},
    )
    r.raise_for_status()
    rows = list((r.json() or {}).get("jobs") or [])
    ids = {str(x.get("id")) for x in rows if x.get("id") is not None}
    return {"count": len(rows), "ids": ids}


def _swiggy_snapshot() -> dict:
    r = requests.post(
        SWIGGY_API,
        json=SWIGGY_PAYLOAD,
        timeout=45,
        headers={
            "User-Agent": "Phase4ExactVerifier/1.0",
            "Accept": "application/json, text/plain, */*",
            "Origin": "https://careers.swiggy.com",
            "Referer": "https://careers.swiggy.com/#/careers",
        },
    )
    r.raise_for_status()
    rows = list((r.json() or {}).get("reqDetailsBOList") or [])
    ids = {str(x.get("reqId")) for x in rows if x.get("reqId") is not None}
    full_jd_ids = {
        str(x.get("reqId"))
        for x in rows
        if x.get("reqId") is not None and _description_ok(x.get("jdDisplay"))
    }
    return {"count": len(rows), "ids": ids, "full_jd_ids": full_jd_ids}


def _lowes_snapshot() -> dict:
    ids: set[str] = set()
    displayed_count = None
    page_counts = []
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(
            user_agent=(
                "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
                "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
            ),
            locale="en-IN",
        )
        page = context.new_page()
        try:
            page.goto(LOWES_URL, wait_until="domcontentloaded", timeout=90000)
            page.wait_for_timeout(3000)
            body = page.locator("body").inner_text(timeout=5000)
            m = re.search(r"\b(\d+)\s+jobs?\b", body, re.I)
            if not m:
                raise RuntimeError("lowes_official_count_not_found")
            displayed_count = int(m.group(1))
            pages = max(1, math.ceil(displayed_count / 10))
            for index in range(pages):
                url = LOWES_URL if index == 0 else f"{LOWES_URL}?from={index * 10}&s=1"
                if index:
                    page.goto(url, wait_until="domcontentloaded", timeout=90000)
                    page.wait_for_timeout(2200)
                hrefs = page.locator('a[href*="/in/en/job/"]').evaluate_all(
                    "els => els.map(a => a.href).filter(Boolean)"
                )
                page_ids = set()
                for href in hrefs:
                    match = LOWES_ID_RE.search(urlparse(href).path)
                    if match:
                        page_ids.add(match.group("id").upper())
                ids.update(page_ids)
                page_counts.append({"page": index + 1, "ids": len(page_ids), "url": url})
        finally:
            browser.close()
    return {"count": displayed_count, "ids": ids, "page_counts": page_counts}


def verify(company_id: str) -> dict:
    if company_id == "qualtrics":
        before = _greenhouse_snapshot("qualtrics")
        production = _production(company_id)
        after = _greenhouse_snapshot("qualtrics")
    elif company_id == "lowes_india":
        before = _lowes_snapshot()
        production = _production(company_id)
        after = _lowes_snapshot()
    elif company_id == "swiggy":
        before = _swiggy_snapshot()
        production = _production(company_id)
        after = _swiggy_snapshot()
    else:
        raise RuntimeError(f"unsupported_target:{company_id}")

    stable = set(before["ids"]) & set(after["ids"])
    prod_ids = set(production["ids"])
    missing = sorted(stable - prod_ids)
    extras = sorted(prod_ids - (set(before["ids"]) | set(after["ids"])))
    snapshots_complete = (
        len(before["ids"]) == before["count"] and len(after["ids"]) == after["count"]
    )
    india_jd_complete = (
        production["india_jobs_with_full_description"] == production["india_jobs"]
    )
    india_location_complete = (
        production["india_jobs_with_location"] == production["india_jobs"]
    )
    passed = bool(
        snapshots_complete
        and stable
        and not missing
        and not extras
        and production["jobs"] > 0
        and india_jd_complete
        and india_location_complete
    )
    return {
        "company_id": company_id,
        "verdict": "CERTIFIED" if passed else "FAILED",
        "passed": passed,
        "official_before_count": before["count"],
        "official_before_ids": len(before["ids"]),
        "official_after_count": after["count"],
        "official_after_ids": len(after["ids"]),
        "stable_official_ids": len(stable),
        "production": production,
        "missing_stable_ids": missing,
        "extra_production_ids": extras,
        "official_before_page_counts": before.get("page_counts"),
        "official_after_page_counts": after.get("page_counts"),
        "snapshots_complete": snapshots_complete,
        "india_jd_complete": india_jd_complete,
        "india_location_complete": india_location_complete,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--company", choices=["qualtrics", "lowes_india", "swiggy"], required=True)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    result = verify(args.company)
    out = Path(args.output)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(result, indent=2, ensure_ascii=False), encoding="utf-8")
    prod = result["production"]
    print(
        f"{args.company}: verdict={result['verdict']} production={prod['jobs']} "
        f"stable_official={result['stable_official_ids']} missing={len(result['missing_stable_ids'])} "
        f"extras={len(result['extra_production_ids'])} "
        f"india_jd={prod['india_jobs_with_full_description']}/{prod['india_jobs']} "
        f"india_location={prod['india_jobs_with_location']}/{prod['india_jobs']}"
    )
    if result["missing_stable_ids"]:
        print("MISSING", result["missing_stable_ids"][:100])
    if result["extra_production_ids"]:
        print("EXTRAS", result["extra_production_ids"][:100])
    if prod["india_missing_description_ids"]:
        print("INDIA_JD_MISSING", prod["india_missing_description_ids"][:100])
    if prod["india_missing_location_ids"]:
        print("INDIA_LOCATION_MISSING", prod["india_missing_location_ids"][:100])
    return 0 if result["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
