from __future__ import annotations

import json
import re
from urllib.parse import urljoin, urlparse

from bs4 import BeautifulSoup

from job_fetcher.models import Job
from job_fetcher.sources.base import JobSource
from job_fetcher.sources.http_client import session, timeout_seconds


class OfficialLinksSource(JobSource):
    """Fetch only first-party detail URLs matching an explicit vacancy pattern."""

    def fetch(self, company):
        src = company["source"]
        list_url = src.get("list_url") or company["career_url"]
        path_re = re.compile(src["job_path_regex"], re.I)
        client = session()
        page = client.get(list_url, timeout=timeout_seconds())
        page.raise_for_status()
        links = {}
        for anchor in BeautifulSoup(page.text, "html.parser").select("a[href]"):
            url = urljoin(page.url, anchor.get("href") or "")
            match = path_re.match(urlparse(url).path)
            if match:
                links[url] = match.groupdict().get("id") or match.group(0)

        jobs = []
        for url, fallback_id in links.items():
            detail = client.get(url, timeout=timeout_seconds())
            detail.raise_for_status()
            soup = BeautifulSoup(detail.text, "html.parser")
            posting = self._posting(soup)
            title = self._text(posting.get("title")) if posting else None
            location = self._location(posting) if posting else None
            description = posting.get("description") if posting else None
            posted_at = self._text(posting.get("datePosted")) if posting else None
            external_id = self._identifier(posting) if posting else None
            if not title:
                h1 = soup.select_one(src.get("detail_title_selector", "h1"))
                title = h1.get_text(" ", strip=True) if h1 else None
            jobs.append(Job(
                company["id"], company["name"], "official_links",
                external_id or str(fallback_id), title or "", location,
                description, url, posted_at, {"listing_url": page.url},
            ))
        return jobs

    @classmethod
    def _posting(cls, soup):
        for node in soup.select('script[type="application/ld+json"]'):
            try:
                found = cls._find(json.loads(node.string or node.get_text() or "null"))
            except Exception:
                found = None
            if found:
                return found
        return None

    @classmethod
    def _find(cls, value):
        if isinstance(value, dict):
            kind = value.get("@type")
            if kind == "JobPosting" or (isinstance(kind, list) and "JobPosting" in kind):
                return value
            for nested in value.values():
                found = cls._find(nested)
                if found:
                    return found
        elif isinstance(value, list):
            for nested in value:
                found = cls._find(nested)
                if found:
                    return found
        return None

    @staticmethod
    def _text(value):
        text = str(value).strip() if value is not None else ""
        return text or None

    @classmethod
    def _identifier(cls, posting):
        value = posting.get("identifier")
        if isinstance(value, dict):
            value = value.get("value") or value.get("name")
        return cls._text(value)

    @classmethod
    def _location(cls, posting):
        value = posting.get("jobLocation")
        values = value if isinstance(value, list) else ([value] if value else [])
        locations = []
        for item in values:
            address = item.get("address") if isinstance(item, dict) else None
            if isinstance(address, dict):
                parts = [cls._text(address.get(k)) for k in ("addressLocality", "addressRegion", "addressCountry")]
                text = ", ".join(part for part in parts if part)
            else:
                text = cls._text(address)
            if text and text not in locations:
                locations.append(text)
        return "; ".join(locations) or None
