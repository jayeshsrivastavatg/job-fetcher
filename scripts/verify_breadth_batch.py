from __future__ import annotations

import argparse
import json
import sys

from job_fetcher.certification import audit_company
from job_fetcher.config import load_config


DEFAULT_COMPANIES = [
    "elastic",
    "druva",
    "thoughtworks",
    "zomato_blinkit",
    "dynatrace",
    "meesho",
    "zeta",
    "slice",
    "cashfree",
    "clevertap",
    "target_india",
    "home_depot_tech",
    "wells_fargo",
    "mastercard",
    "fidelity",
]
KULA_COMPANIES = {"slice", "cashfree", "clevertap"}


def main() -> int:
    parser = argparse.ArgumentParser(description="Fast live validation for breadth-first ATS promotions")
    parser.add_argument("--company", choices=DEFAULT_COMPANIES, required=True)
    args = parser.parse_args()

    companies = {str(c.get("id") or ""): c for c in load_config().get("companies", [])}
    company = companies[args.company]

    # The provider's own count endpoint is the primary completeness witness for
    # Greenhouse/Lever/SmartRecruiters/Workday. Skip separate HTML detail samples
    # here so the batch can be checked cheaply in parallel.
    row = audit_company(company, sample_size=0, detail_timeout=5.0)
    print(json.dumps({
        "id": row.get("id"),
        "name": row.get("name"),
        "verdict": row.get("verdict"),
        "adapter": row.get("adapter"),
        "jobs_found": row.get("jobs_found"),
        "expected_count": row.get("expected_count"),
        "completeness_pct": row.get("completeness_pct"),
        "rejected_non_job_records": row.get("rejected_non_job_records"),
        "valid_url_ratio": row.get("valid_url_ratio"),
        "stable_id_ratio": row.get("stable_id_ratio"),
        "description_ratio": row.get("description_ratio"),
        "location_ratio": row.get("location_ratio"),
        "source_types": row.get("source_types"),
        "count_probe": row.get("count_probe"),
        "failure_category": row.get("failure_category"),
        "error": row.get("error"),
    }, indent=2, ensure_ascii=False))

    # Kula has a direct public board adapter but the general certification layer
    # does not yet expose an independent count probe. For this breadth gate require
    # non-empty usable output; every provider with a count API must certify exactly.
    if args.company in KULA_COMPANIES:
        ok = row.get("verdict") in {"CERTIFIED", "UNVERIFIED"} and int(row.get("jobs_found") or 0) > 0
    else:
        ok = row.get("verdict") == "CERTIFIED"
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
