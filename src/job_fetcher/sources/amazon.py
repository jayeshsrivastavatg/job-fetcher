from __future__ import annotations

import os
import re
from urllib.parse import urljoin, urlparse

from bs4 import BeautifulSoup
from playwright.sync_api import sync_playwright

from job_fetcher.models import Job
from job_fetcher.sources.base import JobSource
from job_fetcher.sources.browser_limit import BROWSER_SEMAPHORE
from job_fetcher.sources.generic_extract import clean_text, dedupe, extract_jobs_from_json
from job_fetcher.sources.http_client import session, timeout_seconds

JOB_RE = re.compile(r"/(?:[a-z]{2}/)?jobs/(?P<id>[0-9]+)/", re.I)
POSTED_RE = re.compile(r"\bPosted:\s*([^|•]+)", re.I)
LOCATION_RE = re.compile(r"\bLocation:\s*([^|•]+)", re.I)


class AmazonSource(JobSource):
    """Amazon.jobs public search/result crawler.

    Uses the public search UI and public /jobs/<id>/ detail links. It does not
    call private applicant APIs or attempt to bypass login/challenges.
    """

    def fetch(self, company):
        src = company.get("source") or {}
        entry = src.get("entry_url") or company["career_url"]
        max_pages = max(1, int(src.get("max_pages") or 60))
        client = session()
        jobs = []
        seen_pages = set()
        url = entry
        static_error = None
        for _ in range(max_pages):
            if not url or url in seen_pages:
                break
            seen_pages.add(url)
            try:
                r = client.get(url, timeout=timeout_seconds(), allow_redirects=True)
                r.raise_for_status()
            except Exception as exc:
                static_error = exc
                break
            batch = self.parse_search_page(company, r.text, r.url)
            before = len(jobs)
            jobs = dedupe([*jobs, *batch])
            next_url = self.next_page(r.text, r.url)
            if not next_url or (not batch and len(jobs) == before):
                break
            url = next_url
        if jobs:
            return jobs
        if src.get("disable_browser_fallback") or os.getenv("JOB_FETCHER_DISABLE_BROWSER", "0") == "1":
            raise RuntimeError(f"amazon_static_fetch_failed: {static_error or 'no public job cards found'}")
        return self._fetch_browser(company, entry, max_pages)

    @staticmethod
    def parse_search_page(company, html: str, base_url: str):
        soup = BeautifulSoup(html, "html.parser")
        jobs = []
        seen = set()
        for a in soup.select('a[href*="/jobs/"]'):
            href = a.get("href") or ""
            url = urljoin(base_url, href)
            m = JOB_RE.search(urlparse(url).path)
            if not m:
                continue
            jid = m.group("id")
            if jid in seen:
                continue
            title = clean_text(a.get_text(" ", strip=True))
            if not title or title.lower() in {"apply now", "read more", "learn more"}:
                continue
            parent = a.parent
            text = ""
            for _ in range(5):
                if parent is None:
                    break
                text = clean_text(parent.get_text(" ", strip=True)) or ""
                if len(text) > len(title) + 5:
                    break
                parent = parent.parent
            lm = LOCATION_RE.search(text or "")
            loc = clean_text(lm.group(1)) if lm else None
            pm = POSTED_RE.search(text or "")
            posted = clean_text(pm.group(1)) if pm else None
            seen.add(jid)
            jobs.append(Job(company["id"], company["name"], "amazon", jid, title, loc,
                            None, url, posted, {"card_text": text}))
        return dedupe(jobs)

    @staticmethod
    def next_page(html: str, base_url: str):
        soup = BeautifulSoup(html, "html.parser")
        nxt = soup.select_one('a[rel~="next"][href]')
        if nxt:
            return urljoin(base_url, nxt.get("href"))
        for a in soup.select("a[href]"):
            text = (a.get_text(" ", strip=True) or "").strip().lower()
            aria = (a.get("aria-label") or "").strip().lower()
            if text in {"next", "next page", "›", "»", ">"} or "next" in aria:
                return urljoin(base_url, a.get("href"))
        return None

    def _fetch_browser(self, company, entry, max_pages):
        src = company.get("source") or {}
        timeout_ms = int(src.get("browser_timeout_ms") or os.getenv("JOB_FETCHER_BROWSER_TIMEOUT_MS", "60000"))
        jobs = []
        payloads = []
        with BROWSER_SEMAPHORE:
            with sync_playwright() as p:
                browser = p.chromium.launch(headless=True)
                page = browser.new_page(locale="en-US")

                def on_response(resp):
                    try:
                        if "json" in (resp.headers.get("content-type") or "").lower():
                            payloads.append(resp.json())
                    except Exception:
                        pass

                page.on("response", on_response)
                page.goto(entry, wait_until="domcontentloaded", timeout=timeout_ms)
                for _ in range(max_pages):
                    page.wait_for_timeout(800)
                    batch = self.parse_search_page(company, page.content(), page.url)
                    before = len(jobs)
                    jobs = dedupe([*jobs, *batch])
                    try:
                        candidates = [
                            page.locator('a[rel~="next"]'),
                            page.locator('a[aria-label*="next" i]'),
                            page.get_by_text("Next", exact=True),
                            page.get_by_text("Next page", exact=False),
                        ]
                        nxt = next((x.first for x in candidates if x.count() and x.first.is_visible()), None)
                        if nxt is None:
                            break
                        href = nxt.get_attribute("href")
                        if href:
                            page.goto(urljoin(page.url, href), wait_until="domcontentloaded", timeout=timeout_ms)
                        else:
                            nxt.click(timeout=2000)
                            page.wait_for_load_state("domcontentloaded", timeout=timeout_ms)
                    except Exception:
                        break
                    if len(jobs) == before and not batch:
                        break
                final_url = page.url
                browser.close()
        for payload in payloads:
            for j in extract_jobs_from_json(company, payload, final_url, "amazon_browser_json"):
                j.source_type = "amazon"
                # Amazon browser payloads sometimes include the numeric requisition
                # id but omit the redundant detail URL. The public detail route is
                # stable, so reconstruct it rather than storing a title-only row.
                if not j.job_url and j.external_id and str(j.external_id).isdigit():
                    j.job_url = f"https://www.amazon.jobs/en/jobs/{j.external_id}/"
                    raw = dict(j.raw or {}) if isinstance(j.raw, dict) else {}
                    raw["_canonical_job_url_reconstructed"] = True
                    j.raw = raw
                jobs.append(j)
        jobs = dedupe(jobs)
        if not jobs:
            raise RuntimeError("amazon_no_jobs_detected")
        return jobs
