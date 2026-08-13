from job_fetcher.config import load_config
from job_fetcher.job_quality import prefer_usable_jobs, valid_http_url
from job_fetcher.sources.cohesity import CohesitySource, authoritative_count

company = next(c for c in load_config()["companies"] if c.get("id") == "cohesity")
jobs = prefer_usable_jobs(CohesitySource().fetch(company))
expected, evidence = authoritative_count()
unique = {str(job.external_id) for job in jobs if job.external_id}
valid = all(valid_http_url(job.job_url) for job in jobs)
print({"jobs": len(jobs), "unique": len(unique), "expected": expected, "valid_urls": valid, "evidence": evidence})
if not (len(jobs) == len(unique) == expected and valid):
    raise SystemExit(1)
