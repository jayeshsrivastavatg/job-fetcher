from job_fetcher.models import Job
from job_fetcher.sources.base import JobSource
from job_fetcher.sources.http_client import session, timeout_seconds

class AshbySource(JobSource):
    def fetch(self, company):
        board = company["source"]["board_name"]
        url = f"https://api.ashbyhq.com/posting-api/job-board/{board}"
        r = session().get(url, params={"includeCompensation": "true"}, timeout=timeout_seconds(),
                         headers={"User-Agent": "PersonalJobFetcher/0.1"})
        r.raise_for_status()
        out = []
        for x in r.json().get("jobs", []):
            out.append(Job(company["id"], company["name"], "ashby",
                           str(x.get("id") or x.get("jobUrl") or "") or None,
                           x.get("title") or "", x.get("location"),
                           x.get("descriptionPlain") or x.get("descriptionHtml"),
                           x.get("jobUrl"), x.get("publishedAt"), x))
        return out
