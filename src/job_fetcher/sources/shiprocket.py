from __future__ import annotations

import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from urllib.parse import urljoin, urlparse

from bs4 import BeautifulSoup

from job_fetcher.models import Job
from job_fetcher.sources.base import JobSource
from job_fetcher.sources.generic_extract import clean_text, dedupe, extract_jsonld
from job_fetcher.sources.http_client import session, timeout_seconds

JOB_RE = re.compile(r"^/jobs/(?P<slug>[^/?#]+)/?$", re.I)
DATE_RE = re.compile(r"Job posted on\s+([^|•]+?)(?:Employee Type|Experience range|$)", re.I)


class ShiprocketSource(JobSource):
    """Strict parser for the complete first-party Shiprocket jobs section."""

    DEFAULT_URL = "https://careers.shiprocket.in/"

    def fetch(self, company):
        entry = (company.get("source") or {}).get("entry_url") or self.DEFAULT_URL
        response = session().get(entry, timeout=timeout_seconds(), allow_redirects=True)
        response.raise_for_status()
        jobs = self.parse_listing(company, response.text, response.url)
        if not jobs:
            raise RuntimeError("shiprocket_first_party_listing_returned_no_jobs")
        for job in jobs:
            raw = dict(job.raw or {})
            raw["_provider_total"] = len(jobs)
            raw["_provider_returned"] = len(jobs)
            raw["_provider_complete"] = True
            job.raw = raw
        self._enrich(company, jobs, workers=5)
        return jobs

    @classmethod
    def parse_listing(cls, company, html: str, base_url: str):
        soup = BeautifulSoup(html, "html.parser")
        jobs, seen = [], set()
        for anchor in soup.select("a[href]"):
            absolute = urljoin(base_url, anchor.get("href") or "")
            match = JOB_RE.match(urlparse(absolute).path)
            if not match or absolute in seen:
                continue
            # The listing CTA is usually `View Job`; get title/location from its
            # compact card rather than storing the CTA itself as a vacancy title.
            card = cls._card(anchor)
            title = cls._title(card)
            if not title:
                continue
            location = cls._location(card, title)
            seen.add(absolute)
            jobs.append(Job(
                company["id"], company["name"], "shiprocket", match.group("slug"),
                title, location, None, absolute, None,
                {"listing_text": clean_text(card.get_text(" ", strip=True)) if card else None},
            ))
        return dedupe(jobs)

    @staticmethod
    def _card(anchor):
        node = anchor
        fallback = anchor.parent
        for _ in range(7):
            node = getattr(node, "parent", None)
            if node is None:
                break
            text = clean_text(node.get_text(" ", strip=True)) or ""
            headings = node.find_all(["h4", "h5", "h3"])
            if headings and 8 <= len(text) <= 500:
                return node
            if len(text) <= 800:
                fallback = node
        return fallback

    @staticmethod
    def _title(card):
        if card is None:
            return None
        for node in card.find_all(["h5", "h4", "h3"], recursive=True):
            value = clean_text(node.get_text(" ", strip=True))
            if value and value.lower() not in {"view job", "all", "engineering"} and 3 < len(value) <= 220:
                return value
        return None

    @staticmethod
    def _location(card, title):
        if card is None:
            return None
        text = clean_text(card.get_text(" ", strip=True)) or ""
        text = text.replace(title or "", " ")
        match = re.search(r"\b(Gurugram|Gurgaon|Delhi|Noida|Mumbai|Bengaluru|Bangalore|Hyderabad|Pune|Chennai)(?:,\s*[A-Za-z ]+)?\b", text, re.I)
        return f"{clean_text(match.group(0))}, India" if match else "India"

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
                structured = extract_jsonld(company, html, final_url, "shiprocket")
                if structured:
                    d = structured[0]
                    job.title = clean_text(d.title) or job.title
                    job.location = clean_text(d.location) or job.location
                    job.description = clean_text(d.description) or job.description
                    job.posted_at = clean_text(d.posted_at) or job.posted_at
                soup = BeautifulSoup(html, "html.parser")
                main = soup.select_one("main") or soup.body
                text = clean_text(main.get_text(" ", strip=True)) if main else None
                if text:
                    dm = DATE_RE.search(text)
                    if dm and not job.posted_at:
                        job.posted_at = clean_text(dm.group(1))
                    if not job.description and len(text) >= 180:
                        job.description = text[:50000]
                raw["_detail_enriched"] = bool(job.description)
                job.raw = raw
