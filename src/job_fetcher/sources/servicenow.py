from __future__ import annotations

import math
import re
import time
from copy import deepcopy
from urllib.parse import urljoin, urlparse

from bs4 import BeautifulSoup
from playwright.sync_api import sync_playwright

from job_fetcher.models import Job
from job_fetcher.sources.base import JobSource
from job_fetcher.sources.generic_extract import extract_jsonld
from job_fetcher.sources.http_client import session, timeout_seconds
from job_fetcher.sources.smartrecruiters import SmartRecruitersSource


_BASE = "https://careers.servicenow.com/jobs/"
_JOB_RE = re.compile(r"/jobs/(?P<id>\d{8,})/", re.I)
_TOTAL_RE = re.compile(r"\bof\s+([\d,]+)\s+matching jobs\b", re.I)
_USER_AGENT = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/131 Safari/537.36"


def _clean(value):
    return re.sub(r"\s+", " ", str(value or "")).strip()


def _numeric_id(value):
    match = re.search(r"(\d{8,})", str(value or ""))
    return match.group(1) if match else None


class ServiceNowSource(JobSource):
    """SmartRecruiters first, official ServiceNow careers site as a coverage guard.

    ServiceNow's employer site has been observed publishing current vacancies that
    are absent from the public SmartRecruiters company listing. SmartRecruiters
    remains the primary structured API, but the fetch is accepted only when every
    current vacancy on careers.servicenow.com is present in the returned set.

    The employer job board is server-rendered for a browser. Plain requests receive
    the first page even for later page parameters, so exhaustive enumeration uses
    Chromium with a fresh browser context per page. Extra API jobs are deliberately
    retained: the required invariant is official_website_jobs <= returned_jobs.
    """

    def fetch(self, company):
        provider_company = deepcopy(company)
        provider_company["source"] = {"type": "smartrecruiters", "company_identifier": "ServiceNow"}
        provider_jobs = list(SmartRecruitersSource().fetch(provider_company) or [])

        website_records, expected, final_total = self._enumerate_official_site()
        if expected is None or final_total is None:
            raise RuntimeError("servicenow_website_total_unavailable")
        if expected != final_total:
            raise RuntimeError(
                f"servicenow_website_changed_during_fetch: before={expected} after={final_total}"
            )
        if len(website_records) < expected:
            raise RuntimeError(
                f"servicenow_website_incomplete_pagination: expected={expected} enumerated={len(website_records)}"
            )

        by_numeric_id = {}
        for job in provider_jobs:
            jid = _numeric_id(getattr(job, "external_id", None)) or _numeric_id(getattr(job, "job_url", None))
            if jid:
                by_numeric_id[jid] = job

        missing = [record for jid, record in website_records.items() if jid not in by_numeric_id]
        supplements = [self._fetch_official_detail(company, record) for record in missing]

        out = provider_jobs + supplements
        returned_ids = {
            _numeric_id(getattr(job, "external_id", None)) or _numeric_id(getattr(job, "job_url", None))
            for job in out
        }
        returned_ids.discard(None)
        still_missing = sorted(set(website_records) - returned_ids)
        if still_missing:
            raise RuntimeError(
                f"servicenow_website_coverage_failed: missing={len(still_missing)} ids={','.join(still_missing[:10])}"
            )
        return out

    @staticmethod
    def _parse_browser_page(page):
        body = page.locator("body").inner_text(timeout=10000)
        total_match = _TOTAL_RE.search(body)
        total = int(total_match.group(1).replace(",", "")) if total_match else None

        records = {}
        anchors = page.locator("a[href]")
        for index in range(anchors.count()):
            anchor = anchors.nth(index)
            try:
                href = urljoin(page.url, anchor.get_attribute("href") or "")
                title = _clean(anchor.inner_text(timeout=800))
            except Exception:
                continue
            match = _JOB_RE.search(urlparse(href).path + "/")
            if not match or not title:
                continue
            jid = match.group("id")
            records[jid] = {"id": jid, "title": title, "url": href}
        return total, records

    def _enumerate_official_site(self):
        records = {}
        totals = []
        max_pages = 80

        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=True)

            # The first page establishes the official display total. A fresh
            # context avoids sticky state/cached pagination from previous pages.
            context = browser.new_context(user_agent=_USER_AGENT, locale="en-US")
            page = context.new_page()
            page.goto(
                f"{_BASE}?page=1&pagesize=20&audit={int(time.time() * 1000)}",
                wait_until="domcontentloaded", timeout=90000,
            )
            page.wait_for_timeout(500)
            expected, first_records = self._parse_browser_page(page)
            context.close()
            if expected is None:
                browser.close()
                return {}, None, None
            totals.append(expected)
            records.update(first_records)

            expected_pages = min(max_pages, math.ceil(expected / 20) + 2)
            for page_number in range(2, expected_pages + 1):
                best_records = {}
                page_total = None

                # The employer board occasionally serves a stale/repeated page.
                # Retry each page with a brand-new context and cache-busting value;
                # accept the attempt as soon as it contributes unseen posting IDs.
                for attempt in range(3):
                    context = browser.new_context(user_agent=_USER_AGENT, locale="en-US")
                    page = context.new_page()
                    page.goto(
                        f"{_BASE}?page={page_number}&pagesize=20&audit={int(time.time() * 1000)}-{attempt}",
                        wait_until="domcontentloaded", timeout=90000,
                    )
                    page.wait_for_timeout(450 + attempt * 200)
                    page_total, current = self._parse_browser_page(page)
                    context.close()
                    if len(current) > len(best_records):
                        best_records = current
                    if set(current) - set(records):
                        break
                    time.sleep(0.25)

                if page_total is not None:
                    totals.append(page_total)
                records.update(best_records)
                if len(records) >= expected:
                    break

            # Boundary re-read prevents a moving board from receiving a false
            # completeness guarantee.
            context = browser.new_context(user_agent=_USER_AGENT, locale="en-US")
            page = context.new_page()
            page.goto(
                f"{_BASE}?page=1&pagesize=20&audit=final-{int(time.time() * 1000)}",
                wait_until="domcontentloaded", timeout=90000,
            )
            page.wait_for_timeout(450)
            final_total, _ = self._parse_browser_page(page)
            context.close()
            browser.close()

        return records, expected, final_total

    def _fetch_official_detail(self, company, record):
        client = session()
        response = client.get(
            record["url"], timeout=timeout_seconds(), allow_redirects=True,
            headers={"User-Agent": "Mozilla/5.0 PersonalJobFetcher/0.4"},
        )
        response.raise_for_status()
        jsonld = extract_jsonld(company, response.text, response.url, source_type="servicenow_official")
        if jsonld:
            job = jsonld[0]
            job.external_id = record["id"]
            job.job_url = response.url
            raw = dict(job.raw or {})
            raw["_servicenow_website_coverage_supplement"] = True
            raw["_website_listing_title"] = record["title"]
            job.raw = raw
            return job

        soup = BeautifulSoup(response.text, "html.parser")
        description = None
        main = soup.select_one("main") or soup.select_one("article")
        if main:
            description = _clean(main.get_text(" ", strip=True))
        return Job(
            company["id"], company["name"], "servicenow_official", record["id"],
            record["title"], None, description, response.url, None,
            {"_servicenow_website_coverage_supplement": True},
        )
