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

ROLE_RE = re.compile(r"/roles/(?P<id>\d+)(?:[/?#]|$)", re.I)


class GoldmanSource(JobSource):
    """Goldman Sachs public higher.gs.com careers source."""

    def fetch(self, company):
        src = company.get("source") or {}
        entry = src.get("entry_url") or "https://higher.gs.com/results"
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
            raise RuntimeError(f"goldman_static_fetch_failed: {static_error or 'dynamic results page'}")

        jobs = self.normalize_browser_jobs(company, PlaywrightAutoSource().fetch(company))
        if jobs:
            return jobs
        raise RuntimeError("goldman_no_jobs_detected")

    @staticmethod
    def parse_listing(company, html: str, base_url: str):
        soup = BeautifulSoup(html, "html.parser")
        out = []
        seen = set()
        for a in soup.select('a[href*="/roles/"]'):
            url = urljoin(base_url, a.get("href") or "")
            m = ROLE_RE.search(urlparse(url).path)
            if not m:
                continue
            jid = m.group("id")
            if jid in seen:
                continue
            title = clean_text(a.get_text(" ", strip=True))
            if not title or title.lower() in {"apply", "apply now", "learn more", "view job"}:
                title = GoldmanSource._title_from_ancestors(a)
            if not title or len(title) < 4:
                continue
            context = GoldmanSource._context(a)
            loc = GoldmanSource._location(context, title)
            seen.add(jid)
            out.append(Job(company["id"], company["name"], "goldman", jid, title, loc,
                           None, f"https://higher.gs.com/roles/{jid}", None,
                           {"card_text": context}))
        return dedupe(out)

    @staticmethod
    def normalize_browser_jobs(company, jobs):
        out = []
        for job in jobs:
            jid = None
            if job.job_url:
                m = ROLE_RE.search(urlparse(job.job_url).path)
                if m:
                    jid = m.group("id")
            if not jid and job.external_id and str(job.external_id).isdigit():
                jid = str(job.external_id)
            if not jid:
                continue
            job.company_id = company["id"]
            job.company_name = company["name"]
            job.source_type = "goldman"
            job.external_id = jid
            job.job_url = f"https://higher.gs.com/roles/{jid}"
            out.append(job)
        return dedupe(out)

    @staticmethod
    def _title_from_ancestors(node):
        parent = node.parent
        for _ in range(5):
            if parent is None:
                break
            for selector in ("h1", "h2", "h3", "h4", "[class*='title']"):
                found = parent.select_one(selector) if hasattr(parent, "select_one") else None
                value = clean_text(found.get_text(" ", strip=True)) if found else None
                if value and 3 < len(value) <= 240:
                    return value
            parent = parent.parent
        return None

    @staticmethod
    def _context(node):
        parent = node.parent
        for _ in range(5):
            if parent is None:
                break
            text = clean_text(parent.get_text(" ", strip=True)) or ""
            if 25 <= len(text) <= 600:
                return text
            parent = parent.parent
        return ""

    @staticmethod
    def _location(context, title):
        text = (context or "").replace(title or "", " ")
        m = re.search(r"\b(Bengaluru|Bangalore|Hyderabad|Mumbai|Pune|Chennai|Gurugram|Gurgaon|Noida|Delhi)\b[^|•]{0,80}\bIndia\b", text, re.I)
        return clean_text(m.group(0)) if m else None
