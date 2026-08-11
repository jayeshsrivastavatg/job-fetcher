from __future__ import annotations

import os
import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from urllib.parse import urljoin, urlparse

from bs4 import BeautifulSoup
from playwright.sync_api import sync_playwright

from job_fetcher.models import Job
from job_fetcher.sources.base import JobSource
from job_fetcher.sources.browser_limit import BROWSER_SEMAPHORE
from job_fetcher.sources.generic_extract import clean_text, dedupe, extract_embedded_json, extract_jobs_from_json, extract_jsonld
from job_fetcher.sources.http_client import session, timeout_seconds


DETAIL_RE = re.compile(r"^/careers/job/(?P<id>\d+)(?:[/?#]|$)", re.I)
COUNT_RES = (
    re.compile(r"\b(?P<n>\d{1,5})\s+(?:openings|jobs|positions|results)\b", re.I),
    re.compile(r"\b(?P<n>\d{1,5})\s+matching jobs\b", re.I),
)
INDIA_RE = re.compile(
    r"\b(?:India|Bengaluru|Bangalore|Hyderabad|Gurugram|Gurgaon|Pune|Chennai|Noida|Mumbai|Delhi|Kolkata)\b",
    re.I,
)
DATE_RE = re.compile(r"\b20\d{2}-\d{2}-\d{2}\b")


