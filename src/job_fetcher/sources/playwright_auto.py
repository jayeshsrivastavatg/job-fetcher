from __future__ import annotations

import os
import re
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

BOT_RE = re.compile(r"(captcha|verify you are human|access denied|cloudflare|bot detection|unusual traffic)", re.I)


class PlaywrightAutoSource(JobSource):
    """Bounded public-browser fallback for dynamic career sites.

    It captures public JSON/XHR responses, scroll/lazy-load content, clicks common
    load-more controls and can advance through numbered/Next pagination. Browser
    concurrency remains separately bounded by BROWSER_SEMAPHORE.
    """

    def fetch(self, company):
        src = company.get("source", {})
        url = src.get("entry_url") or company["career_url"]
        payloads = []
        documents = []
        timeout_ms = int(src.get("browser_timeout_ms") or os.getenv("JOB_FETCHER_BROWSER_TIMEOUT_MS", "60000"))
        max_pages = max(1, int(src.get("browser_max_pages") or os.getenv("JOB_FETCHER_BROWSER_MAX_PAGES", "6")))
        max_scrolls = max(1, int(src.get("browser_max_scrolls") or os.getenv("JOB_FETCHER_BROWSER_MAX_SCROLLS", "10")))
        stable_scrolls = max(1, int(src.get("browser_stable_scrolls") or 2))
        load_more_clicks = max(0, int(src.get("browser_load_more_clicks") or os.getenv("JOB_FETCHER_BROWSER_LOAD_MORE_CLICKS", "10")))
        wait_ms = max(100, int(src.get("browser_wait_ms") or 1000))

        with BROWSER_SEMAPHORE:
            with sync_playwright() as p:
                browser = p.chromium.launch(headless=True)
                context = browser.new_context(
                    user_agent="Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/131 Safari/537.36",
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

                visited_urls = set()
                titles = []
                for page_index in range(max_pages):
                    page.wait_for_timeout(wait_ms)
                    current_url = page.url
                    # Do not stop only because the URL is unchanged: many SPA career
                    # sites paginate client-side while keeping the same route. The
                    # loop is bounded by browser_max_pages and document dedupe.
                    visited_urls.add(current_url)
                    titles.append(page.title())

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

        combined_head = " ".join(titles) + " " + " ".join(h[:3000] for h, _ in documents[:4])
        if BOT_RE.search(combined_head):
            raise RuntimeError("anti_bot_or_captcha: browser was challenged")

        jobs = []
        for response_url, payload in payloads:
            extracted = extract_jobs_from_json(company, payload, final_url)
            for job in extracted:
                raw = dict(job.raw or {})
                raw["_source_response_url"] = response_url
                raw["_fetch_via_browser"] = True
                job.raw = raw
            jobs.extend(extracted)
        for html, frame_url in documents:
            document_jobs = []
            document_jobs.extend(extract_jsonld(company, html, frame_url, "browser_jsonld"))
            document_jobs.extend(extract_embedded_json(company, html, frame_url, "browser_embedded_json"))
            document_jobs.extend(extract_html_links(company, html, frame_url, "browser_html"))
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
        if max_clicks <= 0:
            return
        labels = ("Load more", "Show more", "View more", "More jobs", "Show more jobs", "See more jobs")
        for _ in range(max_clicks):
            clicked = False
            for label in labels:
                try:
                    btn = page.get_by_text(label, exact=False)
                    if btn.count() and btn.first.is_visible() and btn.first.is_enabled():
                        btn.first.click(timeout=1500)
                        page.wait_for_timeout(650)
                        clicked = True
                        break
                except Exception:
                    pass
            if not clicked:
                break

    @staticmethod
    def _go_next(page, timeout_ms, visited_urls):
        candidates = []
        try:
            candidates.append(page.locator('a[rel~="next"]'))
            candidates.append(page.locator('a[aria-label*="next" i]'))
            candidates.append(page.locator('button[aria-label*="next" i]'))
            candidates.append(page.get_by_text("Next", exact=True))
            candidates.append(page.get_by_text("Next page", exact=False))
        except Exception:
            return False

        for locator in candidates:
            try:
                if not locator.count():
                    continue
                node = locator.first
                if not node.is_visible() or not node.is_enabled():
                    continue
                href = node.get_attribute("href")
                old_url = page.url
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
                # SPA pagination can keep the same URL; allow it once if content
                # changed. The outer loop snapshots and stops on stable repeats.
                return True
            except Exception:
                continue
        return False
