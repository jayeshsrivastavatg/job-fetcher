from __future__ import annotations

import json
from urllib.parse import urlparse

import requests

TARGETS = {
    "microsoft": "https://apply.careers.microsoft.com/careers?domain=microsoft.com&hl=en",
    "twilio": "https://jobs.twilio.com/careers?domain=twilio.com&hl=en",
    "morgan_stanley": "https://morganstanley.eightfold.ai/careers?domain=morganstanley.com&hl=en",
}

HEADERS = {
    "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/131 Safari/537.36",
    "Accept": "application/json",
}


def api_url(career_url: str) -> str:
    p = urlparse(career_url)
    return f"{p.scheme}://{p.netloc}/api/pcsx/search"


def main():
    for name, career in TARGETS.items():
        endpoint = api_url(career)
        domain = "microsoft.com" if name == "microsoft" else "twilio.com" if name == "twilio" else "morganstanley.com"
        print(f"=== {name} ===")
        first = requests.get(endpoint, params={"domain": domain, "query": "", "location": "", "start": 0, "hl": "en"}, headers=HEADERS, timeout=30)
        print("URL", first.url, "STATUS", first.status_code)
        first.raise_for_status()
        data = first.json()["data"]
        positions = data.get("positions") or []
        count = int(data.get("count") or 0)
        print("COUNT", count, "PAGE0", len(positions))
        if positions:
            print("FIRST_POSITION", json.dumps(positions[0], ensure_ascii=False)[:10000])
            print("LAST_POSITION_PAGE0", json.dumps(positions[-1], ensure_ascii=False)[:10000])
        for start in sorted({10, max(0, count - 10)}):
            response = requests.get(endpoint, params={"domain": domain, "query": "", "location": "", "start": start, "hl": "en"}, headers=HEADERS, timeout=30)
            response.raise_for_status()
            page = response.json()["data"]
            rows = page.get("positions") or []
            print("PAGE", start, "COUNT_FIELD", page.get("count"), "ROWS", len(rows))
            if rows:
                print("PAGE_FIRST", start, json.dumps(rows[0], ensure_ascii=False)[:5000])
                print("PAGE_LAST", start, json.dumps(rows[-1], ensure_ascii=False)[:5000])


if __name__ == "__main__":
    main()
