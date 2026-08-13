from copy import deepcopy
from job_fetcher.config import load_config
from job_fetcher.job_quality import prefer_usable_jobs
from job_fetcher.sources.factory import build_source
from job_fetcher.sources.goldman import GoldmanSource
from job_fetcher.sources.http_client import session, timeout_seconds

company = next(c for c in load_config()["companies"] if c.get("id") == "goldman_sachs")
entry = (company.get("source") or {}).get("entry_url") or "https://higher.gs.com/results"

def snapshot():
    response = session().get(entry, timeout=timeout_seconds(), allow_redirects=True)
    response.raise_for_status()
    jobs = GoldmanSource.parse_listing(company, response.text, response.url)
    return {str(job.external_id) for job in jobs if job.external_id}

before = snapshot()
source = build_source(deepcopy(company))
jobs = list(prefer_usable_jobs(source.fetch(deepcopy(company))) or [])
production = {str(job.external_id) for job in jobs if job.external_id}
after = snapshot()
stable = before & after
missing = stable - production
print({"official_before": len(before), "official_after": len(after), "stable": len(stable), "production": len(jobs), "missing": len(missing)})
if missing or len(production) != len(jobs):
    raise SystemExit(1)
