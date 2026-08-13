from copy import deepcopy
from job_fetcher.config import load_config
from job_fetcher.sources.factory import build_source

company = next(c for c in load_config()["companies"] if c.get("id") == "lowes_india")
source = build_source(deepcopy(company))
for job in source.fetch(deepcopy(company)):
    if str(job.external_id) == "JR-02597297":
        print({
            "id": job.external_id,
            "title": job.title,
            "location": job.location,
            "description_chars": len(str(job.description or "")),
            "url": job.job_url,
            "raw": job.raw,
        })
        break
else:
    raise SystemExit("target job not found")
