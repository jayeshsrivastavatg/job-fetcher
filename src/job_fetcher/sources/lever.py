from job_fetcher.models import Job
from job_fetcher.sources.base import JobSource
from job_fetcher.sources.http_client import session, timeout_seconds

class LeverSource(JobSource):
    def fetch(self, company):
        site = company["source"]["site"]
        url = f"https://api.lever.co/v0/postings/{site}"
        r = session().get(url, params={"mode": "json"}, timeout=timeout_seconds(),
                         headers={"User-Agent": "PersonalJobFetcher/0.1"})
        r.raise_for_status()
        out = []
        for x in r.json():
            cats = x.get("categories") or {}
            loc = cats.get("location") or ", ".join(cats.get("allLocations") or []) or None
            desc = "\n\n".join(v for v in [x.get("descriptionPlain"), x.get("additionalPlain")] if v) or None
            out.append(Job(company["id"], company["name"], "lever", x.get("id"),
                           x.get("text") or "", loc, desc, x.get("hostedUrl"), None, x))
        return out
