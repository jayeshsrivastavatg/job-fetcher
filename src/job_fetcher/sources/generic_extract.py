import json
import re
from urllib.parse import urljoin, urlparse
from bs4 import BeautifulSoup
from job_fetcher.models import Job

GENERIC_LABELS = {
    "jobs", "careers", "open roles", "open positions", "view jobs", "view all jobs",
    "see jobs", "search jobs", "explore jobs", "apply", "apply now", "learn more",
}
JOB_URL_RE = re.compile(r"/(job|jobs|jobdetail|career|careers|position|positions|opening|openings|requisition|vacanc(?:y|ies))([/?#]|$)", re.I)
TITLE_HINT_RE = re.compile(r"(engineer|engineering|developer|manager|analyst|architect|scientist|designer|consultant|specialist|director|lead|intern|associate|member|technical|product|sales|marketing|finance|legal|recruiter|operations|support|security|qa|sdet|devops|data|software)", re.I)


def clean_text(value):
    if value is None:
        return None
    if isinstance(value, list):
        value = ", ".join(str(x) for x in value if x is not None)
    return re.sub(r"\s+", " ", str(value)).strip() or None


def location_text(value):
    if not value:
        return None
    if isinstance(value, str):
        return clean_text(value)
    if isinstance(value, list):
        return ", ".join(filter(None, (location_text(x) for x in value))) or None
    if isinstance(value, dict):
        parts = [value.get("addressLocality"), value.get("addressRegion"), value.get("addressCountry")]
        addr = value.get("address")
        if isinstance(addr, dict):
            parts = [addr.get("addressLocality"), addr.get("addressRegion"), addr.get("addressCountry")]
        return ", ".join(str(x) for x in parts if x) or None
    return clean_text(value)


def extract_jsonld(company, html, base_url, source_type="jsonld"):
    soup = BeautifulSoup(html, "html.parser")
    jobs = []
    for node in soup.select('script[type="application/ld+json"]'):
        try:
            data = json.loads(node.string or node.get_text() or "null")
        except Exception:
            continue
        for obj in walk_objects(data):
            typ = obj.get("@type")
            types = typ if isinstance(typ, list) else [typ]
            if "JobPosting" not in types:
                continue
            title = clean_text(obj.get("title") or obj.get("name")) or ""
            if not title:
                continue
            identifier = obj.get("identifier")
            if isinstance(identifier, dict):
                identifier = identifier.get("value") or identifier.get("name")
            job_url = obj.get("url") or obj.get("sameAs")
            if job_url:
                job_url = urljoin(base_url, str(job_url))
            desc = obj.get("description")
            if desc:
                desc = BeautifulSoup(str(desc), "html.parser").get_text(" ", strip=True)
            loc = location_text(obj.get("jobLocation")) or location_text(obj.get("applicantLocationRequirements"))
            jobs.append(Job(
                company["id"], company["name"], source_type,
                clean_text(identifier) or job_url,
                title, loc, clean_text(desc), job_url,
                clean_text(obj.get("datePosted")), obj,
            ))
    return dedupe(jobs)


def walk_objects(value):
    if isinstance(value, dict):
        yield value
        for v in value.values():
            yield from walk_objects(v)
    elif isinstance(value, list):
        for v in value:
            yield from walk_objects(v)


def extract_embedded_json(company, html, base_url, source_type="embedded_json"):
    """Extract job-like records from JSON state embedded in script tags.

    Modern React/Next/Apollo sites often ship job data in __NEXT_DATA__, application/json,
    or a raw JSON assignment before client-side rendering. This fallback intentionally
    ignores arbitrary JavaScript and only parses script bodies that are valid JSON.
    """
    soup = BeautifulSoup(html, "html.parser")
    jobs = []
    for node in soup.find_all("script"):
        typ = (node.get("type") or "").lower()
        ident = (node.get("id") or "").lower()
        if typ not in {"application/json", "application/ld+json"} and ident not in {"__next_data__", "__nuxt_data__"}:
            continue
        raw = node.string or node.get_text() or ""
        if not raw.strip() or len(raw) > 12_000_000:
            continue
        try:
            payload = json.loads(raw)
        except Exception:
            continue
        jobs.extend(extract_jobs_from_json(company, payload, base_url, source_type))
    return dedupe(jobs)


def extract_html_links(company, html, base_url, source_type="generic_html"):
    soup = BeautifulSoup(html, "html.parser")
    out = []
    seen = set()
    base_host = urlparse(base_url).netloc.lower()
    for a in soup.select("a[href]"):
        anchor_text = clean_text(a.get_text(" ", strip=True)) or ""
        href = a.get("href")
        if not href or href.startswith(("mailto:", "tel:", "javascript:")):
            continue
        url = urljoin(base_url, href)
        if url in seen:
            continue

        # Some ATS/cards use a generic "Apply now" anchor and place the actual
        # job title in a sibling heading. Walk a few ancestors and infer that title.
        candidate_texts = [anchor_text]
        parent = a.parent
        for _ in range(3):
            if parent is None:
                break
            for selector in ("h1", "h2", "h3", "h4", "h5", "[class*='title']", "[class*='job']"):
                node = parent.select_one(selector) if hasattr(parent, "select_one") else None
                if node:
                    value = clean_text(node.get_text(" ", strip=True))
                    if value:
                        candidate_texts.append(value)
            parent = parent.parent
        title = next((x for x in candidate_texts if TITLE_HINT_RE.search(x or "") and 3 < len(x) <= 180), anchor_text)

        low = anchor_text.lower().strip(" :–—-")
        url_match = bool(JOB_URL_RE.search(urlparse(url).path))
        title_match = bool(TITLE_HINT_RE.search(title))
        if low in GENERIC_LABELS and not title_match and not ("/job" in url.lower() or "/position" in url.lower() or "/requisition" in url.lower()):
            continue
        if not url_match and not title_match:
            continue
        if len(title) < 4 or len(title) > 180:
            continue
        path = urlparse(url).path.rstrip("/").lower()
        if path in {"/jobs", "/careers", "/career", "/openings", "/positions"} and not title_match:
            continue
        seen.add(url)
        parent_text = clean_text(a.parent.get_text(" ", strip=True)) if a.parent else None
        loc = infer_location(parent_text, title)
        out.append(Job(company["id"], company["name"], source_type, url, title, loc, None, url, None,
                       {"anchor_text": anchor_text, "base_host": base_host}))
    return dedupe(out)


