from __future__ import annotations

import os
import re
from urllib.parse import urljoin, urlparse

from bs4 import BeautifulSoup

from job_fetcher.sources.ashby import AshbySource
from job_fetcher.sources.base import JobSource
from job_fetcher.sources.generic_extract import (
    dedupe,
    extract_embedded_json,
    extract_html_links,
    extract_jsonld,
)
from job_fetcher.sources.greenhouse import GreenhouseSource
from job_fetcher.sources.http_client import session, timeout_seconds
from job_fetcher.sources.lever import LeverSource
from job_fetcher.sources.playwright_auto import PlaywrightAutoSource
from job_fetcher.sources.smartrecruiters import SmartRecruitersSource
from job_fetcher.sources.workday import WorkdaySource
from job_fetcher.sources.oracle import OracleSource
from job_fetcher.sources.eightfold import EightfoldSource
from job_fetcher.sources.successfactors import SuccessFactorsSource
from job_fetcher.sources.kula import KulaSource
from job_fetcher.sources.avature import AvatureSource

ATS_DOMAINS = (
    "greenhouse.io", "lever.co", "ashbyhq.com", "smartrecruiters.com", "myworkdayjobs.com",
    "oraclecloud.com", "eightfold.ai", "icims.com", "successfactors.com", "jobs2web.com",
    "phenompeople.com", "avature.net", "kula.ai", "zwayam.com", "trakstar.com", "openings.co",
)
BOT_MARKERS = re.compile(r"(captcha|verify you are human|access denied|unusual traffic|cf-chl-|cloudflare ray id)", re.I)
EMPTY_MARKERS = re.compile(r"(no (current )?(job |role |position )?(openings|vacancies)|no open (roles|positions|jobs)|there are no jobs|no opportunities available)", re.I)
URLISH_RE = re.compile(r'''https?://[^\"'<>\\\s]+''', re.I)


