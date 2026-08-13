import pytest

from job_fetcher.sources.factory import build_source
from job_fetcher.sources.greenhouse import GreenhouseSource
from job_fetcher.sources.kula import KulaSource
from job_fetcher.sources.lever import LeverSource
from job_fetcher.sources.mynexthire import MyNextHireSource
from job_fetcher.sources.phenom import PhenomSource
from job_fetcher.sources.smartrecruiters import SmartRecruitersSource
from job_fetcher.sources.trakstar import TrakstarSource
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
        ("elastic", GreenhouseSource, {"type": "greenhouse", "board_token": "elastic"}),
        ("druva", GreenhouseSource, {"type": "greenhouse", "board_token": "druva"}),
        ("thoughtworks", GreenhouseSource, {"type": "greenhouse", "board_token": "thoughtworks"}),
        ("qualtrics", GreenhouseSource, {"type": "greenhouse", "board_token": "qualtrics"}),
        (
            "swiggy",
            MyNextHireSource,
            {
                "type": "mynexthire",
                "tenant": "swiggy",
                "base_url": "https://swiggy.mynexthire.com",
                "source_short_name": "careers",
                "filter_by_bu_id": -1,
                "origin": "https://careers.swiggy.com",
                "referer": "https://careers.swiggy.com/#/careers",
            },
        ),
        (
            "lowes_india",
            PhenomSource,
            {
                "type": "phenom",
                "entry_url": "https://talent.lowes.com/in/en/search-results",
                "canonical_base_url": "https://talent.lowes.com",
                "browser_max_pages": 20,
                "browser_max_scrolls": 10,
                "browser_load_more_clicks": 10,
                "hydrate_details": True,
                "detail_workers": 8,
                "locale": "en-IN",
            },
        ),
        ("freshworks", SmartRecruitersSource, {"type": "smartrecruiters", "company_identifier": "Freshworks"}),
        ("arista_networks", SmartRecruitersSource, {"type": "smartrecruiters", "company_identifier": "AristaNetworks"}),
        ("nagarro", SmartRecruitersSource, {"type": "smartrecruiters", "company_identifier": "Nagarro1"}),
        ("zomato_blinkit", SmartRecruitersSource, {"type": "smartrecruiters", "company_identifier": "Zomato1"}),
        ("dynatrace", SmartRecruitersSource, {"type": "smartrecruiters", "company_identifier": "Dynatrace1"}),
        ("mindtickle", LeverSource, {"type": "lever", "site": "mindtickle"}),
        ("meesho", LeverSource, {"type": "lever", "site": "meesho"}),
        ("zeta", LeverSource, {"type": "lever", "site": "zeta"}),
        (
            "slice",
            KulaSource,
            {
                "type": "kula",
                "entry_url": "https://careers.kula.ai/slice",
                "tenant": "slice",
                "max_jobs": 5000,
            },
        ),
        (
            "chargebee",
            TrakstarSource,
            {
                "type": "trakstar",
                "entry_url": "https://chargebee.hire.trakstar.com/",
            },
        ),
        (
            "target_india",
            WorkdaySource,
            {
                "type": "workday",
                "host": "target.wd5.myworkdayjobs.com",
                "tenant": "target",
                "site": "targetcareers",
                "locale": "en-US",
                "max_jobs": 10000,
            },
        ),
        (
            "home_depot_tech",
            WorkdaySource,
            {
                "type": "workday",
                "host": "homedepot.wd5.myworkdayjobs.com",
                "tenant": "homedepot",
                "site": "CareerDepot",
                "locale": "en-US",
                "max_jobs": 10000,
            },
        ),
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
