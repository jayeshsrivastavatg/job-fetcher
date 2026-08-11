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

DETAIL_RE = re.compile(r"/company/careers/details/(?P<id>\d+)(?:[/?#]|$)", re.I)


class AtlassianSource(JobSource):
    """Atlassian public careers crawler.

    The all-jobs page is dynamic, while public detail pages use stable
    /company/careers/details/<id> URLs. We extract those links from server HTML
    when available and otherwise use the bounded browser/network fallback.
    """

    def fetch(self, company):
        src = company.get("source") or {}
        entry = src.get("entry_url") or "https://www.atlassian.com/company/careers/all-jobs"
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
            raise RuntimeError(f"atlassian_static_fetch_failed: {static_error or 'no public detail links found'}")

        browser_jobs = PlaywrightAutoSource().fetch(company)
        normalized = self.normalize_browser_jobs(company, browser_jobs)
        if normalized:
            return normalized
        raise RuntimeError("atlassian_no_jobs_detected")

    @staticmethod
    def parse_listing(company, html: str, base_url: str):
        soup = BeautifulSoup(html, "html.parser")
        jobs = []
        seen = set()
        for a in soup.select('a[href*="/company/careers/details/"]'):
            url = urljoin(base_url, a.get("href") or "")
            m = DETAIL_RE.search(urlparse(url).path)
            if not m:
                continue
            jid = m.group("id")
            if jid in seen:
                continue
            title = clean_text(a.get_text(" ", strip=True))
            if not title or title.lower() in {"apply", "apply now", "learn more", "view role", "view job"}:
                title = AtlassianSource._title_from_ancestors(a)
            if not title or len(title) < 4:
                continue
            context = AtlassianSource._context(a)
            loc = AtlassianSource._location_from_context(context, title)
            seen.add(jid)
            jobs.append(Job(company["id"], company["name"], "atlassian", jid, title, loc,
                            None, url, None, {"card_text": context}))
        return dedupe(jobs)

    @staticmethod
    def normalize_browser_jobs(company, jobs):
        out = []
        for job in jobs:
            jid = None
            if job.job_url:
                m = DETAIL_RE.search(urlparse(job.job_url).path)
                if m:
                    jid = m.group("id")
            if not jid and job.external_id and str(job.external_id).isdigit():
                jid = str(job.external_id)
            if not jid:
                continue
            job.company_id = company["id"]
            job.company_name = company["name"]
            job.source_type = "atlassian"
            job.external_id = jid
            job.job_url = f"https://www.atlassian.com/company/careers/details/{jid}"
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
                if value and 3 < len(value) <= 220:
                    return value
            parent = parent.parent
        return None

    @staticmethod
    def _context(node):
        parent = node.parent
        best = ""
        for _ in range(5):
            if parent is None:
                break
            text = clean_text(parent.get_text(" ", strip=True)) or ""
            if len(text) > len(best):
                best = text
            if 30 <= len(text) <= 500:
                return text
            parent = parent.parent
        return best

    @staticmethod
    def _location_from_context(context, title):
        if not context:
            return None
        text = context.replace(title or "", " ")
        m = re.search(r"\b(Bengaluru|Bangalore|Pune|Hyderabad|Chennai|Mumbai|Gurugram|Gurgaon|Noida|Delhi)\b[^|•]{0,80}\bIndia\b", text, re.I)
        if m:
            return clean_text(m.group(0))
        if re.search(r"\bRemote,?\s*India\b", text, re.I):
            return "Remote, India"
        return None
