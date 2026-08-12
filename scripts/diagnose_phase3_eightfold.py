from __future__ import annotations

import json
import re
import time
from collections import Counter
from copy import deepcopy
from pathlib import Path
from urllib.parse import urlparse

from playwright.sync_api import sync_playwright

from job_fetcher.config import load_config
from job_fetcher.job_quality import prefer_usable_jobs
from job_fetcher.sources.factory import build_source

TARGETS = ("microsoft", "twilio", "morgan_stanley")


def clean(value):
    return re.sub(r"\s+", " ", str(value or "")).strip()


def summarize_json(value, depth=0):
    if depth >= 3:
        return type(value).__name__
    if isinstance(value, dict):
        out = {}
        for key, val in list(value.items())[:80]:
            out[key] = summarize_json(val, depth + 1)
        return out
    if isinstance(value, list):
        return {
            "_type": "list",
            "_len": len(value),
            "_sample": summarize_json(value[0], depth + 1) if value else None,
        }
    if isinstance(value, (str, int, float, bool)) or value is None:
        text = value
        if isinstance(text, str) and len(text) > 500:
            text = text[:500]
        return text
    return type(value).__name__


def app_snapshot(company):
    source = build_source(deepcopy(company))
    jobs = list(prefer_usable_jobs(source.fetch(deepcopy(company))) or [])
    return {
        "adapter": type(source).__name__,
        "count": len(jobs),
        "ids": [clean(getattr(j, "external_id", None)) for j in jobs],
        "sample": [
            {
                "id": clean(getattr(j, "external_id", None)),
                "title": clean(getattr(j, "title", None)),
                "location": clean(getattr(j, "location", None)),
                "url": clean(getattr(j, "job_url", None)),
                "source_type": clean(getattr(j, "source_type", None)),
            }
            for j in jobs[:20]
        ],
    }


def inspect(company, page):
    src = company.get("source") or {}
    url = src.get("entry_url") or company["career_url"]
    responses = []

    def on_response(resp):
        try:
            ctype = (resp.headers.get("content-type") or "").lower()
            low = resp.url.lower()
            if "json" not in ctype and not any(k in low for k in ("job", "career", "position", "search", "candidate", "graphql")):
                return
            item = {"url": resp.url, "status": resp.status, "content_type": ctype}
            if "json" in ctype:
                try:
                    payload = resp.json()
                    item["shape"] = summarize_json(payload)
                    body = json.dumps(payload, ensure_ascii=False)
                    item["preview"] = body[:12000]
                except Exception as exc:
                    item["parse_error"] = f"{type(exc).__name__}: {exc}"
            responses.append(item)
        except Exception:
            pass

    page.on("response", on_response)
    page.goto(url, wait_until="domcontentloaded", timeout=120000)
    page.wait_for_timeout(2500)

    counts = []
    for _ in range(10):
        try:
            text = clean(page.locator("body").inner_text(timeout=5000))
            match = re.findall(r"\b([0-9][0-9,]{0,8})\s+jobs?\b", text, flags=re.I)
            counts.append(match[:10])
        except Exception:
            pass
        try:
            page.evaluate("window.scrollTo(0, document.body ? document.body.scrollHeight : 0)")
        except Exception:
            pass
        page.wait_for_timeout(700)

    anchors = []
    loc = page.locator("a[href]")
    for i in range(min(loc.count(), 3000)):
        node = loc.nth(i)
        try:
            href = node.get_attribute("href") or ""
            text = clean(node.inner_text(timeout=500))
        except Exception:
            continue
        if any(k in href.lower() for k in ("career", "job", "position")):
            anchors.append({"text": text[:250], "href": href})

    return {
        "requested_url": url,
        "final_url": page.url,
        "title": page.title(),
        "visible_job_count_matches": counts,
        "anchor_count": len(anchors),
        "anchors": anchors[:100],
        "response_hosts": dict(Counter(urlparse(r["url"]).netloc for r in responses)),
        "responses": responses[-250:],
    }


def main():
    companies = {c["id"]: c for c in load_config().get("companies", [])}
    report = {"generated_at": time.time(), "companies": {}}

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        for cid in TARGETS:
            company = companies[cid]
            print(f"=== {company['name']} production ===", flush=True)
            try:
                app = app_snapshot(company)
                print(f"adapter={app['adapter']} count={app['count']}", flush=True)
                for row in app["sample"]:
                    print("APP", json.dumps(row, ensure_ascii=False), flush=True)
            except Exception as exc:
                app = {"error": f"{type(exc).__name__}: {exc}", "count": 0, "ids": [], "sample": []}
                print("APP_ERROR", app["error"], flush=True)

            context = browser.new_context(
                user_agent="Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/131 Safari/537.36",
                locale="en-US",
            )
            page = context.new_page()
            print(f"=== {company['name']} official careers network ===", flush=True)
            try:
                web = inspect(company, page)
                print("FINAL", web["final_url"], flush=True)
                print("COUNTS", web["visible_job_count_matches"], flush=True)
                print("HOSTS", json.dumps(web["response_hosts"], ensure_ascii=False), flush=True)
                for row in web["responses"]:
                    if row.get("shape"):
                        print("XHR", json.dumps({"url": row["url"], "status": row["status"], "shape": row["shape"]}, ensure_ascii=False)[:10000], flush=True)
            except Exception as exc:
                web = {"error": f"{type(exc).__name__}: {exc}"}
                print("WEB_ERROR", web["error"], flush=True)
            context.close()
            report["companies"][cid] = {"app": app, "official": web}
        browser.close()

    out = Path("reports/phase3-eightfold-diagnostics.json")
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")


if __name__ == "__main__":
    main()
