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
    for params in (
        {"pagesize": 100},
        {"pagesize": 100, "page": 1},
        {"pagesize": 100, "page": 2},
        {"pagesize": 100, "page": 3},
        {"pagesize": 1, "page": 1},
    ):
        r = requests.get(base, params=params, headers=UA, timeout=30)
        print("UBER", r.url, r.status_code, r.headers.get("content-type"), flush=True)
        r.raise_for_status()
        data = r.json()
        jobs = data.get("jobs") if isinstance(data, dict) else None
        print("UBER_SHAPE", json.dumps(summarize(data), ensure_ascii=False)[:10000], flush=True)
        if isinstance(jobs, list):
            ids = []
            for row in jobs[:8]:
                if isinstance(row, dict):
                    ids.append({k: row.get(k) for k in row if re.search(r"(^id$|job.*id|requisition|title|location|city|country)", k, re.I)})
            print("UBER_ROWS", json.dumps(ids, ensure_ascii=False)[:10000], flush=True)


def atlassian():
    url = "https://www.atlassian.com/endpoint/careers/listings"
    r = requests.get(url, headers=UA, timeout=30)
    print("ATLAS", r.status_code, r.headers.get("content-type"), flush=True)
    r.raise_for_status()
    data = r.json()
    print("ATLAS_SHAPE", json.dumps(summarize(data), ensure_ascii=False)[:10000], flush=True)
    if isinstance(data, list):
        print("ATLAS_COUNT", len(data), flush=True)
        for row in data[:3] + data[-3:]:
            print("ATLAS_ROW", json.dumps(row, ensure_ascii=False)[:6000], flush=True)


def navi():
    urls = [
        "https://navi.com/careers",
        "https://navi.com/careers/jobs",
        "https://navi.freshteam.com/jobs",
        "https://navi.freshteam.com/jobs?remote=false",
    ]
    for url in urls:
        try:
            r = requests.get(url, headers=UA, timeout=30, allow_redirects=True)
            print("NAVI", url, "->", r.url, r.status_code, r.headers.get("content-type"), flush=True)
            print("NAVI_BODY", re.sub(r"\s+", " ", r.text[:8000])[:8000], flush=True)
        except Exception as exc:
            print("NAVI_ERROR", url, type(exc).__name__, str(exc), flush=True)


if __name__ == "__main__":
    uber()
    atlassian()
    navi()
