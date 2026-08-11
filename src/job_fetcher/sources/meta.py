from __future__ import annotations

import json
import os
import re
from urllib.parse import urlencode, urljoin, urlparse

from bs4 import BeautifulSoup
from playwright.sync_api import sync_playwright

from job_fetcher.models import Job
from job_fetcher.sources.base import JobSource
from job_fetcher.sources.browser_limit import BROWSER_SEMAPHORE
from job_fetcher.sources.generic_extract import clean_text, dedupe, extract_jobs_from_json, walk_objects
from job_fetcher.sources.http_client import session, timeout_seconds

JOB_RE = re.compile(r"/profile/job_details/(?P<id>[0-9]+)/?", re.I)
BOT_RE = re.compile(r"(captcha|verify you are human|access denied|unusual traffic)", re.I)


class MetaSource(JobSource):
    """Meta public job-search crawler using location-filtered public pages."""

    DEFAULT_OFFICES = [
        "Bangalore, India", "Gurgaon, India", "Hyderabad, India",
        "Mumbai, India", "New Delhi, India",
    ]

    def fetch(self, company):
        src = company.get("source") or {}
        offices = src.get("offices") or self.DEFAULT_OFFICES
        max_pages = max(1, int(src.get("max_pages") or 40))
        jobs = []
        client = session()
        static_failed = False
        static_errors = []
        for office in offices:
            office_jobs = []
            for page in range(1, max_pages + 1):
                url = self.search_url(src.get("entry_url") or company["career_url"], office, page)
                try:
                    r = client.get(url, timeout=timeout_seconds(), allow_redirects=True)
                    r.raise_for_status()
                except Exception as exc:
                    static_failed = True
                    static_errors.append(f"{office} page {page}: {exc}")
                    break
                if BOT_RE.search(r.text[:20000]):
                    static_failed = True
                    break
                batch = self.parse_search_page(company, r.text, r.url, default_location=office)
                before = len(office_jobs)
                office_jobs = dedupe([*office_jobs, *batch])
                if not batch or len(office_jobs) == before:
                    break
            jobs.extend(office_jobs)
        jobs = dedupe(jobs)
        if jobs:
            return jobs
        if src.get("disable_browser_fallback") or os.getenv("JOB_FETCHER_DISABLE_BROWSER", "0") == "1":
            reason = "; ".join(static_errors) if static_errors else ("static request failed" if static_failed else "no public job cards found")
            raise RuntimeError(f"meta_static_fetch_failed: {reason}")
        return self._fetch_browser(company, offices, max_pages)

    @staticmethod
    def search_url(entry: str, office: str, page: int) -> str:
        parsed = urlparse(entry)
        base = f"{parsed.scheme or 'https'}://{parsed.netloc or 'www.metacareers.com'}/jobsearch/"
        return base + "?" + urlencode({"offices[0]": office, "page": page})

    @staticmethod
    def parse_search_page(company, html: str, base_url: str, default_location=None):
        soup = BeautifulSoup(html, "html.parser")
        jobs = []
        seen = set()
        for a in soup.select('a[href*="/profile/job_details/"]'):
            href = a.get("href") or ""
            url = urljoin(base_url, href)
            m = JOB_RE.search(urlparse(url).path)
            if not m:
                continue
            jid = m.group("id")
            if jid in seen:
                continue
            title = clean_text(a.get_text(" ", strip=True))
            container = a.parent
            text = ""
            for _ in range(5):
                if container is None:
                    break
                text = clean_text(container.get_text(" ", strip=True)) or ""
                if title and len(text) > len(title):
                    break
                container = container.parent
            if not title or len(title) < 3:
                heading = container.find(["h2", "h3", "h4"]) if container else None
                title = clean_text(heading.get_text(" ", strip=True)) if heading else None
            if not title:
                continue
            location = default_location
            # When the search itself is scoped to one office, that filter is the
            # most reliable location signal; card text often concatenates the
            # title immediately before the city (e.g. "Design Bangalore, India").
            if not default_location or default_location.lower() not in (text or "").lower():
                stripped = (text or "").replace(title, " ", 1)
                lm = re.search(r"\b([A-Za-z .'-]+,\s*India)\b", stripped, re.I)
                if lm:
                    location = clean_text(lm.group(1))
            seen.add(jid)
            jobs.append(Job(company["id"], company["name"], "meta", jid, title, location,
                            None, url, None, {"card_text": text}))
        return dedupe(jobs)

    def _fetch_browser(self, company, offices, max_pages):
        src = company.get("source") or {}
        timeout_ms = int(src.get("browser_timeout_ms") or os.getenv("JOB_FETCHER_BROWSER_TIMEOUT_MS", "60000"))
        jobs = []
        with BROWSER_SEMAPHORE:
            with sync_playwright() as p:
                browser = p.chromium.launch(headless=True)
                context = browser.new_context(locale="en-US")
                page = context.new_page()
                payloads = []

                def on_response(resp):
                    try:
                        if "json" in (resp.headers.get("content-type") or "").lower():
                            payloads.append(resp.json())
                    except Exception:
                        pass

                page.on("response", on_response)
                for office in offices:
                    for n in range(1, max_pages + 1):
                        url = self.search_url(src.get("entry_url") or company["career_url"], office, n)
                        page.goto(url, wait_until="domcontentloaded", timeout=timeout_ms)
                        page.wait_for_timeout(1000)
                        if "/login" in page.url or BOT_RE.search(page.title()):
                            browser.close()
                            raise RuntimeError("authentication_required_or_anti_bot")
                        html = page.content()
                        batch = self.parse_search_page(company, html, page.url, office)
                        before = len(jobs)
                        jobs = dedupe([*jobs, *batch])
                        if not batch or len(jobs) == before:
                            break
                final_url = page.url
                browser.close()
        for payload in payloads:
            for j in extract_jobs_from_json(company, payload, final_url, "meta_browser_json"):
                j.source_type = "meta"
                jobs.append(j)
        jobs = dedupe(jobs)
        if not jobs:
            raise RuntimeError("meta_no_jobs_detected")
        return jobs
