from __future__ import annotations

import json
import re

import requests

UA = {"User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/131 Safari/537.36"}


def summarize(value, depth=0):
    if depth > 2:
        return type(value).__name__
    if isinstance(value, dict):
        return {k: summarize(v, depth + 1) for k, v in list(value.items())[:40]}
    if isinstance(value, list):
        return {"type": "list", "len": len(value), "sample": summarize(value[0], depth + 1) if value else None}
    return value if isinstance(value, (str, int, float, bool, type(None))) else type(value).__name__


def uber():
    base = "https://jobs.uber.com/api/jobs/search/"
    for params in ({"pagesize": 100, "page": 1}, {"pagesize": 100, "page": 2}, {"pagesize": 100, "page": 7}):
        r = requests.get(base, params=params, headers=UA, timeout=30)
        r.raise_for_status()
        data = r.json()
        print("UBER", r.url, json.dumps({k: data.get(k) for k in ("totalPages", "totalJobs", "page", "pageSize")}), "jobs", len(data.get("jobs") or []), flush=True)
        if data.get("jobs"):
            print("UBER_LAST", json.dumps(data["jobs"][-1], ensure_ascii=False)[:4000], flush=True)


def atlassian():
    url = "https://www.atlassian.com/endpoint/careers/listings"
    r = requests.get(url, headers=UA, timeout=30)
    r.raise_for_status()
    data = r.json()
    print("ATLAS_COUNT", len(data), "UNIQUE", len({str(x.get('id')) for x in data if isinstance(x, dict)}), flush=True)


def navi():
    urls = [
        "https://navi.com/careers",
        "https://navi.com/careers/jobs",
        "https://navi.freshteam.com/jobs",
        "https://lnkd.in/gZCfjnGQ",
        "https://lnkd.in/ghZniGmy",
        "https://lnkd.in/gct596Pc",
        "https://lnkd.in/g4WGb7u3",
    ]
    for url in urls:
        try:
            r = requests.get(url, headers=UA, timeout=30, allow_redirects=True)
            print("NAVI", url, "->", r.url, r.status_code, flush=True)
            print("NAVI_HISTORY", [(h.status_code, h.url, h.headers.get("location")) for h in r.history], flush=True)
            if "navi.com" not in r.url and "lnkd.in" not in r.url:
                print("NAVI_EXTERNAL_BODY", re.sub(r"\s+", " ", r.text[:2000]), flush=True)
        except Exception as exc:
            print("NAVI_ERROR", url, type(exc).__name__, str(exc), flush=True)


if __name__ == "__main__":
    uber()
    atlassian()
    navi()
