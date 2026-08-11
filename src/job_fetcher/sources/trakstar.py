from __future__ import annotations

import os
import re
from urllib.parse import urljoin, urlparse

from bs4 import BeautifulSoup

from job_fetcher.models import Job
from job_fetcher.sources.base import JobSource
from job_fetcher.sources.generic_extract import clean_text, dedupe
from job_fetcher.sources.http_client import session, timeout_seconds
from job_fetcher.sources.playwright_auto import PlaywrightAutoSource

JOB_RE = re.compile(r"/jobs/(?P<id>[a-z0-9_-]+)(?:[/?#]|$)", re.I)


class TrakstarSource(JobSource):
    """Public Trakstar Hire / Recruiterbox job-board source."""

    def fetch(self, company):
        src = company.get("source") or {}
        entry = src.get("entry_url") or company["career_url"]
        static_error = None
        try:
            r = session().get(entry, timeout=timeout_seconds(), allow_redirects=True)
            r.raise_for_status()
            jobs = self.parse_listing(company, r.text, r.url)
            if jobs:
                return jobs
        except Exception as exc:
            static_error = exc

        if src.get("disable_browser_fallback") or os.getenv("JOB_FETCHER_DISABLE_BROWSER", "0") == "1":
            raise RuntimeError(f"trakstar_static_fetch_failed: {static_error or 'no jobs found'}")
        jobs = self.normalize_browser_jobs(company, PlaywrightAutoSource().fetch(company))
        if jobs:
            return jobs
        raise RuntimeError("trakstar_no_jobs_detected")

    @staticmethod
    def parse_listing(company, html: str, base_url: str):
        soup = BeautifulSoup(html, "html.parser")
        jobs = []
        seen = set()
        for a in soup.select('a[href*="/jobs/"]'):
            url = urljoin(base_url, a.get("href") or "")
            m = JOB_RE.search(urlparse(url).path)
            if not m:
                continue
            jid = m.group("id")
            if jid in seen:
                continue
            title = clean_text(a.get_text(" ", strip=True))
            if not title or len(title) < 4:
                continue
            context = clean_text(a.parent.get_text(" ", strip=True)) if a.parent else ""
            loc = TrakstarSource._location(context, title)
            seen.add(jid)
            jobs.append(Job(company["id"], company["name"], "trakstar", jid, title, loc,
                            None, url, None, {"card_text": context}))
        return dedupe(jobs)

    @staticmethod
    def normalize_browser_jobs(company, jobs):
        out = []
        for job in jobs:
            jid = None
            if job.job_url:
                m = JOB_RE.search(urlparse(job.job_url).path)
                if m:
                    jid = m.group("id")
            if not jid:
                continue
            job.company_id = company["id"]
            job.company_name = company["name"]
            job.source_type = "trakstar"
            job.external_id = jid
            out.append(job)
        return dedupe(out)

    @staticmethod
    def _location(context, title):
        text = (context or "").replace(title or "", " ")
        m = re.search(r"\b([A-Za-z .'-]+,\s*[A-Za-z .'-]+,\s*India)\b", text)
        return clean_text(m.group(1)) if m else None
