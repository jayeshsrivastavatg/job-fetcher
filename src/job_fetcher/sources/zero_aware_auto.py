from __future__ import annotations

from job_fetcher.sources.http_client import session, timeout_seconds
from job_fetcher.sources.strict_auto import StrictAutoSource


class ZeroAwareAutoSource(StrictAutoSource):
    """Avoid turning navigation links into fake jobs on explicit empty boards.

    This is used only for sources configured with allow_zero_jobs. Generic link
    extraction can otherwise find role-looking navigation text before AutoSource's
    later empty-page check and incorrectly report a browser fallback with fake jobs.
    """

    def fetch(self, company):
        src = company.get("source") or {}
        entry = src.get("entry_url") or company["career_url"]
        try:
            response = session().get(entry, timeout=timeout_seconds(), allow_redirects=True)
            response.raise_for_status()
            if self._is_empty(response.text):
                return []
        except Exception:
            # The normal StrictAutoSource path owns network/browser fallback semantics.
            pass
        return super().fetch(company)
