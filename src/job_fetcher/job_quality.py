from __future__ import annotations

from urllib.parse import urlparse


def valid_http_url(value) -> bool:
    if not value:
        return False
    try:
        parsed = urlparse(str(value))
        return parsed.scheme in {"http", "https"} and bool(parsed.netloc)
    except Exception:
        return False


def _title_location(job):
    title = str(getattr(job, "title", "") or "").strip().casefold()
    location = str(getattr(job, "location", "") or "").strip().casefold()
    return (title, location) if title and location else None


def _dedupe_preserving_ids(jobs):
    out = []
    seen = set()
    for job in jobs:
        url = str(getattr(job, "job_url", "") or "").strip()
        eid = str(getattr(job, "external_id", "") or "").strip()
        if valid_http_url(url):
            key = ("url", url)
        elif eid:
            key = ("id", str(getattr(job, "company_id", "") or ""), eid)
        else:
            key = ("fallback", _title_location(job))
        if key in seen:
            continue
        seen.add(key)
        out.append(job)
    return out


def prefer_usable_jobs(jobs):
    """Repair/drop duplicate extraction fragments without hiding unique bad rows.

    Browser and embedded-JSON extraction can discover the same requisition several
    ways. A complete record may contain title/location/URL while a second fragment
    contains the same requisition with no URL. We repair URL-shaped external IDs
    and discard only incomplete rows that can be matched to an already usable row.

    Unique incomplete records are deliberately preserved. That means source health
    remains Suspicious until the adapter can produce a real URL instead of turning
    the row green by silently throwing potentially real vacancies away.
    """
    rows = list(jobs or [])
    if not rows:
        return []

    for job in rows:
        title = str(getattr(job, "title", "") or "").strip()
        if title:
            job.title = title
        if not valid_http_url(getattr(job, "job_url", None)) and valid_http_url(getattr(job, "external_id", None)):
            job.job_url = str(job.external_id)
            raw = dict(job.raw or {}) if isinstance(getattr(job, "raw", None), dict) else {}
            raw["_quality_repaired_job_url"] = True
            job.raw = raw

    usable = [
        job for job in rows
        if str(getattr(job, "title", "") or "").strip() and valid_http_url(getattr(job, "job_url", None))
    ]
    if not usable:
        return _dedupe_preserving_ids(rows)

    usable_ids = {
        str(getattr(job, "external_id", "") or "").strip()
        for job in usable
        if str(getattr(job, "external_id", "") or "").strip()
    }
    usable_title_locations = {key for job in usable if (key := _title_location(job)) is not None}

    kept = []
    for job in rows:
        if job in usable:
            kept.append(job)
            continue
        eid = str(getattr(job, "external_id", "") or "").strip()
        title_location = _title_location(job)
        if (eid and eid in usable_ids) or (title_location and title_location in usable_title_locations):
            continue
        kept.append(job)

    return _dedupe_preserving_ids(kept)
