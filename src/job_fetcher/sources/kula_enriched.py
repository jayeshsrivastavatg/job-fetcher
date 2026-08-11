from __future__ import annotations

from bs4 import BeautifulSoup

from job_fetcher.sources.generic_extract import clean_text, extract_embedded_json, extract_jsonld
from job_fetcher.sources.http_client import session, timeout_seconds
from job_fetcher.sources.kula import KulaSource


class EnrichedKulaSource(KulaSource):
    """Kula adapter that treats the board as an ID index and detail pages as truth.

    Several Kula themes render a department/category heading more prominently than
    the vacancy title on the listing card.  The public detail URL is stable and
    contains the authoritative title/location/JD, so enrich each board result from
    that detail page instead of storing category names such as "Technology" or
    "Growth & Strategy" as jobs.
    """

    def fetch(self, company):
        jobs = list(super().fetch(company) or [])
        if not jobs:
            return jobs
        client = session()
        for job in jobs:
            self._enrich_one(client, company, job)
        return jobs

    @staticmethod
    def _enrich_one(client, company, job):
        if not job.job_url:
            return
        try:
            response = client.get(job.job_url, timeout=timeout_seconds(), allow_redirects=True)
            response.raise_for_status()
        except Exception:
            # A listing row is still preferable to losing the vacancy entirely if a
            # detail request is transiently blocked. Health/audit can flag missing JD.
            return

        html = response.text
        candidates = []
        candidates.extend(extract_jsonld(company, html, response.url, "kula"))
        candidates.extend(extract_embedded_json(company, html, response.url, "kula"))
        detail = next((x for x in candidates if x.title and x.job_url), None) or next(
            (x for x in candidates if x.title), None
        )
        if detail is not None:
            job.title = clean_text(detail.title) or job.title
            job.location = clean_text(detail.location) or job.location
            job.description = clean_text(detail.description) or job.description
            job.posted_at = clean_text(detail.posted_at) or job.posted_at
            raw = dict(job.raw or {})
            raw["_detail_enriched"] = True
            raw["_detail_source"] = "structured"
            job.raw = raw
            return

        soup = BeautifulSoup(html, "html.parser")
        # Public Kula detail pages normally expose the vacancy name as the primary
        # heading even when the listing card showed only a department/category.
        heading = soup.find("h1") or soup.find("h2")
        title = clean_text(heading.get_text(" ", strip=True)) if heading else None
        if title and title.lower() not in {"careers", "job details", "open positions"}:
            job.title = title
        description_node = (
            soup.select_one("[class*='job-description']")
            or soup.select_one("[class*='description']")
            or soup.select_one("main")
        )
        description = clean_text(description_node.get_text(" ", strip=True)) if description_node else None
        if description and len(description) >= 120:
            job.description = description[:50000]
        raw = dict(job.raw or {})
        raw["_detail_enriched"] = bool(title or description)
        raw["_detail_source"] = "html"
        job.raw = raw
