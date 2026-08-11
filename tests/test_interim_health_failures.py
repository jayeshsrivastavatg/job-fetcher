from __future__ import annotations

from job_fetcher.models import Job


def _company(cid, source_type="auto"):
    return {
        "id": cid,
        "name": cid,
        "career_url": "https://example.com/jobs",
        "source": {"type": source_type, "entry_url": "https://example.com/jobs"},
    }


def test_factory_routes_current_provider_overrides():
    from job_fetcher.sources.amazon import AmazonSource
    from job_fetcher.sources.auto import AutoSource
    from job_fetcher.sources.factory import build_source
    from job_fetcher.sources.phenom import PhenomSource

    assert isinstance(build_source(_company("amazon", "amazon")), AmazonSource)
    assert isinstance(build_source(_company("uber", "auto")), AutoSource)
    assert isinstance(build_source(_company("confluent", "auto")), AutoSource)
    assert isinstance(build_source(_company("snowflake", "phenom")), PhenomSource)


def test_confluent_and_snowflake_use_ashby(monkeypatch):
    import job_fetcher.sources.current_provider_overrides as module

    seen = []

    class FakeAshby:
        def fetch(self, company):
            seen.append(company["source"].get("board_name"))
            return [Job(company["id"], company["name"], "ashby", "1", "Software Engineer", "India", None,
                        "https://jobs.ashbyhq.com/example/1")]

    monkeypatch.setattr(module, "AshbySource", FakeAshby)
    module.ConfluentAshbySource().fetch(_company("confluent"))
    module.SnowflakeAshbySource().fetch(_company("snowflake", "phenom"))
    assert seen == ["confluent", "snowflake"]


def test_amazon_json_parser_builds_canonical_jobs(monkeypatch):
    import job_fetcher.sources.current_provider_overrides as module

    class Response:
        def raise_for_status(self):
            return None

        def json(self):
            return {
                "hits": 1,
                "jobs": [{
                    "id_icims": "10433370",
                    "title": "SDE-II",
                    "job_path": "/en/jobs/10433370/sde-ii",
                    "location": "Hyderabad, TS, IND",
                    "posted_date": "May 28, 2026",
                }],
            }

    class Session:
        def get(self, *args, **kwargs):
            return Response()

    monkeypatch.setattr(module, "session", lambda: Session())
    company = {
        "id": "amazon",
        "name": "Amazon",
        "career_url": "https://www.amazon.jobs/en/search?base_query=Software+Development&country=IND",
        "source": {
            "type": "amazon",
            "entry_url": "https://www.amazon.jobs/en/search?base_query=Software+Development&country=IND",
            "max_pages": 3,
        },
    }
    jobs = module.AmazonJsonSource()._fetch_json(company)
    assert len(jobs) == 1
    assert jobs[0].external_id == "10433370"
    assert jobs[0].job_url == "https://www.amazon.jobs/en/jobs/10433370/sde-ii"
    assert jobs[0].location == "Hyderabad, TS, IND"


def test_uber_numeric_id_reconstruction():
    from job_fetcher.sources.current_provider_overrides import UberIndiaSource

    job = Job("uber", "Uber", "browser_json", "uber:160443", "Staff Program Manager, Tech",
              "Hyderabad, India", None, None, raw={"jobId": 160443})
    assert UberIndiaSource._numeric_id(job, job.raw) == "160443"


def test_sample_detail_extracts_retry_wrapped_429(monkeypatch):
    import job_fetcher.health as health

    class Session:
        def get(self, *args, **kwargs):
            raise RuntimeError("RetryError: too many 429 error responses")

    monkeypatch.setattr(health, "session", lambda: Session())
    job = Job("gojek", "GoTo", "test", "1", "Software Engineer", "Bengaluru, India", None,
              "https://www.gojek.io/careers/view/software-engineer")
    url, ok, code, error = health._sample_detail([job], 1.0)
    assert url == job.job_url
    assert ok is False
    assert code == 429
    assert "429" in error
