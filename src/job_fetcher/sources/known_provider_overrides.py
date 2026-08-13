from __future__ import annotations

from copy import deepcopy


# Verified source contracts. These companies may advertise jobs on a branded
# careers page, but the live audit has already shown the actual returned vacancy
# records come from the structured provider below. Production fetches therefore go
# straight to that provider instead of re-discovering it through generic HTML.
KNOWN_PROVIDER_CONFIGS: dict[str, dict] = {
    "postman": {"type": "greenhouse", "board_token": "postman"},
    "razorpay": {"type": "greenhouse", "board_token": "razorpaysoftwareprivatelimited"},
    "inmobi": {"type": "greenhouse", "board_token": "inmobi"},
    "hackerrank": {"type": "greenhouse", "board_token": "hackerrank"},
    "elastic": {"type": "greenhouse", "board_token": "elastic"},
    "druva": {"type": "greenhouse", "board_token": "druva"},
    "thoughtworks": {"type": "greenhouse", "board_token": "thoughtworks"},
    "qualtrics": {"type": "greenhouse", "board_token": "qualtrics"},
    "swiggy": {
        "type": "mynexthire",
        "tenant": "swiggy",
        "base_url": "https://swiggy.mynexthire.com",
        "source_short_name": "careers",
        "filter_by_bu_id": -1,
        "origin": "https://careers.swiggy.com",
        "referer": "https://careers.swiggy.com/#/careers",
    },
    "lowes_india": {
        "type": "phenom",
        "entry_url": "https://talent.lowes.com/in/en/search-results",
        "canonical_base_url": "https://talent.lowes.com",
        "browser_max_pages": 20,
        "browser_max_scrolls": 10,
        "browser_load_more_clicks": 10,
        "hydrate_details": True,
        "detail_workers": 8,
        "locale": "en-IN",
    },
    "freshworks": {"type": "smartrecruiters", "company_identifier": "Freshworks"},
    "arista_networks": {"type": "smartrecruiters", "company_identifier": "AristaNetworks"},
    "nagarro": {"type": "smartrecruiters", "company_identifier": "Nagarro1"},
    "zomato_blinkit": {"type": "smartrecruiters", "company_identifier": "Zomato1"},
    "dynatrace": {"type": "smartrecruiters", "company_identifier": "Dynatrace1"},
    "mindtickle": {"type": "lever", "site": "mindtickle"},
    "meesho": {"type": "lever", "site": "meesho"},
    "zeta": {"type": "lever", "site": "zeta"},
    "slice": {
        "type": "kula",
        "entry_url": "https://careers.kula.ai/slice",
        "tenant": "slice",
        "max_jobs": 5000,
    },
    "cashfree": {
        "type": "kula",
        "entry_url": "https://careers.kula.ai/cashfree",
        "tenant": "cashfree",
        "max_jobs": 5000,
    },
    "clevertap": {
        "type": "kula",
        "entry_url": "https://careers.kula.ai/clevertap",
        "tenant": "clevertap",
        "max_jobs": 5000,
    },
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
    "target_india": {
        "type": "workday", "host": "target.wd5.myworkdayjobs.com",
        "tenant": "target", "site": "targetcareers", "locale": "en-US",
        "max_jobs": 10000,
    },
    "home_depot_tech": {
        "type": "workday", "host": "homedepot.wd5.myworkdayjobs.com",
        "tenant": "homedepot", "site": "CareerDepot", "locale": "en-US",
        "max_jobs": 10000,
    },
    "wells_fargo": {
        "type": "workday", "host": "wf.wd1.myworkdayjobs.com",
        "tenant": "wf", "site": "WellsFargoJobs", "locale": "en-US",
        "max_jobs": 10000,
    },
    "mastercard": {
        "type": "workday", "host": "mastercard.wd1.myworkdayjobs.com",
        "tenant": "mastercard", "site": "CorporateCareers", "locale": "en-US",
        "max_jobs": 10000,
    },
    "fidelity": {
        "type": "workday", "host": "fmr.wd1.myworkdayjobs.com",
        "tenant": "fmr", "site": "FidelityCareers", "locale": "en-US",
        "max_jobs": 10000,
    },
    "siemens_healthineers": {
        "type": "workday", "host": "onehealthineers.wd3.myworkdayjobs.com",
        "tenant": "onehealthineers", "site": "SHSJB", "locale": "en-US",
        "max_jobs": 10000,
    },
    "dell": {
        "type": "oracle",
        "entry_url": "https://iawmqy.fa.ocs.oraclecloud.com/hcmUI/CandidateExperience/en/sites/careers",
        "max_jobs": 5000,
        "page_size": 100,
    },
}


def known_provider_config(company_or_id) -> dict | None:
    company_id = company_or_id if isinstance(company_or_id, str) else str((company_or_id or {}).get("id") or "")
    config = KNOWN_PROVIDER_CONFIGS.get(company_id)
    return deepcopy(config) if config else None