from __future__ import annotations

import json
import re
from pathlib import Path

import requests
from playwright.sync_api import sync_playwright


LEGACY_CAREER = "https://careers.swiggy.com/list.html?dept=Engineering&loc=1"
CURRENT_CAREER = "https://careers.swiggy.com/#/careers"
PAGE_JS = "https://careers.swiggy.com/careers/js/page.js"
INTERESTING = re.compile(r"(?:mynexthire|hirexp|job|jobs|career|requisition|reqid|opening|api|search)", re.I)


def _clip(value, limit=12000):
    text = str(value or "")
    return text if len(text) <= limit else text[:limit] + "…"


def _shape(value, depth=0):
    if depth >= 4:
        return type(value).__name__
    if isinstance(value, dict):
        return {str(k): _shape(v, depth + 1) for k, v in list(value.items())[:35]}
    if isinstance(value, list):
        return [(_shape(value[0], depth + 1) if value else "empty"), f"len={len(value)}"]
    if value is None:
        return None
    return type(value).__name__


def _browser_probe():
    report = {
        "entry_url": CURRENT_CAREER,
        "final_url": None,
        "goto_error": None,
        "body": None,
        "requests": [],
        "responses": [],
        "links": [],
        "scripts": [],
        "resources": [],
    }
    seen_requests = set()
    seen_responses = set()
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(
            user_agent=(
                "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
                "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
            ),
            locale="en-IN",
        )
        page = context.new_page()

        def on_request(req):
            if req.url in seen_requests or not INTERESTING.search(req.url):
                return
            seen_requests.add(req.url)
            report["requests"].append({
                "method": req.method,
                "resource_type": req.resource_type,
                "url": req.url,
                "post_data": _clip(req.post_data, 8000),
            })

        def on_response(resp):
            ct = (resp.headers.get("content-type") or "").lower()
            if resp.url in seen_responses:
                return
            if "json" not in ct and not INTERESTING.search(resp.url):
                return
            seen_responses.add(resp.url)
            row = {"status": resp.status, "url": resp.url, "content_type": ct}
            try:
                if "json" in ct:
                    payload = resp.json()
                    row["json_shape"] = _shape(payload)
                    row["sample"] = _clip(json.dumps(payload, ensure_ascii=False), 20000)
                else:
                    row["sample"] = _clip(resp.text(), 8000)
            except Exception as exc:
                row["read_error"] = f"{type(exc).__name__}: {exc}"
            report["responses"].append(row)

        page.on("request", on_request)
        page.on("response", on_response)
        try:
            page.goto(CURRENT_CAREER, wait_until="domcontentloaded", timeout=90000)
        except Exception as exc:
            report["goto_error"] = f"{type(exc).__name__}: {exc}"

        # Give the SPA enough time to resolve the hash route and call its ATS.
        for _ in range(10):
            try:
                page.wait_for_timeout(1000)
                page.evaluate("window.scrollTo(0, document.body ? document.body.scrollHeight : 0)")
            except Exception:
                break

        report["final_url"] = page.url
        try:
            report["body"] = _clip(page.locator("body").inner_text(timeout=5000), 20000)
            report["links"] = page.locator("a").evaluate_all(
                "els => els.map(a => ({text:(a.innerText||'').trim(), href:a.href||''}))"
            )[:1000]
            report["scripts"] = page.locator("script[src]").evaluate_all(
                "els => els.map(s => s.src).filter(Boolean)"
            )[:500]
            report["resources"] = [
                u for u in page.evaluate(
                    "performance.getEntriesByType('resource').map(x => x.name).filter(Boolean)"
                )
                if INTERESTING.search(u)
            ][:1000]
        except Exception as exc:
            report["dom_error"] = f"{type(exc).__name__}: {exc}"
        browser.close()
    return report


def main() -> int:
    s = requests.Session()
    s.headers.update({
        "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/131 Safari/537.36",
        "Accept-Language": "en-IN,en;q=0.9",
    })
    report = {"legacy_contract": {}, "current_browser": {}}

    # Retain proof of the stale legacy dependency without copying its public keys
    # into our production source. It is useful evidence when the old list page fails.
    try:
        rr = s.get(PAGE_JS, timeout=30, headers={"Referer": LEGACY_CAREER})
        text = rr.text
        match = re.search(r'https://developer\.hirexp\.com/[^"\s]+', text, re.I)
        report["legacy_contract"] = {
            "page_js_status": rr.status_code,
            "api_url": match.group(0) if match else None,
            "has_1_based_page_offset": "page_offset" in text,
        }
    except Exception as exc:
        report["legacy_contract"] = {"error": f"{type(exc).__name__}: {exc}"}

    report["current_browser"] = _browser_probe()
    current = report["current_browser"]
    print(
        "CURRENT",
        "final=", current.get("final_url"),
        "error=", current.get("goto_error"),
        "requests=", len(current.get("requests") or []),
        "responses=", len(current.get("responses") or []),
    )
    for row in current.get("requests") or []:
        print("REQUEST", row["method"], row["resource_type"], row["url"])
        if row.get("post_data"):
            print("POST", row["post_data"][:3000])
    for row in current.get("responses") or []:
        print("RESPONSE", row["status"], row["content_type"], row["url"])
        if row.get("json_shape") is not None:
            print("SHAPE", json.dumps(row["json_shape"], ensure_ascii=False)[:5000])

    Path("reports/phase4").mkdir(parents=True, exist_ok=True)
    Path("reports/phase4/swiggy-contract.json").write_text(
        json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
