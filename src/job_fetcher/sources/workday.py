from urllib.parse import urljoin
from job_fetcher.models import Job
from job_fetcher.sources.base import JobSource
from job_fetcher.sources.http_client import session, timeout_seconds
from job_fetcher.sources.generic_extract import clean_text

class WorkdaySource(JobSource):
    def fetch(self, company):
        src = company["source"]
        host, tenant, site = src["host"], src["tenant"], src["site"]
        locale = src.get("locale", "en-US")
        api = f"https://{host}/wday/cxs/{tenant}/{site}/jobs"
        s = session(); out = []; offset = 0; limit = 20
        while True:
            body = {"appliedFacets": {}, "limit": limit, "offset": offset, "searchText": ""}
            r = s.post(api, json=body, timeout=timeout_seconds(), headers={"Content-Type": "application/json"})
            r.raise_for_status(); data = r.json(); items = data.get("jobPostings") or []
            for x in items:
                ext = x.get("externalPath")
                job_url = f"https://{host}/{locale}/{site}{ext}" if ext else None
                eid = x.get("bulletFields", [None])[0] if isinstance(x.get("bulletFields"), list) and x.get("bulletFields") else None
                out.append(Job(company["id"], company["name"], "workday", clean_text(eid) or job_url,
                               x.get("title") or "", clean_text(x.get("locationsText")), None,
                               job_url, clean_text(x.get("postedOn")), x))
            offset += len(items)
            total = int(data.get("total") or offset)
            if not items or offset >= total or offset >= int(src.get("max_jobs", 5000)):
                break
        return out
