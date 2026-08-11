from job_fetcher.sources.oracle import OracleSource


COMPANY = {"id": "jpmorgan_chase", "name": "JPMorgan Chase (Tech)"}


def test_parse_oracle_candidate_experience_url():
    parsed = OracleSource.parse_candidate_experience_url(
        "https://jpmc.fa.oraclecloud.com/hcmUI/CandidateExperience/en/sites/CX_1001/jobs"
    )
    assert parsed == {
        "host": "jpmc.fa.oraclecloud.com",
        "locale": "en",
        "site_number": "CX_1001",
    }


def test_requisition_wrapper_normalization():
    payload = {
        "items": [
            {
                "TotalJobsCount": 2,
                "requisitionList": [
                    {"Id": "2101", "Title": "Software Engineer"},
                    {"Id": "2102", "Title": "Backend Engineer"},
                ],
            }
        ]
    }
    rows, total = OracleSource._extract_requisition_rows(payload)
    assert total == 2
    assert [x["Id"] for x in rows] == ["2101", "2102"]


def test_oracle_row_to_job():
    row = {
        "Id": "210742917",
        "Title": "Software Engineer I",
        "PrimaryLocation": "Bengaluru, Karnataka, India",
        "PostedDate": "2026-08-10",
        "ShortDescriptionStr": "Build reliable services.",
        "ExternalResponsibilitiesStr": "Own backend systems.",
    }
    job = OracleSource._to_job(
        COMPANY, "jpmc.fa.oraclecloud.com", "CX_1001", "en", row
    )
    assert job is not None
    assert job.source_type == "oracle"
    assert job.external_id == "210742917"
    assert job.title == "Software Engineer I"
    assert job.location == "Bengaluru, Karnataka, India"
    assert job.job_url.endswith("/sites/CX_1001/job/210742917")
    assert "Build reliable services" in job.description


def test_oracle_structured_fetch_paginates(monkeypatch):
    class Response:
        def __init__(self, payload):
            self._payload = payload
        def raise_for_status(self):
            return None
        def json(self):
            return self._payload

    class Client:
        def __init__(self):
            self.calls = []
        def get(self, url, **kwargs):
            self.calls.append((url, kwargs))
            finder = kwargs["params"]["finder"]
            if "offset=0" in finder:
                return Response({"items":[{"TotalJobsCount":3,"requisitionList":[
                    {"Id":"1","Title":"Engineer I","PrimaryLocation":"India"},
                    {"Id":"2","Title":"Engineer II","PrimaryLocation":"India"},
                ]}]})
            return Response({"items":[{"TotalJobsCount":3,"requisitionList":[
                {"Id":"3","Title":"Engineer III","PrimaryLocation":"India"}
            ]}]})

    client = Client()
    monkeypatch.setattr("job_fetcher.sources.oracle.session", lambda: client)
    jobs = OracleSource()._fetch_candidate_experience_api(
        company=COMPANY,
        host="jpmc.fa.oraclecloud.com",
        site_number="CX_1001",
        locale="en",
        page_size=2,
        max_jobs=100,
    )
    assert [j.external_id for j in jobs] == ["1", "2", "3"]
    assert len(client.calls) == 2
    assert "offset=0" in client.calls[0][1]["params"]["finder"]
    assert "offset=2" in client.calls[1][1]["params"]["finder"]
