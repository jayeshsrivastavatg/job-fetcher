from __future__ import annotations

from urllib.parse import urlparse

from job_fetcher.sources.generic_extract import dedupe
from job_fetcher.sources.http_client import session, timeout_seconds
from job_fetcher.sources.official_html import OfficialHtmlSource, visible_challenge


class SlowOfficialHtmlSource(OfficialHtmlSource):
    """OfficialHtmlSource variant for unusually slow first-party/ATS portals.

    Some India career portals are reachable and indexed but regularly exceed the
    project's normal 30-second HTTP deadline. This adapter keeps the same bounded
    parser/pagination rules while allowing a per-recovery timeout. It is not an
    anti-bot bypass and does not change retry policy or authentication behavior.
    """

    def _fetch_url(self, company, entry: str, src: dict):
        client = session()
        max_pages = max(1, int(src.get("max_pages") or 25))
        request_timeout = max(
            timeout_seconds(),
            float(src.get("http_timeout_seconds") or timeout_seconds()),
        )
        current = entry
        seen_pages: set[str] = set()
        jobs = []

        for _ in range(max_pages):
            if not current or current in seen_pages:
                break
            seen_pages.add(current)
            response = client.get(current, timeout=request_timeout, allow_redirects=True)
            response.raise_for_status()
            if visible_challenge(response.text):
                raise RuntimeError("anti_bot_or_captcha: first-party page presented a visible challenge")

            batch = self.parse_page(company, response.text, response.url, src)
            before = len(dedupe(jobs))
            jobs = dedupe([*jobs, *batch])
            next_url = self._find_next(response.text, response.url)
            if not next_url or next_url in seen_pages:
                break
            if urlparse(next_url).netloc.lower() != urlparse(response.url).netloc.lower():
                break
            if len(jobs) == before and not batch:
                break
            current = next_url

        return self._normalize(company, jobs, src)