def infer_location(parent_text, title):
    if not parent_text or parent_text == title:
        return None
    rest = parent_text.replace(title, " ").strip(" |-–—")
    if len(rest) > 120:
        return None
    # Location-ish text often contains commas / remote / known employment modality.
    if re.search(r"\b(remote|hybrid|onsite|on-site|india|bengaluru|bangalore|gurugram|gurgaon|pune|hyderabad|mumbai|chennai|noida|delhi)\b", rest, re.I):
        return clean_text(rest)
    return None


def _canonical_json_job_url(base_url, eid):
    if not eid:
        return None
    parsed = urlparse(base_url)
    host = parsed.netloc.lower()
    origin = f"{parsed.scheme or 'https'}://{parsed.netloc}"
    value = str(eid)

    # Oracle public Candidate Experience pages often return Title/Id/location in
    # XHR JSON but omit a URL from each requisition row.
    if host == "careers.oracle.com":
        m = re.search(r"/(?P<locale>[^/]+)/sites/(?P<site>[^/]+)(?:/|$)", parsed.path, re.I)
        locale = m.group("locale") if m else "en"
        site = m.group("site") if m else "jobsearch"
        return f"{origin}/{locale}/sites/{site}/job/{value}"

    if "oraclecloud.com" in host:
        m = re.search(
            r"/hcmUI/CandidateExperience/(?P<locale>[^/]+)/sites/(?P<site>[^/]+)(?:/|$)",
            parsed.path,
            re.I,
        )
        if m:
            return f"{origin}/hcmUI/CandidateExperience/{m.group('locale')}/sites/{m.group('site')}/job/{value}"

    if host.endswith(".eightfold.ai"):
        return f"{origin}/careers/job/{value}"

    # Swiggy's current SPA uses a requisition id in the URL fragment rather than
    # ordinary <a href> detail links.
    if host == "careers.swiggy.com":
        return f"{origin}/#/careers?reqid={value}"
    return None


def extract_jobs_from_json(company, payload, base_url, source_type="browser_json"):
    records = []
    for obj in walk_objects(payload):
        title = first(obj, "title", "Title", "jobTitle", "job_title", "name", "positionTitle")
        if not isinstance(title, str) or not TITLE_HINT_RE.search(title):
            continue
        # Require at least one extra job-ish field to reduce false positives.
        if not any(k in obj for k in (
            "location", "locations", "PrimaryLocation", "locationCountry", "jobUrl", "job_url", "url",
            "id", "Id", "jobId", "requisitionId", "positionId", "reqId", "externalPath",
            "description", "ShortDescriptionStr",
        )):
            continue
        raw_url = first(obj, "jobUrl", "job_url", "url", "absolute_url", "hostedUrl", "externalPath", "JobUrl")
        eid = first(
            obj, "id", "Id", "_id", "jobId", "jobID", "job_id", "requisitionId", "externalId", "ref",
            "positionId", "positionDisplayId", "atsJobId", "reqId",
        )
        job_url = urljoin(base_url, str(raw_url)) if raw_url else _canonical_json_job_url(base_url, eid)
        loc = first(
            obj, "location", "locations", "locationName", "locationsText", "city", "PrimaryLocation",
            "locationCountry",
        )
        desc = first(
            obj, "description", "descriptionPlain", "jobDescription", "content", "ShortDescriptionStr",
            "ExternalResponsibilitiesStr",
        )
        if isinstance(desc, str) and "<" in desc:
            desc = BeautifulSoup(desc, "html.parser").get_text(" ", strip=True)
        posted = first(obj, "postedAt", "posted_at", "datePosted", "publishedAt", "postedOn", "PostedDate")
        records.append(Job(company["id"], company["name"], source_type,
                           clean_text(eid) or job_url, clean_text(title) or "",
                           location_text(loc), clean_text(desc), job_url, clean_text(posted), obj))
    return dedupe(records)


def first(obj, *keys):
    if not isinstance(obj, dict):
        return None
    for k in keys:
        if obj.get(k) not in (None, "", []):
            return obj.get(k)
    return None


def dedupe(jobs):
    out, seen = [], set()
    for j in jobs:
        key = (j.job_url or "", j.title.lower(), (j.location or "").lower())
        if key in seen:
            continue
        seen.add(key)
        out.append(j)
    return out
