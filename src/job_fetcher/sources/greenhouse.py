import html, re, requests
from job_fetcher.models import Job
from job_fetcher.sources.base import JobSource
from job_fetcher.sources.http_client import session, timeout_seconds

def clean(value):
    if not value:
        return value
    return re.sub(r"<[^>]+>", " ", html.unescape(value)).replace("\xa0", " ").strip()

class GreenhouseSource(JobSource):
    def fetch(self, company):
        token = company["source"]["board_token"]
        url = f"https://boards-api.greenhouse.io/v1/boards/{token}/jobs"
        r = session().get(url, params={"content": "true"}, timeout=timeout_seconds(),
                         headers={"User-Agent": "PersonalJobFetcher/0.1"})
        r.raise_for_status()
        out = []
        for x in r.json().get("jobs", []):
            out.append(Job(company["id"], company["name"], "greenhouse",
                           str(x.get("id")) if x.get("id") is not None else None,
                           x.get("title") or "", (x.get("location") or {}).get("name"),
                           clean(x.get("content")), x.get("absolute_url"),
                           x.get("first_published") or x.get("updated_at"), x))
        return out
