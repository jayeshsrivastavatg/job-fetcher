from __future__ import annotations

import json
from copy import deepcopy

from job_fetcher.certification import audit_company
from job_fetcher.config import load_config


def main():
    candidates = []
    for company in load_config().get("companies", []):
        source = company.get("source") or {}
        entry = str(source.get("entry_url") or company.get("career_url") or "").rstrip("/")
        if company.get("enabled", True) and source.get("type") == "auto" and entry.endswith("/jobs/Careers"):
            candidates.append(company)

    rows = []
    for company in candidates:
        row = audit_company(deepcopy(company), sample_size=0, detail_timeout=5.0)
        result = {
            "id": row.get("id"),
            "verdict": row.get("verdict"),
            "jobs_found": row.get("jobs_found"),
            "expected_count": row.get("expected_count"),
            "rejected_non_job_records": row.get("rejected_non_job_records"),
            "stable_id_ratio": row.get("stable_id_ratio"),
            "valid_url_ratio": row.get("valid_url_ratio"),
            "description_ratio": row.get("description_ratio"),
            "location_ratio": row.get("location_ratio"),
            "error": row.get("error"),
        }
        rows.append(result)
        print(json.dumps(result, ensure_ascii=False), flush=True)

    print(json.dumps({"candidates": len(rows), "certified": sum(r["verdict"] == "CERTIFIED" for r in rows)}, ensure_ascii=False))
    if not rows or any(row["verdict"] != "CERTIFIED" for row in rows):
        raise SystemExit(1)


if __name__ == "__main__":
    main()
