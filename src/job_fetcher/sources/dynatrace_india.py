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
from job_fetcher.sources.generic_extract import clean_text, dedupe, extract_embedded_json, extract_jsonld
from job_fetcher.sources.http_client import session, timeout_seconds


DETAIL_RE = re.compile(r"^/careers/jobs/(?P<id>\d+)/?$", re.I)
INDIA_RE = re.compile(r"\b(?:India|Bengaluru|Bangalore|Mumbai)\b", re.I)
CITY_COUNT_RE = re.compile(r"\b(?P<city>Bengaluru|Bangalore|Mumbai)\s+(?P<count>\d+)\s+jobs?\b", re.I)


class DynatraceIndiaSource(JobSource):
    """Fetch every current Dynatrace India vacancy and prove the India count.

    Dynatrace's first-party location overview publishes live job counts for its two
    India locations. We use that as an independent expected total, then load the
    public jobs application and accept only concrete `/careers/jobs/<id>/` cards
    carrying India/Bengaluru/Mumbai evidence. This prevents the global jobs landing
    page and other navigation objects from becoming vacancies.
    """

    LOCATIONS_URL = "https://www.dynatrace.com/careers/locations/"
    JOBS_URL = "https://www.dynatrace.com/careers/jobs/"

    def fetch(self, company):
        expected, city_counts = self._expected_india_total()
        if expected is None:
            raise RuntimeError("dynatrace_india_location_counts_unavailable")
        jobs = self._browser_jobs(company, company.get("source") or {})
        jobs = dedupe(jobs)
        if len(jobs) != expected:
            raise RuntimeError(
                f"dynatrace_india_incomplete: locations advertise {expected} jobs {city_counts}, collected {len(jobs)}"
            )
        self._enrich(company, jobs, workers=4)
        for job in jobs:
            raw = dict(job.raw or {})
            raw["_provider_total"] = expected
            raw["_provider_returned"] = len(jobs)
            raw["_provider_complete"] = True
            raw["_provider_city_counts"] = city_counts
            job.raw = raw
        return jobs

    @classmethod
    def _expected_india_total(cls):
        r = session().get(cls.LOCATIONS_URL, timeout=timeout_seconds(), allow_redirects=True)
        r.raise_for_status()
        soup = BeautifulSoup(r.text, "html.parser")
        text = clean_text(soup.get_text(" ", strip=True)) or ""
        counts = {}
        for match in CITY_COUNT_RE.finditer(text):
            city = match.group("city").lower()
            city = "bengaluru" if city == "bangalore" else city
            counts[city] = max(counts.get(city, 0), int(match.group("count")))
        if "bengaluru" not in counts and "mumbai" not in counts:
            return None, {}
        return sum(counts.values()), counts

    @classmethod
    def _browser_jobs(cls, company, src):
        timeout_ms = int(src.get("browser_timeout_ms") or os.getenv("JOB_FETCHER_BROWSER_TIMEOUT_MS", "60000"))
        max_rounds = max(10, int(src.get("browser_scroll_rounds") or 60))
        found: dict[str, Job] = {}
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
                        if "json" in (resp.headers.get("content-type") or "").lower():
                            payloads.append((resp.url, resp.json()))
                    except Exception:
                        pass
                page.on("response", on_response)
                page.goto(cls.JOBS_URL, wait_until="domcontentloaded", timeout=timeout_ms)
                stable = 0
                previous = -1
                for _ in range(max_rounds):
                    page.wait_for_timeout(650)
                    cls._collect_dom(company, page, found)
                    clicked = False
                    for label in ("Load more", "Show more", "View more", "More jobs", "Show more jobs"):
                        try:
                            node = page.get_by_text(label, exact=False)
                            if node.count() and node.first.is_visible() and node.first.is_enabled():
                                node.first.click(timeout=1500)
                                clicked = True
                                break
                        except Exception:
                            pass
                    page.evaluate("window.scrollTo(0, document.body ? document.body.scrollHeight : 0)")
                    current = len(found)
                    if current == previous and not clicked:
                        stable += 1
                    else:
                        stable = 0
                    if stable >= 6:
                        break
                    previous = current
                final_url = page.url
                browser.close()

        # Embedded/browser JSON can fill in fields, but it may only update a job
        # already proven by a canonical Dynatrace detail URL.
        for response_url, payload in payloads:
            for candidate in extract_embedded_json(company, f'<script type="application/json">{__import__("json").dumps(payload)}</script>', final_url, "dynatrace"):
                jid = cls._job_id(candidate.job_url)
                if not jid or jid not in found:
                    continue
                existing = found[jid]
                if candidate.location and INDIA_RE.search(str(candidate.location)):
                    existing.location = clean_text(candidate.location) or existing.location
                if candidate.description:
                    existing.description = clean_text(candidate.description)
                raw = dict(existing.raw or {})
                raw["_source_response_url"] = response_url
                raw["_fetch_via_browser"] = True
                existing.raw = raw
        return list(found.values())

    @classmethod
    def _collect_dom(cls, company, page, found):
        try:
            anchors = page.locator('a[href*="/careers/jobs/"]')
            count = anchors.count()
        except Exception:
            return
        for i in range(count):
            try:
                anchor = anchors.nth(i)
                absolute = urljoin(page.url, anchor.get_attribute("href") or "")
                jid = cls._job_id(absolute)
                if not jid:
                    continue
                context = clean_text(anchor.evaluate("""el => {
                  let n=el, best=(el.innerText||'');
                  for(let i=0;i<8 && n;i++,n=n.parentElement){
                    const t=(n.innerText||'').trim();
                    if(t.length>=best.length && t.length<=1400) best=t;
                    if(/India|Bengaluru|Bangalore|Mumbai/i.test(t) && t.length>30 && t.length<1000) return t;
                  }
                  return best;
                }""")) or ""
                if not INDIA_RE.search(context) or not re.search(r"\bIndia\b", context, re.I):
                    continue
                title = clean_text(anchor.inner_text())
                if not title or title.lower() in {"apply", "apply now", "view job", "learn more"}:
                    title = cls._title_from_context(context)
                if not title:
                    continue
                location = cls._location(context)
                found[jid] = Job(
                    company["id"], company["name"], "dynatrace", jid, title,
                    location or "India", None,
                    f"https://www.dynatrace.com/careers/jobs/{jid}/", None,
                    {"card_text": context, "_fetch_via_browser": True},
                )
            except Exception:
                continue

    @staticmethod
    def _title_from_context(context):
        for line in (context or "").splitlines():
            value = clean_text(line)
            if value and 3 < len(value) <= 220 and not INDIA_RE.fullmatch(value):
                if value.lower() not in {"full-time", "part-time", "hybrid", "office based", "remote"}:
                    return value
        return None

    @staticmethod
    def _location(context):
        if not context:
            return None
        city = re.search(r"\b(Bengaluru|Bangalore|Mumbai)\b", context, re.I)
        if city:
            return f"{clean_text(city.group(1))}, India"
        return "India" if re.search(r"\bIndia\b", context, re.I) else None

    @staticmethod
    def _job_id(url):
        if not url:
            return None
        match = DETAIL_RE.match(urlparse(str(url)).path)
        return match.group("id") if match else None

    @staticmethod
    def _enrich(company, jobs, workers=4):
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
                structured = extract_jsonld(company, html, final_url, "dynatrace")
                if structured:
                    d = structured[0]
                    job.title = clean_text(d.title) or job.title
                    job.location = clean_text(d.location) or job.location
                    job.description = clean_text(d.description) or job.description
                    job.posted_at = clean_text(d.posted_at) or job.posted_at
                if not job.description:
                    soup = BeautifulSoup(html, "html.parser")
                    main = soup.select_one("main") or soup.body
                    text = clean_text(main.get_text(" ", strip=True)) if main else None
                    if text and len(text) >= 180:
                        job.description = text[:50000]
                raw["_detail_enriched"] = bool(job.description)
                job.raw = raw
