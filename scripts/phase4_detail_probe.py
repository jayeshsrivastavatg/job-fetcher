from __future__ import annotations

import argparse
import json
import re
from pathlib import Path
from urllib.parse import urlparse

from playwright.sync_api import sync_playwright


INTERESTING = re.compile(r"(?:job|req|requisition|detail|career|phenom|mynexthire|api)", re.I)
LOWES_SEARCH = "https://talent.lowes.com/in/en/search-results"
SWIGGY_DETAIL = "https://swiggy.mynexthire.com/employer/jobs/careers?reqId=24364"


def _clip(value, limit=20000):
    text = str(value or "")
    return text if len(text) <= limit else text[:limit] + "…"


def _shape(value, depth=0):
    if depth >= 5:
        return type(value).__name__
    if isinstance(value, dict):
        return {str(k): _shape(v, depth + 1) for k, v in list(value.items())[:50]}
    if isinstance(value, list):
        return [(_shape(value[0], depth + 1) if value else "empty"), f"len={len(value)}"]
    if value is None:
        return None
    return type(value).__name__


def _capture_page(page, url, wait_ms=5000):
    rows = {"entry_url": url, "requests": [], "responses": [], "scripts_json": [], "body": None, "final_url": None}
    seen_req, seen_resp = set(), set()

    def on_request(req):
        if req.url in seen_req or not INTERESTING.search(req.url):
            return
        seen_req.add(req.url)
        rows["requests"].append({
            "method": req.method,
            "resource_type": req.resource_type,
            "url": req.url,
            "post_data": _clip(req.post_data, 12000),
        })

    def on_response(resp):
        ct = (resp.headers.get("content-type") or "").lower()
        if resp.url in seen_resp or ("json" not in ct and not INTERESTING.search(resp.url)):
            return
        seen_resp.add(resp.url)
        item = {"status": resp.status, "url": resp.url, "content_type": ct}
        try:
            if "json" in ct:
                payload = resp.json()
                item["json_shape"] = _shape(payload)
                item["sample"] = _clip(json.dumps(payload, ensure_ascii=False), 30000)
            else:
                item["sample"] = _clip(resp.text(), 15000)
        except Exception as exc:
            item["read_error"] = f"{type(exc).__name__}: {exc}"
        rows["responses"].append(item)

    page.on("request", on_request)
    page.on("response", on_response)
    page.goto(url, wait_until="domcontentloaded", timeout=90000)
    page.wait_for_timeout(wait_ms)
    rows["final_url"] = page.url
    try:
        rows["body"] = _clip(page.locator("body").inner_text(timeout=5000), 30000)
    except Exception:
        pass
    try:
        scripts = page.locator('script[type="application/ld+json"], script[type="application/json"]').evaluate_all(
            "els => els.map(s => s.textContent || '').filter(Boolean)"
        )
        rows["scripts_json"] = [_clip(x, 30000) for x in scripts[:50]]
    except Exception:
        pass
    return rows


def run(target: str):
    report = {"target": target}
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
        if target == "swiggy":
            report["detail"] = _capture_page(page, SWIGGY_DETAIL, 6000)
        elif target == "lowes_india":
            page.goto(LOWES_SEARCH, wait_until="domcontentloaded", timeout=90000)
            page.wait_for_timeout(3500)
            hrefs = page.locator('a[href*="/in/en/job/"]').evaluate_all(
                "els => els.map(a => a.href).filter(Boolean)"
            )
            detail = next((h for h in hrefs if re.search(r"/in/en/job/JR-", urlparse(h).path, re.I)), None)
            if not detail:
                raise RuntimeError("lowes_no_detail_url")
            page.close()
            page = context.new_page()
            report["chosen_detail"] = detail
            report["detail"] = _capture_page(page, detail, 6000)
        else:
            raise RuntimeError(target)
        browser.close()
    return report


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--target", choices=["swiggy", "lowes_india"], required=True)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    report = run(args.target)
    out = Path(args.output)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    detail = report["detail"]
    print("TARGET", args.target, "URL", detail.get("final_url"))
    print("BODY", _clip(detail.get("body"), 4000))
    for row in detail.get("requests") or []:
        print("REQUEST", row["method"], row["url"])
        if row.get("post_data"):
            print("POST", row["post_data"][:2500])
    for row in detail.get("responses") or []:
        print("RESPONSE", row["status"], row["content_type"], row["url"])
        if row.get("json_shape") is not None:
            print("SHAPE", json.dumps(row["json_shape"], ensure_ascii=False)[:5000])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
