from __future__ import annotations

import re
from concurrent.futures import ThreadPoolExecutor, as_completed

from bs4 import BeautifulSoup

from job_fetcher.models import Job
from job_fetcher.sources.base import JobSource
from job_fetcher.sources.http_client import session, timeout_seconds
from job_fetcher.sources.generic_extract import clean_text


def _slug(value: str | None) -> str:
    text = re.sub(r"[^a-z0-9]+", "-", str(value or "").lower()).strip("-")
    return text[:160] or "job"


def _plain(value):
    if not value:
        return None
    return clean_text(BeautifulSoup(str(value), "html.parser").get_text(" ", strip=True))


def _india(location: str | None) -> bool:
    text = str(location or "").lower()
    return any(x in text for x in (
        "india", "bengaluru", "bangalore", "hyderabad", "gurugram", "gurgaon", "pune",
        "chennai", "noida", "mumbai", "delhi", "kolkata", "ahmedabad", "kochi",
    ))


class SmartRecruitersSource(JobSource):
    """Exhaustive SmartRecruiters public API adapter.

    The postings endpoint publishes `totalFound`, so pagination can be checked
    against the provider total. For India vacancies we additionally fetch the
    public posting detail to retain the complete JD/qualifications instead of only
    the listing-card fields.
    """

    def fetch(self, company):
        ident = company["source"]["company_identifier"]
        s = session()
        offset = 0
        out = []
        total = None
        limit = max(1, min(100, int(company["source"].get("page_size") or 100)))
        max_jobs = int(company["source"].get("max_jobs", 5000))
        while True:
            r = s.get(
                f"https://api.smartrecruiters.com/v1/companies/{ident}/postings",
                params={"limit": limit, "offset": offset},
                timeout=timeout_seconds(),
            )
            r.raise_for_status()
            data = r.json()
            items = data.get("content") or []
            try:
                total = int(data.get("totalFound"))
            except (TypeError, ValueError):
                total = total
            for x in items:
                loc = x.get("location") or {}
                location = ", ".join(str(loc.get(k)) for k in ("city", "region", "country") if loc.get(k)) or None
                posting_id = x.get("id")
                public_url = self._public_url(ident, posting_id, x.get("name"), x.get("ref"))
                raw = dict(x)
                raw["_provider_total"] = total
                out.append(Job(
                    company["id"], company["name"], "smartrecruiters",
                    str(posting_id) if posting_id else public_url,
                    x.get("name") or "", location, None, public_url,
                    x.get("releasedDate"), raw,
                ))
            offset += len(items)
            if not items or (total is not None and offset >= total) or offset >= max_jobs:
                break

        complete = total is None or len(out) >= total
        for job in out:
            raw = dict(job.raw or {})
            raw["_provider_total"] = total
            raw["_provider_returned"] = len(out)
            raw["_provider_complete"] = complete
            job.raw = raw

        if company["source"].get("enrich_details", True):
            self._enrich_india(
                ident,
                out,
                workers=max(1, min(12, int(company["source"].get("detail_workers") or 6))),
            )
        return out

    @staticmethod
    def _public_url(ident, posting_id, title, ref=None):
        ref_text = str(ref or "")
        if ref_text.startswith("https://jobs.smartrecruiters.com/"):
            return ref_text
        if posting_id:
            return f"https://jobs.smartrecruiters.com/{ident}/{posting_id}-{_slug(title)}"
        return ref_text or None

    @classmethod
    def _enrich_india(cls, ident, jobs, workers=6):
        targets = [job for job in jobs if _india(job.location) and job.external_id]
        if not targets:
            return

        def detail(job):
            url = f"https://api.smartrecruiters.com/v1/companies/{ident}/postings/{job.external_id}"
            try:
                r = session().get(url, timeout=timeout_seconds(), headers={"Accept": "application/json"})
                r.raise_for_status()
                return job, r.json(), None
            except Exception as exc:
                return job, None, exc

        with ThreadPoolExecutor(max_workers=workers) as pool:
            futures = [pool.submit(detail, job) for job in targets]
            for future in as_completed(futures):
                job, payload, error = future.result()
                raw = dict(job.raw or {})
                if error is not None:
                    raw["_detail_fetch_error"] = f"{type(error).__name__}: {error}"
                    job.raw = raw
                    continue
                payload = payload or {}
                sections = ((payload.get("jobAd") or {}).get("sections") or {})
                parts = []
                if isinstance(sections, dict):
                    for key in ("jobDescription", "qualifications", "additionalInformation", "companyDescription"):
                        section = sections.get(key) or {}
                        if isinstance(section, dict):
                            text = _plain(section.get("text"))
                        else:
                            text = _plain(section)
                        if text:
                            parts.append(text)
                elif isinstance(sections, list):
                    for section in sections:
                        if isinstance(section, dict):
                            text = _plain(section.get("text") or section.get("content"))
                            if text:
                                parts.append(text)
                if parts:
                    job.description = "\n\n".join(dict.fromkeys(parts))
                loc = payload.get("location") or {}
                if isinstance(loc, dict):
                    value = ", ".join(str(loc.get(k)) for k in ("city", "region", "country") if loc.get(k))
                    if value:
                        job.location = value
                job.title = clean_text(payload.get("name")) or job.title
                job.posted_at = clean_text(payload.get("releasedDate")) or job.posted_at
                job.job_url = cls._public_url(ident, job.external_id, job.title, payload.get("ref"))
                raw["_detail_enriched"] = bool(job.description)
                raw["_smartrecruiters_detail"] = payload
                job.raw = raw
