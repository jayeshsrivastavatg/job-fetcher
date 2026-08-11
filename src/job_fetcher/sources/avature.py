from __future__ import annotations

import os
import re
from urllib.parse import parse_qs, urljoin, urlparse

from bs4 import BeautifulSoup

from job_fetcher.models import Job
from job_fetcher.sources.base import JobSource
from job_fetcher.sources.generic_extract import clean_text, dedupe, extract_embedded_json, extract_jsonld
from job_fetcher.sources.http_client import session, timeout_seconds
from job_fetcher.sources.playwright_auto import PlaywrightAutoSource


JOB_DETAIL_RE = re.compile(
    r"/careers/JobDetail(?:/[^/?#]+)?/(?P<path_id>\d+)(?:/|$)|[?&]jobId=(?P<query_id>\d+)(?:&|$)",
    re.IGNORECASE,
)
BOT_RE = re.compile(r"(captcha|verify that you(?:'|’)re not a robot|verify that you are not a robot|access denied)", re.I)


class AvatureSource(JobSource):
    """Public Avature career-site adapter.

    Avature tenants frequently render listings dynamically while exposing stable public
    ``/careers/JobDetail/.../<id>`` pages. The adapter first consumes normal public HTML/
    embedded JSON, then uses the existing bounded browser/XHR fallback when necessary.
    It never attempts to bypass login, CAPTCHA, or other access controls.
    """

    def fetch(self, company):
        src = company.get("source") or {}
        entry = src.get("entry_url") or company["career_url"]
        provider_url = src.get("provider_url")
        candidates = [entry]
        if provider_url and provider_url not in candidates:
            candidates.append(provider_url)

        client = session()
        errors = []
        for url in candidates:
            try:
                response = client.get(url, timeout=timeout_seconds(), allow_redirects=True)
                response.raise_for_status()
                html = response.text
                if BOT_RE.search(BeautifulSoup(html, "html.parser").get_text(" ", strip=True)[:20000]):
                    errors.append(RuntimeError(f"avature_http_challenge: {response.url}"))
                    continue
                jobs = self.parse_page(company, html, response.url)
                if jobs:
                    return jobs
            except Exception as exc:
                errors.append(exc)

        if src.get("disable_browser_fallback") or os.getenv("JOB_FETCHER_DISABLE_BROWSER", "0") == "1":
            detail = "; ".join(str(e) for e in errors[-3:]) or "no static jobs detected"
            raise RuntimeError(f"avature_static_fetch_failed: {detail}")

        browser_errors = []
        for url in candidates:
            c = dict(company)
            c["source"] = dict(src)
            c["source"]["entry_url"] = url
            try:
                jobs = PlaywrightAutoSource().fetch(c)
                jobs = self.normalize_browser_jobs(company, jobs, src)
                if jobs:
                    return jobs
            except Exception as exc:
                browser_errors.append(exc)

        detail = "; ".join(str(e) for e in (errors + browser_errors)[-4:]) or "no jobs detected"
        raise RuntimeError(f"avature_fetch_failed: {detail}")

    @classmethod
    def parse_page(cls, company, html: str, base_url: str):
        jobs = []
        jobs.extend(extract_jsonld(company, html, base_url, "avature_jsonld"))
        jobs.extend(extract_embedded_json(company, html, base_url, "avature_embedded_json"))

        soup = BeautifulSoup(html, "html.parser")
        for a in soup.select("a[href]"):
            href = a.get("href") or ""
            absolute = urljoin(base_url, href)
            job_id = cls.job_id_from_url(absolute)
            if not job_id:
                continue
            title = clean_text(a.get_text(" ", strip=True))
            if not title or len(title) > 220:
                title = cls._title_from_ancestors(a)
            if not title:
                continue
            container = a
            for _ in range(4):
                if container.parent is None:
                    break
                container = container.parent
            context = clean_text(container.get_text(" ", strip=True)) if container else None
            location = cls._location_from_context(context, title)
            jobs.append(Job(
                company["id"], company["name"], "avature", str(job_id), title,
                location, None, absolute, None,
                {"href": href, "context": context},
            ))
        return cls._normalize(company, dedupe(jobs), {})

    @staticmethod
    def job_id_from_url(url: str) -> str | None:
        parsed = urlparse(url)
        match = JOB_DETAIL_RE.search(parsed.path + ("?" + parsed.query if parsed.query else ""))
        if match:
            return match.group("path_id") or match.group("query_id")
        query = parse_qs(parsed.query)
        value = (query.get("jobId") or query.get("jobid") or [None])[0]
        return str(value) if value else None

    @classmethod
    def normalize_browser_jobs(cls, company, jobs, src):
        return cls._normalize(company, jobs, src)

    @classmethod
    def _normalize(cls, company, jobs, src):
        out = []
        canonical_base = (src.get("canonical_base_url") or "https://careers.ibm.com").rstrip("/")
        locale = src.get("locale") or "en_US"
        for job in jobs:
            job_id = cls.job_id_from_url(job.job_url or "") or (str(job.external_id) if job.external_id else None)
            if job_id and not re.fullmatch(r"\d+", job_id):
                # Avature job ids for the IBM tenant are numeric. Keep explicit JobDetail
                # URLs even if the raw external id is something else.
                from_url = cls.job_id_from_url(job.job_url or "")
                job_id = from_url
            if not job_id:
                continue
            job.company_id = company["id"]
            job.company_name = company["name"]
            job.source_type = "avature"
            job.external_id = str(job_id)
            if not job.job_url or not cls.job_id_from_url(job.job_url):
                job.job_url = f"{canonical_base}/{locale}/careers/JobDetail?jobId={job_id}"
            out.append(job)
        return dedupe(out)

    @staticmethod
    def _title_from_ancestors(node):
        parent = node.parent
        for _ in range(4):
            if parent is None:
                break
            for selector in ("h1", "h2", "h3", "h4", "[class*='title']"):
                found = parent.select_one(selector) if hasattr(parent, "select_one") else None
                value = clean_text(found.get_text(" ", strip=True)) if found else None
                if value and 3 < len(value) <= 220:
                    return value
            parent = parent.parent
        return None

    @staticmethod
    def _location_from_context(context, title):
        if not context:
            return None
        text = context.replace(title or "", " ")
        # IBM/Avature cards commonly render City, State, Country together.
        m = re.search(
            r"\b(Bangalore|Bengaluru|Pune|Hyderabad|Gurgaon|Gurugram|Noida|Mumbai|Chennai|Kochi|Ahmedabad)\b"
            r"(?:\s*,?\s*([A-Za-z ]+?))?\s*,?\s*India\b",
            text,
            re.I,
        )
        if m:
            parts = [clean_text(m.group(1)), clean_text(m.group(2)), "India"]
            return ", ".join(x for x in parts if x)
        if re.search(r"\bIndia\b", text, re.I):
            return "India"
        return None
