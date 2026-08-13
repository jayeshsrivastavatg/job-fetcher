from __future__ import annotations

import json
from urllib.parse import urlparse

from playwright.sync_api import sync_playwright


ENTRY = "https://careers.nykaa.com/"


def compact(value, depth=0):
    if depth > 2:
        return type(value).__name__
    if isinstance(value, dict):
        return {str(k): compact(v, depth + 1) for k, v in list(value.items())[:25]}
    if isinstance(value, list):
        return [compact(v, depth + 1) for v in value[:3]]
    if isinstance(value, str):
        return value[:160]
    return value


def main():
    records = []
    seen = set()
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page(viewport={"width": 1440, "height": 1200})

        def on_response(response):
            request = response.request
            resource = request.resource_type
            url = response.url
            host = urlparse(url).netloc.casefold()
            if resource not in {"xhr", "fetch"}:
                return
            if url in seen:
                return
            seen.add(url)
            row = {
                "method": request.method,
                "resource": resource,
                "status": response.status,
                "url": url,
                "request_post_data": (request.post_data or "")[:500],
                "content_type": response.headers.get("content-type"),
                "first_party": host.endswith("careers.nykaa.com") or host.endswith("skima.ai"),
            }
            try:
                if "json" in (row["content_type"] or "").casefold():
                    row["json_shape"] = compact(response.json())
            except Exception as exc:
                row["json_error"] = f"{type(exc).__name__}: {exc}"
            records.append(row)
            print(json.dumps({"network": row}, ensure_ascii=False), flush=True)

        page.on("response", on_response)
        page.goto(ENTRY, wait_until="networkidle", timeout=90000)
        print(json.dumps({
            "title": page.title(),
            "url": page.url,
            "body_text_prefix": page.locator("body").inner_text()[:500],
            "scripts": page.locator("script[src]").evaluate_all("els => els.map(e => e.src)")[:30],
        }, ensure_ascii=False), flush=True)

        page2 = page.get_by_role("button", name="Go to page 2")
        if page2.count() == 0:
            page2 = page.get_by_text("2", exact=True)
        print(json.dumps({"page2_candidates": page2.count()}, flush=True))
        if page2.count():
            page2.first.click()
            page.wait_for_timeout(2500)
            print(json.dumps({
                "after_page2_url": page.url,
                "after_page2_text_prefix": page.locator("body").inner_text()[:700],
            }, ensure_ascii=False), flush=True)

        browser.close()

    print(json.dumps({
        "xhr_fetch_count": len(records),
        "first_party_count": sum(bool(row.get("first_party")) for row in records),
        "urls": [row["url"] for row in records],
    }, ensure_ascii=False), flush=True)


if __name__ == "__main__":
    main()
