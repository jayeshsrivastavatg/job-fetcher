from __future__ import annotations

from copy import deepcopy

from job_fetcher.job_quality import prefer_usable_jobs, valid_http_url
from job_fetcher.sources.generic_extract import dedupe
from job_fetcher.sources.recovery import RECOVERY_PLANS, RecoverySource
from job_fetcher.sources.rippling_board import RipplingBoardSource
from job_fetcher.sources.slow_official_html import SlowOfficialHtmlSource
from job_fetcher.sources.smartrecruiters import SmartRecruitersSource


def _result_score(jobs):
    rows = list(jobs or [])
    usable = sum(
        bool(str(getattr(job, "title", "") or "").strip())
        and valid_http_url(getattr(job, "job_url", None))
        for job in rows
    )
    ratio = usable / len(rows) if rows else 0.0
    return usable, ratio, len(rows)


class BestRecoverySource(RecoverySource):
    """Compare recovery paths and keep the most complete usable provider result."""

    TRUSTED_STRUCTURED = {"greenhouse", "successfactors", "smartrecruiters", "hosted_board"}

    def fetch(self, company):
        company_id = str(company.get("id") or "")
        attempts = RECOVERY_PLANS.get(company_id) or []
        errors: list[str] = []
        best = []

        for index, attempt in enumerate(attempts, 1):
            candidate = deepcopy(company)
            source = dict(candidate.get("source") or {})
            source.update({k: v for k, v in attempt.items() if k != "kind"})
            candidate["source"] = source
            kind = str(attempt.get("kind") or "")
            adapter = self._adapter(kind)
            try:
                jobs = list(prefer_usable_jobs(adapter.fetch(candidate)) or [])
            except Exception as exc:
                errors.append(f"recovery[{index}] {kind}: {type(exc).__name__}: {exc}")
                continue
            if not jobs:
                errors.append(f"recovery[{index}] {kind}: returned zero jobs")
                continue
            if kind in self.TRUSTED_STRUCTURED:
                return jobs
            if _result_score(jobs) > _result_score(best):
                best = jobs

        try:
            primary = self.primary_source
            if primary is None:
                from job_fetcher.sources.factory import build_raw_source
                primary = build_raw_source(company)
            jobs = list(prefer_usable_jobs(primary.fetch(company)) or [])
            if jobs and _result_score(jobs) > _result_score(best):
                best = jobs
            elif not jobs:
                errors.append("configured_source: returned zero jobs")
        except Exception as exc:
            errors.append(f"configured_source: {type(exc).__name__}: {exc}")

        if best:
            return dedupe(best)
        raise RuntimeError(
            f"recovery_exhausted[{company_id}]: " + ("; ".join(errors) or "all sources returned zero jobs")
        )

    @staticmethod
    def _adapter(kind: str | None):
        if kind == "smartrecruiters":
            return SmartRecruitersSource()
        if kind == "slow_official_html":
            return SlowOfficialHtmlSource()
        if kind == "hosted_board":
            return RipplingBoardSource()
        return RecoverySource._adapter(kind)
