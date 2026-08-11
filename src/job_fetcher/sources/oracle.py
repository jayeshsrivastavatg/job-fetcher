from __future__ import annotations

import os
import re
from urllib.parse import urljoin, urlparse

from bs4 import BeautifulSoup

from job_fetcher.models import Job
from job_fetcher.sources.base import JobSource
from job_fetcher.sources.generic_extract import clean_text, dedupe, extract_embedded_json, extract_jsonld
from job_fetcher.sources.http_client import session, timeout_seconds
from job_fetcher.sources.playwright_auto import PlaywrightAutoSource


ORACLE_CE_PATH_RE = re.compile(
    r"/hcmUI/CandidateExperience/(?P<locale>[^/]+)/sites/(?P<site>[^/]+)(?:/|$)",
    re.IGNORECASE,
)


class OracleSource(JobSource):
    """Oracle Fusion Candidate Experience source.

    Oracle documents the recruitingCEJobRequisitions endpoint but labels the
    resource as Oracle-internal. Some public Candidate Experience tenants still
    expose it to anonymous career-site traffic, while others do not. We therefore
    use the endpoint as a fast structured path and fall back to browser/network
    discovery instead of treating 401/403/404 as a permanent scraper failure.
    """

    API_PATH = "/hcmRestApi/resources/11.13.18.05/recruitingCEJobRequisitions"

    @staticmethod
    def parse_candidate_experience_url(url: str) -> dict[str, str] | None:
        parsed = urlparse(url)
        host = parsed.netloc.lower()
        if "oraclecloud.com" not in host:
            return None
        match = ORACLE_CE_PATH_RE.search(parsed.path)
        if not match:
            return None
        return {
            "host": host,
            "locale": match.group("locale"),
            "site_number": match.group("site"),
        }

    def fetch(self, company):
        src = company.get("source") or {}
        entry = src.get("entry_url") or company["career_url"]

        # careers.oracle.com is Oracle's public Candidate Experience front door.
        # For this surface, prefer the public filtered job listing instead of the
        # internal recruitingCE REST resource used by some oraclecloud.com tenants.
        if src.get("mode") == "public_search" or urlparse(entry).netloc.lower() == "careers.oracle.com":
            return self._fetch_public_search(company, entry, src)

        parsed = self._source_settings(src, entry)
        if not parsed:
            raise ValueError(
                "oracle source requires host/site_number or an Oracle CandidateExperience entry_url"
            )

        host = parsed["host"]
        site_number = parsed["site_number"]
        locale = parsed.get("locale") or "en"
        max_jobs = max(1, int(src.get("max_jobs", 5000)))
        page_size = min(max(1, int(src.get("page_size", 25))), 100)

        try:
            jobs = self._fetch_candidate_experience_api(
                company=company,
                host=host,
                site_number=site_number,
                locale=locale,
                page_size=page_size,
                max_jobs=max_jobs,
            )
            if jobs:
                return jobs
        except Exception as exc:
            if src.get("strict_api"):
                raise
            # Candidate Experience availability differs by tenant. Keep the
            # error as context if the browser path also fails.
            api_error = exc
        else:
            api_error = RuntimeError("oracle_candidate_api_returned_no_jobs")

        if src.get("disable_browser_fallback") or os.getenv("JOB_FETCHER_DISABLE_BROWSER", "0") == "1":
            raise RuntimeError(f"oracle_structured_fetch_failed: {api_error}")

        browser_company = dict(company)
        browser_company["source"] = dict(src)
        browser_company["source"]["entry_url"] = entry
        try:
            jobs = PlaywrightAutoSource().fetch(browser_company)
        except Exception as browser_exc:
            raise RuntimeError(
                f"oracle_fetch_failed: structured={api_error}; browser={browser_exc}"
            ) from browser_exc
        if not jobs:
            raise RuntimeError(
                f"oracle_fetch_failed: structured={api_error}; browser returned no jobs"
            )
        return jobs


    def _fetch_public_search(self, company, entry: str, src: dict):
        client = session()
        max_pages = max(1, int(src.get("max_pages", 20)))
        jobs = []
        seen_pages = set()
        current = entry
        static_error = None

        for _ in range(max_pages):
            if not current or current in seen_pages:
                break
            seen_pages.add(current)
            try:
                response = client.get(current, timeout=timeout_seconds(), allow_redirects=True)
                response.raise_for_status()
                page_jobs = self.parse_public_search_page(company, response.text, response.url)
                jobs.extend(page_jobs)
                jobs = dedupe(jobs)
                next_url = self._find_public_next(response.text, response.url)
                if not next_url or next_url in seen_pages:
                    break
                current = next_url
            except Exception as exc:
                static_error = exc
                break

        if jobs:
            return jobs

        if src.get("disable_browser_fallback") or os.getenv("JOB_FETCHER_DISABLE_BROWSER", "0") == "1":
            raise RuntimeError(f"oracle_public_search_failed: {static_error or 'no jobs detected'}")

        browser_company = dict(company)
        browser_company["source"] = dict(src)
        browser_company["source"]["entry_url"] = entry
        try:
            browser_jobs = PlaywrightAutoSource().fetch(browser_company)
        except Exception as browser_exc:
            raise RuntimeError(
                f"oracle_public_search_failed: static={static_error}; browser={browser_exc}"
            ) from browser_exc
        if not browser_jobs:
            raise RuntimeError(
                f"oracle_public_search_failed: static={static_error}; browser returned no jobs"
            )
        for job in browser_jobs:
            job.source_type = "oracle"
        return dedupe(browser_jobs)

    @staticmethod
    def parse_public_search_page(company, html: str, base_url: str):
        jobs = []
        jobs.extend(extract_jsonld(company, html, base_url, "oracle"))
        jobs.extend(extract_embedded_json(company, html, base_url, "oracle"))
        soup = BeautifulSoup(html, "html.parser")
        job_re = re.compile(r"/[^/]+/sites/[^/]+/job/(?P<id>\d+)(?:/|$)", re.I)
        seen = set()
        for a in soup.select("a[href]"):
            absolute = urljoin(base_url, a.get("href") or "")
            match = job_re.search(urlparse(absolute).path)
            if not match:
                continue
            job_id = match.group("id")
            title = clean_text(a.get_text(" ", strip=True))
            if not title or title.lower() in {"apply now", "view job", "view details", "learn more"}:
                parent = a.parent
                title = None
                for _ in range(4):
                    if parent is None:
                        break
                    for selector in ("h1", "h2", "h3", "h4", "[class*='title']"):
                        node = parent.select_one(selector) if hasattr(parent, "select_one") else None
                        value = clean_text(node.get_text(" ", strip=True)) if node else None
                        if value and 3 < len(value) <= 220:
                            title = value
                            break
                    if title:
                        break
                    parent = parent.parent
            if not title or job_id in seen:
                continue
            seen.add(job_id)
            context_node = a.parent
            for _ in range(3):
                if context_node is None or context_node.parent is None:
                    break
                context_node = context_node.parent
            context = clean_text(context_node.get_text(" ", strip=True)) if context_node else None
            location = None
            if context:
                m = re.search(
                    r"\b(BENGALURU|BANGALORE|HYDERABAD|PUNE|CHENNAI|NOIDA|MUMBAI|GURUGRAM|GURGAON)(?:,\s*([A-Z ]+))?,\s*India\b",
                    context, re.I,
                )
                if m:
                    parts = [clean_text(m.group(1)), clean_text(m.group(2)), "India"]
                    location = ", ".join(x for x in parts if x)
                elif re.search(r"\bIndia\b", context, re.I):
                    location = "India"
            jobs.append(Job(
                company["id"], company["name"], "oracle", job_id, title, location,
                None, absolute, None, {"context": context},
            ))
        return dedupe(jobs)

    @staticmethod
    def _find_public_next(html: str, base_url: str):
        soup = BeautifulSoup(html, "html.parser")
        for a in soup.select("a[rel~=next][href]"):
            return urljoin(base_url, a.get("href"))
        for a in soup.select("a[href]"):
            text = clean_text(a.get_text(" ", strip=True)) or ""
            aria = clean_text(a.get("aria-label")) or ""
            if text.lower() in {"next", "next page", "›", "»", ">"} or "next" in aria.lower():
                return urljoin(base_url, a.get("href"))
        return None

    @staticmethod
    def _source_settings(src: dict, entry: str) -> dict[str, str] | None:
        if src.get("host") and src.get("site_number"):
            return {
                "host": str(src["host"]).lower(),
                "site_number": str(src["site_number"]),
                "locale": str(src.get("locale") or "en"),
            }
        return OracleSource.parse_candidate_experience_url(entry)

    def _fetch_candidate_experience_api(
        self,
        *,
        company,
        host: str,
        site_number: str,
        locale: str,
        page_size: int,
        max_jobs: int,
    ):
        api = f"https://{host}{self.API_PATH}"
        client = session()
        jobs = []
        offset = 0

        while offset < max_jobs:
            finder = (
                "findReqs;"
                f"siteNumber={site_number},limit={page_size},offset={offset},"
                "sortBy=POSTING_DATES_DESC"
            )
            response = client.get(
                api,
                params={"finder": finder, "onlyData": "true"},
                timeout=timeout_seconds(),
                headers={"Accept": "application/json"},
            )
            response.raise_for_status()
            data = response.json()
            rows, total = self._extract_requisition_rows(data)
            if not rows:
                break

            for row in rows:
                job = self._to_job(company, host, site_number, locale, row)
                if job:
                    jobs.append(job)
                if len(jobs) >= max_jobs:
                    break

            step = len(rows)
            offset += step
            if step == 0 or (total is not None and offset >= total):
                break

        return dedupe(jobs)

    @staticmethod
    def _extract_requisition_rows(data) -> tuple[list[dict], int | None]:
        """Normalize Oracle's wrapper shape into requisition rows.

        The CE collection commonly returns one/more search wrapper objects under
        ``items`` and the actual jobs under each wrapper's ``requisitionList``.
        Keeping this normalizer isolated makes the adapter resilient to a tenant
        returning the list directly in tests/alternate versions.
        """
        if not isinstance(data, dict):
            return [], None

        rows: list[dict] = []
        totals: list[int] = []

        direct = data.get("requisitionList")
        if isinstance(direct, list):
            rows.extend(x for x in direct if isinstance(x, dict))
        if isinstance(data.get("TotalJobsCount"), int):
            totals.append(data["TotalJobsCount"])

        for wrapper in data.get("items") or []:
            if not isinstance(wrapper, dict):
                continue
            nested = wrapper.get("requisitionList")
            if isinstance(nested, list):
                rows.extend(x for x in nested if isinstance(x, dict))
            total = wrapper.get("TotalJobsCount")
            if isinstance(total, int):
                totals.append(total)

        total = max(totals) if totals else None
        return rows, total

    @staticmethod
    def _to_job(company, host: str, site_number: str, locale: str, row: dict) -> Job | None:
        title = clean_text(row.get("Title"))
        external_id = clean_text(row.get("Id"))
        if not title:
            return None

        job_url = None
        if external_id:
            job_url = (
                f"https://{host}/hcmUI/CandidateExperience/{locale}/sites/"
                f"{site_number}/job/{external_id}"
            )

        description_parts = [
            clean_text(row.get("ShortDescriptionStr")),
            clean_text(row.get("ExternalResponsibilitiesStr")),
            clean_text(row.get("ExternalQualificationsStr")),
        ]
        description = "\n\n".join(x for x in description_parts if x) or None

        raw = dict(row)
        return Job(
            company_id=company["id"],
            company_name=company["name"],
            source_type="oracle",
            external_id=external_id or job_url,
            title=title,
            location=clean_text(row.get("PrimaryLocation")),
            description=description,
            job_url=job_url,
            posted_at=clean_text(row.get("PostedDate")),
            raw=raw,
        )
