from __future__ import annotations

from copy import deepcopy
from typing import Any

from job_fetcher.sources.base import JobSource
from job_fetcher.sources.generic_extract import dedupe
from job_fetcher.sources.official_html import OfficialHtmlSource
from job_fetcher.sources.recovery_browser import RecoveryBrowserSource


# First-party recovery surfaces for the red/failing companies reported by the UI.
# The configured source remains the final fallback and the public source factory
# continues to return the configured adapter type for diagnostics/tests.
RECOVERY_PLANS: dict[str, list[dict[str, Any]]] = {
    "microsoft": [
        {
            "kind": "official_html",
            "entry_url": "https://careers.microsoft.com/v2/global/en/locations/india.html",
            "default_location": "India",
            "require_india": True,
            "max_pages": 3,
            "job_href_patterns": [r"apply\.careers\.microsoft\.com/careers/job/\d+"],
        },
        {
            "kind": "recovery_browser",
            "entry_url": "https://careers.microsoft.com/v2/global/en/locations/india.html",
            "browser_max_pages": 3,
            "browser_max_scrolls": 8,
        },
    ],
    "twilio": [
        {
            "kind": "official_html",
            "entry_url": "https://jobs.twilio.com/careers?domain=twilio.com&hl=en",
            "max_pages": 30,
            "job_href_patterns": [r"/careers/job/\d+"],
        },
        {
            "kind": "recovery_browser",
            "entry_url": "https://jobs.twilio.com/careers?domain=twilio.com&hl=en",
            "browser_max_pages": 20,
            "browser_max_scrolls": 40,
            "browser_stable_scrolls": 5,
        },
    ],
    "morgan_stanley": [
        {
            "kind": "official_html",
            "entry_url": "https://morganstanley.eightfold.ai/careers?domain=morganstanley.com&hl=en",
            "max_pages": 40,
            "job_href_patterns": [r"/careers/job/\d+"],
        },
        {
            "kind": "recovery_browser",
            "entry_url": "https://morganstanley.eightfold.ai/careers?domain=morganstanley.com&hl=en",
            "browser_max_pages": 25,
            "browser_max_scrolls": 50,
            "browser_stable_scrolls": 5,
        },
    ],
    "citi": [
        {
            "kind": "official_html",
            "entry_url": "https://jobs.citi.com/location/india-jobs/287/1269750/2",
            "default_location": "India",
            "require_india": True,
            "max_pages": 50,
            "job_href_patterns": [r"jobs\.citi\.com/job/"],
        },
        {
            "kind": "recovery_browser",
            "entry_url": "https://jobs.citi.com/location/india-jobs/287/1269750/2",
            "browser_max_pages": 30,
            "browser_max_scrolls": 10,
        },
    ],
    "american_express": [
        {
            "kind": "official_html",
            "entry_url": "https://careers.americanexpress.com/en/sites/CX_1/jobs?location=India&locationId=300000000228786&locationLevel=country&mode=location",
            "default_location": "India",
            "require_india": True,
            "max_pages": 50,
            "job_href_patterns": [r"/en/sites/CX_1/job/\d+"],
        },
        {
            "kind": "recovery_browser",
            "entry_url": "https://careers.americanexpress.com/en/sites/CX_1/jobs?location=India&locationId=300000000228786&locationLevel=country&mode=location",
            "browser_max_pages": 30,
            "browser_max_scrolls": 12,
        },
    ],
    "mastercard": [
        {
            "kind": "official_html",
            "entry_url": "https://careers.mastercard.com/us/en/software-engineering-jobs",
            "require_india": True,
            "max_pages": 50,
            "job_href_patterns": [r"careers\.mastercard\.com/.*/job/", r"/job/[^/?#]+"],
        },
        {
            "kind": "recovery_browser",
            "entry_url": "https://careers.mastercard.com/us/en/software-engineering-jobs",
            "browser_max_pages": 35,
            "browser_max_scrolls": 12,
        },
    ],
    "atlassian": [
        {
            "kind": "recovery_browser",
            "entry_url": "https://www.atlassian.com/company/careers/all-jobs",
            "browser_max_pages": 25,
            "browser_max_scrolls": 18,
            "browser_stable_scrolls": 4,
            "browser_load_more_clicks": 25,
        },
    ],
    "swiggy": [
        {
            "kind": "recovery_browser",
            "entry_url": "https://careers.swiggy.com/list.html?dept=Engineering&loc=1",
            "browser_max_pages": 25,
            "browser_max_scrolls": 16,
            "browser_load_more_clicks": 25,
        },
    ],
    "gojek": [
        {
            "kind": "official_html",
            "entry_url": "https://www.gojek.io/careers/all",
            "max_pages": 20,
            "job_href_patterns": [r"gojek\.io/careers/.*job", r"/careers/job/"],
        },
        {
            "kind": "recovery_browser",
            "entry_url": "https://www.gojek.io/careers/all",
            "browser_max_pages": 20,
            "browser_max_scrolls": 18,
            "browser_load_more_clicks": 20,
        },
    ],
    "confluent": [
        {
            "kind": "official_html",
            "entry_url": "https://careers.confluent.io/open-positions/",
            "max_pages": 30,
            "job_href_patterns": [r"careers\.confluent\.io/job/", r"/open-positions/[^/?#]+"],
        },
        {
            "kind": "recovery_browser",
            "entry_url": "https://careers.confluent.io/open-positions/",
            "browser_max_pages": 20,
            "browser_max_scrolls": 12,
        },
    ],
    "makemytrip": [
        {
            "kind": "official_html",
            "entry_url": "https://careers.makemytrip.com/prod/",
            "default_location": "India",
            "require_india": True,
            "max_pages": 25,
            "job_href_patterns": [r"/prod/opportunity/[^/?#]+"],
        },
        {
            "kind": "recovery_browser",
            "entry_url": "https://careers.makemytrip.com/prod/",
            "browser_max_pages": 15,
            "browser_max_scrolls": 10,
        },
    ],
    "rakuten_india": [
        {
            "kind": "official_html",
            "entry_url": "https://rakuten.openings.co/rakuten/jobslist",
            "default_location": "India",
            "require_india": True,
            "max_pages": 30,
            "job_href_patterns": [r"openings\.co/rakuten/.*job"],
        },
        {
            "kind": "recovery_browser",
            "entry_url": "https://rakuten.openings.co/rakuten/jobslist",
            "browser_max_pages": 20,
            "browser_max_scrolls": 12,
        },
    ],
    "sony_tech_india": [
        {
            "kind": "official_html",
            "entry_url": "https://careers.sonyindiasoftware.co.in/sonyindiasoftware/",
            "default_location": "India",
            "require_india": True,
            "max_pages": 20,
            "job_href_patterns": [r"sonyindiasoftware.*(?:job|opening|position)"],
        },
        {
            "kind": "recovery_browser",
            "entry_url": "https://careers.sonyindiasoftware.co.in/sonyindiasoftware/",
            "browser_max_pages": 20,
            "browser_max_scrolls": 12,
        },
    ],
    "ibm_software_labs": [
        {
            "kind": "official_html",
            "entry_url": "https://www.ibm.com/in-en/careers/search?field_keyword_05%5B0%5D=India&field_keyword_08%5B0%5D=Software%20Engineering&p=1",
            "default_location": "India",
            "require_india": True,
            "max_pages": 30,
            "job_href_patterns": [r"careers\.ibm\.com/careers/JobDetail/", r"ibm\.com/.*/careers/.*job"],
        },
        {
            "kind": "recovery_browser",
            "entry_url": "https://www.ibm.com/in-en/careers/search?field_keyword_05%5B0%5D=India&field_keyword_08%5B0%5D=Software%20Engineering&p=1",
            "browser_max_pages": 25,
            "browser_max_scrolls": 15,
        },
    ],
}


