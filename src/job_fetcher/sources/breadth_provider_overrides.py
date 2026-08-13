from __future__ import annotations

from copy import deepcopy


# Small, fast-moving provider promotions discovered during breadth-first coverage.
# Keep these separate from the older hand-curated registry so new obvious ATS
# mappings can be added without repeatedly rewriting the larger compatibility map.
BREADTH_PROVIDER_CONFIGS: dict[str, dict] = {
    "intuit": {"type": "smartrecruiters", "company_identifier": "intuit2"},
    "uber": {
        "type": "oracle",
        "entry_url": "https://iaziqy.fa.ocs.oraclecloud.com/hcmUI/CandidateExperience/en/sites/UberCareers/jobs",
        "host": "iaziqy.fa.ocs.oraclecloud.com",
        "site_number": "UberCareers",
        "locale": "en",
        "max_pages": 100,
    },
    "citi": {
        "type": "workday",
        "host": "citi.wd5.myworkdayjobs.com",
        "tenant": "citi",
        "site": "2",
        "locale": "en-US",
        "max_jobs": 10000,
    },
    "dell": {
        "type": "oracle",
        "entry_url": "https://enterpriseplatform.dell.com/hcmUI/CandidateExperience/en/sites/careers/jobs?mode=location",
        "host": "enterpriseplatform.dell.com",
        "site_number": "careers",
        "locale": "en",
        "max_pages": 100,
    },
}


def breadth_provider_config(company_or_id) -> dict | None:
    company_id = company_or_id if isinstance(company_or_id, str) else str((company_or_id or {}).get("id") or "")
    # Uber already has a stronger Phase-2 first-party source and Dell's discovered
    # Oracle host does not expose the anonymous structured collection. Keep both
    # discoveries as evidence only; do not let them replace production routing.
    if company_id in {"uber", "dell"}:
        return None
    config = BREADTH_PROVIDER_CONFIGS.get(company_id)
    return deepcopy(config) if config else None
