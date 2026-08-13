from __future__ import annotations

import argparse
import json
from copy import deepcopy

from job_fetcher.certification import audit_company
from job_fetcher.config import load_config


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("rank", type=int)
    args = parser.parse_args()
    company = next(c for c in load_config().get("companies", []) if c.get("rank") == args.rank)
    row = audit_company(deepcopy(company), sample_size=3, detail_timeout=10.0)
    result = {
        "rank": args.rank,
        "id": row.get("id"),
        "adapter": row.get("adapter"),
        "verdict": row.get("verdict"),
        "raw_records": row.get("raw_records"),
        "jobs_found": row.get("jobs_found"),
        "expected_count": row.get("expected_count"),
        "completeness_pct": row.get("completeness_pct"),
        "rejected_non_job_records": row.get("rejected_non_job_records"),
        "valid_url_ratio": row.get("valid_url_ratio"),
        "stable_id_ratio": row.get("stable_id_ratio"),
        "description_ratio": row.get("description_ratio"),
        "location_ratio": row.get("location_ratio"),
        "count_probe": row.get("count_probe"),
        "failure_category": row.get("failure_category"),
        "error": row.get("error"),
    }
    print(json.dumps(result, ensure_ascii=False, indent=2), flush=True)
    if row.get("verdict") != "CERTIFIED":
        raise SystemExit(1)


if __name__ == "__main__":
    main()
