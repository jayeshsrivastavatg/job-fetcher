from __future__ import annotations


def test_second_pass_recovery_registry_loads_from_factory():
    from job_fetcher.sources.factory import build_source
    from job_fetcher.sources.phenom import PhenomSource

    snowflake = {
        "id": "snowflake",
        "name": "Snowflake",
        "career_url": "https://careers.snowflake.com/us/en/search-results",
        "source": {"type": "phenom", "entry_url": "https://careers.snowflake.com/us/en/search-results"},
    }
    assert isinstance(build_source(snowflake), PhenomSource)

    from job_fetcher.sources.recovery import RECOVERY_PLANS
    for company_id in (
        "cars24", "urban_company", "epam", "snowflake", "zomato_blinkit", "winzo"
    ):
        assert company_id in RECOVERY_PLANS
    assert RECOVERY_PLANS["zomato_blinkit"][0] == {
        "kind": "smartrecruiters",
        "company_identifier": "Zomato1",
    }
    assert RECOVERY_PLANS["winzo"][0]["entry_url"] == "https://winzo.keka.com/careers"


def test_allow_zero_auto_source_honors_explicit_empty_page(monkeypatch):
    from job_fetcher.sources.factory import build_source
    import job_fetcher.sources.zero_aware_auto as module

    class Response:
        text = "<html><body><h1>Careers</h1><p>No job openings</p></body></html>"
        def raise_for_status(self):
            return None

    class Session:
        def get(self, *args, **kwargs):
            return Response()

    monkeypatch.setattr(module, "session", lambda: Session())
    company = {
        "id": "zerodha",
        "name": "Zerodha",
        "career_url": "https://careers.zerodha.com/",
        "source": {"type": "auto", "entry_url": "https://careers.zerodha.com/", "allow_zero_jobs": True},
    }
    source = build_source(company)
    assert source.fetch(company) == []
