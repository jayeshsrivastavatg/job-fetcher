from __future__ import annotations

from job_fetcher.sources.atlassian import AtlassianSource
from job_fetcher.sources.auto import AutoSource
from job_fetcher.sources.avature import AvatureSource
from job_fetcher.sources.eightfold import EightfoldSource
from job_fetcher.sources.oracle import OracleSource
from job_fetcher.sources.recovery_best import BestRecoverySource


class RecoveryAutoSource(AutoSource):
    """AutoSource-compatible adapter with first-party recovery before AutoSource."""

    def fetch(self, company):
        return BestRecoverySource(primary_source=AutoSource()).fetch(company)


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


RECOVERY_ADAPTERS = {
    "auto": RecoveryAutoSource,
    "eightfold": RecoveryEightfoldSource,
    "atlassian": RecoveryAtlassianSource,
    "avature": RecoveryAvatureSource,
    "oracle": RecoveryOracleSource,
}


def build_recovery_adapter(company):
    source_type = str((company.get("source") or {}).get("type") or "")
    cls = RECOVERY_ADAPTERS.get(source_type)
    if cls is None:
        raise ValueError(f"No recovery adapter wrapper for configured source type: {source_type}")
    return cls()
