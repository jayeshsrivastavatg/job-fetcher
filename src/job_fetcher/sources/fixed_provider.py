from __future__ import annotations

from copy import deepcopy

from job_fetcher.sources.base import JobSource


class FixedProviderSource(JobSource):
    """Route a branded company config through a verified public ATS contract."""

    def __init__(self, adapter, source_config: dict):
        self.adapter = adapter
        self.source_config = dict(source_config)

    def fetch(self, company):
        candidate = deepcopy(company)
        candidate["source"] = dict(self.source_config)
        return self.adapter.fetch(candidate)
