from __future__ import annotations

import re
from urllib.parse import urljoin, urlparse

from bs4 import BeautifulSoup

from job_fetcher.job_quality import prefer_usable_jobs
from job_fetcher.sources.auto import AutoSource, URLISH_RE


class StrictAutoSource(AutoSource):
    """AutoSource with a conservative gate for generic careers-page extraction.

    The old AutoSource could stop as soon as it found any title+URL pair. On a
    branded careers landing page that often meant footer/menu links such as
    Products, Support, Career Growth or All Teams. This subclass keeps AutoSource's
    provider discovery/browser behavior but refuses to treat those low-confidence
    records as a successful job list, allowing the crawler to continue to the real
    jobs page/ATS/browser XHR instead.
    """

    @staticmethod
    def _extract_static(company, html, url):
        return prefer_usable_jobs(AutoSource._extract_static(company, html, url))

    @staticmethod
    def _find_jobs_links(html, base):
        links = list(AutoSource._find_jobs_links(html, base))
        soup = BeautifulSoup(html, "html.parser")
        scored = []
        seen = set(links)
        for anchor in soup.select("a[href]"):
            text = " ".join((anchor.get_text(" ", strip=True) or "").lower().split())
            href = anchor.get("href") or ""
            url = urljoin(base, href)
            if url in seen:
                continue
            score = 0
            if any(label in text for label in (
                "explore opportunities", "view opportunities", "all opportunities",
                "current opportunities", "career opportunities", "open vacancies",
                "current vacancies", "see open positions", "explore roles",
            )):
                score += 8
            path = urlparse(url).path.lower()
            if re.search(r"/(?:open-jobs|job-openings|career-opportunities|vacancies)(?:/|$)", path):
                score += 5
            if score:
                scored.append((score, url))
                seen.add(url)
        scored.sort(key=lambda item: (-item[0], item[1]))
        return links + [url for _, url in scored]

    @staticmethod
    def _find_ats_links(html, base):
        links = list(AutoSource._find_ats_links(html, base))
        seen = set(links)
        candidates = []
        soup = BeautifulSoup(html, "html.parser")
        for anchor in soup.select("a[href]"):
            candidates.append(urljoin(base, anchor.get("href") or ""))
        candidates.extend(URLISH_RE.findall(html))
        for url in candidates:
            try:
                host = urlparse(url).netloc.lower()
            except Exception:
                continue
            if not (host.endswith(".darwinbox.in") or host == "darwinbox.in" or host.endswith(".darwinbox.com") or host == "darwinbox.com"):
                continue
            url = url.replace("&amp;", "&")
            if url not in seen:
                seen.add(url)
                links.append(url)
        return links
