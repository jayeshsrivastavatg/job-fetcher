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
from job_fetcher.sources.kula_enriched import EnrichedKulaSource
from job_fetcher.sources.apple import AppleSource
from job_fetcher.sources.meta import MetaSource
from job_fetcher.sources.amazon import AmazonSource
from job_fetcher.sources.manual import ManualSource
from job_fetcher.sources.avature import AvatureSource
from job_fetcher.sources.strict_auto import StrictAutoSource
from job_fetcher.sources.atlassian import AtlassianSource
from job_fetcher.sources.phenom import PhenomSource
from job_fetcher.sources.goldman import GoldmanSource
from job_fetcher.sources.trakstar import TrakstarSource

SOURCES = {
    "auto": StrictAutoSource,
    "greenhouse": GreenhouseSource,
    "lever": LeverSource,
    "ashby": AshbySource,
    "smartrecruiters": SmartRecruitersSource,
    "workday": WorkdaySource,
    "oracle": OracleSource,
    "eightfold": EightfoldSource,
    "successfactors": SuccessFactorsSource,
    "kula": EnrichedKulaSource,
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
    """Return the configured adapter, enhanced with recovery where necessary.

    Recovery wrappers subclass the configured adapter, so existing diagnostics and
    type expectations remain valid while actual fetches get hardened first-party
    fallback paths.
    """
    company_id = str(company.get("id") or "")

    # A few employers have moved to a cleaner public provider/API than the source
    # originally discovered for their branded career page. Keep these overrides
    # isolated so they are easy to remove if the employer changes providers again.
    if company_id in {"amazon", "uber", "snowflake", "confluent"}:
        from job_fetcher.sources.current_provider_overrides import (
            AmazonJsonSource,
            ConfluentAshbySource,
            SnowflakeAshbySource,
        )
        from job_fetcher.sources.guarded_provider_overrides import GuardedUberIndiaSource
        current = {
            "amazon": AmazonJsonSource,
            "uber": GuardedUberIndiaSource,
            "snowflake": SnowflakeAshbySource,
            "confluent": ConfluentAshbySource,
        }
        return current[company_id]()

    # `allow_zero_jobs` means an explicit "no openings" page is a valid result.
    # Check that signal before generic extraction can mistake navigation links for
    # vacancies (Zerodha is the current example).
    source = company.get("source") or {}
    if source.get("type") == "auto" and source.get("allow_zero_jobs"):
        from job_fetcher.sources.zero_aware_auto import ZeroAwareAutoSource
        return ZeroAwareAutoSource()

    # Importing the second-pass registry mutates the shared recovery plan mapping
    # before we decide whether this company needs a wrapper.
    import job_fetcher.sources.recovery_extra  # noqa: F401
    from job_fetcher.sources.recovery import has_recovery_plan

    if has_recovery_plan(company):
        from job_fetcher.sources.recovery_adapters import build_recovery_adapter
        return build_recovery_adapter(company)
    return build_raw_source(company)
