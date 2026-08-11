from __future__ import annotations

import json
import os
import re
from urllib.parse import parse_qs, urlencode, urljoin, urlparse, urlunparse

from bs4 import BeautifulSoup
from playwright.sync_api import sync_playwright

from job_fetcher.models import Job
from job_fetcher.sources.base import JobSource
from job_fetcher.sources.browser_limit import BROWSER_SEMAPHORE
from job_fetcher.sources.generic_extract import (
    clean_text,
    dedupe,
    extract_embedded_json,
    extract_html_links,
    extract_jobs_from_json,
    extract_jsonld,
    first,
    location_text,
    walk_objects,
)
from job_fetcher.sources.http_client import session, timeout_seconds


TOTAL_JOBS_RE = re.compile(r"\b([0-9][0-9,]{0,8})\s+(?:open\s+)?jobs?\b", re.I)
JOB_PATH_RE = re.compile(r"/careers/job/(?P<id>[0-9]+)", re.I)
BOT_RE = re.compile(r"(captcha|verify you are human|access denied|cloudflare|unusual traffic)", re.I)


class EightfoldSource(JobSource):
    """Public Eightfold Candidate Experience source.

    Eightfold's documented Positions API requires bearer-token authorization, so
    this source deliberately does *not* depend on that private/customer API. It
    consumes the public career experience instead:

      1. server-rendered HTML / JSON-LD / embedded application state;
      2. a bounded Playwright run that captures public JSON/XHR responses and
         scrolls the job list until it is complete or stable.

    ``career_url`` remains the canonical employer URL (for Twilio this is
    jobs.twilio.com), even when ``tenant`` supplies an Eightfold provider-domain
    fallback such as twilio.eightfold.ai.
    """

    @staticmethod
    def parse_eightfold_url(url: str) -> dict[str, str] | None:
        parsed = urlparse(url)
        host = parsed.netloc.lower().split(":", 1)[0]
        if host == "eightfold.ai":
            return {"host": host, "tenant": "app"}
        if not host.endswith(".eightfold.ai"):
            return None
        tenant = host[: -len(".eightfold.ai")]
        if not tenant:
            return None
        return {"host": host, "tenant": tenant.split(".")[-1]}

    def fetch(self, company):
        src = company.get("source") or {}
        entry = src.get("entry_url") or company["career_url"]
        candidates = [entry]
        provider = src.get("provider_url") or self._provider_url(src, entry)
        if provider and provider not in candidates:
            candidates.append(provider)

        static_jobs = []
        expected_total = None
        static_errors = []
        for url in candidates:
            try:
                jobs, total = self._fetch_static(company, url)
                static_jobs.extend(jobs)
                static_jobs = self._canonicalize(company, dedupe(static_jobs))
                if total is not None:
                    expected_total = max(expected_total or 0, total)
                if expected_total is not None and len(static_jobs) >= expected_total:
                    break
            except Exception as exc:
                static_errors.append(f"{url}: {exc}")

        static_jobs = self._canonicalize(company, dedupe(static_jobs))
        # If the page itself tells us the full count and static extraction reached
        # it, there is no reason to pay the browser cost.
        if static_jobs and expected_total is not None and len(static_jobs) >= expected_total:
            return static_jobs

        if src.get("disable_browser_fallback") or os.getenv("JOB_FETCHER_DISABLE_BROWSER", "0") == "1":
            if static_jobs:
                return static_jobs
            raise RuntimeError("eightfold_static_fetch_failed: " + "; ".join(static_errors))

        browser_errors = []
        best = list(static_jobs)
        for url in candidates:
            try:
                browser_jobs = self._fetch_browser(company, url, expected_total)
                merged = self._canonicalize(company, dedupe([*best, *browser_jobs]))
                if len(merged) > len(best):
                    best = merged
                if expected_total is not None and len(best) >= expected_total:
                    break
            except Exception as exc:
                browser_errors.append(f"{url}: {exc}")

        if best:
            return best
        raise RuntimeError(
            "eightfold_fetch_failed: static=" + ("; ".join(static_errors) or "no jobs")
            + "; browser=" + ("; ".join(browser_errors) or "no jobs")
        )

    def _fetch_static(self, company, url):
        client = session()
        response = client.get(url, timeout=timeout_seconds(), allow_redirects=True)
        response.raise_for_status()
        html = response.text
        if BOT_RE.search(html[:20000]):
            raise RuntimeError("anti_bot_or_captcha: static request was challenged")
        jobs = self._extract_document(company, html, response.url)
        total = self.extract_total_jobs(html)
        return jobs, total

    def _fetch_browser(self, company, url, expected_total=None):
        src = company.get("source") or {}
        timeout_ms = int(src.get("browser_timeout_ms") or os.getenv("JOB_FETCHER_BROWSER_TIMEOUT_MS", "60000"))
        max_scrolls = max(1, int(src.get("browser_max_scrolls") or os.getenv("JOB_FETCHER_EIGHTFOLD_MAX_SCROLLS", "60")))
        stable_target = max(2, int(src.get("browser_stable_scrolls") or 4))
        payloads = []

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
                        payloads.append(resp.json())
                    except Exception:
                        pass

                page.on("response", on_response)
                page.goto(url, wait_until="domcontentloaded", timeout=timeout_ms)
                page.wait_for_timeout(1500)
                if expected_total is None:
                    try:
                        expected_total = self.extract_total_jobs(page.locator("body").inner_text())
                    except Exception:
                        pass

                # Eightfold career pages commonly lazy-load more results while the
                # list is scrolled. Stop only after the discovered job-link count is
                # stable for several rounds, or the page-declared total is reached.
                stable = 0
                previous_count = -1
                for _ in range(max_scrolls):
                    try:
                        current_count = page.eval_on_selector_all(
                            'a[href*="/careers/job/"]',
                            "els => new Set(els.map(e => (e.href || '').split('?')[0])).size",
                        )
                    except Exception:
                        current_count = page.locator('a[href*="/careers/job/"]').count()
                    if expected_total is not None and current_count >= expected_total:
                        break
                    if current_count == previous_count:
                        stable += 1
                    else:
                        stable = 0
                    if stable >= stable_target:
                        break
                    previous_count = current_count
                    page.evaluate("window.scrollTo(0, document.body ? document.body.scrollHeight : 0)")
                    page.wait_for_timeout(550)

                    # Some tenant themes expose an explicit load-more control.
                    for label in ("Load more", "Show more", "More jobs", "Show more jobs", "See more jobs"):
                        try:
                            button = page.get_by_text(label, exact=False)
                            if button.count() and button.first.is_visible():
                                button.first.click(timeout=1000)
                                page.wait_for_timeout(500)
                                break
                        except Exception:
                            pass

                documents = []
                for frame in page.frames:
                    try:
                        documents.append((frame.content(), frame.url or page.url))
                    except Exception:
                        pass
                title = page.title()
                final_url = page.url
                browser.close()

        if BOT_RE.search(title + " " + " ".join(h[:3000] for h, _ in documents[:3])):
            raise RuntimeError("anti_bot_or_captcha: browser was challenged")

        jobs = []
        for payload in payloads:
            jobs.extend(self.extract_eightfold_payload(company, payload, final_url))
            jobs.extend(extract_jobs_from_json(company, payload, final_url, "eightfold_browser_json"))
        for html, frame_url in documents:
            jobs.extend(self._extract_document(company, html, frame_url))
        return dedupe(jobs)

    def _extract_document(self, company, html, base_url):
        jobs = []
        jobs.extend(extract_jsonld(company, html, base_url, "eightfold_jsonld"))
        jobs.extend(extract_embedded_json(company, html, base_url, "eightfold_embedded_json"))
        jobs.extend(extract_html_links(company, html, base_url, "eightfold_html"))

        # Parse any application/json script again with Eightfold-specific field
        # names (positionId/positionDisplayId/jobDescription), which the generic
        # detector intentionally treats conservatively.
        soup = BeautifulSoup(html, "html.parser")
        for node in soup.find_all("script"):
            typ = (node.get("type") or "").lower()
            ident = (node.get("id") or "").lower()
            if typ != "application/json" and ident not in {"__next_data__", "__nuxt_data__"}:
                continue
            raw = node.string or node.get_text() or ""
            if not raw.strip() or len(raw) > 12_000_000:
                continue
            try:
                payload = json.loads(raw)
            except Exception:
                continue
            jobs.extend(self.extract_eightfold_payload(company, payload, base_url))
        return dedupe(jobs)

    @staticmethod
    def extract_total_jobs(html: str) -> int | None:
        text = BeautifulSoup(html, "html.parser").get_text(" ", strip=True)
        matches = []
        for raw in TOTAL_JOBS_RE.findall(text[:250000]):
            try:
                matches.append(int(raw.replace(",", "")))
            except ValueError:
                pass
        # Pages can contain unrelated phrases such as "1 job" in navigation;
        # the largest declared count is the useful completeness target.
        return max(matches) if matches else None

    def extract_eightfold_payload(self, company, payload, base_url):
        jobs = []
        for obj in walk_objects(payload):
            if not isinstance(obj, dict):
                continue
            title = first(obj, "name", "title", "jobTitle", "positionTitle")
            position_id = first(obj, "positionId", "id", "jobId", "positionDisplayId", "atsJobId")
            # Eightfold's documented Position schema uses ``name`` and
            # ``jobDescription``; public career payloads may use title/jobTitle.
            if not isinstance(title, str) or not title.strip() or not position_id:
                continue
            if not any(k in obj for k in (
                "positionId", "positionDisplayId", "jobDescription", "location",
                "locations", "locationCountry", "atsJobId", "department",
            )):
                continue

            raw_url = first(obj, "jobUrl", "job_url", "url", "applyUrl", "externalPath")
            if raw_url:
                job_url = urljoin(base_url, str(raw_url))
            else:
                job_url = self._job_url(company, str(position_id))

            loc = first(obj, "location", "locations", "locationName", "locationCountry", "city")
            if isinstance(loc, dict):
                loc = location_text(loc)
            desc = first(obj, "jobDescription", "description", "descriptionPlain", "content")
            if isinstance(desc, str) and "<" in desc:
                desc = BeautifulSoup(desc, "html.parser").get_text(" ", strip=True)
            posted = first(obj, "datePosted", "postedAt", "posted_on", "createdAt", "publishedAt")

            jobs.append(Job(
                company_id=company["id"],
                company_name=company["name"],
                source_type="eightfold",
                external_id=clean_text(position_id),
                title=clean_text(title) or "",
                location=location_text(loc) if not isinstance(loc, str) else clean_text(loc),
                description=clean_text(desc),
                job_url=job_url,
                posted_at=clean_text(posted),
                raw=obj,
            ))
        return dedupe(jobs)

    def _canonicalize(self, company, jobs):
        out = []
        for job in jobs:
            job.source_type = "eightfold"
            # If extraction returned an Eightfold provider URL, keep the employer's
            # branded careers domain when the same position id can be identified.
            if job.job_url and ".eightfold.ai" in urlparse(job.job_url).netloc.lower():
                match = JOB_PATH_RE.search(urlparse(job.job_url).path)
                if match:
                    job.job_url = self._job_url(company, match.group("id"))
            out.append(job)
        return dedupe(out)

    @staticmethod
    def _job_url(company, position_id: str) -> str:
        src = company.get("source") or {}
        base = src.get("canonical_base_url")
        if not base:
            parsed = urlparse(company["career_url"])
            base = f"{parsed.scheme or 'https'}://{parsed.netloc}" if parsed.netloc else company["career_url"]
        template = src.get("canonical_job_path_template") or "careers/job/{id}"
        path = str(template).format(id=position_id).lstrip("/")
        return urljoin(base.rstrip("/") + "/", path)

    @staticmethod
    def _provider_url(src: dict, entry: str) -> str | None:
        tenant = clean_text(src.get("tenant"))
        if not tenant:
            parsed = EightfoldSource.parse_eightfold_url(entry)
            tenant = parsed.get("tenant") if parsed else None
        if not tenant or tenant == "app":
            return None
        query = parse_qs(urlparse(entry).query)
        flat = {k: v[-1] for k, v in query.items() if v}
        suffix = ("?" + urlencode(flat)) if flat else ""
        return f"https://{tenant}.eightfold.ai/careers{suffix}"
