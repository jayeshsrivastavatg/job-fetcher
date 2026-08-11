from __future__ import annotations

import os
from urllib.parse import urljoin

from playwright.sync_api import sync_playwright

from job_fetcher.sources.base import JobSource
from job_fetcher.sources.browser_limit import BROWSER_SEMAPHORE
from job_fetcher.sources.generic_extract import (
    dedupe,
    extract_embedded_json,
    extract_html_links,
    extract_jobs_from_json,
    extract_jsonld,
)
from job_fetcher.sources.official_html import visible_challenge


class RecoveryBrowserSource(JobSource):
    """Browser fallback that only flags a *visible* challenge.

    This deliberately does not solve CAPTCHAs or bypass challenge pages. It fixes a
    false-positive in older logic where merely loading dormant captcha JavaScript
    caused normal public career pages to be rejected as bot challenges.
    """

    def fetch(self, company):
        src = company.get("source") or {}
        url = src.get("entry_url") or company["career_url"]
        payloads = []
        documents = []
        timeout_ms = int(src.get("browser_timeout_ms") or os.getenv("JOB_FETCHER_BROWSER_TIMEOUT_MS", "60000"))
        max_pages = max(1, int(src.get("browser_max_pages") or 12))
        max_scrolls = max(1, int(src.get("browser_max_scrolls") or 16))
        stable_scrolls = max(1, int(src.get("browser_stable_scrolls") or 3))
        load_more_clicks = max(0, int(src.get("browser_load_more_clicks") or 15))
        wait_ms = max(150, int(src.get("browser_wait_ms") or 1200))

        with BROWSER_SEMAPHORE:
            with sync_playwright() as p:
                browser = p.chromium.launch(headless=True)
                context = browser.new_context(
                    user_agent="Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
                    locale=str(src.get("locale") or "en-US"),
                )
                page = context.new_page()

                def on_response(resp):
                    try:
                        ct = (resp.headers.get("content-type") or "").lower()
                        if "json" not in ct:
                            return
                        length = int(resp.headers.get("content-length") or "0")
                        if length and length > 12_000_000:
                            return
                        payloads.append((resp.url, resp.json()))
                    except Exception:
                        pass

                page.on("response", on_response)
                page.goto(url, wait_until="domcontentloaded", timeout=timeout_ms)
                titles = []
                visited_urls = set()
                for page_index in range(max_pages):
                    page.wait_for_timeout(wait_ms)
                    titles.append(page.title())
                    current_url = page.url
                    visited_urls.add(current_url)

                    self._scroll_until_stable(page, max_scrolls, stable_scrolls)
                    self._click_load_more(page, load_more_clicks)
                    self._scroll_until_stable(page, max_scrolls, stable_scrolls)
                    self._snapshot_documents(page, documents)

                    if page_index >= max_pages - 1:
                        break
                    if not self._go_next(page, timeout_ms, visited_urls):
                        break
                final_url = page.url
                browser.close()

        combined_html = "\n".join(html for html, _ in documents[:4])
        if visible_challenge(combined_html, " ".join(titles)):
            raise RuntimeError("anti_bot_or_captcha: browser presented a visible challenge")

        jobs = []
        for response_url, payload in payloads:
            extracted = extract_jobs_from_json(company, payload, final_url, "recovery_browser_json")
            for job in extracted:
                raw = dict(job.raw or {})
                raw["_source_response_url"] = response_url
                raw["_fetch_via_browser"] = True
                job.raw = raw
            jobs.extend(extracted)
        for html, frame_url in documents:
            document_jobs = []
            document_jobs.extend(extract_jsonld(company, html, frame_url, "recovery_browser_jsonld"))
            document_jobs.extend(extract_embedded_json(company, html, frame_url, "recovery_browser_embedded_json"))
            document_jobs.extend(extract_html_links(company, html, frame_url, "recovery_browser_html"))
            for job in document_jobs:
                raw = dict(job.raw or {})
                raw["_fetch_via_browser"] = True
                job.raw = raw
            jobs.extend(document_jobs)
        return dedupe(jobs)

    @staticmethod
    def _snapshot_documents(page, documents):
        seen = {(u, hash(h)) for h, u in documents}
        for frame in page.frames:
            try:
                item = (frame.content(), frame.url or page.url)
            except Exception:
                continue
            key = (item[1], hash(item[0]))
            if key not in seen:
                seen.add(key)
                documents.append(item)

    @staticmethod
    def _scroll_until_stable(page, max_scrolls, stable_target):
        previous_height = -1
        stable = 0
        for _ in range(max_scrolls):
            try:
                height = page.evaluate("document.body ? document.body.scrollHeight : 0")
                page.evaluate("window.scrollTo(0, document.body ? document.body.scrollHeight : 0)")
                page.wait_for_timeout(450)
            except Exception:
                break
            if height == previous_height:
                stable += 1
            else:
                stable = 0
            if stable >= stable_target:
                break
            previous_height = height

    @staticmethod
    def _click_load_more(page, max_clicks):
        labels = ("Load more", "Show more", "View more", "More jobs", "Show more jobs", "See more jobs")
        for _ in range(max_clicks):
            clicked = False
            for label in labels:
                try:
                    locator = page.get_by_text(label, exact=False)
                    if locator.count() and locator.first.is_visible() and locator.first.is_enabled():
                        locator.first.click(timeout=1500)
                        page.wait_for_timeout(650)
                        clicked = True
                        break
                except Exception:
                    pass
            if not clicked:
                break

    @staticmethod
    def _go_next(page, timeout_ms, visited_urls):
        try:
            candidates = [
                page.locator('a[rel~="next"]'),
                page.locator('a[aria-label*="next" i]'),
                page.locator('button[aria-label*="next" i]'),
                page.get_by_text("Next", exact=True),
                page.get_by_text("Next page", exact=False),
            ]
        except Exception:
            return False

        for locator in candidates:
            try:
                if not locator.count():
                    continue
                node = locator.first
                if not node.is_visible() or not node.is_enabled():
                    continue
                old_url = page.url
                href = node.get_attribute("href")
                if href:
                    target = urljoin(old_url, href)
                    if target in visited_urls or target == old_url:
                        continue
                    page.goto(target, wait_until="domcontentloaded", timeout=timeout_ms)
                    return True
                node.click(timeout=2000)
                try:
                    page.wait_for_load_state("domcontentloaded", timeout=min(timeout_ms, 12000))
                except Exception:
                    pass
                page.wait_for_timeout(700)
                return True
            except Exception:
                continue
        return False
