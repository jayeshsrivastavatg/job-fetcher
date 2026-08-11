from __future__ import annotations

from job_fetcher.sources.official_html import OfficialHtmlSource, visible_challenge


def test_dormant_captcha_script_is_not_a_challenge():
    html = """
    <html><head><script>window.config = {captchaEnabled: true};</script></head>
    <body><h1>Careers</h1><p>162 jobs</p></body></html>
    """
    assert visible_challenge(html) is False


def test_visible_human_verification_is_a_challenge():
    html = "<html><body><h1>Verify you are human</h1><p>Complete the security check.</p></body></html>"
    assert visible_challenge(html) is True


def test_official_html_parses_generic_apply_link_from_microsoft_style_card():
    company = {"id": "microsoft", "name": "Microsoft"}
    html = """
    <section class="job-card">
      <h3>Senior Software Engineer</h3>
      <div>India, Karnataka, Bangalore</div>
      <div>2026-07-27</div>
      <p>Build reliable distributed systems using Java and cloud services.</p>
      <a href="https://apply.careers.microsoft.com/careers/job/1970393556952571?hl=en">See details</a>
    </section>
    """
    jobs = OfficialHtmlSource.parse_page(
        company,
        html,
        "https://careers.microsoft.com/v2/global/en/locations/india.html",
        {"job_href_patterns": [r"apply\.careers\.microsoft\.com/careers/job/\d+"]},
    )
    assert len(jobs) == 1
    assert jobs[0].title == "Senior Software Engineer"
    assert "India" in (jobs[0].location or "")
    assert jobs[0].external_id == "1970393556952571"


def test_official_html_parses_oracle_branded_job_link():
    company = {"id": "american_express", "name": "American Express"}
    html = """
    <article>
      <h2>Software Engineer II</h2>
      <p>Gurgaon, Haryana, India</p>
      <a href="/en/sites/CX_1/job/26011155/">View details</a>
    </article>
    """
    jobs = OfficialHtmlSource.parse_page(
        company,
        html,
        "https://careers.americanexpress.com/en/sites/CX_1/jobs",
        {"job_href_patterns": [r"/en/sites/CX_1/job/\d+"]},
    )
    assert len(jobs) == 1
    assert jobs[0].title == "Software Engineer II"
    assert jobs[0].external_id == "26011155"
    assert "India" in (jobs[0].location or "")


def test_recovery_registry_covers_every_current_red_failure():
    from job_fetcher.sources.recovery import RECOVERY_PLANS

    # Captured from the failing-company run on 2026-08-12. Some of these companies
    # now have a stronger dedicated/provider override, but keeping the recovery
    # registry populated preserves the older fallback diagnostics and test fixtures.
    expected = {
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
    assert isinstance(build_source(company), MicrosoftIndiaSource)


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
