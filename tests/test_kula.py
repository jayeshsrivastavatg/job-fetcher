from job_fetcher.sources.kula import KulaSource

COMPANY = {"id": "cashfree", "name": "Cashfree Payments"}


def test_parse_kula_url():
    assert KulaSource.parse_kula_url("https://careers.kula.ai/cashfree?jobs=true")["tenant"] == "cashfree"
    assert KulaSource.parse_kula_url("https://careers.kula.ai/clevertap/12616")["tenant"] == "clevertap"
    assert KulaSource.parse_kula_url("https://example.com/jobs") is None


def test_parse_kula_apply_card_and_canonicalize():
    html = '''
    <section>
      <div class="job-card">
        <h3>Software Development Engineer 2</h3>
        <p>Engineering</p>
        <p>Bellandur, Karnataka, India</p>
        <p>Full Time • On-Site</p>
        <a href="/cashfree/32123/apply">Apply Now</a>
      </div>
    </section>
    '''
    jobs = KulaSource.parse_board(COMPANY, html, "https://careers.kula.ai/cashfree", "cashfree")
    assert len(jobs) == 1
    j = jobs[0]
    assert j.external_id == "32123"
    assert j.title == "Software Development Engineer 2"
    assert j.location == "Bellandur, Karnataka, India"
    assert j.job_url == "https://careers.kula.ai/cashfree/32123"
    assert j.raw["employment_type"] == "Full Time"
    assert j.raw["work_type"] == "On-Site"


def test_accepts_jobs_path_and_dedupes_apply_link():
    html = '''
      <div><h3>Senior Backend Engineer</h3><p>Mumbai, Maharashtra, India</p>
      <a href="/clevertap/jobs/24305">Job details</a>
      <a href="/clevertap/24305/apply">Apply Now</a></div>
    '''
    c = {"id":"clevertap","name":"CleverTap"}
    jobs = KulaSource.parse_board(c, html, "https://careers.kula.ai/clevertap", "clevertap")
    assert len(jobs) == 1
    assert jobs[0].external_id == "24305"
    assert jobs[0].job_url == "https://careers.kula.ai/clevertap/24305"
