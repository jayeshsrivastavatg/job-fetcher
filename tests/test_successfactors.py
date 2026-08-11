from job_fetcher.sources.successfactors import SuccessFactorsSource

COMPANY = {"id": "payu", "name": "PayU"}


def test_detects_branded_successfactors_page():
    html = '<img src="https://rmkcdn.successfactors.com/x.png"><div>careerSiteCompanyId</div>'
    assert SuccessFactorsSource.looks_like_successfactors(html, "https://careers.payu.in/PayU/go/_/514880/")


def test_parses_jobs2web_listing_and_total():
    html = '''
      <div>Results 1 – 25 of 132 Page 1 of 6</div>
      <table>
        <tr><td>2031</td><td><a href="/PayU/job/Bengaluru-Senior-Data-Engineer-1/53502780/">Senior Data Engineer 1</a></td><td>Bengaluru, IN</td><td>10 Aug 2026</td></tr>
        <tr><td>2057</td><td><a href="/PayU/job/Gurgaon-M1-Manager-TPM/53500000/">M1 Manager - TPM</a></td><td>Gurgaon, IN</td><td>10 Aug 2026</td></tr>
      </table>
    '''
    jobs, total = SuccessFactorsSource.parse_listing_page(COMPANY, html, "https://careers.payu.in/PayU/go/_/514880/")
    assert total == 132
    assert len(jobs) == 2
    assert jobs[0].external_id == "53502780"
    assert jobs[0].title == "Senior Data Engineer 1"
    assert jobs[0].location == "Bengaluru, IN"
    assert jobs[0].posted_at == "10 Aug 2026"


def test_finds_jobs2web_pagination_links_not_job_links():
    html = '''
      <a href="/PayU/go/_/514880/25/?q=&sortColumn=referencedate&sortDirection=desc">2</a>
      <a href="/PayU/go/_/514880/50/?q=&sortColumn=referencedate&sortDirection=desc">3</a>
      <a href="/PayU/job/Foo/123456/">123456</a>
    '''
    links = SuccessFactorsSource.pagination_links(html, "https://careers.payu.in/PayU/go/_/514880/")
    assert len(links) == 2
    assert links[0].startswith("https://careers.payu.in/PayU/go/_/514880/25/")


def test_crawls_multiple_pages_and_reconciles_total(monkeypatch):
    pages = {
      "https://careers.payu.in/PayU/go/_/514880/": '''
        <div>Results 1 – 1 of 2</div>
        <table><tr><td>1</td><td><a href="/PayU/job/A/111/">Software Engineer</a></td><td>Bengaluru, IN</td><td>10 Aug 2026</td></tr></table>
        <a href="/PayU/go/_/514880/1/?q=">2</a>
      ''',
      "https://careers.payu.in/PayU/go/_/514880/1/?q=": '''
        <div>Results 2 – 2 of 2</div>
        <table><tr><td>2</td><td><a href="/PayU/job/B/222/">Backend Engineer</a></td><td>Gurgaon, IN</td><td>9 Aug 2026</td></tr></table>
      ''',
    }
    class R:
        def __init__(self, url): self.url=url; self.text=pages[url]
        def raise_for_status(self): pass
    class C:
        def get(self, url, **kwargs): return R(url)
    jobs, total = SuccessFactorsSource()._crawl_listing(COMPANY, C(), list(pages)[0], max_pages=5, max_jobs=100)
    assert total == 2
    assert [j.external_id for j in jobs] == ["111", "222"]
