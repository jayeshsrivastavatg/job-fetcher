from __future__ import annotations

import os
import re
from urllib.parse import parse_qs, urlencode, urljoin, urlparse, urlunparse

from bs4 import BeautifulSoup

from job_fetcher.models import Job
from job_fetcher.sources.base import JobSource
from job_fetcher.sources.generic_extract import clean_text, dedupe
from job_fetcher.sources.http_client import session, timeout_seconds
from job_fetcher.sources.playwright_auto import PlaywrightAutoSource

DETAIL_RE = re.compile(r"/(?:[a-z]{2}-[a-z]{2}/)?details/(?P<id>[0-9]+(?:-[0-9]+)?)/", re.I)
DATE_RE = re.compile(r"\b(?:0?[1-9]|[12][0-9]|3[01])\s+(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)\s+20\d{2}\b", re.I)
LOCATION_RE = re.compile(r"\bLocation\s+(.+?)(?=\s+(?:Actions|Role Number:|Weekly Hours:|Submit CV|$))", re.I)


class AppleSource(JobSource):
    """Apple's public server-rendered jobs search.

    Apple exposes stable search pagination (?page=N) and public detail URLs under
    /details/<role-number>/<slug>. This adapter stays on those public pages and
    uses the generic browser extractor only as a last resort.
    """

    def fetch(self, company):
        src = company.get("source") or {}
        entry = src.get("entry_url") or company["career_url"]
        max_pages = max(1, int(src.get("max_pages") or 50))
        client = session()
        jobs = []
        seen = set()
        errors = []

        for page in range(1, max_pages + 1):
            url = self.with_page(entry, page)
            try:
                r = client.get(url, timeout=timeout_seconds(), allow_redirects=True)
                r.raise_for_status()
            except Exception as exc:
                errors.append(f"page {page}: {exc}")
                break
            batch = self.parse_search_page(company, r.text, r.url)
            new = [j for j in batch if j.external_id not in seen]
            if page > 1 and not new:
                break
            for j in new:
                seen.add(j.external_id)
                jobs.append(j)
            if not batch:
                break

        jobs = dedupe(jobs)
        if jobs:
            return jobs
        if src.get("disable_browser_fallback") or os.getenv("JOB_FETCHER_DISABLE_BROWSER", "0") == "1":
            raise RuntimeError("apple_static_fetch_failed: " + ("; ".join(errors) or "no public job cards found"))
        c = dict(company)
        c["source"] = dict(src)
        c["source"]["entry_url"] = entry
        browser_jobs = PlaywrightAutoSource().fetch(c)
        if browser_jobs:
            for j in browser_jobs:
                j.source_type = "apple"
            return dedupe(browser_jobs)
        raise RuntimeError("apple_no_jobs_detected")

    @staticmethod
    def with_page(url: str, page: int) -> str:
        parsed = urlparse(url)
        query = parse_qs(parsed.query, keep_blank_values=True)
        query["page"] = [str(page)]
        flat = []
        for k, vals in query.items():
            for v in vals:
                flat.append((k, v))
        return urlunparse(parsed._replace(query=urlencode(flat)))

    @staticmethod
    def parse_search_page(company, html: str, base_url: str):
        soup = BeautifulSoup(html, "html.parser")
        jobs = []
        seen = set()
        for a in soup.select('a[href*="/details/"]'):
            href = a.get("href") or ""
            url = urljoin(base_url, href)
            m = DETAIL_RE.search(urlparse(url).path)
            if not m:
                continue
            external_id = m.group("id")
            if external_id in seen:
                continue
            title = clean_text(a.get_text(" ", strip=True))
            if not title or title.lower() in {"see full role description", "submit cv", "share"}:
                # Apple frequently duplicates the detail link under an action label.
                parent = a.parent
                title = None
                for _ in range(5):
                    if parent is None:
                        break
                    heading = parent.find(["h2", "h3", "h4", "h5"])
                    if heading:
                        candidate = clean_text(heading.get_text(" ", strip=True))
                        if candidate and candidate.lower() not in {"actions"}:
                            title = candidate
                            break
                    parent = parent.parent
            if not title:
                continue

            card = a.parent
            for _ in range(5):
                if card is None:
                    break
                text = clean_text(card.get_text(" ", strip=True)) or ""
                if "Role Number:" in text or "Location " in text:
                    break
                card = card.parent
            text = clean_text(card.get_text(" ", strip=True)) if card is not None else ""
            loc = None
            lm = LOCATION_RE.search(text or "")
            if lm:
                loc = clean_text(lm.group(1))
            dm = DATE_RE.search(text or "")
            posted = clean_text(dm.group(0)) if dm else None
            seen.add(external_id)
            jobs.append(Job(
                company_id=company["id"], company_name=company["name"], source_type="apple",
                external_id=external_id, title=title, location=loc, description=None,
                job_url=url, posted_at=posted, raw={"card_text": text},
            ))
        return dedupe(jobs)
