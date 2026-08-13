from __future__ import annotations

from copy import deepcopy
from urllib.parse import urlparse

from job_fetcher.sources.atlassian import AtlassianSource
from job_fetcher.sources.avature import AvatureSource
from job_fetcher.sources.eightfold import EightfoldSource
from job_fetcher.sources.oracle import OracleSource
from job_fetcher.sources.phenom import PhenomSource
from job_fetcher.sources.recovery import RECOVERY_PLANS
from job_fetcher.sources.recovery_best import BestRecoverySource
from job_fetcher.sources.rippling_board import RipplingBoardSource
from job_fetcher.sources.strict_auto import StrictAutoSource


class RecoveryAutoSource(StrictAutoSource):
    """AutoSource-compatible adapter with first-party recovery before strict auto."""

    def fetch(self, company):
        for attempt in RECOVERY_PLANS.get(str(company.get("id") or ""), []):
            entry = str(attempt.get("entry_url") or "")
            parsed = urlparse(entry)
            parts = [part for part in parsed.path.split("/") if part]
            if parsed.scheme == "https" and parsed.netloc.casefold() == "ats.rippling.com" and len(parts) >= 2 and parts[1].casefold() == "jobs":
                candidate = deepcopy(company)
                candidate["source"] = {
                    "type": "rippling_board",
                    "tenant": parts[0],
                    "require_india": bool(attempt.get("require_india")),
                }
                jobs = RipplingBoardSource().fetch(candidate)
                if jobs:
                    return jobs
        return BestRecoverySource(primary_source=StrictAutoSource()).fetch(company)


class RecoveryEightfoldSource(EightfoldSource):
    """EightfoldSource-compatible adapter with first-party recovery first."""

    def fetch(self, company):
        return BestRecoverySource(primary_source=EightfoldSource()).fetch(company)


class RecoveryAtlassianSource(AtlassianSource):
    """AtlassianSource-compatible adapter with the hardened browser recovery."""

    def fetch(self, company):
        return BestRecoverySource(primary_source=AtlassianSource()).fetch(company)


class RecoveryAvatureSource(AvatureSource):
    """AvatureSource-compatible adapter with IBM's first-party search recovery."""

    def fetch(self, company):
        return BestRecoverySource(primary_source=AvatureSource()).fetch(company)


class RecoveryOracleSource(OracleSource):
    """OracleSource-compatible adapter with public-browser recovery first."""

    def fetch(self, company):
        return BestRecoverySource(primary_source=OracleSource()).fetch(company)


class RecoveryPhenomSource(PhenomSource):
    """PhenomSource-compatible adapter with richer branded-page recovery."""

    def fetch(self, company):
        return BestRecoverySource(primary_source=PhenomSource()).fetch(company)


RECOVERY_ADAPTERS = {
    "auto": RecoveryAutoSource,
    "eightfold": RecoveryEightfoldSource,
    "atlassian": RecoveryAtlassianSource,
    "avature": RecoveryAvatureSource,
    "oracle": RecoveryOracleSource,
    "phenom": RecoveryPhenomSource,
}


def build_recovery_adapter(company):
    source_type = str((company.get("source") or {}).get("type") or "")
    cls = RECOVERY_ADAPTERS.get(source_type)
    if cls is None:
        raise ValueError(f"No recovery adapter wrapper for configured source type: {source_type}")
    return cls()
