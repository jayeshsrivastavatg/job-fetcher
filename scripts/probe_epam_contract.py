from __future__ import annotations

import json
import re
from urllib.parse import urlencode, urljoin, urlparse

from bs4 import BeautifulSoup

from job_fetcher.sources.http_client import session, timeout_seconds


BASE = "https://careers.epam.com/en/jobs/india"
VACANCY_RE = re.compile(r"^/en/vacancy/(?P<id>[^/?#]+)", re.I)
TOTAL_RES = [
    re.compile(r"Viewing\s+\d+\s*-\s*\d+\s+out of\s+(\d+)\s+jobs found", re.I),
    re.compile(r"(\d+)\s+Jobs Found", re.I),
]


def fetch(url):
    response = session().get(url, timeout=timeout_seconds(), allow_redirects=True, headers={"User-Agent": "PersonalJobFetcher/0.1"})
    response.raise_for_status()
    soup = BeautifulSoup(response.text, "html.parser")
    ids = set()
    links = []
    for anchor in soup.select("a[href]"):
        absolute = urljoin(response.url, anchor.get("href") or "")
        match = VACANCY_RE.match(urlparse(absolute).path)
        if match:
            ids.add(match.group("id").casefold())
            if len(links) < 20:
                links.append(absolute)
    text = soup.get_text(" ", strip=True)
    total = None
    for pattern in TOTAL_RES:
        match = pattern.search(text)
        if match:
            total = int(match.group(1))
            break
    pagination = []
    for anchor in soup.select("a[href]"):
        label = anchor.get_text(" ", strip=True)
        href = anchor.get("href") or ""
        if label in {"2", "Next", "next", ">", "›"} or "page" in href.casefold():
            pagination.append({"text": label, "href": href})
    return {"url": response.url, "total": total, "ids": ids, "sample_links": links, "pagination": pagination[:40]}


def main():
    first = fetch(BASE)
    print(json.dumps({k: (len(v) if k == "ids" else v) for k, v in first.items()}, ensure_ascii=False), flush=True)
    first_ids = first["ids"]
    candidates = [
        ("page", "2"),
        ("p", "2"),
        ("pageNumber", "2"),
        ("currentPage", "2"),
        ("pageNo", "2"),
        ("offset", "10"),
    ]
    for key, value in candidates:
        url = f"{BASE}?{urlencode({key: value})}"
        try:
            result = fetch(url)
            print(json.dumps({
                "param": key,
                "url": result["url"],
                "total": result["total"],
                "ids": len(result["ids"]),
                "new_vs_first": len(result["ids"] - first_ids),
                "sample_new": sorted(result["ids"] - first_ids)[:5],
            }, ensure_ascii=False), flush=True)
        except Exception as exc:
            print(json.dumps({"param": key, "error": f"{type(exc).__name__}: {exc}"}), flush=True)


if __name__ == "__main__":
    main()
