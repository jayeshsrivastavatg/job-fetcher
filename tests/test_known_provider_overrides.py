import pytest

from job_fetcher.sources.factory import build_source
from job_fetcher.sources.greenhouse import GreenhouseSource
from job_fetcher.sources.lever import LeverSource
from job_fetcher.sources.smartrecruiters import SmartRecruitersSource
from job_fetcher.sources.workday import WorkdaySource
import job_fetcher.sources.workday as workday_module


def _company(company_id):
    return {
        "id": company_id,
        "name": company_id,
        "career_url": "https://example.com/careers",
        "source": {"type": "auto", "entry_url": "https://example.com/careers"},
    }


@pytest.mark.parametrize(
    "company_id, expected_cls, expected_source",
    [
        ("postman", GreenhouseSource, {"type": "greenhouse", "board_token": "postman"}),
        ("inmobi", GreenhouseSource, {"type": "greenhouse", "board_token": "inmobi"}),
        ("hackerrank", GreenhouseSource, {"type": "greenhouse", "board_token": "hackerrank"}),
        ("qualtrics", GreenhouseSource, {"type": "greenhouse", "board_token": "qualtrics"}),
        ("freshworks", SmartRecruitersSource, {"type": "smartrecruiters", "company_identifier": "Freshworks"}),
        ("arista_networks", SmartRecruitersSource, {"type": "smartrecruiters", "company_identifier": "AristaNetworks"}),
        ("nagarro", SmartRecruitersSource, {"type": "smartrecruiters", "company_identifier": "Nagarro1"}),
        ("mindtickle", LeverSource, {"type": "lever", "site": "mindtickle"}),
    ],
)
def test_known_branded_pages_are_promoted_to_structured_provider(company_id, expected_cls, expected_source):
    company = _company(company_id)
    source = build_source(company)
    assert isinstance(source, expected_cls)
    assert company["source"] == expected_source


class FakeResponse:
    def __init__(self, payload):
        self.payload = payload

    def raise_for_status(self):
        return None

    def json(self):
        return self.payload


class FakeWorkdayClient:
    def __init__(self, persistent_empty=False):
        self.calls = []
        self.empty_seen = False
        self.persistent_empty = persistent_empty

    def post(self, _url, json, **_kwargs):
        offset = json["offset"]
        self.calls.append(offset)
        if offset == 0:
            return FakeResponse({"total": 45, "jobPostings": self._rows(0, 20)})
        if offset == 20:
            return FakeResponse({"total": 45, "jobPostings": self._rows(20, 20)})
        if offset == 40:
            if self.persistent_empty or not self.empty_seen:
                self.empty_seen = True
                return FakeResponse({"total": 45, "jobPostings": []})
            return FakeResponse({"total": 45, "jobPostings": self._rows(40, 5)})
        raise AssertionError(f"unexpected offset {offset}")

    @staticmethod
    def _rows(start, count):
        return [
            {
                "externalPath": f"/job/Test-{i}_JR{i}",
                "bulletFields": [f"JR{i}"],
                "title": f"Software Engineer {i}",
                "locationsText": "Bengaluru, India",
                "postedOn": "Posted Today",
            }
            for i in range(start, start + count)
        ]


def _workday_company():
    return {
        "id": "test",
        "name": "Test",
        "source": {
            "type": "workday",
            "host": "example.wd1.myworkdayjobs.com",
            "tenant": "example",
            "site": "External",
            "page_delay_seconds": 0,
            "premature_empty_retries": 2,
        },
    }


def test_workday_retries_premature_empty_page_and_finishes(monkeypatch):
    client = FakeWorkdayClient()
    monkeypatch.setattr(workday_module, "session", lambda: client)
    monkeypatch.setattr(workday_module.time, "sleep", lambda _seconds: None)

    jobs = WorkdaySource().fetch(_workday_company())

    assert len(jobs) == 45
    assert client.calls == [0, 20, 40, 40]


def test_workday_refuses_known_partial_snapshot(monkeypatch):
    client = FakeWorkdayClient(persistent_empty=True)
    monkeypatch.setattr(workday_module, "session", lambda: client)
    monkeypatch.setattr(workday_module.time, "sleep", lambda _seconds: None)

    with pytest.raises(RuntimeError, match="workday_incomplete_pagination"):
        WorkdaySource().fetch(_workday_company())
