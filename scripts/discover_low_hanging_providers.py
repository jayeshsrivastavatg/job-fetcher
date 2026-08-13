from __future__ import annotations

import concurrent.futures
import html
import json
import re
from urllib.parse import urlparse

import requests

from job_fetcher.config import load_config
from job_fetcher.sources.breadth_provider_overrides import breadth_provider_config
from job_fetcher.sources.known_provider_overrides import known_provider_config


TIMEOUT = 12
WORKERS = 16
UA = "Mozilla/5.0 PersonalJobFetcher/0.1 provider-discovery"

PROVIDER_PATTERNS = {
    "greenhouse": [
        re.compile(r"https?://(?:boards|job-boards)\.greenhouse\.io/[A-Za-z0-9_-]+[^\s\"'<>]*", re.I),
        re.compile(r"https?://boards-api\.greenhouse\.io/v1/boards/[A-Za-z0-9_-]+[^\s\"'<>]*", re.I),
    ],
    "lever": [re.compile(r"https?://jobs\.lever\.co/[A-Za-z0-9_-]+[^\s\"'<>]*", re.I)],
    "smartrecruiters": [
        re.compile(r"https?://careers\.smartrecruiters\.com/[A-Za-z0-9_-]+[^\s\"'<>]*", re.I),
        re.compile(r"https?://api\.smartrecruiters\.com/v1/companies/[A-Za-z0-9_-]+[^\s\"'<>]*", re.I),
    ],
    "workday": [re.compile(r"https?://[A-Za-z0-9.-]+\.wd\d+\.myworkdayjobs\.com/[^\s\"'<>]+", re.I)],
    "kula": [re.compile(r"https?://careers\.kula\.ai/[A-Za-z0-9_-]+[^\s\"'<>]*", re.I)],
    "trakstar": [re.compile(r"https?://[A-Za-z0-9.-]+\.hire\.trakstar\.com[^\s\"'<>]*", re.I)],
    "oracle": [
        re.compile(r"https?://[A-Za-z0-9.-]+\.fa\.[A-Za-z0-9.-]*oraclecloud\.com/[^\s\"'<>]+", re.I),
        re.compile(r"https?://[^\s\"'<>]+/hcmUI/CandidateExperience/[^\s\"'<>]+", re.I),
    ],
    "successfactors": [
        re.compile(r"https?://[^\s\"'<>]*successfactors\.[^\s\"'<>]+", re.I),
        re.compile(r"https?://[^\s\"'<>]*/go/[^\s\"'<>]+", re.I),
    ],
    "eightfold": [re.compile(r"https?://[A-Za-z0-9.-]+\.eightfold\.ai/careers[^\s\"'<>]*", re.I)],
    "mynexthire": [re.compile(r"https?://[A-Za-z0-9.-]+\.mynexthire\.com[^\s\"'<>]*", re.I)],
}


def _normalize(text: str) -> str:
    return html.unescape(text or "").replace("\\/", "/").replace("\\u002F", "/").replace("\\u002f", "/")


def _signals(text: str) -> list[dict]:
    normalized = _normalize(text)
    out = []
    seen = set()
    for provider, patterns in PROVIDER_PATTERNS.items():
        for pattern in patterns:
            for match in pattern.findall(normalized):
                value = str(match).rstrip("),.;]")
                key = (provider, value)
                if key in seen:
                    continue
                seen.add(key)
                out.append({"provider": provider, "url": value})
                if len([x for x in out if x["provider"] == provider]) >= 5:
                    break
    return out


def _scan(company: dict) -> dict:
    company_id = str(company.get("id") or "")
    source = company.get("source") or {}
    urls = []
    for value in [source.get("entry_url"), company.get("career_url")]:
        value = str(value or "").strip()
        if value and value not in urls:
            urls.append(value)

    combined = []
    attempts = []
    for url in urls:
        try:
            response = requests.get(url, timeout=TIMEOUT, allow_redirects=True, headers={"User-Agent": UA})
            body = response.text[:2_500_000]
            evidence = _signals(response.url + "\n" + body)
            combined.extend(evidence)
            attempts.append({
                "url": url,
                "status": response.status_code,
                "final_url": response.url,
                "signals": evidence,
            })
        except Exception as exc:
            attempts.append({"url": url, "error": f"{type(exc).__name__}: {exc}"})

    unique = []
    seen = set()
    for item in combined:
        key = (item["provider"], item["url"])
        if key not in seen:
            seen.add(key)
            unique.append(item)

    return {
        "id": company_id,
        "name": company.get("name"),
        "research_hint": (company.get("research") or {}).get("provider_hint"),
        "signals": unique,
        "attempts": attempts,
    }


def main() -> None:
    companies = []
    for company in load_config().get("companies", []):
        if not company.get("enabled", True):
            continue
        if str((company.get("source") or {}).get("type") or "").casefold() != "auto":
            continue
        if known_provider_config(company) or breadth_provider_config(company):
            continue
        companies.append(company)

    with concurrent.futures.ThreadPoolExecutor(max_workers=WORKERS) as executor:
        rows = list(executor.map(_scan, companies))

    candidates = [row for row in rows if row["signals"]]
    unresolved = [row for row in rows if not row["signals"]]
    print(json.dumps({
        "scanned_auto_companies": len(rows),
        "candidate_count": len(candidates),
        "candidates": candidates,
        "unresolved_ids": [row["id"] for row in unresolved],
    }, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
