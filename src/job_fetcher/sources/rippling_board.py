from __future__ import annotations

from urllib.parse import urlparse

from job_fetcher.models import Job
from job_fetcher.sources.base import JobSource
from job_fetcher.sources.generic_extract import clean_text
from job_fetcher.sources.http_client import session, timeout_seconds


class RipplingBoardSource(JobSource):
    API_BASE = "https://api.rippling.com/platform/api/ats/v1/board"

    def fetch(self, company):
        src = company.get("source") or {}
        tenant = str(src.get("tenant") or "").strip()
        if not tenant or "/" in tenant or ".." in tenant:
            raise ValueError("invalid Rippling board tenant")
        response = session().get(
            f"{self.API_BASE}/{tenant}/jobs",
            timeout=timeout_seconds(),
            headers={"Accept": "application/json"},
            allow_redirects=False,
        )
        response.raise_for_status()
        rows = response.json()
        if not isinstance(rows, list):
            raise RuntimeError("Rippling board did not return a jobs array")
        jobs = []
        seen = set()
        for row in rows:
            if not isinstance(row, dict):
                continue
            job_id = clean_text(row.get("uuid"))
            title = clean_text(row.get("name"))
            url = clean_text(row.get("url"))
            if not job_id or not title or job_id in seen or not self._valid_url(url, tenant, job_id):
                continue
            location_value = row.get("workLocation")
            location = clean_text(location_value.get("label")) if isinstance(location_value, dict) else clean_text(location_value)
            if src.get("require_india") and not self._is_india(location):
                continue
            seen.add(job_id)
            jobs.append(Job(
                company["id"], company["name"], "rippling_board",
                job_id, title, location, None, url, None, row,
            ))
        return jobs

    @staticmethod
    def _is_india(location):
        low = str(location or "").casefold()
        return any(token in low for token in ("india", "bangalore", "bengaluru", "hyderabad"))

    @staticmethod
    def _valid_url(url, tenant, job_id):
        if not url:
            return False
        parsed = urlparse(url)
        if parsed.scheme != "https" or parsed.netloc.lower() != "ats.rippling.com":
            return False
        parts = [part for part in parsed.path.split("/") if part]
        return len(parts) >= 3 and parts[0].casefold() == tenant.casefold() and parts[1] == "jobs" and parts[2].casefold() == job_id.casefold()
