from __future__ import annotations

import json
from copy import deepcopy

from job_fetcher.certification import audit_company
from job_fetcher.config import load_config
from job_fetcher.sources.breadth_provider_overrides import breadth_provider_config
from job_fetcher.sources.known_provider_overrides import known_provider_config


def effective_source(company: dict) -> dict:
    company_id = str(company.get("id") or "")
    return known_provider_config(company_id) or breadth_provider_config(company_id) or dict(company.get("source") or {})


def main():
    rows = []
    for company in load_config().get("companies", []):
        if not company.get("enabled", True):
            continue
        if str(effective_source(company).get("type") or "").casefold() != "workday":
            continue
        row = audit_company(deepcopy(company), sample_size=0, detail_timeout=5.0)
        rows.append({
            "id": row.get("id"),
            "verdict": row.get("verdict"),
            "jobs_found": row.get("jobs_found"),
            "expected_count": row.get("expected_count"),
            "rejected_non_job_records": row.get("rejected_non_job_records"),
            "valid_url_ratio": row.get("valid_url_ratio"),
            "stable_id_ratio": row.get("stable_id_ratio"),
            "failure_category": row.get("failure_category"),
            "error": row.get("error"),
        })
        print(json.dumps(rows[-1], ensure_ascii=False), flush=True)

    print(json.dumps({
        "workday_companies": len(rows),
        "certified": sum(row["verdict"] == "CERTIFIED" for row in rows),
        "not_certified": [row["id"] for row in rows if row["verdict"] != "CERTIFIED"],
    }, ensure_ascii=False), flush=True)


if __name__ == "__main__":
    main()
