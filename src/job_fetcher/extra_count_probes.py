from __future__ import annotations

import re
from urllib.parse import urljoin, urlparse

from bs4 import BeautifulSoup

from job_fetcher.sources.http_client import session, timeout_seconds


_KULA_JOB_RE = re.compile(r"^/(?P<tenant>[^/]+)/(?:jobs/)?(?P<id>\d+)(?:/(?:apply)?)?/?$", re.I)
_TRAKSTAR_JOB_RE = re.compile(r"/jobs/(?P<id>[a-z0-9_-]+)(?:[/?#]|$)", re.I)
_SUCCESSFACTORS_TOTAL_RE = re.compile(r"Results\s+\d+\s*[–—-]\s*\d+\s+of\s+(?P<total>\d+)", re.I)


def _html(url: str) -> tuple[str, str]:
    response = session().get(
        url,
        timeout=timeout_seconds(),
        allow_redirects=True,
        headers={"User-Agent": "PersonalJobFetcher/0.1"},
    )
    response.raise_for_status()
    return response.text, response.url


def extra_provider_count(company: dict):
    """Return (provider, count, evidence) for simple public exhaustive boards."""
    source = company.get("source") or {}
    source_type = str(source.get("type") or "").casefold()

    if source_type == "kula":
        tenant = str(source.get("tenant") or "").strip()
        entry = str(source.get("entry_url") or company.get("career_url") or "").strip()
        if not tenant or not entry:
            return None
        board = entry if "jobs=" in entry else f"{entry}{'&' if '?' in entry else '?'}jobs=true"
        body, final_url = _html(board)
        ids = set()
        for anchor in BeautifulSoup(body, "html.parser").select("a[href]"):
            absolute = urljoin(final_url, anchor.get("href") or "")
            match = _KULA_JOB_RE.match(urlparse(absolute).path)
            if match and match.group("tenant").casefold() == tenant.casefold():
                ids.add(match.group("id"))
        if ids:
            return "kula", len(ids), "Kula public board stable-ID enumeration"
        return None

    if source_type == "trakstar":
        entry = str(source.get("entry_url") or company.get("career_url") or "").strip()
        if not entry:
            return None
        body, final_url = _html(entry)
        ids = set()
        for anchor in BeautifulSoup(body, "html.parser").select('a[href*="/jobs/"]'):
            absolute = urljoin(final_url, anchor.get("href") or "")
            match = _TRAKSTAR_JOB_RE.search(urlparse(absolute).path)
            if match:
                ids.add(match.group("id"))
        if ids:
            return "trakstar", len(ids), "Trakstar public board stable-ID enumeration"
        return None

    if source_type == "successfactors":
        entry = str(source.get("entry_url") or company.get("career_url") or "").strip()
        if not entry:
            return None
        body, _ = _html(entry)
        text = BeautifulSoup(body, "html.parser").get_text(" ", strip=True)
        match = _SUCCESSFACTORS_TOTAL_RE.search(text)
        if match:
            return "successfactors", int(match.group("total")), "SuccessFactors public Results X-Y of N total"
        return None

    if source_type == "zohorecruit":
        from job_fetcher.sources.zohorecruit import ZohoRecruitSource
        entry = str(source.get("entry_url") or company.get("career_url") or "").strip()
        if not entry:
            return None
        body, _ = _html(entry)
        rows = ZohoRecruitSource.parse_openings(body)
        ids = {
            str(row.get("id")).strip()
            for row in rows
            if isinstance(row, dict)
            and row.get("id") is not None
            and row.get("Is_Locked") is not True
            and row.get("Publish") is not False
        }
        ids.discard("")
        if ids:
            return "zohorecruit", len(ids), "Zoho Recruit first-party embedded openings array"
        return None

    if source_type == "custom_html" and source.get("job_path_regex"):
        entry = str(source.get("list_url") or company.get("career_url") or "").strip()
        body, final_url = _html(entry)
        path_re = re.compile(str(source["job_path_regex"]), re.I)
        ids = set()
        for anchor in BeautifulSoup(body, "html.parser").select("a[href]"):
            absolute = urljoin(final_url, anchor.get("href") or "")
            match = path_re.match(urlparse(absolute).path)
            if match:
                ids.add((match.groupdict().get("id") if match.groupdict() else None) or match.group(0))
        if ids:
            return "official_links", len(ids), "first-party careers page vacancy-link enumeration"
        return None

    return None
