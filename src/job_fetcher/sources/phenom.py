from __future__ import annotations

import os
import re
from urllib.parse import urljoin, urlparse

from bs4 import BeautifulSoup

from job_fetcher.models import Job
from job_fetcher.sources.base import JobSource
from job_fetcher.sources.generic_extract import clean_text, dedupe, extract_embedded_json, extract_html_links, extract_jsonld
from job_fetcher.sources.http_client import session, timeout_seconds
from job_fetcher.sources.playwright_auto import PlaywrightAutoSource

SNOWFLAKE_JOB_RE = re.compile(r"/us/en/job/(?P<id>[^/]+)/", re.I)


class PhenomSource(JobSource):
    """Public Phenom career-site source.

    Phenom tenants often render search results client-side. We first consume any
    server-rendered JobPosting/links, then use the generic browser network capture
    plus numbered-pagination hardening. No authenticated/private Phenom API is
    required.
    """

    def fetch(self, company):
        src = company.get("source") or {}
        entry = src.get("entry_url") or company["career_url"]
        static_error = None
        try:
            r = session().get(entry, timeout=timeout_seconds(), allow_redirects=True)
            r.raise_for_status()
            jobs = self._extract_static(company, r.text, r.url)
            jobs = self._normalize(company, jobs, src)
            if jobs:
                return jobs
        except Exception as exc:
            static_error = exc

        if src.get("disable_browser_fallback") or os.getenv("JOB_FETCHER_DISABLE_BROWSER", "0") == "1":
            raise RuntimeError(f"phenom_static_fetch_failed: {static_error or 'client-side search results'}")

        jobs = PlaywrightAutoSource().fetch(company)
        jobs = self._normalize(company, jobs, src)
        if jobs:
            return jobs
        raise RuntimeError("phenom_no_jobs_detected")

    @staticmethod
    def _extract_static(company, html, url):
        jobs = []
        jobs.extend(extract_jsonld(company, html, url, "phenom_jsonld"))
        jobs.extend(extract_embedded_json(company, html, url, "phenom_embedded_json"))
        jobs.extend(extract_html_links(company, html, url, "phenom_html"))
        return dedupe(jobs)

    @staticmethod
    def _normalize(company, jobs, src):
        out = []
        canonical = (src.get("canonical_base_url") or company.get("career_url") or "").rstrip("/")
        for job in jobs:
            # Snowflake/Phenom detail URLs are stable and should be preferred.
            jid = None
            if job.job_url:
                m = SNOWFLAKE_JOB_RE.search(urlparse(job.job_url).path)
                if m:
                    jid = m.group("id")
            if not jid and job.external_id:
                raw = str(job.external_id)
                if len(raw) >= 8:
                    jid = raw
            if not jid and not job.job_url:
                continue
            job.company_id = company["id"]
            job.company_name = company["name"]
            job.source_type = "phenom"
            if jid:
                job.external_id = jid
            if job.job_url:
                # Keep employer-branded career URL; discard CDN/analytics URLs.
                host = urlparse(job.job_url).netloc.lower()
                if "phenompeople.com" in host and canonical:
                    continue
            out.append(job)
        return dedupe(out)
