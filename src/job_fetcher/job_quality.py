from __future__ import annotations

from urllib.parse import urlparse

from job_fetcher.sources.generic_extract import dedupe


def valid_http_url(value) -> bool:
    if not value:
        return False
    try:
        parsed = urlparse(str(value))
        return parsed.scheme in {"http", "https"} and bool(parsed.netloc)
    except Exception:
        return False


def prefer_usable_jobs(jobs):
    """Return the usable portion of a provider result without inventing jobs.

    Generic browser/embedded-JSON extraction can legitimately discover the same
    requisition several ways. Some of those fragments contain a title/id but no
    clickable URL, which made otherwise healthy sources look low-quality and also
    polluted the persistent job inventory. Repair the common case where an HTTP
    external_id is itself the URL, then prefer records that have both a title and
    a real HTTP(S) detail/apply URL.

    If a source returns *only* incomplete records, preserve them rather than
    silently converting the provider result into zero jobs; health verification
    can then continue to flag that source as suspicious for investigation.
    """
    rows = list(jobs or [])
    if not rows:
        return []

    usable = []
    for job in rows:
        title = str(getattr(job, "title", "") or "").strip()
        if title:
            job.title = title

        if not valid_http_url(getattr(job, "job_url", None)) and valid_http_url(getattr(job, "external_id", None)):
            job.job_url = str(job.external_id)
            raw = dict(job.raw or {}) if isinstance(getattr(job, "raw", None), dict) else {}
            raw["_quality_repaired_job_url"] = True
            job.raw = raw

        if title and valid_http_url(getattr(job, "job_url", None)):
            usable.append(job)

    return dedupe(usable if usable else rows)