def has_recovery_plan(company: dict[str, Any]) -> bool:
    return str(company.get("id") or "") in RECOVERY_PLANS


def fetch_with_recovery(company: dict[str, Any], primary_source: JobSource | None = None):
    """Fetch using a recovery plan when one exists, otherwise the configured adapter.

    This function belongs at the orchestration boundary. `build_source()` therefore
    keeps returning the configured adapter type while actual fetches can recover from
    a brittle listing, misleading secondary ATS, timeout, or false challenge.
    """
    if has_recovery_plan(company):
        return RecoverySource(primary_source=primary_source).fetch(company)
    if primary_source is None:
        from job_fetcher.sources.factory import build_raw_source
        primary_source = build_raw_source(company)
    return primary_source.fetch(company)


class RecoverySource(JobSource):
    """Try verified first-party recovery surfaces, then the configured source."""

    def __init__(self, primary_source: JobSource | None = None):
        self.primary_source = primary_source

    def fetch(self, company):
        company_id = str(company.get("id") or "")
        attempts = RECOVERY_PLANS.get(company_id) or []
        errors: list[str] = []

        for index, attempt in enumerate(attempts, 1):
            candidate = deepcopy(company)
            source = dict(candidate.get("source") or {})
            source.update({k: v for k, v in attempt.items() if k != "kind"})
            candidate["source"] = source
            kind = attempt.get("kind")
            adapter = self._adapter(kind)
            try:
                jobs = list(adapter.fetch(candidate) or [])
            except Exception as exc:
                errors.append(f"recovery[{index}] {kind}: {type(exc).__name__}: {exc}")
                continue
            if jobs:
                return dedupe(jobs)
            errors.append(f"recovery[{index}] {kind}: returned zero jobs")

        # Final fallback is exactly the adapter configured in companies.yaml.
        try:
            primary = self.primary_source
            if primary is None:
                from job_fetcher.sources.factory import build_raw_source
                primary = build_raw_source(company)
            jobs = list(primary.fetch(company) or [])
            if jobs:
                return dedupe(jobs)
            errors.append("configured_source: returned zero jobs")
        except Exception as exc:
            errors.append(f"configured_source: {type(exc).__name__}: {exc}")

        raise RuntimeError(
            f"recovery_exhausted[{company_id}]: " + ("; ".join(errors) or "all sources returned zero jobs")
        )

    @staticmethod
    def _adapter(kind: str | None) -> JobSource:
        if kind == "official_html":
            return OfficialHtmlSource()
        if kind == "recovery_browser":
            return RecoveryBrowserSource()
        raise ValueError(f"unsupported recovery kind: {kind}")
