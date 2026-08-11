from job_fetcher.models import Job

def test_stable_id():
    j=Job("x","X","custom_html",None,"SWE","India",None,"https://example.com/1")
    assert j.stable_external_id() == j.stable_external_id()
