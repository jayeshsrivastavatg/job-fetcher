from __future__ import annotations

import argparse
import json
import math
import re
import sys
from urllib.parse import urljoin, urlparse

from bs4 import BeautifulSoup

from job_fetcher.certification import audit_company
from job_fetcher.config import load_config
from job_fetcher.sources.http_client import session, timeout_seconds


KULA_JOB_RE = re.compile(r"^/(?P<tenant>[^/]+)/(?:jobs/)?(?P<id>\d+)(?:/(?:apply)?)?/?$", re.I)
TRAKSTAR_JOB_RE = re.compile(r"/jobs/(?P<id>[a-z0-9_-]+)(?:[/?#]|$)", re.I)
SUCCESSFACTORS_TOTAL_RE = re.compile(r"Results\s+\d+\s*[–—-]\s*\d+\s+of\s+(?P<total>\d+)", re.I)
NUTANIX_TOTAL_RE = re.compile(r"Displaying\s+\d+\s+to\s+\d+\s+of\s+(?P<total>\d+)\s+matching jobs", re.I)
NUTANIX_JOB_RE = re.compile(r"^/en/jobs/(?P<id>[^/]+)/", re.I)
SHIPROCKET_JOB_RE = re.compile(r"^/jobs/(?P<id>[^/]+)/?$", re.I)
SCALER_JOB_RE = re.compile(r"^/careers/(?P<id>[^/]+)/?$", re.I)
NYKAA_TOTAL_RE = re.compile(r"Showing\s+\d+\s+of\s+(?P<total>\d+)\s*-?\s*Jobs", re.I)
NYKAA_JOB_RE = re.compile(r"^/(?P<id>[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})/?$", re.I)
STATIC_DEBUG_IDS = {"shiprocket", "scaler", "nykaa"}


def _get_html(url: str) -> tuple[str, str]:
    response = session().get(
        url,
        timeout=timeout_seconds(),
        allow_redirects=True,
        headers={"User-Agent": "PersonalJobFetcher/0.1"},
    )
    response.raise_for_status()
    return response.text, response.url


def _ids_from_links(html: str, base_url: str, pattern: re.Pattern) -> set[str]:
    soup = BeautifulSoup(html, "html.parser")
    ids = set()
    for anchor in soup.select("a[href]"):
        absolute = urljoin(base_url, anchor.get("href") or "")
        match = pattern.match(urlparse(absolute).path)
        if match:
            ids.add(match.group("id").casefold())
    return ids


def _link_debug(html: str, base_url: str, pattern: re.Pattern) -> list[dict]:
    out = []
    seen = set()
    for anchor in BeautifulSoup(html, "html.parser").select("a[href]"):
        absolute = urljoin(base_url, anchor.get("href") or "")
        if not pattern.match(urlparse(absolute).path):
            continue
        if absolute in seen:
            continue
        seen.add(absolute)
        out.append({
            "url": absolute,
            "text": anchor.get_text(" ", strip=True),
            "class": anchor.get("class") or [],
            "parent": getattr(anchor.parent, "name", None),
            "parent_class": anchor.parent.get("class") if getattr(anchor.parent, "get", None) else [],
        })
    return out[:50]


def _kula_witness(company: dict) -> dict:
    src = company.get("source") or {}
    tenant = str(src.get("tenant") or "").strip()
    entry = str(src.get("entry_url") or company.get("career_url") or "").strip()
    if not tenant or not entry:
        return {"provider": "kula", "status": "unavailable", "expected_count": None}
    board_url = entry if "jobs=" in entry else f"{entry}{'&' if '?' in entry else '?'}jobs=true"
    html, final_url = _get_html(board_url)
    ids = set()
    for anchor in BeautifulSoup(html, "html.parser").select("a[href]"):
        absolute = urljoin(final_url, anchor.get("href") or "")
        match = KULA_JOB_RE.match(urlparse(absolute).path)
        if match and match.group("tenant").casefold() == tenant.casefold():
            ids.add(match.group("id"))
    return {"provider": "kula", "status": "verified" if ids else "empty", "expected_count": len(ids), "evidence": "public Kula stable-ID enumeration"}


def _trakstar_witness(company: dict) -> dict:
    entry = str((company.get("source") or {}).get("entry_url") or company.get("career_url") or "").strip()
    if not entry:
        return {"provider": "trakstar", "status": "unavailable", "expected_count": None}
    html, final_url = _get_html(entry)
    ids = set()
    for anchor in BeautifulSoup(html, "html.parser").select('a[href*="/jobs/"]'):
        match = TRAKSTAR_JOB_RE.search(urlparse(urljoin(final_url, anchor.get("href") or "")).path)
        if match:
            ids.add(match.group("id"))
    return {"provider": "trakstar", "status": "verified" if ids else "empty", "expected_count": len(ids), "evidence": "public Trakstar stable-ID enumeration"}


def _successfactors_witness(company: dict) -> dict:
    entry = str((company.get("source") or {}).get("entry_url") or company.get("career_url") or "").strip()
    if not entry:
        return {"provider": "successfactors", "status": "unavailable", "expected_count": None}
    html, _ = _get_html(entry)
    match = SUCCESSFACTORS_TOTAL_RE.search(BeautifulSoup(html, "html.parser").get_text(" ", strip=True))
    return {
        "provider": "successfactors",
        "status": "verified" if match else "unavailable",
        "expected_count": int(match.group("total")) if match else None,
        "evidence": "public SuccessFactors Results X-Y of N total" if match else "public total unavailable",
    }


