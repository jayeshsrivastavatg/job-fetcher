from __future__ import annotations

import json
import re

from playwright.sync_api import sync_playwright

ENTRY = "https://careers.servicenow.com/jobs/"
KEY_RE = re.compile(r"(job|career|search|position|posting|requisition|api|graphql|smartrecruit)", re.I)


def summarize(value):
    if isinstance(value, list):
        out = {"type": "list", "length": len(value)}
        if value and isinstance(value[0], dict):
            out["first_keys"] = sorted(value[0].keys())
            out["first"] = value[0]
        return out
    if isinstance(value, dict):
        return {"type": "dict", "keys": sorted(value.keys()), "sample": dict(list(value.items())[:5])}
    return value


def main():
    seen = set()
    rows = []
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(
            user_agent="Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/131 Safari/537.36",
            locale="en-US",
        )
        page = context.new_page()

        def on_response(resp):
            try:
                url = resp.url
                ct = (resp.headers.get("content-type") or "").lower()
                if not (KEY_RE.search(url) or "json" in ct):
                    return
                key = (url, resp.status, ct)
                if key in seen:
                    return
                seen.add(key)
                row = {"url": url, "status": resp.status, "content_type": ct}
                try:
                    req = resp.request
                    row["method"] = req.method
                    row["post_data"] = req.post_data[:3000] if req.post_data else None
                except Exception:
                    pass
                if "json" in ct:
                    try:
                        payload = resp.json()
                        if isinstance(payload, dict):
                            row["keys"] = sorted(payload.keys())
                            for field in ("total", "totalFound", "count", "jobs", "results", "items", "data", "content", "postings"):
                                if field in payload:
                                    row[f"field_{field}"] = summarize(payload[field])
                        elif isinstance(payload, list):
                            row["list"] = summarize(payload)
                    except Exception as exc:
                        row["json_error"] = str(exc)[:300]
                rows.append(row)
            except Exception:
                pass

        page.on("response", on_response)
        page.goto(ENTRY, wait_until="domcontentloaded", timeout=90000)
        page.wait_for_timeout(2500)
        for _ in range(4):
            page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
            page.wait_for_timeout(400)
        print("PAGE", page.url)
        try:
            text = page.locator("body").inner_text(timeout=5000)
            print("BODY_HEAD", " ".join(text.split())[:3000])
        except Exception:
            pass
        print("NETWORK")
        for row in rows:
            print(json.dumps(row, ensure_ascii=False, default=str)[:20000])
        browser.close()


if __name__ == "__main__":
    main()
