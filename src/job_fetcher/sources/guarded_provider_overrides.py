from __future__ import annotations

from job_fetcher.job_quality import prefer_usable_jobs
from job_fetcher.sources.current_provider_overrides import UberIndiaSource
from job_fetcher.sources.strict_auto import StrictAutoSource


class GuardedUberIndiaSource(UberIndiaSource):
    """Do not accept unrelated Uber page/XHR objects as successful vacancies."""

    def fetch(self, company):
        jobs = prefer_usable_jobs(super().fetch(company))
        if jobs:
            return jobs

        # If Uber's provider-specific normalization only found navigation/policy
        # objects, retry through the strict generic crawler so the run is either
        # populated with plausible roles or fails explicitly instead of storing
        # fake jobs such as "Google Data Policy" and "Uber Careers".
        return prefer_usable_jobs(StrictAutoSource().fetch(company))
