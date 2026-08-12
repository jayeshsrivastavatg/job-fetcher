from __future__ import annotations

import requests

ENDPOINT = "https://apply.careers.microsoft.com/api/pcsx/search"
DOMAIN = "microsoft.com"
HEADERS = {
    "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/131 Safari/537.36",
    "Accept": "application/json",
}


def main():
    for page_size in (10, 15, 20, 25, 30, 40, 50, 75):
        params = {
            "domain": DOMAIN,
            "query": "",
            "location": "",
            "start": 0,
            "hl": "en",
            "page_size": page_size,
        }
        response = requests.get(ENDPOINT, params=params, headers=HEADERS, timeout=30)
        print("PAGE_SIZE", page_size, "STATUS", response.status_code, flush=True)
        if response.status_code == 200:
            data = response.json().get("data") or {}
            print("RESULT", "count", data.get("count"), "rows", len(data.get("positions") or []), flush=True)
        else:
            print("BODY", response.text[:500], flush=True)


if __name__ == "__main__":
    main()
