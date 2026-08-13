from __future__ import annotations

from copy import deepcopy


BREADTH_PROVIDER_CONFIGS: dict[str, dict] = {
    "intuit": {"type": "smartrecruiters", "company_identifier": "intuit2"},
    "gojek": {"type": "lever", "site": "GoToGroup"},
    "citi": {
        "type": "workday",
        "host": "citi.wd5.myworkdayjobs.com",
        "tenant": "citi",
        "site": "2",
        "locale": "en-US",
        "max_jobs": 10000,
    },
}


def breadth_provider_config(company_or_id) -> dict | None:
    company_id = company_or_id if isinstance(company_or_id, str) else str((company_or_id or {}).get("id") or "")
    config = BREADTH_PROVIDER_CONFIGS.get(company_id)
    return deepcopy(config) if config else None