def _shiprocket_witness() -> dict:
    html, final_url = _get_html("https://careers.shiprocket.in/")
    ids = _ids_from_links(html, final_url, SHIPROCKET_JOB_RE)
    return {
        "provider": "official_html", "status": "verified" if ids else "empty",
        "expected_count": len(ids), "evidence": "official Shiprocket /jobs/<slug>/ inventory",
        "links": _link_debug(html, final_url, SHIPROCKET_JOB_RE),
    }


def _scaler_witness() -> dict:
    html, final_url = _get_html("https://www.scaler.com/careers/")
    ids = _ids_from_links(html, final_url, SCALER_JOB_RE)
    ids.discard("careers")
    return {
        "provider": "official_html", "status": "verified" if ids else "empty",
        "expected_count": len(ids), "evidence": "official Scaler /careers/<job-slug> inventory",
        "links": _link_debug(html, final_url, SCALER_JOB_RE),
    }


def _nykaa_witness() -> dict:
    html, final_url = _get_html("https://careers.nykaa.com/")
    text = BeautifulSoup(html, "html.parser").get_text(" ", strip=True)
    total_match = NYKAA_TOTAL_RE.search(text)
    ids = _ids_from_links(html, final_url, NYKAA_JOB_RE)
    total = int(total_match.group("total")) if total_match else None
    return {
        "provider": "official_html",
        "status": "verified" if isinstance(total, int) else "unavailable",
        "expected_count": total,
        "first_page_ids": len(ids),
        "evidence": "official Nykaa displayed Showing X of N Jobs total",
        "links": _link_debug(html, final_url, NYKAA_JOB_RE),
    }


def _nutanix_witness() -> dict:
    base = "https://careers.nutanix.com/en/jobs/"
    html, final_url = _get_html(base)
    text = BeautifulSoup(html, "html.parser").get_text(" ", strip=True)
    match = NUTANIX_TOTAL_RE.search(text)
    if not match:
        return {"provider": "official_html", "status": "unavailable", "expected_count": None, "evidence": "official displayed total unavailable"}
    total = int(match.group("total"))
    ids = _ids_from_links(html, final_url, NUTANIX_JOB_RE)
    for page in range(2, max(1, math.ceil(total / 20)) + 1):
        page_html, page_url = _get_html(f"{base}?page={page}")
        ids.update(_ids_from_links(page_html, page_url, NUTANIX_JOB_RE))
    return {
        "provider": "official_html", "status": "verified" if len(ids) == total else "witness_partial",
        "expected_count": total, "enumerated_count": len(ids),
        "evidence": "official Nutanix displayed total plus numbered server-rendered pages",
    }


def _extra_witness(company: dict, row: dict) -> dict | None:
    company_id = str(company.get("id") or "")
    if company_id == "shiprocket":
        return _shiprocket_witness()
    if company_id == "scaler":
        return _scaler_witness()
    if company_id == "nykaa":
        return _nykaa_witness()
    if company_id == "nutanix":
        return _nutanix_witness()

    source = company.get("source") or {}
    source_type = str(source.get("type") or "").casefold()
    source_types = {str(x).casefold() for x in row.get("source_types") or []}
    if source_type == "kula" or "kula" in source_types:
        return _kula_witness(company)
    if source_type == "trakstar" or "trakstar" in source_types:
        return _trakstar_witness(company)
    if source_type == "successfactors" or "successfactors" in source_types:
        return _successfactors_witness(company)
    return None


def _exact_from_extra_witness(row: dict, witness: dict | None) -> bool:
    if not witness or witness.get("status") != "verified":
        return False
    expected = witness.get("expected_count")
    return (
        isinstance(expected, int)
        and int(row.get("jobs_found") or 0) == expected
        and int(row.get("rejected_non_job_records") or 0) == 0
        and row.get("valid_url_ratio") in {None, 1.0}
        and row.get("stable_id_ratio") in {None, 1.0}
    )


def main() -> int:
    parser = argparse.ArgumentParser(description="Fast live validation for breadth-first source promotions")
    parser.add_argument("--company", required=True)
    args = parser.parse_args()

    companies = {str(c.get("id") or ""): c for c in load_config().get("companies", [])}
    if args.company not in companies:
        raise SystemExit(f"unknown company: {args.company}")
    company = companies[args.company]
    row = audit_company(company, sample_size=0, detail_timeout=5.0)
    try:
        witness = _extra_witness(company, row)
        witness_error = None
    except Exception as exc:
        witness = None
        witness_error = f"{type(exc).__name__}: {exc}"

    payload = {
        "id": row.get("id"), "name": row.get("name"), "verdict": row.get("verdict"),
        "adapter": row.get("adapter"), "jobs_found": row.get("jobs_found"),
        "expected_count": row.get("expected_count"), "completeness_pct": row.get("completeness_pct"),
        "rejected_non_job_records": row.get("rejected_non_job_records"),
        "valid_url_ratio": row.get("valid_url_ratio"), "stable_id_ratio": row.get("stable_id_ratio"),
        "description_ratio": row.get("description_ratio"), "location_ratio": row.get("location_ratio"),
        "source_types": row.get("source_types"), "count_probe": row.get("count_probe"),
        "breadth_witness": witness, "breadth_witness_error": witness_error,
        "failure_category": row.get("failure_category"), "error": row.get("error"),
    }
    if args.company in STATIC_DEBUG_IDS:
        payload["production_jobs"] = [
            {"id": job.get("external_id"), "title": job.get("title"), "location": job.get("location"), "url": job.get("job_url")}
            for job in (row.get("jobs") or [])
        ]
    print(json.dumps(payload, indent=2, ensure_ascii=False))
    return 0 if row.get("verdict") == "CERTIFIED" or _exact_from_extra_witness(row, witness) else 1


if __name__ == "__main__":
    sys.exit(main())
