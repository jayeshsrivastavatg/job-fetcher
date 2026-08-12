from __future__ import annotations

from copy import deepcopy

from job_fetcher.sources.base import JobSource
from job_fetcher.sources.greenhouse import GreenhouseSource
from job_fetcher.sources.lever import LeverSource
from job_fetcher.sources.smartrecruiters import SmartRecruitersSource
from job_fetcher.sources.workday import WorkdaySource


# These are not heuristic guesses. They are source contracts for companies whose
# branded careers page has already been verified to be backed by a structured ATS.
# Keeping them here lets production ingestion and the certification runner use the
# same authoritative source even before companies.yaml is fully migrated.
KNOWN_PROVIDER_CONFIGS: dict[str, dict] = {
    "postman": {"type": "greenhouse", "board_token": "postman"},
    "inmobi": {"type": "greenhouse", "board_token": "inmobi"},
    "hackerrank": {"type": "greenhouse", "board_token": "hackerrank"},
    "freshworks": {"type": "smartrecruiters", "company_identifier": "Freshworks"},
    "arista_networks": {"type": "smartrecruiters", "company_identifier": "AristaNetworks"},
    "nagarro": {"type": "smartrecruiters", "company_identifier": "Nagarro1"},
    "mindtickle": {"type": "lever", "site": "mindtickle"},
    "broadcom_vmware": {
        "type": "workday",
        "host": "broadcom.wd1.myworkdayjobs.com",
        "tenant": "broadcom",
        "site": "External_Career",
        "locale": "en-US",
    },
    "visa": {
        "type": "workday",
        "host": "visa.wd5.myworkdayjobs.com",
        "tenant": "visa",
        "site": "Visa",
        "locale": "en-US",
    },
    "browserstack": {
        "type": "workday",
        "host": "browserstack.wd3.myworkdayjobs.com",
        "tenant": "browserstack",
        "site": "External",
        "locale": "en-US",
    },
}

_PROVIDER_CLASSES = {
    "greenhouse": GreenhouseSource,
    "lever": LeverSource,
    "smartrecruiters": SmartRecruitersSource,
    "workday": WorkdaySource,
}


def known_provider_config(company_or_id) -> dict | None:
    company_id = company_or_id if isinstance(company_or_id, str) else str((company_or_id or {}).get("id") or "")
    config = KNOWN_PROVIDER_CONFIGS.get(company_id)
    return deepcopy(config) if config else None


def effective_provider_company(company: dict) -> dict:
    config = known_provider_config(company)
    if not config:
        return company
    candidate = deepcopy(company)
    candidate["source"] = config
    return candidate


class KnownProviderSource(JobSource):
    """Fetch only from a verified structured provider; do not fall back to HTML guessing."""

    def fetch(self, company):
        candidate = effective_provider_company(company)
        source_type = str((candidate.get("source") or {}).get("type") or "")
        source_cls = _PROVIDER_CLASSES.get(source_type)
        if source_cls is None:
            raise RuntimeError(f"unsupported_known_provider:{source_type}")
        return source_cls().fetch(candidate)
