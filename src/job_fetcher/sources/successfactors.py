from __future__ import annotations

import os
import re
from collections import deque
from urllib.parse import urljoin, urlparse

from bs4 import BeautifulSoup

from job_fetcher.models import Job
from job_fetcher.sources.base import JobSource
from job_fetcher.sources.generic_extract import clean_text, dedupe, extract_jsonld
from job_fetcher.sources.http_client import session, timeout_seconds
from job_fetcher.sources.playwright_auto import PlaywrightAutoSource


SF_MARKERS = (
    "rmkcdn.successfactors.com",
    "careersitecompanyid",
    "sap as service provider",
    "successfactors",
)
JOB_PATH_RE = re.compile(r"/job/(?:[^/?#]+/)?(?P<id>\d+)(?:/|$)", re.I)
RESULTS_RE = re.compile(r"Results\s+\d+\s*[–—-]\s*\d+\s+of\s+(?P<total>\d+)", re.I)
DATE_RE = re.compile(r"\b(?:\d{1,2}\s+[A-Za-z]{3}\s+\d{4}|\d{1,2}/\d{1,2}/\d{2,4})\b")


class SuccessFactorsSource(JobSource):
    """Public SAP SuccessFactors / jobs2web career-site adapter.

    This adapter intentionally uses the public external career site rather than
    authenticated Recruiting OData APIs. SAP documents external career sites as
    the candidate-facing place where organizations publish jobs; its recruiting
    OData entities are permission-controlled, so they are not a safe anonymous
    dependency for a personal job fetcher.
    """

    @staticmethod
    def looks_like_successfactors(html: str, url: str = "") -> bool:
        host = urlparse(url).netloc.lower()
        if "successfactors.com" in host or "jobs2web.com" in host:
            return True
        low = (html or "")[:250_000].lower()
        return any(marker in low for marker in SF_MARKERS)

    def fetch(self, company):
        src = company.get("source") or {}
        entry = src.get("entry_url") or company["career_url"]
        max_pages = max(1, int(src.get("max_pages", os.getenv("JOB_FETCHER_SUCCESSFACTORS_MAX_PAGES", "30"))))
        max_jobs = max(1, int(src.get("max_jobs", "5000")))

        client = session()
        try:
            jobs = self._crawl_listing(company, client, entry, max_pages=max_pages, max_jobs=max_jobs)
            if jobs:
                return jobs
            static_error = RuntimeError("successfactors_public_listing_returned_no_jobs")
        except Exception as exc:
            static_error = exc

        if src.get("disable_browser_fallback") or os.getenv("JOB_FETCHER_DISABLE_BROWSER", "0") == "1":
            raise RuntimeError(f"successfactors_static_fetch_failed: {static_error}")

        c = dict(company)
        c["source"] = dict(src)
        c["source"]["entry_url"] = entry
        try:
            jobs = PlaywrightAutoSource().fetch(c)
        except Exception as browser_exc:
            raise RuntimeError(
                f"successfactors_fetch_failed: static={static_error}; browser={browser_exc}"
            ) from browser_exc
        if not jobs:
            raise RuntimeError(
                f"successfactors_fetch_failed: static={static_error}; browser returned no jobs"
            )
        for job in jobs:
            job.source_type = "successfactors"
        return dedupe(jobs)

    def _crawl_listing(self, company, client, entry: str, *, max_pages: int, max_jobs: int):
        queue = deque([entry])
        seen_pages: set[str] = set()
        jobs = []
        expected_total = None
        listing_host = urlparse(entry).netloc.lower()

        while queue and len(seen_pages) < max_pages and len(jobs) < max_jobs:
            url = queue.popleft()
            if url in seen_pages:
                continue
            response = client.get(url, timeout=timeout_seconds(), allow_redirects=True)
            response.raise_for_status()
            final_url = response.url
            if urlparse(final_url).netloc.lower() != listing_host:
                raise RuntimeError(f"successfactors_listing_redirected_off_host: {final_url}")
            seen_pages.add(final_url)

            page_jobs, total = self.parse_listing_page(company, response.text, final_url)
            jobs.extend(page_jobs)
            jobs = dedupe(jobs)
            if total is not None:
                expected_total = max(expected_total or 0, total)
            if len(jobs) >= max_jobs or (expected_total is not None and len(jobs) >= expected_total):
                break

            for page_url in self.pagination_links(response.text, final_url):
                if page_url not in seen_pages and page_url not in queue:
                    queue.append(page_url)

        return dedupe(jobs[:max_jobs])

    @staticmethod
    def parse_listing_page(company, html: str, base_url: str) -> tuple[list[Job], int | None]:
        soup = BeautifulSoup(html, "html.parser")
        text = soup.get_text(" ", strip=True)
        match = RESULTS_RE.search(text)
        total = int(match.group("total")) if match else None

        jobs = []
        seen_urls = set()
        for a in soup.select('a[href*="/job/"]'):
            href = a.get("href") or ""
            job_url = urljoin(base_url, href)
            path_match = JOB_PATH_RE.search(urlparse(job_url).path)
            if not path_match or job_url in seen_urls:
                continue
            title = clean_text(a.get_text(" ", strip=True))
            if not title or title.lower() in {"apply now", "apply now »", "view job"}:
                continue
            seen_urls.add(job_url)

            container = a.find_parent("tr") or a.find_parent(class_=re.compile(r"job|search", re.I)) or a.parent
            cells = []
            if container is not None:
                cells = [clean_text(x.get_text(" ", strip=True)) for x in container.find_all(["td", "div"], recursive=True)]
                cells = [x for x in cells if x]
            row_text = clean_text(container.get_text(" ", strip=True)) if container is not None else None

            location = None
            posted = None
            req_id = None
            if row_text:
                dm = DATE_RE.search(row_text)
                if dm:
                    posted = dm.group(0)
                # Standard jobs2web row is Req ID | Title | Location | Date.
                compact = [x for x in cells if x != title and x not in {posted}]
                for value in compact:
                    if req_id is None and re.fullmatch(r"\d{2,12}", value):
                        req_id = value
                        continue
                    if location is None and len(value) <= 120 and re.search(
                        r"\b(IN|India|Bengaluru|Bangalore|Gurgaon|Gurugram|Mumbai|Pune|Hyderabad|Chennai|Noida|Delhi|Remote)\b",
                        value,
                        re.I,
                    ):
                        location = value
            external_id = path_match.group("id") or req_id or job_url
            jobs.append(Job(
                company_id=company["id"],
                company_name=company["name"],
                source_type="successfactors",
                external_id=external_id,
                title=title,
                location=clean_text(location),
                description=None,
                job_url=job_url,
                posted_at=clean_text(posted),
                raw={"req_id": req_id, "listing_row": row_text},
            ))

        # Some detail pages include JobPosting JSON-LD but no listing rows.
        if not jobs:
            for job in extract_jsonld(company, html, base_url, "successfactors"):
                jobs.append(job)
        return dedupe(jobs), total

    @staticmethod
    def pagination_links(html: str, base_url: str) -> list[str]:
        soup = BeautifulSoup(html, "html.parser")
        base = urlparse(base_url)
        out = []
        seen = set()
        for a in soup.select("a[href]"):
            href = a.get("href") or ""
            url = urljoin(base_url, href)
            parsed = urlparse(url)
            if parsed.netloc.lower() != base.netloc.lower():
                continue
            # jobs2web page offsets appear as a numeric path segment, e.g. /25/.
            text = clean_text(a.get_text(" ", strip=True)) or ""
            paginationish = bool(re.search(r"/\d+/(?:\?|$)", parsed.path + ("?" if parsed.query else "")))
            paginationish = paginationish or text.lower() in {"next", "next page", "›", "»"} or text.isdigit()
            if not paginationish:
                continue
            if "/job/" in parsed.path.lower():
                continue
            if url not in seen:
                seen.add(url)
                out.append(url)
        return out
