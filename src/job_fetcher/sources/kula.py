from __future__ import annotations

import os
import re
from urllib.parse import urljoin, urlparse

from bs4 import BeautifulSoup

from job_fetcher.models import Job
from job_fetcher.sources.base import JobSource
from job_fetcher.sources.generic_extract import clean_text, dedupe, extract_embedded_json, extract_jsonld
from job_fetcher.sources.http_client import session, timeout_seconds
from job_fetcher.sources.playwright_auto import PlaywrightAutoSource


KULA_HOST = "careers.kula.ai"
JOB_PATH_RE = re.compile(r"^/(?P<tenant>[^/]+)/(?:jobs/)?(?P<id>\d+)(?:/(?:apply)?)?/?$", re.I)
EMPLOYMENT_RE = re.compile(r"\b(Full Time|Part Time|Contract|Temporary|Internship|Freelance)\b", re.I)
WORK_RE = re.compile(r"\b(Remote|Hybrid|On[- ]Site)\b", re.I)


class KulaSource(JobSource):
    """Public Kula career-page adapter.

    Kula boards are public candidate-facing pages. The adapter intentionally
    consumes the public listing/detail URLs and does not rely on an authenticated
    customer API. Static HTML/embedded data is preferred; Playwright remains a
    bounded fallback if a tenant changes to client-only rendering.
    """

    @staticmethod
    def parse_kula_url(url: str) -> dict[str, str] | None:
        parsed = urlparse(url)
        if parsed.netloc.lower() != KULA_HOST:
            return None
        parts = [p for p in parsed.path.split("/") if p]
        if not parts:
            return None
        return {"tenant": parts[0], "host": KULA_HOST}

    def fetch(self, company):
        src = company.get("source") or {}
        entry = src.get("entry_url") or company["career_url"]
        parsed = self.parse_kula_url(entry)
        tenant = clean_text(src.get("tenant")) or (parsed or {}).get("tenant")
        if not tenant:
            raise ValueError("kula source requires tenant or a careers.kula.ai entry_url")

        board_url = f"https://{KULA_HOST}/{tenant}"
        if src.get("jobs_query", True):
            board_url += "?jobs=true"
        client = session()
        try:
            response = client.get(board_url, timeout=timeout_seconds(), allow_redirects=True)
            response.raise_for_status()
            jobs = self.parse_board(company, response.text, response.url, tenant)
            if jobs:
                return jobs[: max(1, int(src.get("max_jobs", 5000)))]
            static_error = RuntimeError("kula_public_board_returned_no_jobs")
        except Exception as exc:
            static_error = exc

        if src.get("disable_browser_fallback") or os.getenv("JOB_FETCHER_DISABLE_BROWSER", "0") == "1":
            raise RuntimeError(f"kula_static_fetch_failed: {static_error}")

        c = dict(company)
        c["source"] = dict(src)
        c["source"]["entry_url"] = board_url
        try:
            jobs = PlaywrightAutoSource().fetch(c)
        except Exception as browser_exc:
            raise RuntimeError(f"kula_fetch_failed: static={static_error}; browser={browser_exc}") from browser_exc
        if not jobs:
            raise RuntimeError(f"kula_fetch_failed: static={static_error}; browser returned no jobs")
        for job in jobs:
            job.source_type = "kula"
        return self._canonicalize(company, tenant, jobs)

    @staticmethod
    def parse_board(company, html: str, base_url: str, tenant: str) -> list[Job]:
        jobs = []
        jobs.extend(extract_jsonld(company, html, base_url, "kula"))
        jobs.extend(extract_embedded_json(company, html, base_url, "kula"))

        soup = BeautifulSoup(html, "html.parser")
        seen_ids = set()
        for a in soup.select("a[href]"):
            href = a.get("href") or ""
            url = urljoin(base_url, href)
            match = JOB_PATH_RE.match(urlparse(url).path)
            if not match or match.group("tenant").lower() != tenant.lower():
                continue
            job_id = match.group("id")
            if job_id in seen_ids:
                continue

            card = KulaSource._job_card(a)
            title = KulaSource._title_from_card(a, card)
            if not title:
                continue
            seen_ids.add(job_id)
            card_text = clean_text(card.get_text(" ", strip=True)) if card is not None else None
            location = KulaSource._location_from_card(card, title)
            employment = KulaSource._first_match(EMPLOYMENT_RE, card_text)
            work_type = KulaSource._first_match(WORK_RE, card_text)
            department = KulaSource._department_from_card(card, title, location, employment, work_type)
            canonical = f"https://{KULA_HOST}/{tenant}/{job_id}"
            jobs.append(Job(
                company_id=company["id"],
                company_name=company["name"],
                source_type="kula",
                external_id=job_id,
                title=title,
                location=location,
                description=None,
                job_url=canonical,
                posted_at=None,
                raw={
                    "department": department,
                    "employment_type": employment,
                    "work_type": work_type,
                    "listing_text": card_text,
                },
            ))
        return KulaSource._canonicalize(company, tenant, dedupe(jobs))

    @staticmethod
    def _job_card(anchor):
        node = anchor
        fallback = anchor.parent
        for _ in range(7):
            node = getattr(node, "parent", None)
            if node is None:
                break
            text = clean_text(node.get_text(" ", strip=True)) or ""
            headings = node.find_all(["h1", "h2", "h3", "h4", "h5", "h6"])
            if headings and 10 <= len(text) <= 600:
                return node
            if len(text) <= 1000:
                fallback = node
        return fallback

    @staticmethod
    def _title_from_card(anchor, card):
        anchor_text = clean_text(anchor.get_text(" ", strip=True))
        if anchor_text and anchor_text.lower() not in {"apply", "apply now", "job details", "application form", "view job"}:
            return anchor_text
        if card is not None:
            headings = card.find_all(["h1", "h2", "h3", "h4", "h5", "h6"])
            for h in reversed(headings):
                value = clean_text(h.get_text(" ", strip=True))
                if value and value.lower() not in {"open positions", "careers", "job details"}:
                    return value
            # Kula themes do not always use semantic headings for cards. Prefer
            # short text blocks immediately preceding the Apply link.
            candidates = []
            for node in card.find_all(["p", "span", "div"], recursive=True):
                value = clean_text(node.get_text(" ", strip=True))
                if value and 4 <= len(value) <= 160 and value not in candidates:
                    candidates.append(value)
            for value in candidates:
                if not EMPLOYMENT_RE.fullmatch(value) and not WORK_RE.fullmatch(value) and "," not in value:
                    return value
        return None

    @staticmethod
    def _location_from_card(card, title):
        if card is None:
            return None
        candidates = []
        for node in card.find_all(["p", "span", "div"], recursive=True):
            value = clean_text(node.get_text(" ", strip=True))
            if not value or value == title or len(value) > 180:
                continue
            if re.search(r"\b(India|United States|Brazil|Vietnam|Mexico|Indonesia|Remote)\b", value, re.I) or value.count(",") >= 2:
                candidates.append(value)
        return min(candidates, key=len) if candidates else None

    @staticmethod
    def _department_from_card(card, title, location, employment, work_type):
        if card is None:
            return None
        for node in card.find_all(["p", "span", "div"], recursive=True):
            value = clean_text(node.get_text(" ", strip=True))
            if not value or value in {title, location, employment, work_type} or len(value) > 80:
                continue
            if "apply" in value.lower() or "," in value:
                continue
            return value
        return None

    @staticmethod
    def _first_match(pattern, text):
        if not text:
            return None
        match = pattern.search(text)
        return clean_text(match.group(1)) if match else None

    @staticmethod
    def _canonicalize(company, tenant, jobs):
        out = []
        for job in jobs:
            job.source_type = "kula"
            if job.job_url:
                m = JOB_PATH_RE.match(urlparse(job.job_url).path)
                if m and m.group("tenant").lower() == tenant.lower():
                    job.external_id = clean_text(job.external_id) or m.group("id")
                    job.job_url = f"https://{KULA_HOST}/{tenant}/{m.group('id')}"
            out.append(job)
        return dedupe(out)
