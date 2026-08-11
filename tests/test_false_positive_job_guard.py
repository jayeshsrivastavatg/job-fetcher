from __future__ import annotations

from job_fetcher.job_quality import plausible_job, prefer_usable_jobs
from job_fetcher.models import Job
from job_fetcher.sources.strict_auto import StrictAutoSource


COMPANY = {"id": "acme", "name": "Acme"}


def _job(title, url="https://example.com/careers/about", source="generic_html"):
    return Job("acme", "Acme", source, url, title, None, None, url)


def test_rejects_navigation_labels_seen_in_real_company_pages():
    titles = [
        "Google Data Policy",
        "Uber Careers",
        "AI and Machine Learning",
        "All Teams",
        "Atlassian Ascend Resources and support for your transformation",
        "Awards",
        "Benefits and Perks",
        "Candidate Resources Hub",
        "Career Growth",
        "Customer Support Ask questions, report bugs & give us feedback",
        "Explore more",
        "Jobs At Navi",
        "Life At Navi",
        "Products",
        "Teams At Navi",
        "Values At Navi",
        "Contact Sales",
        "Contact Support",
        "Developers",
        "SEE OPEN POSITIONS",
        "Support",
    ]
    assert prefer_usable_jobs([_job(title) for title in titles]) == []


def test_keeps_real_role_titles_even_when_they_contain_navigation_words():
    jobs = [
        _job("Support Engineer", "https://example.com/careers/support-engineer"),
        _job("Product Manager", "https://example.com/careers/product-manager"),
        _job("Privacy Engineer", "https://example.com/careers/privacy-engineer"),
        _job("Sales Executive", "https://example.com/careers/sales-executive"),
    ]
    assert [job.title for job in prefer_usable_jobs(jobs)] == [job.title for job in jobs]


def test_concrete_job_url_keeps_unusual_but_valid_title():
    job = _job("Chief of Staff", "https://example.com/jobs/12345-chief-of-staff")
    assert plausible_job(job) is True


def test_strict_auto_does_not_treat_pine_labs_footer_as_jobs():
    html = """
      <main><a href="/careers/open-jobs">Explore opportunities</a></main>
      <footer>
        <a href="/products">Products</a>
        <a href="/developers">Developers</a>
        <a href="/contact-sales">Contact Sales</a>
        <a href="/contact-support">Contact Support</a>
      </footer>
    """
    assert StrictAutoSource._extract_static(COMPANY, html, "https://www.pinelabs.com/careers") == []
    assert "https://www.pinelabs.com/careers/open-jobs" in StrictAutoSource._find_jobs_links(
        html, "https://www.pinelabs.com/careers"
    )


def test_strict_auto_rejects_atlassian_navigation_but_keeps_detail_role():
    html = """
      <a href="/company/careers/teams/engineering">AI and Machine Learning</a>
      <a href="/company/careers/teams">All Teams</a>
      <a href="/company/careers/resources">Benefits and Perks</a>
      <a href="/company/careers/details/25682">Principal Software Engineer</a>
    """
    jobs = StrictAutoSource._extract_static(COMPANY, html, "https://www.atlassian.com/company/careers/all-jobs")
    assert len(jobs) == 1
    assert jobs[0].title == "Principal Software Engineer"
    assert jobs[0].job_url.endswith("/company/careers/details/25682")


def test_porter_darwinbox_link_is_treated_as_an_ats_surface():
    html = '<a href="https://porter.darwinbox.in/ms/candidate/careers">SEE OPEN POSITIONS</a>'
    assert StrictAutoSource._find_ats_links(html, "https://porter.in/careers") == [
        "https://porter.darwinbox.in/ms/candidate/careers"
    ]
