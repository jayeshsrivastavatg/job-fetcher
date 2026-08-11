from __future__ import annotations

import re
from urllib.parse import urljoin, urlparse

from bs4 import BeautifulSoup

from job_fetcher.models import Job
from job_fetcher.sources.base import JobSource
from job_fetcher.sources.generic_extract import clean_text, dedupe
from job_fetcher.sources.http_client import session, timeout_seconds


DETAIL_RE = re.compile(r"/careers/job/(?P<id>\d+)(?:[/?#]|$)", re.I)
DATE_RE = re.compile(r"\b20\d{2}-\d{2}-\d{2}\b")
INDIA_LOCATION_RE = re.compile(
    r"\bIndia(?:\s*,\s*[A-Za-z .'-]+){0,3}(?:\s*\+\d+\s+more)?\b",
    re.I,
)


class MicrosoftIndiaSource(JobSource):
    """Parse Microsoft's first-party India location page as the canonical index.

    The branded India page is server-readable and contains the full vacancy cards,
    descriptions and links to apply.careers.microsoft.com.  Restricting extraction
    to `/careers/job/<numeric id>` links avoids generic footer/developer links and
    gives us a stable requisition identity without relying on Eightfold discovery.
    """

    DEFAULT_ENTRY = "https://careers.microsoft.com/v2/global/en/locations/india.html"

    def fetch(self, company):
        src = company.get("source") or {}
        entry = src.get("india_index_url") or self.DEFAULT_ENTRY
        response = session().get(entry, timeout=timeout_seconds(), allow_redirects=True)
        response.raise_for_status()
        jobs = self.parse_page(company, response.text, response.url)
        if not jobs:
            raise RuntimeError("microsoft_india_index_returned_no_jobs")
        return jobs

    @classmethod
    def parse_page(cls, company, html: str, base_url: str):
        soup = BeautifulSoup(html, "html.parser")
        out = []
        seen = set()
        for anchor in soup.select("a[href]"):
            absolute = urljoin(base_url, anchor.get("href") or "")
            parsed = urlparse(absolute)
            if parsed.netloc.lower() != "apply.careers.microsoft.com":
                continue
            match = DETAIL_RE.search(parsed.path)
            if not match:
                continue
            job_id = match.group("id")
            if job_id in seen:
                continue

            card, title = cls._card_and_title(anchor)
            if not title:
                continue
            context = clean_text(card.get_text(" ", strip=True)) if card is not None else ""
            location = cls._location(context)
            # This adapter is specifically the India index. Still require explicit
            # India evidence so a future page redesign cannot leak unrelated links.
            if not location or "india" not in location.lower():
                continue
            posted = None
            dm = DATE_RE.search(context or "")
            if dm:
                posted = dm.group(0)
            description = cls._description(context, title)
            seen.add(job_id)
            out.append(Job(
                company_id=company["id"],
                company_name=company["name"],
                source_type="microsoft_india",
                external_id=job_id,
                title=title,
                location=location,
                description=description,
                job_url=absolute,
                posted_at=posted,
                raw={"source_page": base_url, "card_text": context},
            ))
        return dedupe(out)

    @staticmethod
    def _card_and_title(anchor):
        node = anchor
        best = None
        for _ in range(10):
            node = getattr(node, "parent", None)
            if node is None:
                break
            headings = node.find_all(["h2", "h3", "h4"], recursive=True)
            for heading in headings:
                value = clean_text(heading.get_text(" ", strip=True))
                if value and 3 < len(value) <= 220 and value.lower() not in {
                    "explore career opportunities", "life at microsoft india", "microsoft india locations"
                }:
                    best = (node, value)
                    break
            if best:
                text = clean_text(node.get_text(" ", strip=True)) or ""
                if "india" in text.lower() and len(text) >= len(best[1]) + 20:
                    return best
        return best or (None, None)

    @staticmethod
    def _location(context: str | None):
        if not context:
            return None
        match = INDIA_LOCATION_RE.search(context)
        if match:
            value = clean_text(match.group(0))
            # Stop at common card-section labels accidentally consumed by a broad
            # text match after the location.
            if value:
                value = re.split(r"\b(?:Work site|Overview|Responsibilities|Qualifications)\b", value, 1, flags=re.I)[0]
            return clean_text(value)
        return "India" if re.search(r"\bIndia\b", context, re.I) else None

    @staticmethod
    def _description(context: str | None, title: str):
        if not context:
            return None
        text = context
        if title:
            text = text.replace(title, " ", 1)
        # Keep the full card/JD text; deterministic relevance scoring benefits from
        # responsibilities and qualifications already present on the location page.
        value = clean_text(text)
        return value[:50000] if value and len(value) >= 100 else None
