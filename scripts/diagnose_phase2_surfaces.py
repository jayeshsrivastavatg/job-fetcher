from __future__ import annotations

import json
import re
import time
from collections import Counter
from copy import deepcopy
from pathlib import Path
from urllib.parse import urljoin, urlparse

from playwright.sync_api import sync_playwright

from job_fetcher.config import load_config
from job_fetcher.job_quality import prefer_usable_jobs
from job_fetcher.sources.factory import build_source

TARGETS = ("uber", "atlassian", "navi")


def clean(value):
    return re.sub(r"\s+", " ", str(value or "")).strip()


def app_snapshot(company):
    candidate = deepcopy(company)
    source = build_source(candidate)
    jobs = list(prefer_usable_jobs(source.fetch(candidate)) or [])
    return {
        "adapter": type(source).__name__,
        "count": len(jobs),
        "jobs": [
            {
                "id": clean(getattr(job, "external_id", None)),
                "title": clean(getattr(job, "title", None)),
                "location": clean(getattr(job, "location", None)),
                "url": clean(getattr(job, "job_url", None)),
                "source_type": clean(getattr(job, "source_type", None)),
            }
            for job in jobs
        ],
    }


def inspect_page(page, name: str):
    responses = []

    def on_response(resp):
        try:
            ctype = (resp.headers.get("content-type") or "").lower()
            url = resp.url
            low = url.lower()
            if "json" not in ctype and not any(k in low for k in ("job", "career", "search", "position", "requisition")):
                return
            item = {"url": url, "status": resp.status, "content_type": ctype}
            if "json" in ctype:
                try:
                    body = resp.text()
                    item["preview"] = body[:3000]
                except Exception as exc:
                    item["preview_error"] = f"{type(exc).__name__}: {exc}"
            responses.append(item)
        except Exception:
            pass

    page.on("response", on_response)

    if name == "uber":
        url = f"https://jobs.uber.com/en/jobs/?page=1&pagesize=100&audit={int(time.time())}"
    elif name == "atlassian":
        url = f"https://www.atlassian.com/company/careers/all-jobs?audit={int(time.time())}"
    else:
        url = f"https://navi.com/careers/jobs?audit={int(time.time())}"

    page.goto(url, wait_until="domcontentloaded", timeout=120000)
    page.wait_for_timeout(2500)
    for _ in range(8):
        try:
            page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
            page.wait_for_timeout(600)
        except Exception:
            break

    anchors = []
    loc = page.locator("a[href]")
    for i in range(min(loc.count(), 2500)):
        a = loc.nth(i)
        try:
            href = urljoin(page.url, a.get_attribute("href") or "")
            text = clean(a.inner_text(timeout=500))
        except Exception:
            continue
        low = href.lower()
        if any(k in low for k in ("/job", "/jobs", "career", "position", "requisition")):
            anchors.append({"text": text[:300], "href": href})

    body = ""
    try:
        body = clean(page.locator("body").inner_text(timeout=5000))[:12000]
    except Exception:
        pass

    return {
        "final_url": page.url,
        "title": page.title(),
        "anchors": anchors,
        "responses": responses[-300:],
        "response_hosts": dict(Counter(urlparse(r["url"]).netloc for r in responses)),
        "body_preview": body,
    }


def main():
    companies = {c["id"]: c for c in load_config().get("companies", [])}
    payload = {"generated_at": time.time(), "companies": {}}

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        for cid in TARGETS:
            company = companies[cid]
            print(f"=== {cid.upper()} APP ===", flush=True)
            try:
                app = app_snapshot(company)
                print(f"adapter={app['adapter']} count={app['count']}", flush=True)
                for row in app["jobs"][:20]:
                    print("APP", json.dumps(row, ensure_ascii=False), flush=True)
            except Exception as exc:
                app = {"error": f"{type(exc).__name__}: {exc}", "count": 0, "jobs": []}
                print("APP_ERROR", app["error"], flush=True)

            context = browser.new_context(
                user_agent="Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/131 Safari/537.36",
                locale="en-US",
            )
            page = context.new_page()
            print(f"=== {cid.upper()} OFFICIAL PAGE ===", flush=True)
            try:
                website = inspect_page(page, cid)
                print("FINAL_URL", website["final_url"], flush=True)
                print("TITLE", website["title"], flush=True)
                print("RESPONSE_HOSTS", json.dumps(website["response_hosts"], ensure_ascii=False), flush=True)
                print("ANCHORS", len(website["anchors"]), flush=True)
                for row in website["anchors"][:50]:
                    print("WEB", json.dumps(row, ensure_ascii=False), flush=True)
                for row in website["responses"][-80:]:
                    if row.get("preview"):
                        print("XHR", json.dumps(row, ensure_ascii=False)[:4500], flush=True)
            except Exception as exc:
                website = {"error": f"{type(exc).__name__}: {exc}"}
                print("WEB_ERROR", website["error"], flush=True)
            context.close()

            payload["companies"][cid] = {"app": app, "website": website}
        browser.close()

    out = Path("reports/phase2-diagnostics.json")
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")


if __name__ == "__main__":
    main()
