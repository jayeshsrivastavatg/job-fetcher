from job_fetcher.models import Job
from job_fetcher.sources.phenom import PhenomSource


def _job(url, external_id=None, title="Software Engineer"):
    return Job(
        company_id="old",
        company_name="old",
        source_type="browser_html",
        external_id=external_id,
        title=title,
        location="Bengaluru, India",
        description="Build software for Lowe's India.",
        job_url=url,
    )


def _company():
    return {
        "id": "lowes_india",
        "name": "Lowe's India",
        "career_url": "https://talent.lowes.com/in/en/search-results",
    }


def test_lowes_locale_job_path_yields_stable_jr_id():
    jobs = PhenomSource._normalize(
        _company(),
        [_job("https://talent.lowes.com/in/en/job/JR-02597303/software-engineer")],
        {"canonical_base_url": "https://talent.lowes.com"},
    )

    assert len(jobs) == 1
    assert jobs[0].external_id == "JR-02597303"
    assert jobs[0].source_type == "phenom"
    assert jobs[0].company_id == "lowes_india"


def test_phenom_normalization_rejects_navigation_links_without_job_identity():
    jobs = PhenomSource._normalize(
        _company(),
        [
            _job("https://talent.lowes.com/in/en/search-results"),
            _job("https://talent.lowes.com/in/en/life-at-lowes"),
        ],
        {"canonical_base_url": "https://talent.lowes.com"},
    )

    assert jobs == []


def test_structured_phenom_row_with_external_id_survives_nonstandard_apply_url():
    jobs = PhenomSource._normalize(
        _company(),
        [_job("https://talent.lowes.com/apply", external_id="JR-02597303")],
        {"canonical_base_url": "https://talent.lowes.com"},
    )

    assert len(jobs) == 1
    assert jobs[0].external_id == "JR-02597303"
