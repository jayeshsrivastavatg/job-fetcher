from __future__ import annotations

import math
import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from urllib.parse import urljoin, urlparse

from bs4 import BeautifulSoup

from job_fetcher.models import Job
from job_fetcher.sources.base import JobSource
from job_fetcher.sources.generic_extract import clean_text, dedupe, extract_jsonld
from job_fetcher.sources.http_client import session, timeout_seconds


JOB_RE = re.compile(r"^/job/[^/]+/[^/]+/27595/(?P<id>\d+)/?$", re.I)
TOTAL_RE = re.compile(r"(?P<total>\d+)\s+jobs?\s+found\s+in\s+India", re.I)
PAGE_RE = re.compile(r"currently\s+on\s+page\s+(?P<page>\d+)\s+of\s+(?P<pages>\d+)", re.I)
INDIA_LOC_RE = re.compile(
    r"\b(?:Bangalore|Bengaluru|Mumbai|Pune|Hyderabad|Gurugram|Gurgaon|Noida|Chennai|Delhi)?(?:,?\s*)India\b",
    re.I,
)


class IntuitIndiaSource(JobSource):
    """Exhaustive parser for Intuit's server-rendered India TalentBrew search.

    The public India page publishes an exact result count and page count. Only
    concrete `/job/.../27595/<posting-id>` anchors are accepted, so Intuit blog,
    category, saved-job and marketing links can never enter the job inventory.
    """

    DEFAULT_URL = "https://jobs.intuit.com/location/india-jobs/27595/1269750/2"

    def fetch(self, company):
        src = company.get("source") or {}
        entry = src.get("entry_url") or self.DEFAULT_URL
        entry = entry.rstrip("/")
        client = session()
        jobs = []
        total_seen = 0
        pages_seen = 1
        max_pages = int(src.get("max_pages") or 20)

        page = 1
        while page <= min(max_pages, pages_seen if page > 1 else max_pages):
            url = entry if page == 1 else f"{entry}/{page}"
            response = client.get(url, timeout=timeout_seconds(), allow_redirects=True)
            response.raise_for_status()
            batch, total, pages = self.parse_page(company, response.text, response.url)
            total_seen = max(total_seen, total or 0)
            pages_seen = max(pages_seen, pages or 1)
            before = len(jobs)
            jobs = dedupe([*jobs, *batch])
            if not batch or len(jobs) == before:
                break
            if page >= pages_seen:
                break
            page += 1

        complete = bool(total_seen and len(jobs) >= total_seen)
        for job in jobs:
            raw = dict(job.raw or {})
            raw["_provider_total"] = total_seen or None
            raw["_provider_pages"] = pages_seen
            raw["_provider_returned"] = len(jobs)
            raw["_provider_complete"] = complete
            job.raw = raw

        if src.get("enrich_details", True):
            self._enrich(company, jobs, workers=max(1, min(10, int(src.get("detail_workers") or 5))))
        return jobs

    @classmethod
    def parse_page(cls, company, html: str, base_url: str):
        soup = BeautifulSoup(html, "html.parser")
        text = clean_text(soup.get_text(" ", strip=True)) or ""
        tm = TOTAL_RE.search(text)
        pm = PAGE_RE.search(text)
        total = int(tm.group("total")) if tm else None
        pages = int(pm.group("pages")) if pm else None
        jobs = []
        seen = set()
        for anchor in soup.select("a[href]"):
            absolute = urljoin(base_url, anchor.get("href") or "")
            match = JOB_RE.match(urlparse(absolute).path)
            if not match:
                continue
            jid = match.group("id")
            if jid in seen:
                continue
            title = cls._title(anchor)
            if not title:
                continue
            context = cls._context(anchor)
            location = cls._location(context) or "India"
            seen.add(jid)
            jobs.append(Job(
                company["id"], company["name"], "intuit", jid, title, location,
                None, absolute, None, {"listing_text": context, "source_page": base_url},
            ))
        return dedupe(jobs), total, pages

    @staticmethod
    def _title(anchor):
        for selector in ("h2", "h3", "h4", "[class*='title']"):
            node = anchor.select_one(selector)
            value = clean_text(node.get_text(" ", strip=True)) if node else None
            if value and 3 < len(value) <= 220:
                return value
        value = clean_text(anchor.get_text(" ", strip=True))
        if not value:
            return None
        # Location is often included inside the same anchor text.
        value = re.sub(r"\s+(?:Bangalore|Bengaluru|Mumbai|Pune|Hyderabad|Gurugram|Gurgaon|Noida|Chennai|Delhi),?\s+India.*$", "", value, flags=re.I)
        value = re.sub(r"\s+India\s*$", "", value, flags=re.I)
        return clean_text(value)

    @staticmethod
    def _context(anchor):
        node = anchor
        best = clean_text(anchor.get_text(" ", strip=True)) or ""
        for _ in range(5):
            node = getattr(node, "parent", None)
            if node is None:
                break
            text = clean_text(node.get_text(" ", strip=True)) or ""
            if len(best) < len(text) <= 700:
                best = text
            if "india" in text.lower() and len(text) <= 400:
                break
        return best

    @staticmethod
    def _location(context):
        if not context:
            return None
        match = INDIA_LOC_RE.search(context)
        return clean_text(match.group(0)) if match else None

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
                structured = extract_jsonld(company, html, final_url, "intuit")
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
                    if text and len(text) > 200:
                        job.description = text[:50000]
                raw["_detail_enriched"] = bool(job.description)
                job.raw = raw
