from __future__ import annotations

from urllib.parse import urlencode

from job_fetcher.models import Job
from job_fetcher.sources.base import JobSource
from job_fetcher.sources.http_client import session, timeout_seconds


ENDPOINT = "https://www.cohesity.com/bin/cohesity/open-positions/"
DETAIL_BASE = "https://www.cohesity.com/careers/open-positions/"


def _clean(value):
    return " ".join(str(value or "").split()).strip() or None


def flatten_job_data(payload: dict) -> list[dict]:
    grouped = payload.get("job_data") or {}
    rows = []
    if isinstance(grouped, dict):
        for department, items in grouped.items():
            if not isinstance(items, list):
                continue
            for item in items:
                if not isinstance(item, dict):
                    continue
                row = dict(item)
                row.setdefault("careerSiteDept", department)
                rows.append(row)
    elif isinstance(grouped, list):
        rows.extend(item for item in grouped if isinstance(item, dict))
    return rows


class CohesitySource(JobSource):
    """Consume the exact first-party JSON used by Cohesity's careers website.

    The employer page loads this complete grouped job collection from cohesity.com.
    This is a stronger inventory source than guessing from rendered HTML and lets us
    compare exact requisition ids with the Workday/application records.
    """

    def fetch(self, company):
        response = session().get(
            ENDPOINT,
            timeout=timeout_seconds(),
            headers={
                "Accept": "application/json",
                "User-Agent": "Mozilla/5.0 PersonalJobFetcher/0.4",
                "Referer": DETAIL_BASE,
            },
        )
        response.raise_for_status()
        payload = response.json()
        rows = flatten_job_data(payload)
        if not rows:
            raise RuntimeError("cohesity_first_party_feed_returned_no_jobs")

        out = []
        seen = set()
        for row in rows:
            req_id = _clean(row.get("req_id"))
            opaque_id = _clean(row.get("JobID"))
            title = _clean(row.get("title"))
            if not title or not (req_id or opaque_id):
                continue
            key = req_id or opaque_id
            if key in seen:
                continue
            seen.add(key)

            query = urlencode({"gh_jid": opaque_id, "type": "wd"}) if opaque_id else None
            detail_url = f"{DETAIL_BASE}?{query}" if query else _clean(row.get("jobUrl"))
            location = _clean(row.get("primaryLocation"))
            additional = _clean(row.get("AdditionalLocations"))
            if additional and additional not in (location or ""):
                location = "; ".join(v for v in (location, additional) if v)

            raw = dict(row)
            raw["_first_party_endpoint"] = ENDPOINT
            out.append(Job(
                company["id"], company["name"], "cohesity_json",
                req_id or opaque_id,
                title,
                location,
                None,
                detail_url,
                None,
                raw,
            ))

        if len(out) != len(seen):
            raise RuntimeError("cohesity_first_party_feed_dedupe_invariant_failed")
        return out


def authoritative_count() -> tuple[int, str]:
    response = session().get(
        ENDPOINT,
        timeout=timeout_seconds(),
        headers={"Accept": "application/json", "User-Agent": "PersonalJobFetcherAudit/0.4"},
    )
    response.raise_for_status()
    rows = flatten_job_data(response.json())
    ids = {
        _clean(row.get("req_id")) or _clean(row.get("JobID"))
        for row in rows
        if _clean(row.get("req_id")) or _clean(row.get("JobID"))
    }
    return len(ids), "Cohesity first-party careers JSON returned the complete grouped job_data collection"
