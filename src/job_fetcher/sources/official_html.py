from __future__ import annotations

import os
import re
from urllib.parse import urljoin, urlparse

from bs4 import BeautifulSoup

from job_fetcher.models import Job
from job_fetcher.sources.base import JobSource
from job_fetcher.sources.generic_extract import (
    clean_text,
    dedupe,
    extract_embedded_json,
    extract_jobs_from_json,
    extract_jsonld,
)
from job_fetcher.sources.http_client import session, timeout_seconds


DEFAULT_JOB_PATH_RE = re.compile(
    r"(?:/careers?/job/|/jobs?/[^/?#]+|/job/\d+|/details/\d+|/careers/details/\d+|"
    r"/opportunity/[^/?#]+|/JobDetail/|/sites/[^/]+/job/\d+)",
    re.I,
)
INDIA_RE = re.compile(
    r"\b(India|Bengaluru|Bangalore|Hyderabad|Gurugram|Gurgaon|Pune|Chennai|Noida|"
    r"Mumbai|Delhi|New Delhi|Kolkata|Ahmedabad|Kochi|Thiruvananthapuram|Vadodara)\b",
    re.I,
)
DATE_RE = re.compile(
    r"\b(?:20\d{2}[-/]\d{1,2}[-/]\d{1,2}|\d{1,2}[-/]\d{1,2}[-/]20\d{2}|"
    r"(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*\s+\d{1,2},?\s+20\d{2})\b",
    re.I,
)


def visible_challenge(html: str, title: str = "") -> bool:
    """Detect an actual visible anti-bot interstitial, not mere captcha JS/config.

    Several legitimate career pages ship captcha libraries in every document. The
    old generic detector treated the word `captcha` anywhere in raw HTML as a
    challenge, which produced false failures for public Eightfold-style pages.
    """
    soup = BeautifulSoup(html or "", "html.parser")
    for node in soup(["script", "style", "noscript", "template"]):
        node.decompose()
    text = clean_text(soup.get_text(" ", strip=True)) or ""
    haystack = f"{title} {text[:60000]}".lower()
    strong = (
        "verify you are human",
        "verify that you are human",
        "checking your browser",
        "access denied",
        "unusual traffic",
        "are you a robot",
        "are you human",
        "security challenge",
        "request blocked",
    )
    if any(marker in haystack for marker in strong):
        return True
    # The bare word captcha is common in dormant scripts. Count it only when the
    # visible page is explicitly asking the visitor to solve/complete one.
    return bool(re.search(r"\b(?:solve|complete|enter|pass)\b.{0,80}\bcaptcha\b|\bcaptcha\b.{0,80}\b(?:solve|complete|enter|pass)\b", haystack, re.I))


