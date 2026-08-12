from __future__ import annotations

import math
import re
import time
from copy import deepcopy
from urllib.parse import urljoin, urlparse

from bs4 import BeautifulSoup

from job_fetcher.models import Job
from job_fetcher.sources.base import JobSource
from job_fetcher.sources.generic_extract import extract_jsonld
from job_fetcher.sources.http_client import session, timeout_seconds
from job_fetcher.sources.smartrecruiters import SmartRecruitersSource


_BASE = "https://careers.servicenow.com/jobs/"
_JOB_RE = re.compile(r"/jobs/(?P<id>\d{8,})/", re.I)
_TOTAL_RE = re.compile(r"\bof\s+([\d,]+)\s+matching jobs\b", re.I)


def _clean(value):
    return re.sub(r"\s+", " ", str(value or "")).strip()


def _numeric_id(value):
    match = re.search(r"(\d{8,})", str(value or ""))
    return match.group(1) if match else None


class ServiceNowSource(JobSource):
    """SmartRecruiters first, official ServiceNow careers site as a coverage guard.

    ServiceNow's employer site can contain current vacancies that are absent from
    the public SmartRecruiters company listing. The public API remains the primary
    structured source, but a fetch is not complete unless every current vacancy
    enumerated on careers.servicenow.com is represented in the returned set.

    Extra API jobs are deliberately retained; the required invariant is
    official_website_jobs <= returned_jobs.
    """

    def fetch(self, company):
        provider_company = deepcopy(company)
        provider_company["source"] = {"type": "smartrecruiters", "company_identifier": "ServiceNow"}
        provider_jobs = list(SmartRecruitersSource().fetch(provider_company) or [])

        website_records, expected = self._enumerate_official_site()
        if expected is None:
            raise RuntimeError("servicenow_website_total_unavailable")
        if len(website_records) < expected:
            raise RuntimeError(
                f"servicenow_website_incomplete_pagination: expected={expected} enumerated={len(website_records)}"
            )

        by_numeric_id = {}
        for job in provider_jobs:
            jid = _numeric_id(getattr(job, "external_id", None)) or _numeric_id(getattr(job, "job_url", None))
            if jid:
                by_numeric_id[jid] = job

        missing = [record for jid, record in website_records.items() if jid not in by_numeric_id]
        supplements = [self._fetch_official_detail(company, record) for record in missing]

        # Re-read the first website page after the potentially long crawl. If the
        # board changed during the fetch, fail closed instead of blessing a moving
        # snapshot. The next run will naturally retry with the new inventory.
        final_total = self._official_total()
        if final_total != expected:
            raise RuntimeError(
                f"servicenow_website_changed_during_fetch: before={expected} after={final_total}"
            )

        out = provider_jobs + supplements
        returned_ids = {
            _numeric_id(getattr(job, "external_id", None)) or _numeric_id(getattr(job, "job_url", None))
            for job in out
        }
        returned_ids.discard(None)
        still_missing = sorted(set(website_records) - returned_ids)
        if still_missing:
            raise RuntimeError(
                f"servicenow_website_coverage_failed: missing={len(still_missing)} ids={','.join(still_missing[:10])}"
            )
        return out

    def _official_total(self):
        client = session()
        response = client.get(
            _BASE,
            params={"page": 1, "pagesize": 20, "audit": int(time.time() * 1000)},
            timeout=timeout_seconds(),
            headers={"User-Agent": "Mozilla/5.0 PersonalJobFetcher/0.4"},
        )
        response.raise_for_status()
        text = BeautifulSoup(response.text, "html.parser").get_text(" ", strip=True)
        match = _TOTAL_RE.search(text)
        return int(match.group(1).replace(",", "")) if match else None

    def _enumerate_official_site(self):
        client = session()
        records = {}
        expected = None
        seen_page_fingerprints = set()
        max_pages = 80

        for page_number in range(1, max_pages + 1):
            response = client.get(
                _BASE,
                params={"page": page_number, "pagesize": 20, "audit": f"{int(time.time() * 1000)}-{page_number}"},
                timeout=timeout_seconds(),
                headers={"User-Agent": "Mozilla/5.0 PersonalJobFetcher/0.4"},
            )
            response.raise_for_status()
            soup = BeautifulSoup(response.text, "html.parser")
            if expected is None:
                total_match = _TOTAL_RE.search(soup.get_text(" ", strip=True))
                expected = int(total_match.group(1).replace(",", "")) if total_match else None
                if expected is None:
                    return {}, None

            page_records = {}
            for anchor in soup.select("a[href]"):
                href = urljoin(response.url, anchor.get("href") or "")
                match = _JOB_RE.search(urlparse(href).path + "/")
                if not match:
                    continue
                title = _clean(anchor.get_text(" ", strip=True))
                if not title:
                    continue
                jid = match.group("id")
                page_records[jid] = {"id": jid, "title": title, "url": href}

            fingerprint = tuple(sorted(page_records))
            if not page_records:
                break
            if fingerprint in seen_page_fingerprints:
                # The server ignored pagination. Continuing would create false
                # confidence, so fail through the count check below.
                break
            seen_page_fingerprints.add(fingerprint)
            records.update(page_records)

            if len(records) >= expected:
                break
            if page_number >= math.ceil(expected / 20) + 2:
                break
            time.sleep(0.05)

        return records, expected

    def _fetch_official_detail(self, company, record):
        client = session()
        response = client.get(
            record["url"], timeout=timeout_seconds(), allow_redirects=True,
            headers={"User-Agent": "Mozilla/5.0 PersonalJobFetcher/0.4"},
        )
        response.raise_for_status()
        jsonld = extract_jsonld(company, response.text, response.url, source_type="servicenow_official")
        if jsonld:
            job = jsonld[0]
            job.external_id = record["id"]
            job.job_url = response.url
            raw = dict(job.raw or {})
            raw["_servicenow_website_coverage_supplement"] = True
            raw["_website_listing_title"] = record["title"]
            job.raw = raw
            return job

        soup = BeautifulSoup(response.text, "html.parser")
        description = None
        main = soup.select_one("main") or soup.select_one("article")
        if main:
            description = _clean(main.get_text(" ", strip=True))
        return Job(
            company["id"], company["name"], "servicenow_official", record["id"],
            record["title"], None, description, response.url, None,
            {"_servicenow_website_coverage_supplement": True},
        )
