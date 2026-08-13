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
from job_fetcher.sources.strict_auto import StrictAutoSource
from job_fetcher.sources.atlassian import AtlassianSource
from job_fetcher.sources.phenom import PhenomSource
from job_fetcher.sources.goldman import GoldmanSource
from job_fetcher.sources.trakstar import TrakstarSource
from job_fetcher.sources.mynexthire import MyNextHireSource

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
    "mynexthire": MyNextHireSource,
}


def build_raw_source(company):
    """Build exactly the adapter in the company's current in-memory source contract."""
    source_type = company["source"]["type"]
    if source_type not in SOURCES:
        raise ValueError(f"Unsupported source type: {source_type}")
    return SOURCES[source_type]()


def build_source(company):
    """Return the most authoritative known adapter for this company.

    Structured/provider contracts are preferred over branded-page HTML heuristics.
    When a verified provider override exists we update only the in-memory company
    object. This does not rewrite companies.yaml, but it lets fetch/health/
    certification all see and validate the same effective source contract.
    """
    company_id = str(company.get("id") or "")

    # Phase 3: these employers' own careers pages are powered by the public PCS
    # search JSON endpoint. Read the exact full result-row space, preserve each
    # stable vacancy ID, and explicitly handle provider rows that repeat one ID.
    if company_id in {"microsoft", "twilio", "morgan_stanley"}:
        from job_fetcher.sources.eightfold_pcsx_exhaustive import EightfoldPcsxExhaustiveSource
        return EightfoldPcsxExhaustiveSource()

    # Phase 2 exact sources use first-party inventories when one is enumerable.
    # Navi is intentionally fail-closed: its branded careers surface is access
    # restricted and no approved enumerable first-party feed has been identified.
    if company_id in {"uber", "atlassian", "navi"}:
        from job_fetcher.sources.phase2_exact import (
            AtlassianListingsApiSource,
            NaviOfficialCareersSource,
            UberJobsApiSource,
        )
        return {
            "uber": UberJobsApiSource,
            "atlassian": AtlassianListingsApiSource,
            "navi": NaviOfficialCareersSource,
        }[company_id]()

    # Cohesity publishes the complete grouped inventory used by its own careers UI
    # from a first-party JSON endpoint. Prefer that over a separate Workday view.
    if company_id == "cohesity":
        from job_fetcher.sources.cohesity import CohesitySource
        return CohesitySource()

    # ServiceNow's public SmartRecruiters listing is useful but has been observed
    # missing jobs that are simultaneously live on careers.servicenow.com. Use a
    # composite source whose invariant is official website inventory <= app output.
    if company_id == "servicenow":
        from job_fetcher.sources.servicenow import ServiceNowSource
        return ServiceNowSource()

    # A few employers have moved to a cleaner public provider/API than the source
    # originally discovered for their branded career page.
    if company_id in {"amazon", "snowflake", "confluent"}:
        from job_fetcher.sources.current_provider_overrides import (
            AmazonJsonSource,
            ConfluentAshbySource,
            SnowflakeAshbySource,
        )
        current = {
            "amazon": AmazonJsonSource,
            "snowflake": SnowflakeAshbySource,
            "confluent": ConfluentAshbySource,
        }
        return current[company_id]()

    # Promote branded pages whose underlying ATS is already known. Do not fall
    # back to generic HTML guessing for these companies: provider failure should be
    # visible rather than replaced by plausible-looking navigation links.
    from job_fetcher.sources.known_provider_overrides import known_provider_config
    effective_source = known_provider_config(company_id)
    if effective_source:
        company["source"] = effective_source
        return build_raw_source(company)

    # `allow_zero_jobs` means an explicit "no openings" page is a valid result.
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
