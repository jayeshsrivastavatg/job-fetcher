from job_fetcher.sources.auto import AutoSource, EMPTY_MARKERS
from job_fetcher.sources.generic_extract import extract_jsonld, extract_jobs_from_json

COMPANY = {"id":"acme","name":"Acme"}

def test_direct_provider_identification_helpers():
    assert AutoSource._greenhouse_token(["postman"]) == "postman"
    assert AutoSource._smartrecruiters_ident(["Freshworks"]) == "Freshworks"

def test_jsonld_jobposting():
    html = '''<script type="application/ld+json">{
      "@context":"https://schema.org","@type":"JobPosting","title":"Software Engineer",
      "identifier":{"value":"123"},"url":"/jobs/123","datePosted":"2026-08-01",
      "jobLocation":{"address":{"addressLocality":"Bengaluru","addressCountry":"India"}}
    }</script>'''
    jobs = extract_jsonld(COMPANY, html, "https://example.com/careers")
    assert len(jobs) == 1
    assert jobs[0].title == "Software Engineer"
    assert jobs[0].location == "Bengaluru, India"

def test_nested_xhr_job_extraction():
    payload={"data":{"results":[{"jobId":"7","jobTitle":"Backend Engineer","location":"India","url":"/job/7"}]}}
    jobs=extract_jobs_from_json(COMPANY,payload,"https://example.com")
    assert len(jobs)==1 and jobs[0].external_id == "7"

def test_empty_state_marker():
    assert EMPTY_MARKERS.search("There are no current openings at this time")
