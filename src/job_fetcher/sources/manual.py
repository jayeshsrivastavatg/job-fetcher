from job_fetcher.sources.base import JobSource


class ManualSource(JobSource):
    """Placeholder for companies where automated extraction is not permitted/available."""

    def fetch(self, company):
        src = company.get("source") or {}
        reason = src.get("reason") or "manual_or_approved_feed_required"
        raise RuntimeError(f"automation_disallowed_or_unavailable: {reason}")
