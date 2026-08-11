from pathlib import Path
import yaml

from job_fetcher.sources.apple import AppleSource
from job_fetcher.sources.meta import MetaSource
from job_fetcher.sources.amazon import AmazonSource
from job_fetcher.sources.eightfold import EightfoldSource
from job_fetcher.sources.factory import build_source
from job_fetcher.service import classify_error


def test_apple_search_page_parses_role_location_and_date():
    c={"id":"apple","name":"Apple"}
    html='''
    <section class="job-card">
      <h3><a href="/en-in/details/200669067-1052/software-development-engineer-in-test">Software Development Engineer in Test</a></h3>
      <div>Software and Services 04 Aug 2026 Location Hyderabad Actions Role Number: 200669067-1052 Weekly Hours: 40 Hours</div>
      <a href="/en-in/details/200669067-1052/software-development-engineer-in-test">See full role description</a>
    </section>'''
    jobs=AppleSource.parse_search_page(c,html,'https://jobs.apple.com/en-in/search?location=india-INDC')
    assert len(jobs)==1
    assert jobs[0].external_id=='200669067-1052'
    assert jobs[0].title=='Software Development Engineer in Test'
    assert jobs[0].location=='Hyderabad'
    assert jobs[0].posted_at=='04 Aug 2026'


def test_apple_page_parameter_preserves_filters():
    u=AppleSource.with_page('https://jobs.apple.com/en-in/search?location=india-INDC&team=software',3)
    assert 'location=india-INDC' in u and 'team=software' in u and 'page=3' in u


def test_meta_search_url_and_card_parser():
    c={"id":"meta","name":"Meta (Facebook)"}
    u=MetaSource.search_url('https://www.metacareers.com/jobsearch/','Bangalore, India',2)
    assert 'offices%5B0%5D=Bangalore%2C+India' in u and 'page=2' in u
    html='''<div class="job-card"><a href="/profile/job_details/1796275791360038/">ASIC Engineer, Design</a><div>Bangalore, India Engineering</div></div>'''
    jobs=MetaSource.parse_search_page(c,html,'https://www.metacareers.com/jobsearch/',default_location='Bangalore, India')
    assert len(jobs)==1
    assert jobs[0].external_id=='1796275791360038'
    assert jobs[0].job_url=='https://www.metacareers.com/profile/job_details/1796275791360038/'
    assert jobs[0].location=='Bangalore, India'


def test_amazon_search_parser_and_next_page():
    c={"id":"amazon","name":"Amazon"}
    html='''
      <article><a href="/en/jobs/3205082/software-development-engineer-2-in-ads">Software Development Engineer - 2, IN-Ads</a>
      <div>Location: IND, KA, Bengaluru | Posted: August 10, 2026</div></article>
      <a rel="next" href="/en/search?country=IND&offset=10">Next</a>
    '''
    jobs=AmazonSource.parse_search_page(c,html,'https://www.amazon.jobs/en/search?country=IND')
    assert len(jobs)==1
    assert jobs[0].external_id=='3205082'
    assert jobs[0].location=='IND, KA, Bengaluru'
    assert jobs[0].posted_at=='August 10, 2026'
    assert AmazonSource.next_page(html,'https://www.amazon.jobs/en/search?country=IND').endswith('offset=10')


def test_eightfold_supports_microsoft_canonical_job_template():
    c={
      'id':'microsoft','name':'Microsoft','career_url':'https://apply.careers.microsoft.com/careers?domain=microsoft.com',
      'source':{
        'type':'eightfold','canonical_base_url':'https://apply.careers.microsoft.com',
        'canonical_job_path_template':'careerhub/explore/jobs/{id}?domain=microsoft.com&hl=en'
      }
    }
    assert EightfoldSource._job_url(c,'1970393556861699') == 'https://apply.careers.microsoft.com/careerhub/explore/jobs/1970393556861699?domain=microsoft.com&hl=en'


def test_priority_company_config_routes_and_flipkart_is_safe_disabled():
    root=Path(__file__).resolve().parents[1]
    data=yaml.safe_load((root/'config/companies.yaml').read_text())
    by={c['id']:c for c in data['companies']}
    assert by['meta']['source']['type']=='meta' and by['meta']['enabled'] is True
    assert by['apple']['source']['type']=='apple' and by['apple']['enabled'] is True
    assert by['microsoft']['source']['type']=='eightfold' and by['microsoft']['enabled'] is True
    assert by['amazon']['source']['type']=='amazon' and by['amazon']['enabled'] is True
    assert by['flipkart']['source']['type']=='manual' and by['flipkart']['enabled'] is False
    assert build_source(by['meta']).__class__.__name__=='MetaSource'
    assert build_source(by['apple']).__class__.__name__=='AppleSource'
    assert build_source(by['amazon']).__class__.__name__=='AmazonSource'


def test_manual_source_error_is_classified():
    err=RuntimeError('automation_disallowed_or_unavailable: terms restriction')
    assert classify_error(err)=='manual_or_approved_feed_required'
