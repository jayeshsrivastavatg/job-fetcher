from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from urllib.parse import urljoin

from bs4 import BeautifulSoup

from job_fetcher.models import Job
from job_fetcher.sources.base import JobSource
from job_fetcher.sources.http_client import session, timeout_seconds
from job_fetcher.sources.generic_extract import clean_text


def _plain_html(value):
    if not value:
        return None
    return clean_text(BeautifulSoup(str(value), "html.parser").get_text(" ", strip=True))


def _india_location(value) -> bool:
    text = str(value or "").lower()
    return any(x in text for x in (
        "india", "bengaluru", "bangalore", "hyderabad", "gurugram", "gurgaon", "pune",
        "chennai", "noida", "mumbai", "delhi", "kolkata", "ahmedabad", "kochi",
    ))


class WorkdaySource(JobSource):
    """Exhaustive public Workday CXS adapter with India-detail enrichment.

    The CXS listing endpoint exposes an exact `total` and paginated jobPostings,
    which lets us prove listing completeness.  Listing rows intentionally omit the
    full JD, so for India-facing vacancies we additionally fetch the corresponding
    public CXS detail endpoint and persist jobDescription/locations/metadata used by
    relevance scoring and the candidate UI.
    """

    def fetch(self, company):
        src = company["source"]
        host, tenant, site = src["host"], src["tenant"], src["site"]
        locale = src.get("locale", "en-US")
        api = f"https://{host}/wday/cxs/{tenant}/{site}/jobs"
        s = session()
        out = []
        offset = 0
        limit = max(1, min(100, int(src.get("page_size") or 20)))
        total = None
        max_jobs = int(src.get("max_jobs", 5000))
        while True:
            body = {"appliedFacets": {}, "limit": limit, "offset": offset, "searchText": ""}
            r = s.post(api, json=body, timeout=timeout_seconds(), headers={"Content-Type": "application/json"})
            r.raise_for_status()
            data = r.json()
            items = data.get("jobPostings") or []
            if total is None:
                try:
                    total = int(data.get("total"))
                except (TypeError, ValueError):
                    total = None
            for x in items:
                ext = x.get("externalPath")
                job_url = f"https://{host}/{locale}/{site}{ext}" if ext else None
                eid = x.get("bulletFields", [None])[0] if isinstance(x.get("bulletFields"), list) and x.get("bulletFields") else None
                raw = dict(x)
                raw["_workday_external_path"] = ext
                raw["_workday_total"] = total
                out.append(Job(
                    company["id"], company["name"], "workday", clean_text(eid) or job_url,
                    x.get("title") or "", clean_text(x.get("locationsText")), None,
                    job_url, clean_text(x.get("postedOn")), raw,
                ))
            offset += len(items)
            if not items or (total is not None and offset >= total) or offset >= max_jobs:
                break

        # If the provider reports more jobs than our explicit safety cap, do not
        # silently pretend the result is complete. The live audit reads this marker.
        truncated = total is not None and len(out) < total
        for job in out:
            raw = dict(job.raw or {})
            raw["_provider_total"] = total
            raw["_provider_returned"] = len(out)
            raw["_provider_complete"] = not truncated
            job.raw = raw

        if src.get("enrich_details", True):
            self._enrich_india_details(out, api, workers=max(1, min(12, int(src.get("detail_workers") or 6))))
        return out

    @staticmethod
    def _enrich_india_details(jobs, api: str, workers: int = 6):
        targets = [
            job for job in jobs
            if _india_location(job.location)
            and isinstance(job.raw, dict)
            and job.raw.get("_workday_external_path")
        ]
        if not targets:
            return

        def fetch_detail(job):
            ext = str(job.raw.get("_workday_external_path") or "")
            url = urljoin(api.rstrip("/") + "/", ext.lstrip("/"))
            try:
                r = session().get(url, timeout=timeout_seconds(), headers={"Accept": "application/json"})
                r.raise_for_status()
                return job, r.json(), None
            except Exception as exc:
                return job, None, exc

        with ThreadPoolExecutor(max_workers=workers) as pool:
            futures = [pool.submit(fetch_detail, job) for job in targets]
            for future in as_completed(futures):
                job, payload, error = future.result()
                raw = dict(job.raw or {})
                if error is not None:
                    raw["_detail_fetch_error"] = f"{type(error).__name__}: {error}"
                    job.raw = raw
                    continue
                info = (payload or {}).get("jobPostingInfo") or payload or {}
                description = _plain_html(
                    info.get("jobDescription") or info.get("description") or info.get("jobDescriptionText")
                )
                if description:
                    job.description = description
                location = clean_text(info.get("location") or info.get("locationText"))
                additional = info.get("additionalLocations") or []
                if isinstance(additional, list):
                    extra = [clean_text(x) for x in additional if clean_text(x)]
                else:
                    extra = [clean_text(additional)] if clean_text(additional) else []
                if location or extra:
                    values = [x for x in [location, *extra] if x]
                    job.location = "; ".join(dict.fromkeys(values))
                posted = clean_text(info.get("postedOn") or info.get("startDate"))
                if posted:
                    job.posted_at = posted
                raw["_detail_enriched"] = bool(description)
                raw["_workday_detail"] = info
                job.raw = raw
