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
    def __init__(self, list_payload, detail_payloads=None):
        self.list_payload = list_payload
        self.detail_payloads = detail_payloads or {}
        self.calls = []

    def post(self, url, json, **kwargs):
        self.calls.append((url, json, kwargs))
        if url.endswith("/employer/careers/reqlist/get"):
            return FakeResponse(self.list_payload)
        if url.endswith("/employer/careers/req/get"):
            return FakeResponse(self.detail_payloads.get(str(json.get("reqId")), {}))
        raise AssertionError(url)


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


def test_mynexthire_reads_stable_req_ids_location_and_jd(monkeypatch):
    long_jd = "Build reliable services, own production systems, design APIs, test changes, monitor reliability, and collaborate across product and engineering teams."
    payload = {
        "reqDetailsBOList": [
            {
                "reqId": 28352,
                "reqTitle": "Software Engineer",
                "location": "Bengaluru",
                "jdDisplay": f"<p>{long_jd}</p>",
                "approvedOn": "2026-08-12",
            },
            {
                "reqId": 28353,
                "reqTitle": "Senior Software Engineer",
                "locationAddress": "Pune, India",
                "jdDisplay": f"<div>{long_jd} Mentor engineers and lead design reviews.</div>",
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
    assert jobs[0].description == long_jd
    assert jobs[0].posted_at == "2026-08-12"
    assert jobs[0].job_url.endswith("?reqId=28352")
    assert jobs[0].source_type == "mynexthire"
    assert client.calls[0][0] == "https://swiggy.mynexthire.com/employer/careers/reqlist/get"
    assert client.calls[0][1] == {"source": "careers", "code": "", "filterByBuId": -1}
    assert len(client.calls) == 1


def test_mynexthire_hydrates_short_list_jd_from_exact_public_detail(monkeypatch):
    full = (
        "Design and build reliable backend services for high-scale systems. Own APIs, testing, deployment, "
        "observability, incident response, and technical collaboration with product and engineering teams."
    )
    client = FakeClient(
        {
            "reqDetailsBOList": [
                {
                    "reqId": 24364,
                    "reqTitle": "Fleet Excellence Manager",
                    "location": "Bengaluru",
                    "jdDisplay": "<p>FM to FEM transition</p>",
                }
            ]
        },
        {
            "24364": {
                "reqDetailsBO": {
                    "reqId": 24364,
                    "jdDisplay": f"<p>{full}</p>",
                    "approvedOn": "2026-08-10",
                }
            }
        },
    )
    monkeypatch.setattr(mynexthire_module, "session", lambda: client)

    jobs = MyNextHireSource().fetch(_company())

    assert len(jobs) == 1
    assert jobs[0].external_id == "24364"
    assert jobs[0].description == full
    assert jobs[0].raw["detail_status"] == "matched"
    assert client.calls[1][0].endswith("/employer/careers/req/get")
    assert client.calls[1][1] == {"source": "careers", "id": "", "code": "", "reqId": 24364}


def test_mynexthire_never_hydrates_from_wrong_requisition(monkeypatch):
    client = FakeClient(
        {"reqDetailsBOList": [{"reqId": 24364, "reqTitle": "Role", "location": "Bengaluru", "jdDisplay": "short"}]},
        {"24364": {"reqDetailsBO": {"reqId": 99999, "jdDisplay": "This belongs to a different requisition and must never be copied."}}},
    )
    monkeypatch.setattr(mynexthire_module, "session", lambda: client)

    jobs = MyNextHireSource().fetch(_company())

    assert jobs[0].description == "short"
    assert jobs[0].raw["detail_status"] == "id_mismatch"


def test_mynexthire_rejects_malformed_inventory(monkeypatch):
    client = FakeClient({"unexpected": []})
    monkeypatch.setattr(mynexthire_module, "session", lambda: client)

    with pytest.raises(RuntimeError, match="mynexthire_invalid_requisition_payload"):
        MyNextHireSource().fetch(_company())
