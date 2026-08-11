from __future__ import annotations

import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from urllib.parse import urljoin, urlparse

from bs4 import BeautifulSoup

from job_fetcher.models import Job
from job_fetcher.sources.base import JobSource
from job_fetcher.sources.generic_extract import clean_text, dedupe, extract_jsonld
from job_fetcher.sources.http_client import session, timeout_seconds

JOB_RE = re.compile(r"^/in/jobs/(?P<id>\d+)/(?P<slug>[^/?#]+)/?$", re.I)
TOTAL_RE = re.compile(r"\b(?P<total>\d+)\s+open roles\b", re.I)
POSTED_RE = re.compile(r"\bPosted\s+([A-Z][a-z]{2}\s+\d{1,2})\b")
LOCATION_RE = re.compile(r"\b(?:Bangalore|Bengaluru|Chennai)(?:,\s*[A-Z]{2})?(?:\s*/\s*(?:Bangalore|Bengaluru|Chennai),\s*[A-Z]{2})*\b", re.I)


class FidelityIndiaSource(JobSource):
    """Strict first-party Fidelity India job index + detail enrichment."""

    DEFAULT_URL = "https://jobs.fidelity.com/in/jobs/"

    def fetch(self, company):
        entry = (company.get("source") or {}).get("entry_url") or self.DEFAULT_URL
        response = session().get(entry, timeout=timeout_seconds(), allow_redirects=True)
        response.raise_for_status()
        jobs, total = self.parse_page(company, response.text, response.url)
        if total is not None and len(jobs) < total:
            # Fidelity occasionally paginates. Follow explicit numbered/next pages
            # until the advertised India total is reconciled.
            jobs = self._paginate(company, response.text, response.url, jobs, total)
        jobs = dedupe(jobs)
        complete = total is not None and len(jobs) >= total
        for job in jobs:
            raw = dict(job.raw or {})
            raw["_provider_total"] = total
            raw["_provider_returned"] = len(jobs)
            raw["_provider_complete"] = complete
            job.raw = raw
        self._enrich(company, jobs, workers=5)
        return jobs

    @classmethod
    def _paginate(cls, company, first_html, first_url, jobs, total):
        soup = BeautifulSoup(first_html, "html.parser")
        pages = []
        for a in soup.select("a[href]"):
            text = clean_text(a.get_text(" ", strip=True)) or ""
            href = a.get("href") or ""
            if text.isdigit() or "next" in text.lower() or "page=" in href:
                url = urljoin(first_url, href)
                if urlparse(url).netloc.lower() == urlparse(first_url).netloc.lower() and url not in pages:
                    pages.append(url)
        client = session()
        for url in pages[:20]:
            if len(jobs) >= total:
                break
            try:
                r = client.get(url, timeout=timeout_seconds(), allow_redirects=True)
                r.raise_for_status()
            except Exception:
                continue
            batch, _ = cls.parse_page(company, r.text, r.url)
            jobs = dedupe([*jobs, *batch])
        return jobs

    @classmethod
    def parse_page(cls, company, html: str, base_url: str):
        soup = BeautifulSoup(html, "html.parser")
        text = clean_text(soup.get_text(" ", strip=True)) or ""
        tm = TOTAL_RE.search(text)
        total = int(tm.group("total")) if tm else None
        jobs, seen = [], set()
        for a in soup.select("a[href]"):
            absolute = urljoin(base_url, a.get("href") or "")
            match = JOB_RE.match(urlparse(absolute).path)
            if not match or match.group("id") in seen:
                continue
            title = clean_text(a.get_text(" ", strip=True))
            if not title or len(title) > 220:
                continue
            context = cls._context(a, title)
            location_match = LOCATION_RE.search(context or "")
            location = clean_text(location_match.group(0)) if location_match else "India"
            posted_match = POSTED_RE.search(context or "")
            posted = clean_text(posted_match.group(1)) if posted_match else None
            seen.add(match.group("id"))
            jobs.append(Job(
                company["id"], company["name"], "fidelity_india", match.group("id"),
                title, f"{location}, India" if "india" not in location.lower() else location,
                None, absolute, posted, {"listing_text": context},
            ))
        return dedupe(jobs), total

    @staticmethod
    def _context(anchor, title):
        node = anchor
        best = title
        for _ in range(6):
            node = getattr(node, "parent", None)
            if node is None:
                break
            text = clean_text(node.get_text(" ", strip=True)) or ""
            if len(best) < len(text) <= 700:
                best = text
            if "posted" in text.lower() and ("bangalore" in text.lower() or "chennai" in text.lower()):
                break
        return best

    @staticmethod
    def _enrich(company, jobs, workers=5):
        def detail(job):
            try:
                r = session().get(job.job_url, timeout=timeout_seconds(), allow_redirects=True)
                r.raise_for_status()
                return job, r.text, r.url, None
            except Exception as exc:
                return job, None, None, exc
        with ThreadPoolExecutor(max_workers=workers) as pool:
            futures = [pool.submit(detail, j) for j in jobs]
            for future in as_completed(futures):
                job, html, final_url, error = future.result()
                raw = dict(job.raw or {})
                if error is not None:
                    raw["_detail_fetch_error"] = f"{type(error).__name__}: {error}"
                    job.raw = raw
                    continue
                structured = extract_jsonld(company, html, final_url, "fidelity_india")
                if structured:
                    d = structured[0]
                    job.title = clean_text(d.title) or job.title
                    job.location = clean_text(d.location) or job.location
                    job.description = clean_text(d.description) or job.description
                    job.posted_at = clean_text(d.posted_at) or job.posted_at
                if not job.description:
                    soup = BeautifulSoup(html, "html.parser")
                    main = soup.select_one("main") or soup.select_one("[class*='job-description']")
                    text = clean_text(main.get_text(" ", strip=True)) if main else None
                    if text and len(text) >= 180:
                        job.description = text[:50000]
                raw["_detail_enriched"] = bool(job.description)
                job.raw = raw