class OfficialHtmlSource(JobSource):
    """Conservative scraper for first-party, server-readable career pages.

    It is intentionally generic but configurable enough for branded career sites:
    extract JSON-LD/embedded JSON, then stable job-detail links, follow ordinary
    Next pagination, optionally restrict records to India, and never try to solve
    or bypass a real anti-bot challenge.
    """

    def fetch(self, company):
        src = company.get("source") or {}
        urls = list(src.get("entry_urls") or [])
        entry = src.get("entry_url") or company.get("career_url")
        if entry and entry not in urls:
            urls.insert(0, entry)
        if not urls:
            raise ValueError("official_html requires entry_url/entry_urls")

        best: list[Job] = []
        errors: list[str] = []
        for url in urls:
            try:
                jobs = self._fetch_url(company, url, src)
                if len(jobs) > len(best):
                    best = jobs
            except Exception as exc:
                errors.append(f"{url}: {type(exc).__name__}: {exc}")

        if best:
            return dedupe(best)
        raise RuntimeError("official_html_fetch_failed: " + ("; ".join(errors) or "no jobs detected"))

    def _fetch_url(self, company, entry: str, src: dict):
        client = session()
        max_pages = max(1, int(src.get("max_pages") or 25))
        current = entry
        seen_pages: set[str] = set()
        jobs: list[Job] = []

        for _ in range(max_pages):
            if not current or current in seen_pages:
                break
            seen_pages.add(current)
            response = client.get(current, timeout=timeout_seconds(), allow_redirects=True)
            response.raise_for_status()
            if visible_challenge(response.text):
                raise RuntimeError("anti_bot_or_captcha: first-party page presented a visible challenge")

            batch = self.parse_page(company, response.text, response.url, src)
            before = len(dedupe(jobs))
            jobs = dedupe([*jobs, *batch])
            next_url = self._find_next(response.text, response.url)
            if not next_url or next_url in seen_pages:
                break
            # Do not wander off the employer/provider host while following paging.
            if urlparse(next_url).netloc.lower() != urlparse(response.url).netloc.lower():
                break
            if len(jobs) == before and not batch:
                break
            current = next_url

        return self._normalize(company, jobs, src)

    @classmethod
    def parse_page(cls, company, html: str, base_url: str, src: dict):
        jobs: list[Job] = []
        jobs.extend(extract_jsonld(company, html, base_url, "official_html"))
        jobs.extend(extract_embedded_json(company, html, base_url, "official_html"))

        soup = BeautifulSoup(html, "html.parser")
        patterns = [re.compile(x, re.I) for x in (src.get("job_href_patterns") or [])]
        seen_urls: set[str] = set()
        for a in soup.select("a[href]"):
            href = a.get("href") or ""
            absolute = urljoin(base_url, href)
            parsed = urlparse(absolute)
            matches = bool(DEFAULT_JOB_PATH_RE.search(parsed.path)) or any(p.search(absolute) for p in patterns)
            if not matches or absolute in seen_urls:
                continue

            title, context = cls._title_and_context(a)
            if not title:
                continue
            # Avoid navigation links that happen to contain /jobs/.
            low = title.lower().strip()
            if low in {"jobs", "careers", "view jobs", "view all jobs", "search jobs", "apply", "apply now", "see details", "view details"}:
                continue

            location = cls._location_from_context(context, title)
            posted = None
            dm = DATE_RE.search(context or "")
            if dm:
                posted = clean_text(dm.group(0))
            external_id = cls._external_id(absolute)
            seen_urls.add(absolute)
            jobs.append(Job(
                company_id=company["id"],
                company_name=company["name"],
                source_type="official_html",
                external_id=external_id,
                title=title,
                location=location,
                description=cls._description_from_context(context, title),
                job_url=absolute,
                posted_at=posted,
                raw={"card_text": context, "source_page": base_url},
            ))

        # Some branded pages expose JSON in ordinary script/application state that
        # is captured by generic extraction only when supplied as a parsed payload.
        # application/json blocks were handled above; this final pass is just a
        # defensive no-op for callers/tests that provide a JSON root document.
        stripped = (html or "").lstrip()
        if stripped.startswith(("{", "[")):
            try:
                import json
                payload = json.loads(stripped)
                jobs.extend(extract_jobs_from_json(company, payload, base_url, "official_html"))
            except Exception:
                pass
        return dedupe(jobs)

    @classmethod
    def _normalize(cls, company, jobs, src):
        out: list[Job] = []
        default_location = clean_text(src.get("default_location"))
        require_india = bool(src.get("require_india"))
        for job in dedupe(jobs):
            job.company_id = company["id"]
            job.company_name = company["name"]
            job.source_type = "official_html"
            if not job.location and default_location:
                job.location = default_location
            raw = job.raw if isinstance(job.raw, dict) else {}
            evidence = " ".join([
                str(job.location or ""),
                str(raw.get("card_text") or ""),
                str(job.description or ""),
            ])
            if require_india and not INDIA_RE.search(evidence):
                continue
            if not job.external_id:
                job.external_id = cls._external_id(job.job_url or f"{job.title}|{job.location or ''}")
            out.append(job)
        return dedupe(out)

    @staticmethod
    def _title_and_context(anchor):
        anchor_text = clean_text(anchor.get_text(" ", strip=True))
        candidates = []
        if anchor_text:
            candidates.append(anchor_text)
        parent = anchor.parent
        best_context = anchor_text or ""
        for _ in range(7):
            if parent is None:
                break
            text = clean_text(parent.get_text(" ", strip=True)) or ""
            if text and (not best_context or len(text) < 4000):
                best_context = text
            for selector in ("h1", "h2", "h3", "h4", "h5", "[class*='title']", "[class*='job-title']"):
                node = parent.select_one(selector) if hasattr(parent, "select_one") else None
                value = clean_text(node.get_text(" ", strip=True)) if node else None
                if value and 3 < len(value) <= 220:
                    candidates.append(value)
            parent = parent.parent
        generic = {"apply", "apply now", "see details", "view details", "learn more", "save for later"}
        title = next((x for x in candidates if x.lower().strip() not in generic and 3 < len(x) <= 220), None)
        return title, best_context

    @staticmethod
    def _location_from_context(context: str | None, title: str | None):
        if not context:
            return None
        text = context.replace(title or "", " ")
        # Prefer a compact India-bearing fragment rather than an entire card/JD.
        m = re.search(
            r"\b(?:Remote\s*[-,]?\s*)?(?:Bengaluru|Bangalore|Hyderabad|Gurugram|Gurgaon|Pune|Chennai|Noida|Mumbai|Delhi|New Delhi|Kolkata|Ahmedabad|Kochi|Thiruvananthapuram|Vadodara)?(?:,?\s*(?:Karnataka|Telangana|Haryana|Maharashtra|Tamil Nadu|Uttar Pradesh|West Bengal|Gujarat|Kerala))?(?:,?\s*)India\b",
            text,
            re.I,
        )
        if m:
            return clean_text(m.group(0))
        m = re.search(r"\bIndia\s*,\s*[A-Za-z .'-]+\s*,\s*[A-Za-z .'-]+\b", text, re.I)
        if m:
            return clean_text(m.group(0))
        return None

    @staticmethod
    def _description_from_context(context: str | None, title: str | None):
        if not context:
            return None
        value = context
        if title:
            value = value.replace(title, " ", 1)
        value = clean_text(value)
        if not value or len(value) < 80:
            return None
        return value[:12000]

    @staticmethod
    def _external_id(url: str):
        parsed = urlparse(url)
        path = parsed.path.rstrip("/")
        for pattern in (
            r"/job/(?P<id>\d+)(?:-|/|$)",
            r"/details/(?P<id>\d+(?:-\d+)?)(?:/|$)",
            r"/careers/details/(?P<id>\d+)(?:/|$)",
            r"/jobs/(?P<id>\d+)(?:/|$)",
            r"/opportunity/(?P<id>[^/]+)$",
        ):
            match = re.search(pattern, path, re.I)
            if match:
                return clean_text(match.group("id"))
        return url

    @staticmethod
    def _find_next(html: str, base_url: str):
        soup = BeautifulSoup(html, "html.parser")
        for a in soup.select("a[rel~=next][href]"):
            return urljoin(base_url, a.get("href"))
        candidates = []
        for a in soup.select("a[href]"):
            text = clean_text(a.get_text(" ", strip=True)) or ""
            aria = clean_text(a.get("aria-label")) or ""
            title = clean_text(a.get("title")) or ""
            label = f"{text} {aria} {title}".lower()
            if text.strip() in {">", "›", "»"} or "next" in label:
                candidates.append(urljoin(base_url, a.get("href")))
        return candidates[0] if candidates else None
