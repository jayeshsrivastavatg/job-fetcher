from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from urllib.parse import urljoin, urlparse

from bs4 import BeautifulSoup

from job_fetcher.models import Job
from job_fetcher.sources.base import JobSource
from job_fetcher.sources.generic_extract import clean_text, extract_jsonld
from job_fetcher.sources.http_client import session, timeout_seconds


class RadancySource(JobSource):
    """Public Radancy/TalentBrew careers source."""

    def fetch(self, company):
        src = company.get("source") or {}
        entry = str(src.get("entry_url") or company.get("career_url") or "").strip()
        parsed = urlparse(entry)
        if parsed.scheme != "https" or not parsed.netloc:
            raise ValueError("radancy source requires an https tenant URL")
        origin = f"https://{parsed.netloc}"
        lang = str(src.get("locale") or "en").strip("/") or "en"
        page_size = max(1, min(int(src.get("page_size", 50)), 100))
        max_pages = max(1, min(int(src.get("max_pages", 100)), 200))
        client = session()
        headers = {
            "Accept": "application/json, text/javascript, */*; q=0.01",
            "X-Requested-With": "XMLHttpRequest",
            "User-Agent": "Mozilla/5.0 PersonalJobFetcher/0.1",
        }

        jobs = []
        seen = set()
        exhausted = False
        for page in range(1, max_pages + 1):
            response = client.get(
                f"{origin}/{lang}/search-jobs/results",
                params={"ActiveFacetID": 0, "CurrentPage": page, "RecordsPerPage": page_size, "FacetType": 0},
                timeout=timeout_seconds(),
                headers=headers,
            )
            response.raise_for_status()
            fragment, has_jobs = self._result_fragment(response)
            if has_jobs is False:
                exhausted = True
                break
            tiles = self._parse_tiles(fragment, origin, parsed.netloc)
            if not tiles:
                exhausted = True
                break
            for tile in tiles:
                job_id = tile["id"]
                if job_id in seen:
                    continue
                seen.add(job_id)
                jobs.append(Job(
                    company["id"], company["name"], "radancy",
                    job_id, tile["title"], tile["location"], None,
                    tile["url"], None, tile,
                ))
            if len(tiles) < page_size:
                exhausted = True
                break

        if not exhausted:
            raise RuntimeError(f"radancy pagination exceeded max_pages={max_pages}")
        if not jobs and src.get("allow_zero_jobs") is not True:
            raise RuntimeError("radancy public board returned zero jobs")

        if src.get("hydrate_india_details", True):
            india = [job for job in jobs if self._is_india(job.location)]
            workers = max(1, min(int(src.get("detail_workers", 8)), 12))
            if india:
                with ThreadPoolExecutor(max_workers=workers) as pool:
                    list(pool.map(lambda job: self._hydrate_detail(company, job), india))
        return jobs

    @staticmethod
    def _result_fragment(response):
        try:
            payload = response.json()
        except Exception:
            return response.text, None
        if isinstance(payload, dict):
            return payload.get("results") or "", payload.get("hasJobs")
        if isinstance(payload, str):
            return payload, None
        return response.text, None

    @staticmethod
    def _parse_tiles(fragment, origin, expected_host):
        if not isinstance(fragment, str) or not fragment.strip():
            return []
        soup = BeautifulSoup(fragment, "html.parser")
        out = []
        for anchor in soup.select("a[href]"):
            absolute = urljoin(origin, anchor.get("href") or "")
            parsed = urlparse(absolute)
            if parsed.scheme != "https" or parsed.netloc.casefold() != expected_host.casefold():
                continue
            parts = [part for part in parsed.path.split("/") if part]
            job_index = next((i for i, part in enumerate(parts) if part.casefold() == "job"), None)
            if job_index is None or len(parts) < job_index + 4:
                continue
            path_id = parts[-1] if parts[-1].isdigit() else None
            job_id = clean_text(anchor.get("data-job-id")) or path_id
            title = clean_text(anchor.get_text(" ", strip=True))
            if not job_id or not title:
                continue
            container = anchor.find_parent("li") or anchor.parent
            location_node = container.select_one(".job-location, .job-location-label, .location") if container else None
            location = clean_text(location_node.get_text(" ", strip=True)) if location_node else None
            out.append({"id": job_id, "title": title, "location": location, "url": absolute})
        return out

    @staticmethod
    def _is_india(location):
        low = str(location or "").casefold()
        return any(token in low for token in ("india", "bengaluru", "bangalore", "hyderabad", "pune", "chennai", "gurgaon", "gurugram", "mumbai"))

    @staticmethod
    def _hydrate_detail(company, job):
        try:
            response = session().get(
                job.job_url,
                timeout=timeout_seconds(),
                headers={"User-Agent": "Mozilla/5.0 PersonalJobFetcher/0.1"},
                allow_redirects=True,
            )
            response.raise_for_status()
            details = extract_jsonld(company, response.text, response.url, "radancy_job_jsonld")
            detail = next(
                (candidate for candidate in details if str(candidate.external_id or "").strip() == str(job.external_id).strip()),
                details[0] if len(details) == 1 else None,
            )
            if detail:
                if detail.description:
                    job.description = detail.description
                if detail.location:
                    job.location = detail.location
                if detail.posted_at:
                    job.posted_at = detail.posted_at
                job.raw = {"listing": job.raw, "detail": detail.raw, "detail_source": "public_jobposting_jsonld"}
        except Exception as exc:
            raw = dict(job.raw or {}) if isinstance(job.raw, dict) else {"listing": job.raw}
            raw["detail_hydration_error"] = f"{type(exc).__name__}: {exc}"
            job.raw = raw
