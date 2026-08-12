from __future__ import annotations

import json
import re
from urllib.parse import urlparse

from playwright.sync_api import sync_playwright

ENTRY = "https://careers.cohesity.com/open-positions/"
KEY_RE = re.compile(r"(job|career|workday|wday|greenhouse|api|position|requisition|opening)", re.I)


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
                item = {"url": url, "status": resp.status, "content_type": ct}
                if "json" in ct:
                    try:
                        payload = resp.json()
                        if isinstance(payload, dict):
                            item["json_keys"] = sorted(payload.keys())[:40]
                            for k in ("total", "totalFound", "count", "jobs", "jobPostings", "items", "results", "data"):
                                if k in payload:
                                    value = payload[k]
                                    item[f"field_{k}"] = len(value) if isinstance(value, list) else (list(value.keys())[:20] if isinstance(value, dict) else value)
                        elif isinstance(payload, list):
                            item["json_list_length"] = len(payload)
                    except Exception as exc:
                        item["json_error"] = str(exc)[:200]
                rows.append(item)
            except Exception:
                pass

        page.on("response", on_response)
        page.goto(ENTRY, wait_until="domcontentloaded", timeout=90000)
        page.wait_for_timeout(3500)
        for _ in range(12):
            try:
                page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
                page.wait_for_timeout(600)
            except Exception:
                break

        print("PAGE_URL", page.url)
        print("TITLE", page.title())
        print("IFRAMES")
        for frame in page.frames:
            print(" ", frame.url)
        print("SCRIPT_SRCS")
        try:
            scripts = page.locator("script[src]")
            for i in range(scripts.count()):
                src = scripts.nth(i).get_attribute("src") or ""
                if KEY_RE.search(src) or "cohesity" not in urlparse(src).netloc.lower():
                    print(" ", src)
        except Exception:
            pass
        print("NETWORK")
        for row in rows:
            print(json.dumps(row, ensure_ascii=False))
        browser.close()


if __name__ == "__main__":
    main()
