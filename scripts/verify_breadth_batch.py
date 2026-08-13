from __future__ import annotations

import argparse
import json
import re
import sys
from urllib.parse import urljoin, urlparse

from bs4 import BeautifulSoup

from job_fetcher.certification import audit_company
from job_fetcher.config import load_config
from job_fetcher.sources.http_client import session, timeout_seconds


DEFAULT_COMPANIES = [
    "elastic",
    "druva",
    "thoughtworks",
    "zomato_blinkit",
    "dynatrace",
    "intuit",
    "meesho",
    "zeta",
    "slice",
    "cashfree",
    "clevertap",
    "home_depot_tech",
    "wells_fargo",
    "mastercard",
    "fidelity",
    "siemens_healthineers",
    "payu",
    "dream11",
    "chargebee",
    "postman",
    "razorpay",
    "inmobi",
    "hackerrank",
    "freshworks",
    "arista_networks",
    "nagarro",
    "mindtickle",
    "broadcom_vmware",
    "visa",
    "browserstack",
    "cisco",
    "barclays",
    "hpe",
    "sprinklr",
    "uber",
    "citi",
    "dell",
]

KULA_JOB_RE = re.compile(r"^/(?P<tenant>[^/]+)/(?:jobs/)?(?P<id>\d+)(?:/(?:apply)?)?/?$", re.I)
TRAKSTAR_JOB_RE = re.compile(r"/jobs/(?P<id>[a-z0-9_-]+)(?:[/?#]|$)", re.I)
SUCCESSFACTORS_TOTAL_RE = re.compile(r"Results\s+\d+\s*[–—-]\s*\d+\s+of\s+(?P<total>\d+)", re.I)
ORACLE_PUBLIC_TOTAL_RE = re.compile(r"\b(?P<total>\d[\d,]*)\s+Open Jobs\b", re.I)


def _get_html(url: str) -> tuple[str, str]:
    response = session().get(
        url,
        timeout=timeout_seconds(),
        allow_redirects=True,
        headers={"User-Agent": "PersonalJobFetcher/0.1"},
    )
    response.raise_for_status()
    return response.text, response.url


def _kula_witness(company: dict) -> dict:
    src = company.get("source") or {}
    tenant = str(src.get("tenant") or "").strip()
    entry = str(src.get("entry_url") or company.get("career_url") or "").strip()
    if not tenant or not entry:
        return {"provider": "kula", "status": "unavailable", "expected_count": None}
    separator = "&" if "?" in entry else "?"
    board_url = entry if "jobs=" in entry else f"{entry}{separator}jobs=true"
    html, final_url = _get_html(board_url)
    soup = BeautifulSoup(html, "html.parser")
    ids = set()
    for anchor in soup.select("a[href]"):
        absolute = urljoin(final_url, anchor.get("href") or "")
        match = KULA_JOB_RE.match(urlparse(absolute).path)
        if match and match.group("tenant").casefold() == tenant.casefold():
            ids.add(match.group("id"))
    return {
        "provider": "kula",
        "status": "verified" if ids else "empty",
        "expected_count": len(ids),
        "evidence": "independent public Kula board stable-ID enumeration",
    }


def _trakstar_witness(company: dict) -> dict:
    src = company.get("source") or {}
    entry = str(src.get("entry_url") or company.get("career_url") or "").strip()
    if not entry:
        return {"provider": "trakstar", "status": "unavailable", "expected_count": None}
    html, final_url = _get_html(entry)
    soup = BeautifulSoup(html, "html.parser")
    ids = set()
    for anchor in soup.select('a[href*="/jobs/"]'):
        absolute = urljoin(final_url, anchor.get("href") or "")
        match = TRAKSTAR_JOB_RE.search(urlparse(absolute).path)
        if match:
            ids.add(match.group("id"))
    return {
        "provider": "trakstar",
        "status": "verified" if ids else "empty",
        "expected_count": len(ids),
        "evidence": "independent public Trakstar board stable-ID enumeration",
    }


