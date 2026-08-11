from __future__ import annotations

import os
import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from urllib.parse import urljoin, urlparse

from bs4 import BeautifulSoup
from playwright.sync_api import sync_playwright

from job_fetcher.models import Job
from job_fetcher.sources.base import JobSource
from job_fetcher.sources.browser_limit import BROWSER_SEMAPHORE
from job_fetcher.sources.generic_extract import clean_text, dedupe, extract_embedded_json, extract_jobs_from_json, extract_jsonld
from job_fetcher.sources.http_client import session, timeout_seconds


COUNT_RE = re.compile(r"\b(?P<count>\d+)\s+openings\b", re.I)
DETAIL_RE = re.compile(r"/(?:jobs?|careers)/(?:job/)?(?P<id>[A-Za-z0-9_-]{4,})(?:[/?#]|$)", re.I)


class UrbanCompanySource(JobSource):
    """Strict first-party Urban Company open-positions adapter.

    The official board publishes an explicit `N openings` count. We accept an empty
    result only when that first-party page explicitly says `0 openings`; otherwise
    we use the rendered public board and require the number of canonical vacancies
    to equal the advertised count before marking the snapshot complete.
    """

    URL = "https://careers.urbancompany.com/jobs"

    def fetch(self, company):
        src = company.get("source") or {}
        try:
            r = session().get(self.URL, timeout=timeout_seconds(), allow_redirects=True)
            r.raise_for_status()
            jobs, count = self._parse(company, r.text, r.url)
            if count == 0:
                return []
            if count is not None and len(jobs) >= count:
                return self._finish(company, jobs, count)
        except Exception:
            jobs, count = [], None

        browser_jobs, browser_count = self._browser(company, src)
        count = browser_count if browser_count is not None else count
        jobs = dedupe([*jobs, *browser_jobs])
        if count == 0:
            return []
        if count is None:
            raise RuntimeError(f"urban_company_opening_count_unavailable: collected={len(jobs)}")
        if len(jobs) != count:
            raise RuntimeError(f"urban_company_incomplete: advertised={count} collected={len(jobs)}")
        return self._finish(company, jobs, count)

    @classmethod
    def _parse(cls, company, html: str, base_url: str):
        soup = BeautifulSoup(html, "html.parser")
        text = clean_text(soup.get_text(" ", strip=True)) or ""
        cm = COUNT_RE.search(text)
        count = int(cm.group("count")) if cm else None
        jobs = []
        jobs.extend(extract_jsonld(company, html, base_url, "urban_company"))
        jobs.extend(extract_embedded_json(company, html, base_url, "urban_company"))
        for a in soup.select("a[href]"):
            absolute = urljoin(base_url, a.get("href") or "")
            if urlparse(absolute).netloc.lower() != urlparse(base_url).netloc.lower():
                continue
            match = DETAIL_RE.search(urlparse(absolute).path)
            if not match:
                continue
            title = clean_text(a.get_text(" ", strip=True))
            if not title or title.lower() in {"careers", "jobs", "apply", "apply now", "view role", "view job"}:
                continue
            jobs.append(Job(company["id"], company["name"], "urban_company", match.group("id"),
                            title, None, None, absolute, None, {"source_page": base_url}))
        return cls._canonical(company, dedupe(jobs)), count

    @classmethod
    def _browser(cls, company, src):
        timeout_ms = int(src.get("browser_timeout_ms") or os.getenv("JOB_FETCHER_BROWSER_TIMEOUT_MS", "60000"))
        payloads = []
        with BROWSER_SEMAPHORE:
            with sync_playwright() as p:
                browser = p.chromium.launch(headless=True)
                context = browser.new_context(user_agent="Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/131 Safari/537.36", locale="en-US")
                page = context.new_page()
                def on_response(resp):
                    try:
                        if "json" in (resp.headers.get("content-type") or "").lower():
                            payloads.append((resp.url, resp.json()))
                    except Exception:
                        pass
                page.on("response", on_response)
                page.goto(cls.URL, wait_until="domcontentloaded", timeout=timeout_ms)
                page.wait_for_timeout(1400)
                for _ in range(12):
                    page.evaluate("window.scrollTo(0, document.body ? document.body.scrollHeight : 0)")
                    page.wait_for_timeout(400)
                html = page.content()
                final_url = page.url
                body_text = clean_text(page.locator("body").inner_text(timeout=5000)) or ""
                browser.close()
        cm = COUNT_RE.search(body_text)
        count = int(cm.group("count")) if cm else None
        jobs, _ = cls._parse(company, html, final_url)
        for response_url, payload in payloads:
            for job in extract_jobs_from_json(company, payload, final_url, "urban_company"):
                raw = dict(job.raw or {})
                raw["_source_response_url"] = response_url
                raw["_fetch_via_browser"] = True
                job.raw = raw
                jobs.append(job)
        return cls._canonical(company, dedupe(jobs)), count

    @classmethod
    def _canonical(cls, company, jobs):
        out = []
        for job in jobs:
            title = clean_text(job.title)
            url = str(job.job_url or "")
            path = urlparse(url).path
            match = DETAIL_RE.search(path)
            if not title or not match:
                continue
            low = title.casefold()
            if low in {"products", "support", "contact sales", "contact support", "developers", "open positions", "open roles"}:
                continue
            job.company_id = company["id"]
            job.company_name = company["name"]
            job.source_type = "urban_company"
            job.external_id = str(job.external_id or match.group("id"))
            out.append(job)
        return dedupe(out)

    @staticmethod
    def _finish(company, jobs, count):
        for job in jobs:
            raw = dict(job.raw or {})
            raw["_provider_total"] = count
            raw["_provider_returned"] = len(jobs)
            raw["_provider_complete"] = len(jobs) == count
            job.raw = raw
        return jobs
