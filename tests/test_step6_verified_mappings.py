from pathlib import Path
import yaml

from job_fetcher.config import validate_config
from job_fetcher.sources.eightfold import EightfoldSource
from job_fetcher.sources.factory import build_source
from job_fetcher.sources.greenhouse import GreenhouseSource
from job_fetcher.sources.smartrecruiters import SmartRecruitersSource
from job_fetcher.sources.workday import WorkdaySource


def _by_id():
    root = Path(__file__).resolve().parents[1]
    data = yaml.safe_load((root / "config" / "companies.yaml").read_text(encoding="utf-8"))
    return data, {c["id"]: c for c in data["companies"]}


def test_step6_config_still_valid():
    data, _ = _by_id()
    assert validate_config(data) == []


def test_verified_greenhouse_mappings():
    _, by = _by_id()
    expected = {
        "stripe": "stripe",
        "databricks": "databricks",
        "phonepe": "phonepe",
        "mongodb": "mongodb",
        "rubrik": "rubrik",
        "groww": "groww",
    }
    for cid, token in expected.items():
        c = by[cid]
        assert c["source"] == {"type": "greenhouse", "board_token": token}
        assert isinstance(build_source(c), GreenhouseSource)


def test_verified_workday_mappings():
    _, by = _by_id()
    expected = {
        "salesforce": ("salesforce.wd12.myworkdayjobs.com", "salesforce", "External_Career_Site"),
        "walmart_global_tech": ("walmart.wd504.myworkdayjobs.com", "walmart", "WalmartExternal"),
        "adobe": ("adobe.wd5.myworkdayjobs.com", "adobe", "external_experienced"),
        "paypal": ("paypal.wd1.myworkdayjobs.com", "paypal", "jobs"),
        "cohesity": ("cohesity.wd5.myworkdayjobs.com", "cohesity", "Cohesity_Careers"),
    }
    for cid, (host, tenant, site) in expected.items():
        c = by[cid]
        assert c["source"]["type"] == "workday"
        assert (c["source"]["host"], c["source"]["tenant"], c["source"]["site"]) == (host, tenant, site)
        assert isinstance(build_source(c), WorkdaySource)


def test_morgan_stanley_and_servicenow_routing():
    _, by = _by_id()
    ms = by["morgan_stanley"]
    assert ms["source"]["type"] == "eightfold"
    assert ms["source"]["tenant"] == "morganstanley"
    assert isinstance(build_source(ms), EightfoldSource)

    sn = by["servicenow"]
    assert sn["source"] == {"type": "smartrecruiters", "company_identifier": "ServiceNow"}
    assert isinstance(build_source(sn), SmartRecruitersSource)


def test_public_listing_entry_points_are_specific():
    _, by = _by_id()
    assert by["uber"]["source"]["entry_url"] == "https://jobs.uber.com/en/jobs/"
    assert "location/india-jobs" in by["intuit"]["source"]["entry_url"]
    assert by["meesho"]["source"]["entry_url"].endswith("?d=engineering")
