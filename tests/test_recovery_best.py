from __future__ import annotations

from job_fetcher.models import Job


def _job(i):
    return Job("microsoft", "Microsoft", "test", str(i), f"Software Engineer {i}", "India", None, f"https://example.com/jobs/{i}")


def test_best_recovery_does_not_stop_at_small_first_html_result(monkeypatch):
    import job_fetcher.sources.recovery_best as module

    monkeypatch.setitem(
        module.RECOVERY_PLANS,
        "microsoft",
        [
            {"kind": "official_html", "entry_url": "https://example.com/landing"},
            {"kind": "recovery_browser", "entry_url": "https://example.com/jobs"},
        ],
    )

    class Small:
        def fetch(self, company):
            return [_job(1), _job(2), _job(3)]

    class Large:
        def fetch(self, company):
            return [_job(i) for i in range(20)]

    source = module.BestRecoverySource()
    monkeypatch.setattr(source, "_adapter", lambda kind: Small() if kind == "official_html" else Large())
    jobs = source.fetch({"id": "microsoft", "name": "Microsoft", "source": {"type": "auto"}})
    assert len(jobs) == 20


def test_structured_recovery_is_terminal(monkeypatch):
    import job_fetcher.sources.recovery_best as module

    monkeypatch.setitem(
        module.RECOVERY_PLANS,
        "twilio",
        [
            {"kind": "greenhouse", "board_token": "twilio"},
            {"kind": "recovery_browser", "entry_url": "https://example.com/jobs"},
        ],
    )
    browser_called = {"value": False}

    class Greenhouse:
        def fetch(self, company):
            return [_job(i) for i in range(5)]

    class Browser:
        def fetch(self, company):
            browser_called["value"] = True
            return [_job(i) for i in range(50)]

    source = module.BestRecoverySource()
    monkeypatch.setattr(source, "_adapter", lambda kind: Greenhouse() if kind == "greenhouse" else Browser())
    jobs = source.fetch({"id": "twilio", "name": "Twilio", "source": {"type": "eightfold"}})
    assert len(jobs) == 5
    assert browser_called["value"] is False
