from __future__ import annotations

import re
from urllib.parse import parse_qs, urlparse


_ROLE_TITLE_RE = re.compile(
    r"\b(?:engineer|developer|manager|analyst|architect|scientist|designer|consultant|"
    r"specialist|director|lead|intern|associate|recruiter|administrator|coordinator|"
    r"executive|officer|principal|staff|head|counsel|attorney|accountant|controller|"
    r"researcher|programmer|technologist|partner|representative|sre|sdet|devops|qa)\b",
    re.I,
)

# These provider adapters consume actual vacancy/posting feeds rather than careers
# page navigation. A short title such as "Security", "Support" or "Finance" can be
# a real published requisition and must not be rejected by HTML-navigation
# heuristics. Keep this list deliberately narrow: only sources whose adapter reads
# an authoritative structured job collection belong here.
_AUTHORITATIVE_JOB_FEEDS = {
    "greenhouse",
    "lever",
    "ashby",
    "workday",
    "smartrecruiters",
    "amazon_json",
    "cohesity_json",
}

# These are common careers-site navigation/category labels that the old generic
# extractor incorrectly stored as jobs simply because their URL contained
# /careers/ or because the label contained a broad word such as "data", "sales"
# or "support".
_ACTION_ONLY = {
    "apply", "apply now", "learn more", "read more", "see details", "view details",
    "view role", "view job", "view jobs", "view all jobs", "browse jobs", "search jobs",
    "see jobs", "explore jobs", "explore opportunities", "see open positions",
}
_NAVIGATION_EXACT = {
    "jobs", "careers", "open roles", "open positions", "all jobs", "all teams",
    "engineering", "sales", "support", "customer support", "products", "product",
    "developers", "development", "marketing", "finance", "legal", "security",
    "operations", "design", "data", "software", "ai and machine learning",
    "candidate resources", "candidate resources hub", "career growth", "benefits and perks",
    "awards", "talent community", "life at navi", "jobs at navi", "teams at navi",
    "values at navi", "explore more", "contact sales", "contact support",
}
_NAVIGATION_RE = re.compile(
    r"(?:\b(?:privacy|cookie|terms|policy|careers?)\b|\b(?:benefits?|perks?|awards?|resources?|"
    r"teams?|culture|values?|products?|solutions?|developers?)\b|\blife\s+at\b|"
    r"\bjobs?\s+at\b|\bteams?\s+at\b|\bvalues?\s+at\b|\bcandidate\s+resources?\b|"
    r"\bcareer\s+growth\b|\bask questions,?\s*report bugs\b)",
    re.I,
)
_HTMLISH_SOURCE_RE = re.compile(r"(?:^|_)(?:generic|browser|recovery_browser|official)_?html$|_html$", re.I)
_JOB_QUERY_KEYS = {
    "jobid", "job_id", "jid", "gh_jid", "reqid", "req_id", "requisitionid",
    "requisition_id", "positionid", "position_id", "postingid", "posting_id",
}


def valid_http_url(value) -> bool:
    if not value:
        return False
    try:
        parsed = urlparse(str(value))
        return parsed.scheme in {"http", "https"} and bool(parsed.netloc)
    except Exception:
        return False


def role_title_like(value) -> bool:
    title = str(value or "").strip()
    return bool(title and _ROLE_TITLE_RE.search(title))


def strong_job_detail_url(value) -> bool:
    """Return True only for a URL that looks like one concrete vacancy.

    Bare /careers, /jobs and /openings pages are listing/navigation pages. A
    concrete path segment after job/jobs/requisition/etc or an explicit requisition
    query parameter is much stronger evidence that the link is an actual vacancy.
    """
    if not valid_http_url(value):
        return False
    parsed = urlparse(str(value))
    path = re.sub(r"/+", "/", parsed.path or "/").rstrip("/").lower()
    segments = [x for x in path.split("/") if x]
    detail_markers = {"job", "jobs", "jobdetail", "requisition", "requisitions", "position", "positions", "opening", "openings", "vacancy", "vacancies", "details"}
    generic_tail = {"search", "search-results", "all", "open", "list", "listing", "categories", "category"}

    for index, segment in enumerate(segments):
        if segment not in detail_markers or index >= len(segments) - 1:
            continue
        tail = segments[index + 1]
        if tail and tail not in generic_tail:
            return True

    # Common branded forms that do not use a plain /jobs/<id> route.
    if re.search(r"/careers?/details?/[^/]+$", path, re.I):
        return True
    if re.search(r"/careerhub/explore/jobs/[^/]+$", path, re.I):
        return True

    query = {k.lower() for k in parse_qs(parsed.query or "", keep_blank_values=True)}
    if query & _JOB_QUERY_KEYS:
        return True
    fragment_query = {k.lower() for k in parse_qs(parsed.fragment or "", keep_blank_values=True)}
    return bool(fragment_query & _JOB_QUERY_KEYS)


def _looks_like_navigation_title(title: str) -> bool:
    low = re.sub(r"\s+", " ", title.strip().casefold()).strip(" :–—-")
    if not low:
        return True
    if low in _ACTION_ONLY or low in _NAVIGATION_EXACT:
        return True
    if low.startswith(("contact ", "explore ", "browse ", "discover ", "view all ", "learn ")):
        return True
    # A true role title such as "Privacy Engineer" or "Support Engineer" should
    # survive even though it contains a word that also appears in site navigation.
    return not role_title_like(title) and bool(_NAVIGATION_RE.search(title))


def plausible_job(job) -> bool:
    title = str(getattr(job, "title", "") or "").strip()
    if not title:
        return False

    source_type = str(getattr(job, "source_type", "") or "").strip().casefold()
    if source_type in _AUTHORITATIVE_JOB_FEEDS:
        # These adapters already crossed the important trust boundary: their input
        # collection consists of published vacancies. Navigation-label heuristics
        # are for HTML/link discovery and would incorrectly delete legitimate
        # unusually named requisitions from an authoritative feed.
        return True

    url = str(getattr(job, "job_url", "") or "").strip()
    if title.casefold().strip(" :–—-") in _ACTION_ONLY:
        return False

    # A concrete vacancy URL is strong enough to keep even a short/unusual title.
    if strong_job_detail_url(url):
        return True

    if _looks_like_navigation_title(title):
        return False

    if _HTMLISH_SOURCE_RE.search(source_type):
        # Generic HTML extraction is the dangerous case: require an actual role
        # noun when the URL itself is not a concrete vacancy URL.
        return role_title_like(title)

    # Other structured/API records are trusted unless they matched an explicit
    # navigation/action rule above.
    return True


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
    """Remove obvious non-job navigation, repair URLs and dedupe fragments.

    The previous quality pass only asked whether a record had a title and a valid
    URL. That allowed menu/footer links such as "Products", "Support", "All Teams"
    and "Google Data Policy" to look perfectly healthy. We now reject only
    high-confidence navigation false positives first, then preserve the earlier
    conservative behavior for genuinely unique incomplete vacancies.
    """
    rows = [job for job in list(jobs or []) if plausible_job(job)]
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
