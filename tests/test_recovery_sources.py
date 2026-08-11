from __future__ import annotations

from job_fetcher.sources.recovery import RECOVERY_PLANS


def test_initial_recovery_registry_contains_known_failures():
    expected = {
        "uber",
        "atlassian",
        "microsoft",
        "twilio",
        "swiggy",
        "morgan_stanley",
        "gojek",
        "confluent",
        "makemytrip",
        "citi",
        "american_express",
        "mastercard",
        "rakuten_india",
        "sony_tech_india",
        "ibm_software_labs",
    }
    assert expected <= set(RECOVERY_PLANS)


def test_factory_routes_microsoft_through_dedicated_india_source():
    from job_fetcher.sources.factory import build_source
    from job_fetcher.sources.microsoft_india import MicrosoftIndiaSource

    company = {
        "id": "microsoft",
        "name": "Microsoft",
        "career_url": "https://apply.careers.microsoft.com/careers",
        "source": {"type": "eightfold", "entry_url": "https://apply.careers.microsoft.com/careers"},
    }
    source = build_source(company)
    assert isinstance(source, MicrosoftIndiaSource)


def test_factory_keeps_healthy_company_on_configured_adapter():
    from job_fetcher.sources.factory import build_source
    from job_fetcher.sources.greenhouse import GreenhouseSource

    company = {
        "id": "stripe",
        "name": "Stripe",
        "career_url": "https://stripe.com/jobs",
        "source": {"type": "greenhouse", "board_token": "stripe"},
    }
    assert isinstance(build_source(company), GreenhouseSource)
