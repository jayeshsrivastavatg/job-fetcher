from __future__ import annotations

from bs4 import BeautifulSoup

from job_fetcher.models import Job
from job_fetcher.sources.base import JobSource
from job_fetcher.sources.http_client import session, timeout_seconds


def _text(value) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _html_text(*parts) -> str | None:
    html = "\n".join(str(part) for part in parts if part)
    if not html:
        return None
    return BeautifulSoup(html, "html.parser").get_text("\n", strip=True) or None


class UberJobsApiSource(JobSource):
    """Exhaustive source for the first-party API that powers jobs.uber.com."""

    endpoint = "https://jobs.uber.com/api/jobs/search/"
    page_size = 100

    def _page(self, page: int) -> dict:
        response = session().get(
            self.endpoint,
            params={"page": page, "pagesize": self.page_size},
            timeout=timeout_seconds(),
            headers={"Accept": "application/json"},
        )
        response.raise_for_status()
        payload = response.json()
        if not isinstance(payload, dict) or not isinstance(payload.get("jobs"), list):
            raise RuntimeError("uber_jobs_api_invalid_payload")
        return payload

    @staticmethod
    def _location(row: dict) -> str | None:
        values = []
        for loc in row.get("Locations") or []:
            if not isinstance(loc, dict):
                continue
            value = _text(loc.get("Address"))
            if not value:
                pieces = [_text(loc.get(key)) for key in ("City", "Region", "Country")]
                value = ", ".join(piece for piece in pieces if piece) or None
            if value and value not in values:
                values.append(value)
        return " | ".join(values) or None

    @staticmethod
    def _job(company: dict, row: dict) -> Job:
        job_id = _text(row.get("Id"))
        title = _text(row.get("Title"))
        if not job_id or not title:
            raise RuntimeError("uber_jobs_api_record_missing_id_or_title")
        return Job(
            company_id=company["id"],
            company_name=company["name"],
            source_type="uber_jobs_api",
            external_id=job_id,
            title=title,
            location=UberJobsApiSource._location(row),
            description=_html_text(row.get("Description"), row.get("AdditionalText"), row.get("Summary")),
            job_url=f"https://jobs.uber.com/en/jobs/{job_id}/",
            posted_at=_text(row.get("DisplayDate")),
            raw=row,
        )

    def fetch(self, company: dict) -> list[Job]:
        first = self._page(1)
        total_pages = int(first.get("totalPages") or 0)
        total_jobs = int(first.get("totalJobs") or 0)
        if total_pages < 0 or total_jobs < 0:
            raise RuntimeError("uber_jobs_api_invalid_totals")

        rows = list(first.get("jobs") or [])
        expected_page = 1
        if int(first.get("page") or 1) != expected_page:
            raise RuntimeError("uber_jobs_api_unexpected_page")

        for page in range(2, total_pages + 1):
            payload = self._page(page)
            if int(payload.get("page") or page) != page:
                raise RuntimeError(f"uber_jobs_api_unexpected_page:{page}")
            if int(payload.get("totalPages") or total_pages) not in {total_pages - 1, total_pages, total_pages + 1}:
                raise RuntimeError("uber_jobs_api_page_count_drift")
            rows.extend(payload.get("jobs") or [])

        unique: dict[str, dict] = {}
        for row in rows:
            if not isinstance(row, dict):
                continue
            job_id = _text(row.get("Id"))
            if job_id:
                unique[job_id] = row

        end = self._page(1)
        end_total = int(end.get("totalJobs") or 0)
        if abs(end_total - total_jobs) > self.page_size:
            raise RuntimeError(f"uber_jobs_api_large_drift:{total_jobs}->{end_total}")

        lower_bound = min(total_jobs, end_total)
        if len(unique) < max(0, lower_bound - 2):
            raise RuntimeError(f"uber_jobs_api_incomplete:{len(unique)}/{lower_bound}")
        return [self._job(company, row) for row in unique.values()]


class AtlassianListingsApiSource(JobSource):
    """Exhaustive first-party source for the JSON used by Atlassian's all-jobs page."""

    endpoint = "https://www.atlassian.com/endpoint/careers/listings"

    def _rows(self) -> list[dict]:
        response = session().get(
            self.endpoint,
            timeout=timeout_seconds(),
            headers={"Accept": "application/json"},
        )
        response.raise_for_status()
        payload = response.json()
        if not isinstance(payload, list):
            raise RuntimeError("atlassian_listings_invalid_payload")
        return [row for row in payload if isinstance(row, dict)]

    @staticmethod
    def record_key(row: dict) -> str | None:
        job_id = _text(row.get("id"))
        portal_id = _text(row.get("portalId"))
        portal_post = row.get("portalJobPost") if isinstance(row.get("portalJobPost"), dict) else {}
        if not job_id:
            job_id = _text(portal_post.get("id"))
        if not portal_id:
            portal_id = _text(portal_post.get("portalId"))
        if not job_id:
            return None
        return f"{portal_id or 'unknown'}:{job_id}"

    @staticmethod
    def _job(company: dict, row: dict) -> Job:
        key = AtlassianListingsApiSource.record_key(row)
        title = _text(row.get("title"))
        job_id = _text(row.get("id")) or _text((row.get("portalJobPost") or {}).get("id"))
        if not key or not job_id or not title:
            raise RuntimeError("atlassian_listing_missing_id_or_title")
        locations = []
        for value in row.get("locations") or []:
            value = _text(value)
            if value and value not in locations:
                locations.append(value)
        return Job(
            company_id=company["id"],
            company_name=company["name"],
            source_type="atlassian_listings_api",
            external_id=key,
            title=title,
            location=" | ".join(locations) or None,
            description=_html_text(row.get("overview"), row.get("responsibilities"), row.get("qualifications")),
            job_url=f"https://www.atlassian.com/company/careers/details/{job_id}",
            posted_at=_text((row.get("portalJobPost") or {}).get("updatedDate")),
            raw=row,
        )

    def fetch(self, company: dict) -> list[Job]:
        rows = self._rows()
        start_keys = {key for row in rows if (key := self.record_key(row))}
        if len(start_keys) != len(rows):
            raise RuntimeError(f"atlassian_listing_missing_or_duplicate_identity:{len(start_keys)}/{len(rows)}")

        end_rows = self._rows()
        end_keys = {key for row in end_rows if (key := self.record_key(row))}
        if len(end_keys.symmetric_difference(start_keys)) > 2:
            raise RuntimeError("atlassian_listings_board_changed_during_fetch")

        by_key = {self.record_key(row): row for row in end_rows}
        return [self._job(company, row) for key, row in by_key.items() if key]


class NaviOfficialCareersSource(JobSource):
    """Fail closed until Navi exposes an approved enumerable vacancy feed.

    Navi's branded careers URLs currently present a managed access challenge to
    automated clients. The old Freshteam board is reachable but advertises zero
    jobs, while current hiring links observed publicly resolve to LinkedIn. This
    project deliberately does not automate LinkedIn or bypass access controls.
    Returning zero or falling back to generic HTML here would make the dataset look
    complete when it cannot be proven complete, so the source is explicitly blocked.
    """

    def fetch(self, company: dict) -> list[Job]:
        raise RuntimeError(
            "automation_disallowed_or_unavailable: Navi does not expose an approved "
            "enumerable first-party vacancy feed; branded careers access is restricted "
            "and LinkedIn automation is not used. Manual or company-approved feed required."
        )
