import pytest

import job_fetcher.sources.mynexthire as mynexthire_module
from job_fetcher.sources.mynexthire import MyNextHireSource


class FakeResponse:
    def __init__(self, payload):
        self.payload = payload

    def raise_for_status(self):
        return None

    def json(self):
        return self.payload


class FakeClient:
    def __init__(self, payload):
        self.payload = payload
        self.calls = []

    def post(self, url, json, **kwargs):
        self.calls.append((url, json, kwargs))
        return FakeResponse(self.payload)


def _company():
    return {
        "id": "swiggy",
        "name": "Swiggy",
        "career_url": "https://careers.swiggy.com/#/careers",
        "source": {
            "type": "mynexthire",
            "tenant": "swiggy",
            "base_url": "https://swiggy.mynexthire.com",
            "source_short_name": "careers",
            "filter_by_bu_id": -1,
            "origin": "https://careers.swiggy.com",
            "referer": "https://careers.swiggy.com/#/careers",
        },
    }


def test_mynexthire_reads_stable_req_ids_location_and_full_jd(monkeypatch):
    payload = {
        "reqDetailsBOList": [
            {
                "reqId": 28352,
                "reqTitle": "Software Engineer",
                "location": "Bengaluru",
                "jdDisplay": "<p>Build reliable services &amp; own production systems.</p>",
                "approvedOn": "2026-08-12",
            },
            {
                "reqId": 28353,
                "reqTitle": "Senior Software Engineer",
                "locationAddress": "Pune, India",
                "jdDisplay": "<div>Design distributed systems and mentor engineers.</div>",
                "approvedOn": "2026-08-11",
            },
            # Provider duplicate rows must not create duplicate vacancies.
            {
                "reqId": 28352,
                "reqTitle": "Software Engineer duplicate",
                "location": "Bengaluru",
                "jdDisplay": "duplicate",
            },
        ]
    }
    client = FakeClient(payload)
    monkeypatch.setattr(mynexthire_module, "session", lambda: client)

    jobs = MyNextHireSource().fetch(_company())

    assert [job.external_id for job in jobs] == ["28352", "28353"]
    assert jobs[0].title == "Software Engineer"
    assert jobs[0].location == "Bengaluru"
    assert jobs[0].description == "Build reliable services & own production systems."
    assert jobs[0].posted_at == "2026-08-12"
    assert jobs[0].job_url.endswith("?reqId=28352")
    assert jobs[0].source_type == "mynexthire"
    assert client.calls[0][0] == "https://swiggy.mynexthire.com/employer/careers/reqlist/get"
    assert client.calls[0][1] == {"source": "careers", "code": "", "filterByBuId": -1}


def test_mynexthire_rejects_malformed_inventory(monkeypatch):
    client = FakeClient({"unexpected": []})
    monkeypatch.setattr(mynexthire_module, "session", lambda: client)

    with pytest.raises(RuntimeError, match="mynexthire_invalid_requisition_payload"):
        MyNextHireSource().fetch(_company())
