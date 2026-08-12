from __future__ import annotations

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


def fetch(endpoint, domain, extra):
    params = {"domain": domain, "query": "", "location": "", "start": 0, "hl": "en", **extra}
    response = requests.get(endpoint, params=params, headers=HEADERS, timeout=30)
    print("TRY", extra or {"default": True}, "STATUS", response.status_code, "URL", response.url, flush=True)
    if response.status_code != 200:
        print("BODY", response.text[:1000], flush=True)
        return
    data = response.json().get("data") or {}
    rows = data.get("positions") or []
    print("RESULT", "count", data.get("count"), "rows", len(rows), flush=True)


def main():
    variants = [
        {},
        {"num": 100},
        {"limit": 100},
        {"size": 100},
        {"page_size": 100},
    ]
    for name, career in TARGETS.items():
        endpoint = api_url(career)
        domain = "microsoft.com" if name == "microsoft" else "twilio.com" if name == "twilio" else "morganstanley.com"
        print(f"=== {name} ===", flush=True)
        for variant in variants:
            fetch(endpoint, domain, variant)


if __name__ == "__main__":
    main()
