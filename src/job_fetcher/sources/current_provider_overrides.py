from __future__ import annotations

import json
import re
import time
from copy import deepcopy
from urllib.parse import parse_qs, urljoin, urlparse

from job_fetcher.models import Job
from job_fetcher.sources.amazon import AmazonSource
from job_fetcher.sources.ashby import AshbySource
from job_fetcher.sources.auto import AutoSource
from job_fetcher.sources.generic_extract import clean_text, dedupe
from job_fetcher.sources.http_client import session, timeout_seconds
from job_fetcher.sources.phenom import PhenomSource
from job_fetcher.sources.recovery_browser import RecoveryBrowserSource


INDIA_HINT_RE = re.compile(
    r"\b(?:india|bengaluru|bangalore|hyderabad|gurugram|gurgaon|pune|chennai|noida|mumbai|delhi|new delhi)\b",
    re.I,
)


def _as_ashby(company, board_name: str):
    candidate = deepcopy(company)
    candidate["source"] = {"type": "ashby", "board_name": board_name}
    return AshbySource().fetch(candidate)


class ConfluentAshbySource(AutoSource):
    """Confluent's current public application board is Ashby.

    Keep AutoSource inheritance so existing diagnostics that expect the configured
    adapter family continue to work, while fetching the structured provider feed
    instead of repeatedly rate-limited branded HTML.
    """

    def fetch(self, company):
        return _as_ashby(company, "confluent")


class SnowflakeAshbySource(PhenomSource):
    """Prefer Snowflake's current Ashby board over lossy Phenom page extraction."""

    def fetch(self, company):
        return _as_ashby(company, "snowflake")


class AmazonJsonSource(AmazonSource):
    """Use Amazon.jobs' public search JSON endpoint before HTML/browser parsing."""

    API_URL = "https://www.amazon.jobs/en/search.json"

    def fetch(self, company):
        try:
            jobs = self._fetch_json(company)
            if jobs:
                return jobs
        except Exception:
            # Preserve the existing HTML/browser implementation as a fallback for
            # temporary endpoint changes or throttling.
            pass
        return super().fetch(company)

    def _fetch_json(self, company):
        src = company.get("source") or {}
        entry = src.get("entry_url") or company.get("career_url") or ""
        query = parse_qs(urlparse(entry).query)
        max_pages = max(1, int(src.get("max_pages") or 60))
        page_size = max(1, min(100, int(src.get("api_page_size") or 100)))
        max_jobs = max_pages * page_size

        params = {
            "base_query": (query.get("base_query") or ["Software Development"])[0],
            "country": (query.get("country") or ["IND"])[0] or "IND",
            "offset": 0,
            "result_limit": page_size,
            "sort": (query.get("sort") or ["relevant"])[0],
        }
        for key in ("loc_query", "city", "region", "county", "radius", "job_category"):
            if query.get(key):
                params[key] = query[key][0]

        client = session()
        jobs: list[Job] = []
        offset = 0
        hits = None
        for _ in range(max_pages):
            params["offset"] = offset
            response = client.get(
                self.API_URL,
                params=params,
                timeout=timeout_seconds(),
                headers={"Accept": "application/json", "User-Agent": "PersonalJobFetcher/0.1"},
            )
            response.raise_for_status()
            payload = response.json()
            rows = payload.get("jobs") or []
            if not isinstance(rows, list) or not rows:
                break
            if isinstance(payload.get("hits"), int):
                hits = int(payload["hits"])

            for row in rows:
                if not isinstance(row, dict):
                    continue
                job = self._to_job(company, row)
                if job:
                    jobs.append(job)
                if len(jobs) >= max_jobs:
                    break

            step = len(rows)
            offset += step
            if step == 0 or len(jobs) >= max_jobs or (hits is not None and offset >= hits):
                break
            # The public endpoint can throttle concurrent/aggressive crawlers.
            time.sleep(0.15)
        return dedupe(jobs)

    @staticmethod
    def _to_job(company, row: dict) -> Job | None:
        title = clean_text(row.get("title"))
        if not title:
            return None
        path = clean_text(row.get("job_path") or row.get("jobPath"))
        job_url = urljoin("https://www.amazon.jobs", path) if path else clean_text(row.get("job_url") or row.get("url"))
        path_id = None
        if path:
            match = re.search(r"/jobs/([^/?#]+)", path, re.I)
            path_id = match.group(1) if match else None
        external_id = clean_text(
            row.get("id_icims") or row.get("id") or row.get("job_id") or row.get("jobId") or path_id
        )

        location = AmazonJsonSource._location(row)
        desc_parts = []
        for key in ("description", "basic_qualifications", "preferred_qualifications"):
            value = clean_text(row.get(key))
            if value:
                desc_parts.append(value)
        description = "\n\n".join(desc_parts) or None
        return Job(
            company_id=company["id"],
            company_name=company["name"],
            source_type="amazon_json",
            external_id=external_id or job_url,
            title=title,
            location=location,
            description=description,
            job_url=job_url,
            posted_at=clean_text(row.get("posted_date") or row.get("postedDate") or row.get("updated_at")),
            raw=row,
        )

    @staticmethod
    def _location(row: dict):
        direct = clean_text(row.get("location") or row.get("normalized_location"))
        if direct:
            return direct
        values = row.get("locations") or []
        if not isinstance(values, list):
            values = [values]
        rendered = []
        for value in values:
            obj = value
            if isinstance(value, str) and value.lstrip().startswith("{"):
                try:
                    obj = json.loads(value)
                except Exception:
                    obj = value
            if isinstance(obj, dict):
                parts = [
                    obj.get("normalizedCityName") or obj.get("city"),
                    obj.get("normalizedStateName") or obj.get("state"),
                    obj.get("normalizedCountryName") or obj.get("country"),
                ]
                text = ", ".join(str(x) for x in parts if x)
            else:
                text = clean_text(obj)
            if text and text not in rendered:
                rendered.append(text)
        return "; ".join(rendered) or None


