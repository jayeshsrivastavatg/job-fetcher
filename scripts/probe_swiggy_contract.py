from __future__ import annotations

import json
import re
from pathlib import Path

import requests


CAREER = "https://careers.swiggy.com/list.html?dept=Engineering&loc=1"
PAGE_JS = "https://careers.swiggy.com/careers/js/page.js"
SCRIPT_JS = "https://careers.swiggy.com/careers/js/script.js"
API = "https://developer.hirexp.com/Basic/v1/users/jobs/filter"


def main() -> int:
    s = requests.Session()
    s.headers.update({
        "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/131 Safari/537.36",
        "Accept-Language": "en-US,en;q=0.9",
    })
    report = {"career": {}, "scripts": {}, "api_attempts": []}

    r = s.get(CAREER, timeout=30)
    report["career"] = {"status": r.status_code, "headers": dict(r.headers), "sample": r.text[:5000]}
    print("CAREER", r.status_code, r.url)

    page_text = ""
    for url in (PAGE_JS, SCRIPT_JS):
        rr = s.get(url, timeout=30, headers={"Referer": CAREER})
        text = rr.text
        report["scripts"][url] = {"status": rr.status_code, "text": text}
        print("SCRIPT", rr.status_code, url, "bytes", len(text))
        for needle in ("hirexp", "jobs/filter", "Authorization", "Basic/v1", "page_offset", "job_detail"):
            for m in re.finditer(needle, text, re.I):
                print("MATCH", needle, text[max(0, m.start()-800):m.end()+1600].replace("\n", " "))
                break
        if url == PAGE_JS:
            page_text = text

    # Try the exact public request shape observed in the official browser. Include
    # realistic Origin/Referer headers, then a few syntactically valid variants so
    # we can separate a copied Angular serialization typo from server requirements.
    payloads = [
        {
            "count": "true",
            "page_limit": 10,
            "page_offset": 1,
            "search_filters": {"category__title": ["Engineering"], "status": ["active"]},
            "raw_search_string": [],
        },
        {
            "count": True,
            "page_limit": 10,
            "page_offset": 1,
            "search_filters": {"category__title": ["Engineering"], "status": ["active"]},
            "raw_search_string": [],
        },
        {
            "count": "true",
            "page_limit": 10,
            "page_offset": 0,
            "search_filters": {"category__title": ["Engineering"], "status": ["active"]},
            "raw_search_string": [],
        },
    ]
    for payload in payloads:
        try:
            rr = s.post(
                API,
                json=payload,
                timeout=45,
                headers={
                    "Origin": "https://careers.swiggy.com",
                    "Referer": CAREER,
                    "Accept": "application/json, text/plain, */*",
                },
            )
            row = {
                "payload": payload,
                "status": rr.status_code,
                "headers": dict(rr.headers),
                "body": rr.text[:50000],
            }
            print("API", rr.status_code, "bytes", len(rr.content), "payload", json.dumps(payload))
            print("BODY", rr.text[:5000])
        except Exception as exc:
            row = {"payload": payload, "error": f"{type(exc).__name__}: {exc}"}
            print("API ERROR", row["error"])
        report["api_attempts"].append(row)

    Path("reports/phase4").mkdir(parents=True, exist_ok=True)
    Path("reports/phase4/swiggy-contract.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
