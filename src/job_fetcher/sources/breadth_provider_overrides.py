from __future__ import annotations

from copy import deepcopy


# Small, fast-moving provider promotions discovered during breadth-first coverage.
# Keep these separate from the older hand-curated registry so new obvious ATS
# mappings can be added without repeatedly rewriting the larger compatibility map.
BREADTH_PROVIDER_CONFIGS: dict[str, dict] = {
    "intuit": {"type": "smartrecruiters", "company_identifier": "intuit2"},
}


def breadth_provider_config(company_or_id) -> dict | None:
    company_id = company_or_id if isinstance(company_or_id, str) else str((company_or_id or {}).get("id") or "")
    config = BREADTH_PROVIDER_CONFIGS.get(company_id)
    return deepcopy(config) if config else None
