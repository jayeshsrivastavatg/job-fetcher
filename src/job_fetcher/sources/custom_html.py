from urllib.parse import urljoin

from bs4 import BeautifulSoup

from job_fetcher.models import Job
from job_fetcher.sources.base import JobSource
from job_fetcher.sources.http_client import session, timeout_seconds


class CustomHtmlSource(JobSource):
    def fetch(self, company):
        src = company["source"]
        url = src.get("list_url") or company["career_url"]
        sel = src["selectors"]
        r = session().get(url, timeout=timeout_seconds(), headers=src.get("headers", {}))
        r.raise_for_status()
        soup = BeautifulSoup(r.text, "html.parser")
        out = []
        for card in soup.select(sel["card"]):
            t = card.select_one(sel["title"])
            l = card.select_one(sel["location"]) if sel.get("location") else None
            a = card.select_one(sel["link"]) if sel.get("link") else None
            href = a.get("href") if a else None
            job_url = urljoin(url, href) if href else None
            out.append(Job(
                company["id"], company["name"], "custom_html", job_url,
                t.get_text(" ", strip=True) if t else "",
                l.get_text(" ", strip=True) if l else None,
                None, job_url, None, {"html": str(card)},
            ))
        return out
