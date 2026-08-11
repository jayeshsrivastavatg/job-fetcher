from __future__ import annotations

import json

import pytest

from job_fetcher.india_location import classify_india_location, is_india_job


@pytest.mark.parametrize("location", [
    "India",
    "Remote - India",
    "India - Remote",
    "Bengaluru",
    "Bangalore, Karnataka",
    "BLR",
    "Whitefield",
    "Gurgaon",
    "Gurugram, Haryana",
    "GGN",
    "Noida, Uttar Pradesh",
    "Greater Noida",
    "Hyderabad, Telangana",
    "HYD",
    "Gachibowli",
    "Pune, Maharashtra",
    "PNQ",
    "Hinjewadi",
    "Mumbai",
    "Bombay, Maharashtra",
    "BOM",
    "Navi Mumbai",
    "Chennai, Tamil Nadu",
    "Madras",
    "MAA",
    "Kolkata, West Bengal",
    "Calcutta",
    "CCU",
    "Ahmedabad, Gujarat",
    "Kochi, Kerala",
    "Cochin",
    "Thiruvananthapuram",
    "Trivandrum",
    "Coimbatore",
    "Jaipur, Rajasthan",
    "Indore, Madhya Pradesh",
    "Chandigarh",
    "Mohali, Punjab",
    "Bhubaneswar, Odisha",
    "Guwahati, Assam",
    "Lucknow, Uttar Pradesh",
    "Nagpur, Maharashtra",
    "Vadodara, Gujarat",
    "Baroda",
    "Visakhapatnam, Andhra Pradesh",
    "Vizag",
    "Vijayawada",
    "Mysore",
    "Mysuru, Karnataka",
    "Mangalore",
    "Mangaluru",
    "Kozhikode",
    "Calicut",
    "Prayagraj",
    "Allahabad",
    "Jamshedpur, Jharkhand",
    "Ranchi, Jharkhand",
    "Raipur, Chhattisgarh",
    "Dehradun, Uttarakhand",
    "Srinagar, Jammu and Kashmir",
    "Panaji, Goa",
    "Puducherry",
])
def test_common_india_location_variants(location):
    result = classify_india_location(location)
    assert result.is_india is True, (location, result)
    assert result.country_code == "IN"
    assert "India" in result.normalized_location


@pytest.mark.parametrize("state", [
    "Andhra Pradesh", "Arunachal Pradesh", "Assam", "Bihar", "Chhattisgarh", "Goa",
    "Gujarat", "Haryana", "Himachal Pradesh", "Jharkhand", "Karnataka", "Kerala",
    "Madhya Pradesh", "Maharashtra", "Manipur", "Meghalaya", "Mizoram", "Nagaland",
    "Odisha", "Orissa", "Punjab", "Rajasthan", "Sikkim", "Tamil Nadu", "Telangana",
    "Tripura", "Uttar Pradesh", "Uttarakhand", "West Bengal", "Andaman and Nicobar",
    "Chandigarh", "Jammu and Kashmir", "Ladakh", "Lakshadweep", "Pondicherry",
])
def test_all_indian_state_and_ut_names_are_positive(state):
    assert is_india_job(state)


def test_structured_ats_country_fields_find_india_even_when_display_location_is_obscure():
    raw = {
        "job": {
            "locations": [{"city": "Hubballi", "countryCode": "IN"}],
            "workplace": {"location": "Some campus name"},
        }
    }
    result = classify_india_location("Some campus name", raw=raw)
    assert result.is_india
    assert result.country_code == "IN"


def test_raw_json_string_is_supported():
    raw = json.dumps({"primaryLocation": {"country": "India", "city": "Kolkata"}})
    assert classify_india_location("", raw=raw).is_india


def test_description_location_header_is_used_only_as_fallback():
    result = classify_india_location(
        "Remote",
        description="Software Engineer\nLocation: Bengaluru, Karnataka\nBuild backend systems.",
    )
    assert result.is_india
    assert result.normalized_location == "Bengaluru, India"


def test_strong_india_remote_language_is_supported():
    assert classify_india_location(
        "Remote",
        description="This role is remote within India. Candidates must be based in India.",
    ).is_india


@pytest.mark.parametrize("location", [
    "Seattle, WA, United States",
    "New York, United States",
    "Toronto, Canada",
    "London, United Kingdom",
    "Singapore",
    "Sydney, Australia",
    "Berlin, Germany",
    "Dublin, Ireland",
    "Tokyo, Japan",
    "Dubai, United Arab Emirates",
    "Remote - US",
])
def test_explicit_foreign_locations_are_not_india(location):
    assert not classify_india_location(location).is_india


def test_unknown_remote_is_not_assumed_india():
    result = classify_india_location("Remote")
    assert result.status == "unknown"
    assert not result.is_india


def test_indiana_and_indianapolis_do_not_match_india_substring():
    assert not classify_india_location("Indianapolis, Indiana, United States").is_india


def test_lowercase_preposition_in_does_not_act_as_country_code():
    assert not classify_india_location("Work in office").is_india


def test_bare_uppercase_in_is_supported_as_country_code():
    assert classify_india_location("IN").is_india


def test_austin_indiana_code_is_not_treated_as_india_country_code():
    assert not classify_india_location("Austin, IN, United States").is_india


def test_multi_location_role_is_kept_when_india_is_one_valid_location():
    assert classify_india_location("Bengaluru, India / London, United Kingdom").is_india


def test_generic_reference_to_india_team_does_not_make_remote_job_indian():
    result = classify_india_location(
        "Remote",
        description="You will collaborate with engineering teams in India, Europe, and the United States.",
    )
    assert not result.is_india


def test_pipeline_hard_filters_unknown_and_foreign_but_keeps_india(monkeypatch, tmp_path):
    import job_fetcher.storage as storage
    monkeypatch.setattr(storage, "DB", tmp_path / "jobs.db")

    from job_fetcher.models import Job
    from job_fetcher.relevance_service import analyze_relevance

    description = "Requirements\n3+ years\nJava Spring Boot backend REST SQL AWS"
    jobs = [
        Job("acme", "Acme", "test", "india", "Java Backend Engineer", "Kolkata", description, "https://x/in"),
        Job("acme", "Acme", "test", "foreign", "Java Backend Engineer", "Seattle, WA, United States", description, "https://x/us"),
        Job("acme", "Acme", "test", "unknown", "Java Backend Engineer", "Remote", description, "https://x/remote"),
    ]
    store = storage.JobStore()
    try:
        store.upsert_snapshot("acme", jobs, complete=True)
    finally:
        store.close()

    analyze_relevance()
    relevance = storage.RelevanceStore()
    india = relevance.get("acme", "india")
    foreign = relevance.get("acme", "foreign")
    unknown = relevance.get("acme", "unknown")

    assert india["is_relevant"] == 1
    assert india["normalized_location"] == "Kolkata, India"
    assert foreign["is_relevant"] == 0
    assert foreign["filter_reason"] == "location_outside_target"
    assert unknown["is_relevant"] == 0
    assert unknown["filter_reason"] == "location_unverified_india"
