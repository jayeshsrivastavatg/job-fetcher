from job_fetcher.models import Job
import job_fetcher.sources.phenom as phenom_module
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


def test_phenom_rejects_navigation_url_copied_into_external_id():
    jobs = PhenomSource._normalize(
        _company(),
        [
            _job(
                "https://talent.lowes.com/in/en/home",
                external_id="https://talent.lowes.com/i/in/en/home",
                title="Current Associates",
            )
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


class _DetailResponse:
    url = "https://talent.lowes.com/in/en/job/JR-02597303/software-engineer"
    text = """<html><head><script type="application/ld+json">
    {
      "@type": "JobPosting",
      "title": "Software Engineer",
      "identifier": {"@type": "PropertyValue", "value": "JR-02597303"},
      "url": "https://talent.lowes.com/in/en/job/JR-02597303/software-engineer",
      "datePosted": "2026-08-12",
      "jobLocation": {"@type": "Place", "address": {
        "@type": "PostalAddress", "addressLocality": "Bengaluru",
        "addressRegion": "Karnataka", "addressCountry": "India"
      }},
      "description": "<p>Design and build reliable software systems for Lowe's India. Own production quality, testing, operations, and collaboration across engineering teams.</p>"
    }
    </script></head></html>"""

    def raise_for_status(self):
        return None


class _DetailClient:
    def get(self, *_args, **_kwargs):
        return _DetailResponse()


def test_lowes_detail_jobposting_hydrates_location_and_description(monkeypatch):
    job = _job(
        "https://talent.lowes.com/in/en/job/JR-02597303/software-engineer",
        external_id="JR-02597303",
    )
    job.location = None
    job.description = None
    monkeypatch.setattr(phenom_module, "session", lambda: _DetailClient())

    hydrated = PhenomSource._hydrate_one(_company(), job)

    assert hydrated.external_id == "JR-02597303"
    assert hydrated.location == "Bengaluru, Karnataka, India"
    assert "Design and build reliable software systems" in hydrated.description
    assert hydrated.posted_at == "2026-08-12"
    assert hydrated.raw["detail_source"] == "public_jobposting_jsonld"
