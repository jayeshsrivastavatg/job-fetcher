from job_fetcher.models import Job
from job_fetcher.sources.base import JobSource
from job_fetcher.sources.http_client import session, timeout_seconds
from job_fetcher.sources.generic_extract import clean_text

class SmartRecruitersSource(JobSource):
    def fetch(self, company):
        ident = company["source"]["company_identifier"]
        s = session(); offset = 0; out = []
        while True:
            r = s.get(f"https://api.smartrecruiters.com/v1/companies/{ident}/postings",
                      params={"limit": 100, "offset": offset}, timeout=timeout_seconds())
            r.raise_for_status(); data = r.json(); items = data.get("content") or []
            for x in items:
                loc = x.get("location") or {}
                location = ", ".join(str(loc.get(k)) for k in ("city", "region", "country") if loc.get(k)) or None
                ref = x.get("ref") or x.get("id")
                out.append(Job(company["id"], company["name"], "smartrecruiters", str(ref) if ref else None,
                               x.get("name") or "", location, None, x.get("ref") or None,
                               x.get("releasedDate"), x))
            offset += len(items)
            if not items or offset >= int(data.get("totalFound") or offset):
                break
        return out
