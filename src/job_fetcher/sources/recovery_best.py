from __future__ import annotations

from copy import deepcopy

from job_fetcher.job_quality import prefer_usable_jobs
from job_fetcher.sources.generic_extract import dedupe
from job_fetcher.sources.recovery import RECOVERY_PLANS, RecoverySource


class BestRecoverySource(RecoverySource):
    """Prefer the richest verified recovery result instead of the first nonzero one.

    A server-rendered landing page may expose only a handful of featured jobs while
    the same first-party site exposes the complete result set after its public
    browser/XHR application loads. The earlier recovery implementation returned on
    the first nonzero result, which could turn a successful recovery into a partial
    snapshot (for example a few featured jobs).

    Structured provider adapters such as Greenhouse/SuccessFactors are already
    complete paginated feeds, so they remain terminal on success. For HTML/browser
    recovery attempts we try all configured public paths and retain the largest
    usable result. The original configured adapter is used only when every recovery
    attempt produced zero usable jobs or raised an error.
    """

    TRUSTED_STRUCTURED = {"greenhouse", "successfactors"}

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
                jobs = prefer_usable_jobs(adapter.fetch(candidate))
            except Exception as exc:
                errors.append(f"recovery[{index}] {kind}: {type(exc).__name__}: {exc}")
                continue

            jobs = list(jobs or [])
            if not jobs:
                errors.append(f"recovery[{index}] {kind}: returned zero jobs")
                continue

            if kind in self.TRUSTED_STRUCTURED:
                return dedupe(jobs)
            if len(jobs) > len(best):
                best = jobs

        if best:
            return dedupe(best)

        # All explicit recovery paths failed. Only now pay for the original
        # configured adapter so its existing fallback behaviour remains available.
        try:
            primary = self.primary_source
            if primary is None:
                from job_fetcher.sources.factory import build_raw_source
                primary = build_raw_source(company)
            jobs = prefer_usable_jobs(primary.fetch(company))
            if jobs:
                return dedupe(list(jobs))
            errors.append("configured_source: returned zero jobs")
        except Exception as exc:
            errors.append(f"configured_source: {type(exc).__name__}: {exc}")

        raise RuntimeError(
            f"recovery_exhausted[{company_id}]: " + ("; ".join(errors) or "all sources returned zero jobs")
        )
