from __future__ import annotations

import json
import re
from urllib.parse import urljoin, urlparse

from bs4 import BeautifulSoup

from job_fetcher.models import Job
from job_fetcher.sources.base import JobSource
from job_fetcher.sources.generic_extract import clean_text
from job_fetcher.sources.http_client import session, timeout_seconds


class ZohoRecruitSource(JobSource):
    """Public Zoho Recruit career-site source.

    Zoho Recruit server-renders the complete open-role inventory into the public
    ``/jobs/Careers`` document as an HTML-entity-decoded JSON array in
    ``<input id="jobs" value="...">``.  We parse that first-party inventory
    directly, preserve Zoho's stable opening ID, and never fall back to generic
    navigation-link extraction.
    """

    def fetch(self, company):
        src = company.get("source") or {}
        entry = src.get("entry_url") or company.get("career_url")
        if not entry:
            raise ValueError("zohorecruit source requires entry_url or career_url")
        client = session()
        response = client.get(
            entry,
            timeout=timeout_seconds(),
            allow_redirects=True,
            headers={"User-Agent": "Mozilla/5.0 PersonalJobFetcher/0.1"},
        )
        response.raise_for_status()
        rows = self.parse_openings(response.text)
        if not rows:
            raise RuntimeError("zohorecruit_public_inventory_empty_or_missing")

        origin = f"{urlparse(response.url).scheme}://{urlparse(response.url).netloc}"
        jobs = []
        seen = set()
        for row in rows:
            if not isinstance(row, dict):
                continue
            if row.get("Is_Locked") is True or row.get("Publish") is False:
                continue
            external_id = clean_text(row.get("id"))
            title = clean_text(row.get("Posting_Title")) or clean_text(row.get("Job_Opening_Name"))
            if not external_id or not title or external_id in seen:
                continue
            seen.add(external_id)
            slug = self._slug(title)
            job_url = urljoin(origin, f"/jobs/Careers/{external_id}/{slug}?source=CareerSite")
            location = self._location(row)
            description = clean_text(row.get("Job_Description"))
            posted_at = clean_text(row.get("Date_Opened"))
            jobs.append(Job(
                company["id"], company["name"], "zohorecruit",
                external_id, title, location, description, job_url, posted_at, row,
            ))
        return jobs

    @staticmethod
    def parse_openings(html: str) -> list[dict]:
        soup = BeautifulSoup(html or "", "html.parser")
        node = soup.find("input", id="jobs")
        raw = node.get("value") if node else None
        if not raw:
            return []
        try:
            payload = json.loads(raw)
        except Exception:
            return []
        return payload if isinstance(payload, list) else []

    @staticmethod
    def _slug(title: str) -> str:
        slug = re.sub(r"[^A-Za-z0-9]+", "-", title).strip("-")
        return slug or "job"

    @staticmethod
    def _location(row: dict) -> str | None:
        parts = []
        for key in ("City", "State", "Country"):
            value = clean_text(row.get(key))
            if value and value not in parts:
                parts.append(value)
        return ", ".join(parts) or None
