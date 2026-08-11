from __future__ import annotations

import os
import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from urllib.parse import parse_qsl, urlencode, urljoin, urlparse, urlunparse

from bs4 import BeautifulSoup
from playwright.sync_api import sync_playwright

from job_fetcher.models import Job
from job_fetcher.sources.base import JobSource
from job_fetcher.sources.browser_limit import BROWSER_SEMAPHORE
from job_fetcher.sources.generic_extract import clean_text, dedupe, extract_jsonld
from job_fetcher.sources.http_client import session, timeout_seconds


DETAIL_RE = re.compile(r"/searchjobs/JobDetail/(?:[^/?#]+/)?(?P<id>\d+)(?:[/?#]|$)", re.I)
RANGE_RE = re.compile(r"(?P<start>\d+)\s*[-–—]\s*(?P<end>\d+)\s+of\s+(?P<total>\d+)\s+results", re.I)
INDIA_RE = re.compile(r"\b(?:India|Bangalore|Bengaluru|Gurugram|Gurgaon|Mumbai|Pune|Hyderabad|Chennai|Noida|Delhi|Kolkata)\b", re.I)
JOB_ID_RE = re.compile(r"Job ID:\s*(?P<id>\d+)", re.I)


class SiemensHealthineersSource(JobSource):
    """Exhaustively scan Siemens Healthineers' first-party public job index.

    The public search publishes an exact global total and an explicit offset/range.
    We traverse every range, retain only cards with India evidence, and only accept
    concrete `/searchjobs/JobDetail/.../<id>` vacancies. Completeness is proven by
    covering the advertised global range, not by trusting a nonzero page-one count.
    """

    SEARCH_URL = "https://jobs.siemens-healthineers.com/en_US/searchjobs/SearchJobs/"

    def fetch(self, company):
        src = company.get("source") or {}
        jobs, global_total, scanned_to = self._browser_index(company, src)
        jobs = dedupe(jobs)
        if global_total is None or scanned_to < global_total:
            raise RuntimeError(
                f"siemens_healthineers_incomplete_global_scan: total={global_total} scanned_to={scanned_to} india_jobs={len(jobs)}"
            )
        for job in jobs:
            raw = dict(job.raw or {})
            raw["_global_provider_total"] = global_total
            raw["_global_provider_scanned"] = scanned_to
            raw["_provider_total"] = len(jobs)
            raw["_provider_returned"] = len(jobs)
            raw["_provider_complete"] = True
            job.raw = raw
        self._enrich(company, jobs, workers=max(1, min(10, int(src.get("detail_workers") or 5))))
        return jobs

    @classmethod
    def _browser_index(cls, company, src):
        timeout_ms = int(src.get("browser_timeout_ms") or os.getenv("JOB_FETCHER_BROWSER_TIMEOUT_MS", "60000"))
        requested_page_size = max(20, min(200, int(src.get("page_size") or 100)))
        jobs = []
        global_total = None
        scanned_to = 0

        with BROWSER_SEMAPHORE:
            with sync_playwright() as p:
                browser = p.chromium.launch(headless=True)
                context = browser.new_context(
                    user_agent="Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131 Safari/537.36",
                    locale="en-US",
                )
                page = context.new_page()
                page.goto(cls.SEARCH_URL, wait_until="domcontentloaded", timeout=timeout_ms)
                page.wait_for_timeout(1200)
                base_url = page.url

                offset = 0
                seen_offsets = set()
                for _ in range(100):
                    if offset in seen_offsets:
                        break
                    seen_offsets.add(offset)
                    target = cls._page_url(base_url, offset, requested_page_size)
                    if page.url != target:
                        page.goto(target, wait_until="domcontentloaded", timeout=timeout_ms)
                    page.wait_for_timeout(600)
                    html = page.content()
                    body = clean_text(page.locator("body").inner_text(timeout=5000)) or ""
                    rm = RANGE_RE.search(body)
                    if not rm:
                        # The site can briefly render the shell before the result
                        # fragment. Give it one bounded retry.
                        page.wait_for_timeout(1400)
                        html = page.content()
                        body = clean_text(page.locator("body").inner_text(timeout=5000)) or ""
                        rm = RANGE_RE.search(body)
                    if not rm:
                        browser.close()
                        raise RuntimeError(f"siemens_healthineers_result_range_missing: {page.url}")
                    start = int(rm.group("start"))
                    end = int(rm.group("end"))
                    total = int(rm.group("total"))
                    global_total = max(global_total or 0, total)
                    scanned_to = max(scanned_to, end)
                    jobs.extend(cls.parse_page(company, html, page.url))
                    if end >= total:
                        break
                    step = max(1, end - start + 1)
                    offset = end  # zero-based next offset equals displayed 1-based end
                browser.close()
        return dedupe(jobs), global_total, scanned_to

    @classmethod
    def parse_page(cls, company, html: str, base_url: str):
        soup = BeautifulSoup(html, "html.parser")
        out, seen = [], set()
        for anchor in soup.select("a[href]"):
            absolute = urljoin(base_url, anchor.get("href") or "")
            match = DETAIL_RE.search(urlparse(absolute).path)
            if not match:
                continue
            jid = match.group("id")
            if jid in seen:
                continue
            card = cls._card(anchor)
            context = clean_text(card.get_text(" ", strip=True)) if card else ""
            if not INDIA_RE.search(context or "") or not re.search(r"\bIndia\b", context or "", re.I):
                continue
            title = cls._title(anchor, card)
            if not title:
                continue
            location = cls._location(context, title)
            seen.add(jid)
            out.append(Job(
                company["id"], company["name"], "siemens_healthineers", jid, title,
                location or "India", None, absolute, None,
                {"listing_text": context, "source_page": base_url},
            ))
        return dedupe(out)

    @staticmethod
    def _card(anchor):
        node = anchor
        fallback = anchor.parent
        for _ in range(7):
            node = getattr(node, "parent", None)
            if node is None:
                break
            text = clean_text(node.get_text(" ", strip=True)) or ""
            if JOB_ID_RE.search(text) and 20 <= len(text) <= 900:
                return node
            if len(text) <= 1200:
                fallback = node
        return fallback

    @staticmethod
    def _title(anchor, card):
        value = clean_text(anchor.get_text(" ", strip=True))
        if value and value.lower() not in {"learn more", "view job", "apply"} and 3 < len(value) <= 240:
            return value
        if card:
            for node in card.find_all(["h2", "h3", "h4"], recursive=True):
                value = clean_text(node.get_text(" ", strip=True))
                if value and 3 < len(value) <= 240:
                    return value
        return None

    @staticmethod
    def _location(context, title):
        if not context:
            return None
        text = context.replace(title or "", " ")
        # Cards render City State India before the Job ID/field-of-work fields.
        before = JOB_ID_RE.split(text, maxsplit=1)[0]
        match = re.search(
            r"\b(?:Bangalore|Bengaluru|Gurugram|Gurgaon|Mumbai|Pune|Hyderabad|Chennai|Noida|Delhi|Kolkata)(?:\s+[A-Za-z]+){0,3}\s+India\b",
            before, re.I,
        )
        return clean_text(match.group(0)) if match else "India"

    @staticmethod
    def _page_url(base_url: str, offset: int, size: int):
        parsed = urlparse(base_url)
        query = dict(parse_qsl(parsed.query, keep_blank_values=True))
        query["folderOffset"] = str(offset)
        query["folderRecordsPerPage"] = str(size)
        return urlunparse(parsed._replace(query=urlencode(query)))

    @staticmethod
    def _enrich(company, jobs, workers=5):
        def detail(job):
            try:
                r = session().get(job.job_url, timeout=timeout_seconds(), allow_redirects=True)
                r.raise_for_status()
                return job, r.text, r.url, None
            except Exception as exc:
                return job, None, None, exc
        with ThreadPoolExecutor(max_workers=workers) as pool:
            futures = [pool.submit(detail, j) for j in jobs]
            for future in as_completed(futures):
                job, html, final_url, error = future.result()
                raw = dict(job.raw or {})
                if error is not None:
                    raw["_detail_fetch_error"] = f"{type(error).__name__}: {error}"
                    job.raw = raw
                    continue
                structured = extract_jsonld(company, html, final_url, "siemens_healthineers")
                if structured:
                    d = structured[0]
                    job.title = clean_text(d.title) or job.title
                    job.location = clean_text(d.location) or job.location
                    job.description = clean_text(d.description) or job.description
                    job.posted_at = clean_text(d.posted_at) or job.posted_at
                if not job.description:
                    soup = BeautifulSoup(html, "html.parser")
                    main = soup.select_one("main") or soup.select_one("[class*='job-description']") or soup.body
                    text = clean_text(main.get_text(" ", strip=True)) if main else None
                    if text and len(text) >= 180:
                        job.description = text[:50000]
                raw["_detail_enriched"] = bool(job.description)
                job.raw = raw
