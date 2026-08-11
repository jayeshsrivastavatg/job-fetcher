from __future__ import annotations

import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from urllib.parse import urljoin, urlparse

from bs4 import BeautifulSoup

from job_fetcher.models import Job
from job_fetcher.sources.base import JobSource
from job_fetcher.sources.generic_extract import clean_text, dedupe
from job_fetcher.sources.http_client import session, timeout_seconds


COUNT_RE = re.compile(r"^(?P<count>\d+)\s+Positions?$", re.I)
GENERIC = {"view openings", "open positions", "current openings", "apply", "apply now"}


class MediaNetSource(JobSource):
    """Exhaustive first-party Media.net careers crawler.

    Media.net's careers home page publishes the open-position count for each
    discipline. Each non-zero count links to a server-rendered category page whose
    `Current Openings` section links directly to full first-party job descriptions.
    We reconcile discovered detail links with the sum of advertised category counts.
    """

    DEFAULT_URL = "https://careers.media.net/"

    def fetch(self, company):
        entry = (company.get("source") or {}).get("entry_url") or self.DEFAULT_URL
        client = session()
        response = client.get(entry, timeout=timeout_seconds(), allow_redirects=True)
        response.raise_for_status()
        category_urls, advertised_total = self._category_urls(response.text, response.url)
        jobs = []
        for category_url in category_urls:
            r = client.get(category_url, timeout=timeout_seconds(), allow_redirects=True)
            r.raise_for_status()
            jobs.extend(self._category_jobs(company, r.text, r.url))
        jobs = dedupe(jobs)

        complete = advertised_total == len(jobs)
        for job in jobs:
            raw = dict(job.raw or {})
            raw["_provider_total"] = advertised_total
            raw["_provider_returned"] = len(jobs)
            raw["_provider_complete"] = complete
            job.raw = raw

        self._enrich(jobs, workers=5)
        return jobs

    @classmethod
    def _category_urls(cls, html: str, base_url: str):
        soup = BeautifulSoup(html, "html.parser")
        category_urls = []
        total = 0
        seen = set()
        for anchor in soup.select("a[href]"):
            text = clean_text(anchor.get_text(" ", strip=True)) or ""
            match = COUNT_RE.match(text)
            if not match:
                continue
            count = int(match.group("count"))
            total += count
            if count <= 0:
                continue
            url = urljoin(base_url, anchor.get("href") or "")
            if urlparse(url).netloc.lower() != urlparse(base_url).netloc.lower():
                continue
            if url not in seen:
                seen.add(url)
                category_urls.append(url)
        return category_urls, total

    @staticmethod
    def _category_jobs(company, html: str, base_url: str):
        soup = BeautifulSoup(html, "html.parser")
        heading = soup.find(lambda tag: tag.name in {"h1", "h2", "h3"} and "current openings" in (tag.get_text(" ", strip=True) or "").lower())
        container = heading.parent if heading is not None else soup
        # Category pages are small; accept same-host deeper links after the category
        # path but reject navigation back to sibling category pages.
        base = urlparse(base_url)
        category_path = base.path.rstrip("/") + "/"
        jobs = []
        seen = set()
        for anchor in container.find_all("a", href=True):
            title = clean_text(anchor.get_text(" ", strip=True))
            if not title or title.lower() in GENERIC or len(title) > 220:
                continue
            url = urljoin(base_url, anchor.get("href"))
            parsed = urlparse(url)
            if parsed.netloc.lower() != base.netloc.lower() or url in seen:
                continue
            path = parsed.path.rstrip("/") + "/"
            if not path.startswith(category_path) or path == category_path:
                continue
            seen.add(url)
            jobs.append(Job(
                company["id"], company["name"], "media_net", url, title,
                None, None, url, None, {"category_page": base_url},
            ))
        return jobs

    @staticmethod
    def _enrich(jobs, workers=5):
        def detail(job):
            try:
                r = session().get(job.job_url, timeout=timeout_seconds(), allow_redirects=True)
                r.raise_for_status()
                return job, r.text, None
            except Exception as exc:
                return job, None, exc

        with ThreadPoolExecutor(max_workers=workers) as pool:
            futures = [pool.submit(detail, job) for job in jobs]
            for future in as_completed(futures):
                job, html, error = future.result()
                raw = dict(job.raw or {})
                if error is not None:
                    raw["_detail_fetch_error"] = f"{type(error).__name__}: {error}"
                    job.raw = raw
                    continue
                soup = BeautifulSoup(html, "html.parser")
                heading = soup.find(["h1", "h2"], string=re.compile(re.escape(job.title), re.I))
                if heading:
                    job.title = clean_text(heading.get_text(" ", strip=True)) or job.title
                main = soup.select_one("main") or soup.select_one(".entry-content") or soup.select_one("article") or soup.body
                text = clean_text(main.get_text(" ", strip=True)) if main else None
                if text and len(text) >= 180:
                    job.description = text[:50000]
                # Media.net roles are not consistently tagged with a structured
                # location. Keep it unknown rather than inventing India evidence.
                raw["_detail_enriched"] = bool(job.description)
                job.raw = raw