class UberIndiaSource(AutoSource):
    """Capture Uber's current public jobs XHR and emit only canonical India roles."""

    ENTRY_URLS = (
        "https://jobs.uber.com/en/jobs/?location=Bengaluru&radius=100&page=1&pagesize=100",
        "https://jobs.uber.com/en/jobs/?location=Hyderabad&radius=100&page=1&pagesize=100",
        "https://jobs.uber.com/en/jobs/?location=Gurugram&radius=100&page=1&pagesize=100",
    )

    def fetch(self, company):
        jobs = []
        errors = []
        for entry in self.ENTRY_URLS:
            candidate = deepcopy(company)
            source = dict(candidate.get("source") or {})
            source.update({
                "entry_url": entry,
                "browser_max_pages": 8,
                "browser_max_scrolls": 8,
                "browser_stable_scrolls": 3,
                "browser_load_more_clicks": 12,
            })
            candidate["source"] = source
            try:
                jobs.extend(RecoveryBrowserSource().fetch(candidate) or [])
            except Exception as exc:
                errors.append(str(exc))

        normalized = []
        for job in dedupe(jobs):
            raw = job.raw if isinstance(job.raw, dict) else {}
            evidence = " ".join([
                str(job.location or ""),
                str(raw.get("location") or ""),
                str(raw.get("locations") or ""),
                str(raw.get("city") or ""),
                str(raw.get("country") or raw.get("countryName") or ""),
            ])
            if not INDIA_HINT_RE.search(evidence):
                continue
            status = str(raw.get("status") or raw.get("jobStatus") or "").strip().lower()
            if status in {"closed", "removed", "inactive", "expired"} or raw.get("isJobClosed") is True:
                continue

            eid = self._numeric_id(job, raw)
            if eid:
                job.external_id = eid
                job.job_url = f"https://jobs.uber.com/en/jobs/{eid}/"
            elif not re.search(r"https://jobs\.uber\.com/en/jobs/\d+/?(?:$|[?#])", str(job.job_url or ""), re.I):
                continue
            job.source_type = "uber"
            normalized.append(job)

        normalized = dedupe(normalized)
        if normalized:
            return normalized

        # Keep the old generic source available if Uber changes its public XHR
        # shape again; failure detail then still contains the original diagnostics.
        try:
            return super().fetch(company)
        except Exception as exc:
            detail = "; ".join(errors[-3:])
            raise RuntimeError(f"uber_india_fetch_failed: recovery={detail}; configured={exc}") from exc

    @staticmethod
    def _numeric_id(job, raw):
        values = [
            getattr(job, "external_id", None),
            raw.get("id"), raw.get("jobId"), raw.get("jobID"), raw.get("positionId"),
            raw.get("requisitionId"), raw.get("reqId"), raw.get("atsJobId"),
        ]
        for value in values:
            if value is None:
                continue
            match = re.search(r"(?:^|/)(\d{4,})(?:/|$)", str(value))
            if match:
                return match.group(1)
        return None