class AutoSource(JobSource):
    def fetch(self, company):
        src = company.get("source") or {}
        entry = src.get("entry_url") or company["career_url"]

        # Known ATS URLs can go straight to public API adapters.
        delegated = self._delegate(company, entry)
        if delegated is not None:
            return delegated

        client = session()
        try:
            r = client.get(entry, timeout=timeout_seconds(), allow_redirects=True)
            # A number of public career sites return 403/429 to requests while
            # rendering normally in a real browser. Treat those as a browser
            # escalation signal, not as a terminal company failure.
            if getattr(r, "status_code", 200) in (403, 429):
                return self._browser(company, entry, f"HTTP {getattr(r, 'status_code', 'unknown')} requires browser fallback")
            r.raise_for_status()
        except Exception:
            raise
        html = r.text
        final_url = r.url
        if BOT_MARKERS.search(html[:15000]):
            return self._browser(company, entry, "anti-bot marker in HTTP response")

        delegated = self._delegate(company, final_url)
        if delegated is not None:
            return delegated
        if SuccessFactorsSource.looks_like_successfactors(html, final_url):
            c = dict(company)
            c["source"] = {"type": "successfactors", "entry_url": final_url}
            return SuccessFactorsSource().fetch(c)

        # Prefer ATS links discovered in anchors OR embedded JS/HTML strings.
        ats_links = self._find_ats_links(html, final_url)
        for ats_link in ats_links:
            delegated = self._delegate(company, ats_link)
            if delegated is not None:
                return delegated

        # Parse the current page, but do not immediately assume it is the complete
        # job list: careers landing pages often contain only a few featured roles.
        best_jobs = self._extract_static(company, html, final_url)
        if best_jobs:
            best_jobs = self._collect_paginated_static(company, client, html, final_url, best_jobs)

        # Inspect a few high-confidence "all jobs/search jobs" links and keep the
        # richest result. A discovered native ATS still wins immediately.
        for next_link in self._find_jobs_links(html, final_url)[:3]:
            if next_link == final_url:
                continue
            try:
                delegated = self._delegate(company, next_link)
                if delegated is not None:
                    return delegated
                r2 = client.get(next_link, timeout=timeout_seconds(), allow_redirects=True)
                r2.raise_for_status()
                if BOT_MARKERS.search(r2.text[:15000]):
                    continue
                for ats_link in self._find_ats_links(r2.text, r2.url):
                    delegated = self._delegate(company, ats_link)
                    if delegated is not None:
                        return delegated
                candidate = self._extract_static(company, r2.text, r2.url)
                if candidate:
                    candidate = self._collect_paginated_static(company, client, r2.text, r2.url, candidate)
                    if len(candidate) > len(best_jobs):
                        best_jobs = candidate
            except Exception:
                continue

        if best_jobs:
            return best_jobs
        if self._is_empty(html):
            return []

        # For known but non-native ATS families (Oracle, Eightfold, iCIMS, Phenom,
        # SuccessFactors, etc.), the browser network-capture fallback is intentionally
        # the generic path. Dedicated custom_api/playwright config remains available
        # when a site needs special pagination/auth parameters.
        if ats_links:
            return self._browser(company, ats_links[0], "known ATS found but no native adapter matched")
        return self._browser(company, entry, "static extraction found no jobs")


    def _collect_paginated_static(self, company, client, first_html, first_url, first_jobs):
        """Follow simple server-rendered Next/page links and aggregate jobs.

        This covers classic ATS pages such as SuccessFactors/jobs2web without
        requiring a provider-specific API. It is intentionally conservative:
        same host only, one next link at a time, and a bounded page count.
        """
        max_pages = max(1, int(os.getenv("JOB_FETCHER_MAX_STATIC_PAGES", "12")))
        jobs = list(first_jobs)
        seen_pages = {first_url}
        html, current_url = first_html, first_url
        for _ in range(max_pages - 1):
            next_url = self._find_next_page(html, current_url)
            if not next_url or next_url in seen_pages:
                break
            if urlparse(next_url).netloc.lower() != urlparse(first_url).netloc.lower():
                break
            try:
                r = client.get(next_url, timeout=timeout_seconds(), allow_redirects=True)
                r.raise_for_status()
            except Exception:
                break
            seen_pages.add(r.url)
            new_jobs = self._extract_static(company, r.text, r.url)
            if not new_jobs:
                break
            before = len(dedupe(jobs))
            jobs.extend(new_jobs)
            jobs = dedupe(jobs)
            if len(jobs) <= before:
                break
            html, current_url = r.text, r.url
        return dedupe(jobs)

    @staticmethod
    def _find_next_page(html, base):
        soup = BeautifulSoup(html, "html.parser")
        # Strongest signals first.
        for a in soup.select("a[rel~=next][href]"):
            return urljoin(base, a.get("href"))
        candidates = []
        for a in soup.select("a[href]"):
            text = (a.get_text(" ", strip=True) or "").strip().lower()
            aria = (a.get("aria-label") or "").strip().lower()
            title = (a.get("title") or "").strip().lower()
            score = 0
            if text in {"next", "next page", "›", "»", ">"}: score += 8
            if "next" in aria or "next" in title: score += 8
            href = urljoin(base, a.get("href"))
            if score:
                candidates.append((score, href))
        return max(candidates, default=(0, None))[1]

    @staticmethod
    def _extract_static(company, html, url):
        jobs = []
        jobs.extend(extract_jsonld(company, html, url))
        jobs.extend(extract_embedded_json(company, html, url))
        jobs.extend(extract_html_links(company, html, url))
        return dedupe(jobs)

    @staticmethod
    def _is_empty(html):
        text = BeautifulSoup(html, "html.parser").get_text(" ", strip=True)[:50000]
        return bool(EMPTY_MARKERS.search(text))

    def _browser(self, company, entry, reason):
        if os.getenv("JOB_FETCHER_DISABLE_BROWSER", "0") == "1":
            raise RuntimeError(f"browser_fallback_disabled: {reason}")
        c = dict(company)
        c["source"] = dict(company.get("source") or {})
        c["source"]["entry_url"] = entry
        jobs = PlaywrightAutoSource().fetch(c)
        if not jobs:
            raise RuntimeError(f"no_jobs_detected: {reason}; browser fallback also found no job records")
        return jobs

    def _delegate(self, company, url):
        parsed = urlparse(url)
        host = parsed.netloc.lower()
        path = parsed.path.strip("/")
        parts = [x for x in path.split("/") if x]
        c = dict(company)
        if "greenhouse.io" in host:
            token = self._greenhouse_token(parts)
            if token:
                c["source"] = {"type": "greenhouse", "board_token": token}
                return GreenhouseSource().fetch(c)
        if host == "jobs.lever.co" or host.endswith(".lever.co"):
            if parts:
                c["source"] = {"type": "lever", "site": parts[0]}
                return LeverSource().fetch(c)
        if "ashbyhq.com" in host and parts:
            c["source"] = {"type": "ashby", "board_name": parts[0]}
            return AshbySource().fetch(c)
        if "smartrecruiters.com" in host:
            ident = self._smartrecruiters_ident(parts)
            if ident:
                c["source"] = {"type": "smartrecruiters", "company_identifier": ident}
                return SmartRecruitersSource().fetch(c)
        eightfold = EightfoldSource.parse_eightfold_url(url)
        if eightfold:
            c["source"] = {
                "type": "eightfold",
                "entry_url": url,
                "tenant": eightfold["tenant"],
            }
            return EightfoldSource().fetch(c)

        if host.endswith(".avature.net") or host == "avature.net":
            c["source"] = {"type": "avature", "entry_url": url}
            return AvatureSource().fetch(c)

        if "successfactors.com" in host or "jobs2web.com" in host:
            c["source"] = {"type": "successfactors", "entry_url": url}
            return SuccessFactorsSource().fetch(c)
        kula = KulaSource.parse_kula_url(url)
        if kula:
            c["source"] = {"type": "kula", "entry_url": url, "tenant": kula["tenant"]}
            return KulaSource().fetch(c)

        oracle = OracleSource.parse_candidate_experience_url(url)
        if oracle:
            c["source"] = {
                "type": "oracle",
                "entry_url": url,
                "host": oracle["host"],
                "site_number": oracle["site_number"],
                "locale": oracle["locale"],
            }
            return OracleSource().fetch(c)

        m = re.match(r"(?P<tenant>[^.]+)\.wd\d+\.myworkdayjobs\.com$", host)
        if m and parts:
            ignored = {"en-us", "en-gb", "fr-fr", "de-de", "es", "en", "fr", "de"}
            site = next((x for x in parts if x.lower() not in ignored), None)
            if site:
                c["source"] = {"type": "workday", "host": host, "tenant": m.group("tenant"), "site": site}
                return WorkdaySource().fetch(c)
        return None

    @staticmethod
    def _greenhouse_token(parts):
        if not parts:
            return None
        # Common forms: job-boards.greenhouse.io/postman and boards.greenhouse.io/company/jobs/...
        return parts[0]

    @staticmethod
    def _smartrecruiters_ident(parts):
        for x in parts:
            if x.lower() not in {"jobs", "careers", "job"}:
                return x
        return None

    @staticmethod
    def _find_ats_links(html, base):
        candidates = []
        soup = BeautifulSoup(html, "html.parser")
        for a in soup.select("a[href]"):
            candidates.append(urljoin(base, a.get("href")))
        # ATS URLs are often embedded in JS configuration rather than visible anchors.
        candidates.extend(URLISH_RE.findall(html))
        out = []
        seen = set()
        for u in candidates:
            try:
                host = urlparse(u).netloc.lower()
            except Exception:
                continue
            if not any(d in host for d in ATS_DOMAINS):
                continue
            u = u.replace("&amp;", "&")
            if u not in seen:
                seen.add(u)
                out.append(u)
        return out

    @staticmethod
    def _find_jobs_links(html, base):
        soup = BeautifulSoup(html, "html.parser")
        scored = []
        seen = set()
        for a in soup.select("a[href]"):
            text = a.get_text(" ", strip=True).lower()
            href = a.get("href") or ""
            u = urljoin(base, href)
            if u in seen:
                continue
            seen.add(u)
            score = 0
            if any(k in text for k in (
                "open positions", "open roles", "search jobs", "view jobs", "explore jobs",
                "all jobs", "job openings", "current openings", "find jobs", "browse jobs",
            )):
                score += 5
            if re.search(r"/(jobs|openings|positions|search-results|job-search|requisitions)(/|$|\?)", urlparse(u).path, re.I):
                score += 3
            if any(d in urlparse(u).netloc.lower() for d in ATS_DOMAINS):
                score += 10
            if score:
                scored.append((score, u))
        scored.sort(key=lambda x: (-x[0], x[1]))
        return [u for _, u in scored]

    @staticmethod
    def _find_ats_link(html, base):
        """Backward-compatible helper used by earlier tests/callers."""
        links = AutoSource._find_ats_links(html, base)
        return links[0] if links else None

    @staticmethod
    def _find_jobs_link(html, base):
        links = AutoSource._find_jobs_links(html, base)
        return links[0] if links else None
