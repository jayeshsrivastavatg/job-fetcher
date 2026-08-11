from job_fetcher.sources.greenhouse import GreenhouseSource
from job_fetcher.sources.lever import LeverSource
from job_fetcher.sources.ashby import AshbySource
from job_fetcher.sources.custom_api import CustomApiSource
from job_fetcher.sources.custom_html import CustomHtmlSource
from job_fetcher.sources.playwright_source import PlaywrightSource
from job_fetcher.sources.smartrecruiters import SmartRecruitersSource
from job_fetcher.sources.workday import WorkdaySource
from job_fetcher.sources.oracle import OracleSource
from job_fetcher.sources.eightfold import EightfoldSource
from job_fetcher.sources.successfactors import SuccessFactorsSource
from job_fetcher.sources.kula import KulaSource
from job_fetcher.sources.apple import AppleSource
from job_fetcher.sources.meta import MetaSource
from job_fetcher.sources.amazon import AmazonSource
from job_fetcher.sources.manual import ManualSource
from job_fetcher.sources.avature import AvatureSource
from job_fetcher.sources.auto import AutoSource
from job_fetcher.sources.atlassian import AtlassianSource
from job_fetcher.sources.phenom import PhenomSource
from job_fetcher.sources.goldman import GoldmanSource
from job_fetcher.sources.trakstar import TrakstarSource

SOURCES = {
    "auto": AutoSource,
    "greenhouse": GreenhouseSource,
    "lever": LeverSource,
    "ashby": AshbySource,
    "smartrecruiters": SmartRecruitersSource,
    "workday": WorkdaySource,
    "oracle": OracleSource,
    "eightfold": EightfoldSource,
    "successfactors": SuccessFactorsSource,
    "kula": KulaSource,
    "apple": AppleSource,
    "meta": MetaSource,
    "amazon": AmazonSource,
    "avature": AvatureSource,
    "manual": ManualSource,
    "custom_api": CustomApiSource,
    "custom_html": CustomHtmlSource,
    "playwright": PlaywrightSource,
    "atlassian": AtlassianSource,
    "phenom": PhenomSource,
    "goldman": GoldmanSource,
    "trakstar": TrakstarSource,
}


def build_raw_source(company):
    """Build exactly the adapter configured in companies.yaml."""
    source_type = company["source"]["type"]
    if source_type not in SOURCES:
        raise ValueError(f"Unsupported source type: {source_type}")
    return SOURCES[source_type]()


def build_source(company):
    """Public factory contract: return the adapter configured for the company.

    Recovery is applied by the fetch/health orchestration layer rather than here,
    so diagnostics and existing callers can still rely on the configured adapter
    type (AtlassianSource, EightfoldSource, AvatureSource, etc.).
    """
    return build_raw_source(company)
