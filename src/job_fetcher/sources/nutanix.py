from __future__ import annotations

import math
import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from urllib.parse import parse_qsl, urlencode, urljoin, urlparse, urlunparse

from bs4 import BeautifulSoup

from job_fetcher.models import Job
from job_fetcher.sources.base import JobSource
from job_fetcher.sources.generic_extract import clean_text, dedupe, extract_jsonld
from job_fetcher.sources.http_client import session, timeout_seconds


JOB_RE = re.compile(r"^/en/jobs/(?P<id>[a-z]?\d+)/(?P<slug>[^/?#]+)/?$", re.I)
TOTAL_RE = re.compile(r"Displaying\s+\d+\s+to\s+\d+\s+of\s+(?P<total>\d+)\s+matching jobs", re.I)
INDIA_RE = re.compile(r"\b(?:India|Bangalore|Bengaluru|Pune|Hyderabad|Mumbai|Delhi|Gurugram|Gurgaon|Chennai|Noida)\b", re.I)


class NutanixSource(JobSource):
    """Exhaustive server-rendered Nutanix careers adapter.

    Nutanix publishes an explicit provider total and numbered 20-row pages. We only
    accept concrete `/en/jobs/<requisition>/<slug>/` links, paginate until the
    advertised total is covered, and enrich India vacancies from their public detail
    pages so the stored JD is the same candidate-facing content used to apply.
    """

    DEFAULT_URL = "https://careers.nutanix.com/en/jobs/"

    def fetch(self, company):
        src = company.get("source") or {}
        entry = src.get("entry_url") or self.DEFAULT_URL
        client = session()
        jobs = []
        total_seen = 0
        page = 1
        max_pages = int(src.get("max_pages") or 50)
        stagnant = 0
        while page <= max_pages:
            url = self._with_page(entry, page)
            response = client.get(url, timeout=timeout_seconds(), allow_redirects=True)
            response.raise_for_status()
            batch, total = self.parse_page(company, response.text, response.url)
            total_seen = max(total_seen, total or 0)
            before = len(jobs)
            jobs = dedupe([*jobs, *batch])
            stagnant = stagnant + 1 if len(jobs) == before else 0

            expected_pages = math.ceil(total_seen / 20) if total_seen else None
            if expected_pages and page >= expected_pages and len(jobs) >= total_seen:
                break
            if not batch or stagnant >= 2:
                break
            page += 1

        for job in jobs:
            raw = dict(job.raw or {})
            raw["_provider_total"] = total_seen or None
            raw["_provider_returned"] = len(jobs)
            raw["_provider_complete"] = bool(total_seen and len(jobs) >= total_seen)
            job.raw = raw

        if src.get("enrich_details", True):
            self._enrich_india(company, jobs, workers=max(1, min(12, int(src.get("detail_workers") or 6))))
        return jobs

    @classmethod
    def parse_page(cls, company, html: str, base_url: str):
        soup = BeautifulSoup(html, "html.parser")
        page_text = clean_text(soup.get_text(" ", strip=True)) or ""
        tm = TOTAL_RE.search(page_text)
        total = int(tm.group("total")) if tm else None
        jobs = []
        seen = set()
        for anchor in soup.select("a[href]"):
            absolute = urljoin(base_url, anchor.get("href") or "")
            match = JOB_RE.match(urlparse(absolute).path)
            if not match:
                continue
            jid = match.group("id")
            if jid in seen:
                continue
            title = clean_text(anchor.get_text(" ", strip=True))
            if not title or len(title) > 240:
                continue
            card_text = cls._card_text(anchor, title)
            location = cls._location(card_text, title)
            seen.add(jid)
            jobs.append(Job(
                company["id"], company["name"], "nutanix", jid, title, location,
                None, absolute, None, {"listing_text": card_text, "source_page": base_url},
            ))
        return dedupe(jobs), total

    @staticmethod
    def _card_text(anchor, title):
        node = anchor.parent
        best = title
        for _ in range(6):
            if node is None:
                break
            text = clean_text(node.get_text(" ", strip=True)) or ""
            if len(title) < len(text) <= 500:
                best = text
                # Nutanix cards include requisition, title, location and team.
                if re.search(r"\b(?:India|United States|Mexico|Canada|Japan|Korea|Singapore|Australia|Germany|France|UK|United Kingdom)\b", text, re.I):
                    break
            node = node.parent
        return best

    @staticmethod
    def _location(card_text, title):
        if not card_text:
            return None
        text = card_text.replace(title or "", " ")
        # Take the shortest comma-bearing line/fragment that carries a country.
        chunks = [clean_text(x) for x in re.split(r"[|•\n]", text)]
        candidates = [x for x in chunks if x and re.search(r"\b(?:India|United States|Mexico|Canada|Japan|Korea|Singapore|Australia|Germany|France|United Kingdom)\b", x, re.I)]
        return min(candidates, key=len) if candidates else None

    @staticmethod
    def _with_page(url: str, page: int):
        parsed = urlparse(url)
        query = dict(parse_qsl(parsed.query, keep_blank_values=True))
        if page <= 1:
            query.pop("page", None)
        else:
            query["page"] = str(page)
        return urlunparse(parsed._replace(query=urlencode(query)))

    @staticmethod
    def _enrich_india(company, jobs, workers=6):
        targets = [j for j in jobs if INDIA_RE.search(str(j.location or ""))]
        if not targets:
            return

        def fetch_detail(job):
            try:
                r = session().get(job.job_url, timeout=timeout_seconds(), allow_redirects=True)
                r.raise_for_status()
                return job, r.text, r.url, None
            except Exception as exc:
                return job, None, None, exc

        with ThreadPoolExecutor(max_workers=workers) as pool:
            futures = [pool.submit(fetch_detail, j) for j in targets]
            for future in as_completed(futures):
                job, html, final_url, error = future.result()
                raw = dict(job.raw or {})
                if error is not None:
                    raw["_detail_fetch_error"] = f"{type(error).__name__}: {error}"
                    job.raw = raw
                    continue
                structured = extract_jsonld(company, html, final_url, "nutanix")
                if structured:
                    detail = structured[0]
                    job.title = clean_text(detail.title) or job.title
                    job.location = clean_text(detail.location) or job.location
                    job.description = clean_text(detail.description) or job.description
                    job.posted_at = clean_text(detail.posted_at) or job.posted_at
                if not job.description:
                    soup = BeautifulSoup(html, "html.parser")
                    main = soup.select_one("main")
                    text = clean_text(main.get_text(" ", strip=True)) if main else None
                    if text and len(text) > 200:
                        job.description = text[:50000]
                raw["_detail_enriched"] = bool(job.description)
                job.raw = raw