class MicrosoftIndiaSource(JobSource):
    """Exhaustive public Microsoft India candidate-search adapter.

    Microsoft's branded India page intentionally shows only a few featured jobs and
    a `+N openings` link. The complete public candidate search is the Eightfold PCS
    page at apply.careers.microsoft.com. We therefore use that page as an index,
    restrict accepted links to concrete `/careers/job/<numeric id>` vacancies, keep
    scrolling/loading until the displayed result count is reconciled, then enrich
    every India vacancy from its public detail page. No login/private Eightfold API
    or challenge bypass is used.
    """

    SEARCH_URL = "https://apply.careers.microsoft.com/careers?domain=microsoft.com&hl=en&location=India"
    FEATURED_URL = "https://careers.microsoft.com/v2/global/en/locations/india.html"

    def fetch(self, company):
        src = company.get("source") or {}
        search_url = src.get("india_search_url") or self.SEARCH_URL
        jobs, expected_total = self._browser_index(company, search_url, src)

        # The branded India page independently exposes `+N openings`. It is useful
        # as a second completeness signal when the PCS result-count label changes.
        featured_total = self._featured_total()
        if featured_total is not None:
            expected_total = max(expected_total or 0, featured_total)

        jobs = dedupe(jobs)
        if not jobs:
            raise RuntimeError("microsoft_india_public_search_returned_no_jobs")
        if expected_total is not None and len(jobs) < expected_total:
            raise RuntimeError(
                f"microsoft_india_incomplete: public search advertises {expected_total} jobs but only {len(jobs)} canonical jobs were collected"
            )

        self._enrich_details(
            company,
            jobs,
            workers=max(1, min(16, int(src.get("detail_workers") or 8))),
        )
        complete = expected_total is not None and len(jobs) >= expected_total
        for job in jobs:
            raw = dict(job.raw or {})
            raw["_provider_total"] = expected_total
            raw["_provider_returned"] = len(jobs)
            raw["_provider_complete"] = complete
            job.raw = raw
        return jobs

    @classmethod
    def _browser_index(cls, company, search_url: str, src: dict):
        timeout_ms = int(src.get("browser_timeout_ms") or os.getenv("JOB_FETCHER_BROWSER_TIMEOUT_MS", "60000"))
        max_rounds = max(10, int(src.get("browser_scroll_rounds") or 80))
        jobs_by_id: dict[str, Job] = {}
        expected_total = None
        payloads = []

        with BROWSER_SEMAPHORE:
            with sync_playwright() as p:
                browser = p.chromium.launch(headless=True)
                context = browser.new_context(
                    user_agent="Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131 Safari/537.36",
                    locale="en-US",
                )
                page = context.new_page()

                def on_response(resp):
                    try:
                        ct = (resp.headers.get("content-type") or "").lower()
                        if "json" in ct:
                            payloads.append((resp.url, resp.json()))
                    except Exception:
                        pass

                page.on("response", on_response)
                page.goto(search_url, wait_until="domcontentloaded", timeout=timeout_ms)
                stable = 0
                previous = -1
                for _ in range(max_rounds):
                    page.wait_for_timeout(700)
                    text = page.locator("body").inner_text(timeout=5000)
                    displayed = cls._count_from_text(text)
                    if displayed is not None:
                        expected_total = max(expected_total or 0, displayed)
                    cls._collect_anchors(company, page, jobs_by_id)

                    # Prefer explicit load-more controls. PCS variants can use either
                    # a button or lazy loading/infinite scroll.
                    clicked = False
                    for label in ("Load more", "Show more jobs", "See more jobs", "View more"):
                        try:
                            node = page.get_by_text(label, exact=False)
                            if node.count() and node.first.is_visible() and node.first.is_enabled():
                                node.first.click(timeout=1500)
                                clicked = True
                                break
                        except Exception:
                            pass
                    page.evaluate("window.scrollTo(0, document.body ? document.body.scrollHeight : 0)")
                    current = len(jobs_by_id)
                    if expected_total is not None and current >= expected_total:
                        break
                    if current == previous and not clicked:
                        stable += 1
                    else:
                        stable = 0
                    if stable >= 7:
                        break
                    previous = current
                final_url = page.url
                browser.close()

        # Public search JSON is useful for fields that are not printed on the card.
        # It is additive only; canonical vacancy URLs remain mandatory.
        for response_url, payload in payloads:
            for job in extract_jobs_from_json(company, payload, final_url, "microsoft_india"):
                jid = cls._job_id(job.job_url) or cls._numeric_id(job.external_id)
                evidence = " ".join([
                    str(job.location or ""), str(job.description or ""), str(job.raw or ""),
                ])
                if not jid or not INDIA_RE.search(evidence):
                    continue
                job.external_id = jid
                job.job_url = f"https://apply.careers.microsoft.com/careers/job/{jid}?domain=microsoft.com&hl=en"
                raw = dict(job.raw or {})
                raw["_source_response_url"] = response_url
                raw["_fetch_via_browser"] = True
                job.raw = raw
                existing = jobs_by_id.get(jid)
                if existing is None or cls._richness(job) > cls._richness(existing):
                    jobs_by_id[jid] = job

        return list(jobs_by_id.values()), expected_total

    @classmethod
    def _collect_anchors(cls, company, page, jobs_by_id):
        try:
            anchors = page.locator('a[href*="/careers/job/"]')
            count = anchors.count()
        except Exception:
            return
        for i in range(count):
            try:
                anchor = anchors.nth(i)
                href = anchor.get_attribute("href") or ""
                absolute = urljoin(page.url, href)
                jid = cls._job_id(absolute)
                if not jid:
                    continue
                context = cls._dom_card_text(anchor)
                if not INDIA_RE.search(context or ""):
                    continue
                title = cls._dom_title(anchor, context)
                if not title:
                    continue
                location = cls._location(context)
                posted = None
                dm = DATE_RE.search(context or "")
                if dm:
                    posted = dm.group(0)
                job = Job(
                    company["id"], company["name"], "microsoft_india", jid, title,
                    location or "India", None,
                    f"https://apply.careers.microsoft.com/careers/job/{jid}?domain=microsoft.com&hl=en",
                    posted,
                    {"card_text": context, "_fetch_via_browser": True, "source_page": page.url},
                )
                existing = jobs_by_id.get(jid)
                if existing is None or cls._richness(job) > cls._richness(existing):
                    jobs_by_id[jid] = job
            except Exception:
                continue

    @staticmethod
    def _dom_card_text(anchor):
        try:
            return clean_text(anchor.evaluate("""el => {
              let n = el; let best = el.innerText || '';
              for (let i=0; i<8 && n; i++, n=n.parentElement) {
                const t = (n.innerText || '').trim();
                if (t.length >= best.length && t.length <= 2500) best = t;
                if (/India/i.test(t) && t.length > 40 && t.length < 1200) return t;
              }
              return best;
            }""")) or ""
        except Exception:
            return ""

    @staticmethod
    def _dom_title(anchor, context):
        try:
            text = clean_text(anchor.inner_text())
        except Exception:
            text = None
        if text and 3 < len(text) <= 220 and text.lower() not in {"apply", "apply now", "see details", "view job"}:
            return text
        if context:
            lines = [clean_text(x) for x in context.splitlines()]
            for line in lines:
                if line and 3 < len(line) <= 220 and not INDIA_RE.fullmatch(line):
                    if not re.match(r"^(?:India|Date posted|Work site|Job number|Save|Apply)", line, re.I):
                        return line
        return None

    @staticmethod
    def _count_from_text(text: str | None):
        if not text:
            return None
        values = []
        for pattern in COUNT_RES:
            values.extend(int(m.group("n")) for m in pattern.finditer(text))
        # Reject huge unrelated footer/analytics numbers.
        values = [x for x in values if 0 < x < 10000]
        return max(values) if values else None

    def _featured_total(self):
        try:
            response = session().get(self.FEATURED_URL, timeout=timeout_seconds(), allow_redirects=True)
            response.raise_for_status()
        except Exception:
            return None
        soup = BeautifulSoup(response.text, "html.parser")
        text = clean_text(soup.get_text(" ", strip=True)) or ""
        canonical_cards = len({self._job_id(urljoin(response.url, a.get("href") or "")) for a in soup.select('a[href*="apply.careers.microsoft.com"]') if self._job_id(urljoin(response.url, a.get("href") or ""))})
        # AEM prints the remainder as `+154 openings`; total = featured cards + N.
        match = re.search(r"\+\s*(\d{1,5})\s+openings", text, re.I)
        if match:
            return canonical_cards + int(match.group(1))
        return None

    @classmethod
    def _enrich_details(cls, company, jobs, workers=8):
        def detail(job):
            try:
                response = session().get(job.job_url, timeout=timeout_seconds(), allow_redirects=True)
                response.raise_for_status()
                return job, response.text, response.url, None
            except Exception as exc:
                return job, None, None, exc

        with ThreadPoolExecutor(max_workers=workers) as pool:
            futures = [pool.submit(detail, job) for job in jobs]
            for future in as_completed(futures):
                job, html, final_url, error = future.result()
                raw = dict(job.raw or {})
                if error is not None:
                    raw["_detail_fetch_error"] = f"{type(error).__name__}: {error}"
                    job.raw = raw
                    continue
                candidates = []
                candidates.extend(extract_jsonld(company, html, final_url, "microsoft_india"))
                candidates.extend(extract_embedded_json(company, html, final_url, "microsoft_india"))
                try:
                    stripped = (html or "").lstrip()
                    if stripped.startswith(("{", "[")):
                        import json
                        candidates.extend(extract_jobs_from_json(company, json.loads(stripped), final_url, "microsoft_india"))
                except Exception:
                    pass
                detail_job = max(candidates, key=cls._richness, default=None)
                if detail_job is not None:
                    job.title = clean_text(detail_job.title) or job.title
                    job.location = clean_text(detail_job.location) or job.location
                    job.description = clean_text(detail_job.description) or job.description
                    job.posted_at = clean_text(detail_job.posted_at) or job.posted_at
                if not job.description:
                    soup = BeautifulSoup(html, "html.parser")
                    main = soup.select_one("main") or soup.select_one("[class*='job-description']") or soup.body
                    text = clean_text(main.get_text(" ", strip=True)) if main else None
                    if text and len(text) >= 200:
                        job.description = text[:50000]
                raw["_detail_enriched"] = bool(job.description)
                job.raw = raw

    @staticmethod
    def _location(context: str | None):
        if not context:
            return None
        m = re.search(r"\bIndia(?:\s*,\s*[A-Za-z .'-]+){0,3}(?:\s*\+\d+\s+more)?\b", context, re.I)
        return clean_text(m.group(0)) if m else ("India" if INDIA_RE.search(context) else None)

    @staticmethod
    def _job_id(url):
        if not url:
            return None
        match = DETAIL_RE.match(urlparse(str(url)).path)
        return match.group("id") if match else None

    @staticmethod
    def _numeric_id(value):
        match = re.search(r"\b(\d{6,})\b", str(value or ""))
        return match.group(1) if match else None

    @staticmethod
    def _richness(job):
        return sum([
            4 if str(getattr(job, "description", "") or "").strip() else 0,
            2 if str(getattr(job, "location", "") or "").strip() else 0,
            1 if str(getattr(job, "title", "") or "").strip() else 0,
            1 if str(getattr(job, "posted_at", "") or "").strip() else 0,
        ])
