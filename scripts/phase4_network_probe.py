from __future__ import annotations

import argparse
import json
import re
from pathlib import Path

from playwright.sync_api import sync_playwright


TARGETS = {
    "swiggy": "https://careers.swiggy.com/#/careers",
    "lowes_india": "https://talent.lowes.com/in/en/search-results",
    "makemytrip": "https://careers.makemytrip.com/prod/opportunities",
    "rakuten_india": "https://rakuten.openings.co/rakuten/jobslist",
    "sony_tech_india": "https://careers.sonyindiasoftware.co.in/sonyindiasoftware/",
}

INTERESTING = re.compile(
    r"(?:job|jobs|career|search|opening|position|vacan|requisition|opportun|api|graphql|query|zwayam)",
    re.I,
)


def _clip(value: str, limit: int = 2000) -> str:
    value = str(value or "")
    return value if len(value) <= limit else value[:limit] + "…"


def _json_shape(value, depth: int = 0):
    if depth >= 5:
        return type(value).__name__
    if isinstance(value, dict):
        return {str(k): _json_shape(v, depth + 1) for k, v in list(value.items())[:45]}
    if isinstance(value, list):
        return [(_json_shape(value[0], depth + 1) if value else "empty"), f"len={len(value)}"]
    if value is None:
        return None
    return type(value).__name__


def probe(target: str, url: str, timeout_ms: int) -> dict:
    evidence = {
        "target": target,
        "entry_url": url,
        "goto_error": None,
        "final_url": None,
        "title": None,
        "responses": [],
        "interesting_requests": [],
        "resource_urls": [],
        "links": [],
        "scripts": [],
        "body_text": None,
        "storage": {},
    }
    seen_response_urls: set[str] = set()
    seen_requests: set[str] = set()

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
            # For ATS discovery the transport type is stronger evidence than an URL
            # keyword: minified SPAs often call opaque endpoints. Capture every
            # XHR/fetch request plus job-ish document/resource requests.
            if req.url in seen_requests:
                return
            if req.resource_type not in {"xhr", "fetch"} and not INTERESTING.search(req.url):
                return
            seen_requests.add(req.url)
            evidence["interesting_requests"].append({
                "method": req.method,
                "resource_type": req.resource_type,
                "url": req.url,
                "post_data": _clip(req.post_data or "", 12000),
                "headers": {
                    k: v for k, v in req.headers.items()
                    if k.lower() in {"content-type", "accept", "origin", "referer", "authorization", "x-api-key", "tenant", "clientid"}
                },
            })

        def on_response(resp):
            url_ = resp.url
            ct = (resp.headers.get("content-type") or "").lower()
            request_type = resp.request.resource_type
            if url_ in seen_response_urls:
                return
            if request_type not in {"xhr", "fetch"} and "json" not in ct and not INTERESTING.search(url_):
                return
            seen_response_urls.add(url_)
            row = {
                "status": resp.status,
                "url": url_,
                "resource_type": request_type,
                "content_type": ct,
                "json_shape": None,
                "sample": None,
            }
            try:
                if "json" in ct:
                    payload = resp.json()
                    row["json_shape"] = _json_shape(payload)
                    row["sample"] = _clip(json.dumps(payload, ensure_ascii=False), 30000)
                elif request_type in {"xhr", "fetch"}:
                    row["sample"] = _clip(resp.text(), 12000)
            except Exception as exc:
                row["sample"] = f"body-read-error: {type(exc).__name__}: {exc}"
            evidence["responses"].append(row)

        page.on("request", on_request)
        page.on("response", on_response)

        try:
            page.goto(url, wait_until="domcontentloaded", timeout=timeout_ms)
        except Exception as exc:
            evidence["goto_error"] = f"{type(exc).__name__}: {exc}"

        # Let the SPA boot, scroll through lazy result containers and exercise common
        # search/pagination controls. We are discovering a public contract, not trying
        # to scrape DOM cards, so capturing the resulting network requests is the goal.
        for _ in range(10):
            try:
                page.wait_for_timeout(1000)
                page.evaluate("window.scrollTo(0, document.body ? document.body.scrollHeight : 0)")
            except Exception:
                break
        for label in (
            "Search jobs", "Search Jobs", "View jobs", "View Jobs", "Open positions",
            "Explore opportunities", "Load more", "Show more", "View more", "More jobs", "Next",
        ):
            try:
                node = page.get_by_text(label, exact=False).first
                if node.is_visible() and node.is_enabled():
                    node.click(timeout=1800)
                    page.wait_for_timeout(1500)
            except Exception:
                pass

        try:
            evidence["final_url"] = page.url
            evidence["title"] = page.title()
            evidence["body_text"] = _clip(page.locator("body").inner_text(timeout=3000), 20000)
            evidence["links"] = page.locator("a").evaluate_all(
                "els => els.map(a => ({text:(a.innerText||'').trim(), href:a.href||''})).filter(x => x.href)"
            )[:1000]
            evidence["scripts"] = page.locator("script[src]").evaluate_all(
                "els => els.map(s => s.src).filter(Boolean)"
            )[:500]
            resources = page.evaluate(
                "performance.getEntriesByType('resource').map(x => x.name).filter(Boolean)"
            )
            evidence["resource_urls"] = resources[:1500]
            evidence["storage"] = page.evaluate(
                "() => ({local:{...localStorage}, session:{...sessionStorage}})"
            )
        except Exception as exc:
            evidence["dom_snapshot_error"] = f"{type(exc).__name__}: {exc}"

        browser.close()

    return evidence


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--target", choices=sorted(TARGETS), required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--timeout-ms", type=int, default=90000)
    args = parser.parse_args()

    result = probe(args.target, TARGETS[args.target], args.timeout_ms)
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(result, indent=2, ensure_ascii=False), encoding="utf-8")

    print(f"target={args.target} final={result.get('final_url')} error={result.get('goto_error')}")
    print(f"responses={len(result['responses'])} requests={len(result['interesting_requests'])} resources={len(result['resource_urls'])}")
    for row in result["responses"][:80]:
        print(f"RESPONSE {row['status']} {row['resource_type']} {row['content_type']} {row['url']}")
        if row.get("json_shape") is not None:
            print("SHAPE", json.dumps(row["json_shape"], ensure_ascii=False)[:5000])
        if row.get("sample") and row.get("resource_type") in {"xhr", "fetch"}:
            print("SAMPLE", row["sample"][:2500])
    for row in result["interesting_requests"][:100]:
        print(f"REQUEST {row['method']} {row['resource_type']} {row['url']}")
        if row.get("post_data"):
            print("POST", row["post_data"][:3000])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