def _successfactors_witness(company: dict) -> dict:
    src = company.get("source") or {}
    entry = str(src.get("entry_url") or company.get("career_url") or "").strip()
    if not entry:
        return {"provider": "successfactors", "status": "unavailable", "expected_count": None}
    html, _ = _get_html(entry)
    text = BeautifulSoup(html, "html.parser").get_text(" ", strip=True)
    match = SUCCESSFACTORS_TOTAL_RE.search(text)
    if not match:
        return {
            "provider": "successfactors",
            "status": "unavailable",
            "expected_count": None,
            "evidence": "public listing did not expose a Results X-Y of N total",
        }
    return {
        "provider": "successfactors",
        "status": "verified",
        "expected_count": int(match.group("total")),
        "evidence": "independent public SuccessFactors Results X-Y of N total",
    }


def _oracle_public_witness(company: dict) -> dict:
    src = company.get("source") or {}
    entry = str(src.get("entry_url") or company.get("career_url") or "").strip()
    if not entry:
        return {"provider": "oracle_public", "status": "unavailable", "expected_count": None}
    html, _ = _get_html(entry)
    text = BeautifulSoup(html, "html.parser").get_text(" ", strip=True)
    match = ORACLE_PUBLIC_TOTAL_RE.search(text)
    if not match:
        return {
            "provider": "oracle_public",
            "status": "unavailable",
            "expected_count": None,
            "evidence": "public Oracle career page did not expose an Open Jobs total",
        }
    return {
        "provider": "oracle_public",
        "status": "verified",
        "expected_count": int(match.group("total").replace(",", "")),
        "evidence": "independent public Oracle career-page Open Jobs total",
    }


def _extra_witness(company: dict, row: dict) -> dict | None:
    source = company.get("source") or {}
    source_type = str(source.get("type") or "").casefold()
    source_types = {str(x).casefold() for x in row.get("source_types") or []}
    # build_source() may promote an `auto` company in-place while audit_company runs.
    if source_type == "kula" or "kula" in source_types:
        return _kula_witness(company)
    if source_type == "trakstar" or "trakstar" in source_types:
        return _trakstar_witness(company)
    if source_type == "successfactors" or "successfactors" in source_types:
        return _successfactors_witness(company)
    if source_type == "oracle" and source.get("mode") == "public_search":
        return _oracle_public_witness(company)
    return None


def _exact_from_extra_witness(row: dict, witness: dict | None) -> bool:
    if not witness or witness.get("status") != "verified":
        return False
    expected = witness.get("expected_count")
    if not isinstance(expected, int):
        return False
    return (
        int(row.get("jobs_found") or 0) == expected
        and int(row.get("rejected_non_job_records") or 0) == 0
        and (row.get("valid_url_ratio") in {None, 1.0})
        and (row.get("stable_id_ratio") in {None, 1.0})
    )


def main() -> int:
    parser = argparse.ArgumentParser(description="Fast live validation for breadth-first ATS promotions")
    parser.add_argument("--company", choices=DEFAULT_COMPANIES, required=True)
    args = parser.parse_args()

    companies = {str(c.get("id") or ""): c for c in load_config().get("companies", [])}
    company = companies[args.company]

    # Provider totals/stable-ID inventories are the primary completeness witness.
    # Skip separate detail-page sampling so a broad company batch stays cheap.
    row = audit_company(company, sample_size=0, detail_timeout=5.0)
    witness = None
    witness_error = None
    try:
        witness = _extra_witness(company, row)
    except Exception as exc:
        witness_error = f"{type(exc).__name__}: {exc}"

    print(json.dumps({
        "id": row.get("id"),
        "name": row.get("name"),
        "verdict": row.get("verdict"),
        "adapter": row.get("adapter"),
        "jobs_found": row.get("jobs_found"),
        "expected_count": row.get("expected_count"),
        "completeness_pct": row.get("completeness_pct"),
        "rejected_non_job_records": row.get("rejected_non_job_records"),
        "valid_url_ratio": row.get("valid_url_ratio"),
        "stable_id_ratio": row.get("stable_id_ratio"),
        "description_ratio": row.get("description_ratio"),
        "location_ratio": row.get("location_ratio"),
        "source_types": row.get("source_types"),
        "count_probe": row.get("count_probe"),
        "breadth_witness": witness,
        "breadth_witness_error": witness_error,
        "failure_category": row.get("failure_category"),
        "error": row.get("error"),
    }, indent=2, ensure_ascii=False))

    if row.get("verdict") == "CERTIFIED":
        return 0
    if _exact_from_extra_witness(row, witness):
        return 0
    return 1


if __name__ == "__main__":
    sys.exit(main())
