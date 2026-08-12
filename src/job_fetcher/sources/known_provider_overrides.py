from __future__ import annotations

from copy import deepcopy


# Verified source contracts. These companies may advertise jobs on a branded
# careers page, but the live audit has already shown the actual returned vacancy
# records come from the structured provider below. Production fetches therefore go
# straight to that provider instead of re-discovering it through generic HTML.
KNOWN_PROVIDER_CONFIGS: dict[str, dict] = {
    "postman": {"type": "greenhouse", "board_token": "postman"},
    "inmobi": {"type": "greenhouse", "board_token": "inmobi"},
    "hackerrank": {"type": "greenhouse", "board_token": "hackerrank"},
    "freshworks": {"type": "smartrecruiters", "company_identifier": "Freshworks"},
    "arista_networks": {"type": "smartrecruiters", "company_identifier": "AristaNetworks"},
    "nagarro": {"type": "smartrecruiters", "company_identifier": "Nagarro1"},
    "mindtickle": {"type": "lever", "site": "mindtickle"},
    "broadcom_vmware": {
        "type": "workday", "host": "broadcom.wd1.myworkdayjobs.com",
        "tenant": "broadcom", "site": "External_Career", "locale": "en-US",
    },
    "visa": {
        "type": "workday", "host": "visa.wd5.myworkdayjobs.com",
        "tenant": "visa", "site": "Visa", "locale": "en-US",
    },
    "browserstack": {
        "type": "workday", "host": "browserstack.wd3.myworkdayjobs.com",
        "tenant": "browserstack", "site": "External", "locale": "en-US",
    },
    "cisco": {
        "type": "workday", "host": "cisco.wd5.myworkdayjobs.com",
        "tenant": "cisco", "site": "Cisco_Careers", "locale": "en-US",
    },
    "barclays": {
        "type": "workday", "host": "barclays.wd3.myworkdayjobs.com",
        "tenant": "barclays", "site": "External_Career_Site_Barclays", "locale": "en-US",
    },
    "hpe": {
        "type": "workday", "host": "hpe.wd5.myworkdayjobs.com",
        "tenant": "hpe", "site": "Jobsathpe", "locale": "en-US",
    },
    "sprinklr": {
        "type": "workday", "host": "sprinklr.wd1.myworkdayjobs.com",
        "tenant": "sprinklr", "site": "careers", "locale": "en-US",
    },
}


def known_provider_config(company_or_id) -> dict | None:
    company_id = company_or_id if isinstance(company_or_id, str) else str((company_or_id or {}).get("id") or "")
    config = KNOWN_PROVIDER_CONFIGS.get(company_id)
    return deepcopy(config) if config else None
