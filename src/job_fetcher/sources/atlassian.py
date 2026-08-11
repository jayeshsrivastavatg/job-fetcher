from __future__ import annotations

import os
import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from urllib.parse import urljoin, urlparse

from bs4 import BeautifulSoup

from job_fetcher.models import Job
from job_fetcher.sources.base import JobSource
from job_fetcher.sources.generic_extract import clean_text, dedupe, extract_embedded_json, extract_jsonld
from job_fetcher.sources.http_client import session, timeout_seconds
from job_fetcher.sources.playwright_auto import PlaywrightAutoSource

DETAIL_RE = re.compile(r"^/company/careers/details/(?P<id>\d+)(?:[/?#]|$)", re.I)


class AtlassianSource(JobSource):
    """Canonical Atlassian public careers crawler with full-detail enrichment.

    Marketing/category links are never accepted: every returned row must resolve to
    `/company/careers/details/<numeric id>`. The server page and browser/XHR result
    are combined rather than trusting the first non-empty source, then every
    canonical vacancy is enriched from its first-party detail page so India roles
    have the complete candidate-facing JD.
    """

    def fetch(self, company):
        src = company.get("source") or {}
        entry = src.get("entry_url") or "https://www.atlassian.com/company/careers/all-jobs"
        jobs = []
        static_error = None
        try:
            r = session().get(entry, timeout=timeout_seconds(), allow_redirects=True)
            r.raise_for_status()
            jobs.extend(self.parse_listing(company, r.text, r.url))
        except Exception as exc:
            static_error = exc

        if not (src.get("disable_browser_fallback") or os.getenv("JOB_FETCHER_DISABLE_BROWSER", "0") == "1"):
            try:
                browser_jobs = PlaywrightAutoSource().fetch(company)
                jobs.extend(self.normalize_browser_jobs(company, browser_jobs))
            except Exception as exc:
                if not jobs:
                    static_error = RuntimeError(f"static={static_error}; browser={exc}")

        jobs = self.normalize_browser_jobs(company, jobs)
        if not jobs:
            raise RuntimeError(f"atlassian_no_jobs_detected: {static_error or 'no canonical detail links'}")

        self._enrich_details(
            company,
            jobs,
            workers=max(1, min(12, int(src.get("detail_workers") or 6))),
        )
        return dedupe(jobs)

    @staticmethod
    def parse_listing(company, html: str, base_url: str):
        soup = BeautifulSoup(html, "html.parser")
        jobs = []
        seen = set()
        for a in soup.select('a[href*="/company/careers/details/"]'):
            url = urljoin(base_url, a.get("href") or "")
            m = DETAIL_RE.match(urlparse(url).path)
            if not m:
                continue
            jid = m.group("id")
            if jid in seen:
                continue
            title = clean_text(a.get_text(" ", strip=True))
            if not title or title.lower() in {"apply", "apply now", "learn more", "view role", "view job", "see details"}:
                title = AtlassianSource._title_from_ancestors(a)
            if not title or len(title) < 4:
                continue
            context = AtlassianSource._context(a)
            loc = AtlassianSource._location_from_context(context, title)
            seen.add(jid)
            jobs.append(Job(company["id"], company["name"], "atlassian", jid, title, loc,
                            None, f"https://www.atlassian.com/company/careers/details/{jid}", None,
                            {"card_text": context, "source_page": base_url}))
        return dedupe(jobs)

    @staticmethod
    def normalize_browser_jobs(company, jobs):
        out = []
        for job in jobs or []:
            jid = None
            if job.job_url:
                m = DETAIL_RE.match(urlparse(str(job.job_url)).path)
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

    @classmethod
    def _enrich_details(cls, company, jobs, workers=6):
        def detail(job):
            try:
                r = session().get(job.job_url, timeout=timeout_seconds(), allow_redirects=True)
                r.raise_for_status()
                return job, r.text, r.url, None
            except Exception as exc:
                return job, None, None, exc

        with ThreadPoolExecutor(max_workers=workers) as pool:
            futures = [pool.submit(detail, job) for job in jobs]
            for future in as_completed(futures):
                job, html, final_url, error = future.result()
                raw = dict(job.raw or {})
                if error is not None:
                    raw["_detail_fetch_error"] = f"{type(error).__name__}: {error}"
                    job.raw = raw
                    continue
                candidates = []
                candidates.extend(extract_jsonld(company, html, final_url, "atlassian"))
                candidates.extend(extract_embedded_json(company, html, final_url, "atlassian"))
                canonical = next(
                    (x for x in candidates if cls._canonical_same_job(x.job_url, job.external_id)),
                    None,
                ) or next((x for x in candidates if x.title), None)
                if canonical is not None:
                    job.title = clean_text(canonical.title) or job.title
                    job.location = clean_text(canonical.location) or job.location
                    job.description = clean_text(canonical.description) or job.description
                    job.posted_at = clean_text(canonical.posted_at) or job.posted_at
                soup = BeautifulSoup(html, "html.parser")
                if not job.title:
                    h1 = soup.find("h1")
                    job.title = clean_text(h1.get_text(" ", strip=True)) if h1 else job.title
                if not job.location:
                    page_text = clean_text(soup.get_text(" ", strip=True)) or ""
                    job.location = cls._location_from_context(page_text, job.title)
                if not job.description:
                    main = soup.select_one("main") or soup.body
                    text = clean_text(main.get_text(" ", strip=True)) if main else None
                    if text and len(text) >= 200:
                        job.description = text[:50000]
                raw["_detail_enriched"] = bool(job.description)
                job.raw = raw

    @staticmethod
    def _canonical_same_job(url, external_id):
        if not url or not external_id:
            return False
        match = DETAIL_RE.match(urlparse(str(url)).path)
        return bool(match and match.group("id") == str(external_id))

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
            if 30 <= len(text) <= 700:
                return text
            parent = parent.parent
        return best

    @staticmethod
    def _location_from_context(context, title):
        if not context:
            return None
        text = context.replace(title or "", " ")
        m = re.search(r"\b(Bengaluru|Bangalore|Pune|Hyderabad|Chennai|Mumbai|Gurugram|Gurgaon|Noida|Delhi)\b[^|•]{0,100}\bIndia\b", text, re.I)
        if m:
            return clean_text(m.group(0))
        if re.search(r"\bRemote,?\s*India\b", text, re.I):
            return "Remote, India"
        if re.search(r"\blocated in India\b|\bacross India\b|\blocation in India\b", text, re.I):
            return "India"
        return None
