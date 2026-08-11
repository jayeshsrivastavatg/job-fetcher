from __future__ import annotations

from job_fetcher.sources.recovery import RECOVERY_PLANS


# Additional plans discovered during the second health pass. Keeping these
# separate avoids making the original recovery registry harder to review while
# still mutating the same shared dictionary used by the wrappers.
RECOVERY_PLANS.update({
    "cars24": [
        {
            "kind": "official_html",
            "entry_url": "https://careers.cars24.com/",
            "default_location": "India",
            "require_india": True,
            "max_pages": 20,
            "job_href_patterns": [r"careers\.cars24\.com/.+(?:job|role|opening)"],
        },
        {
            "kind": "recovery_browser",
            "entry_url": "https://careers.cars24.com/",
            "browser_max_pages": 20,
            "browser_max_scrolls": 15,
            "browser_load_more_clicks": 20,
        },
    ],
    "urban_company": [
        {
            "kind": "recovery_browser",
            "entry_url": "https://careers.urbancompany.com/jobs",
            "browser_max_pages": 20,
            "browser_max_scrolls": 15,
            "browser_load_more_clicks": 20,
        },
    ],
    "epam": [
        {
            "kind": "official_html",
            "entry_url": "https://careers.epam.com/en/jobs/india",
            "default_location": "India",
            "require_india": True,
            "max_pages": 40,
            "job_href_patterns": [r"careers\.epam\.com/en/vacancy/", r"/en/vacancy/"],
        },
        {
            "kind": "recovery_browser",
            "entry_url": "https://careers.epam.com/en/jobs/india",
            "browser_max_pages": 30,
            "browser_max_scrolls": 12,
        },
    ],
    "snowflake": [
        {
            "kind": "official_html",
            "entry_url": "https://careers.snowflake.com/us/en/search-results",
            "max_pages": 30,
            "job_href_patterns": [r"careers\.snowflake\.com/us/en/job/", r"/us/en/job/"],
        },
        {
            "kind": "recovery_browser",
            "entry_url": "https://careers.snowflake.com/us/en/search-results",
            "browser_max_pages": 30,
            "browser_max_scrolls": 10,
        },
    ],
    "zomato_blinkit": [
        {
            "kind": "smartrecruiters",
            "company_identifier": "Zomato1",
        },
    ],
    "winzo": [
        {
            "kind": "official_html",
            "entry_url": "https://winzo.keka.com/careers",
            "max_pages": 20,
            "job_href_patterns": [
                r"winzo\.keka\.com/careers/jobdetails/\d+",
                r"/careers/jobdetails/\d+",
            ],
        },
        {
            "kind": "recovery_browser",
            "entry_url": "https://winzo.keka.com/careers",
            "browser_max_pages": 20,
            "browser_max_scrolls": 15,
            "browser_load_more_clicks": 20,
        },
    ],
    "rakuten_india": [
        {
            "kind": "slow_official_html",
            "entry_urls": [
                "https://rakuten.openings.co/rakuten/jobslist",
                "https://rakuten.openings.co/rakuten/",
            ],
            "default_location": "India",
            "require_india": True,
            "http_timeout_seconds": 90,
            "max_pages": 30,
            "job_href_patterns": [r"openings\.co/rakuten/jobview/"],
        },
        {
            "kind": "recovery_browser",
            "entry_url": "https://rakuten.openings.co/rakuten/jobslist",
            "browser_timeout_ms": 90000,
            "browser_max_pages": 20,
            "browser_max_scrolls": 12,
        },
    ],
    "sony_tech_india": [
        {
            "kind": "slow_official_html",
            "entry_url": "https://careers.sonyindiasoftware.co.in/sonyindiasoftware/",
            "default_location": "India",
            "require_india": True,
            "http_timeout_seconds": 90,
            "max_pages": 20,
            "job_href_patterns": [r"sonyindiasoftware/jobview/"],
        },
        {
            "kind": "recovery_browser",
            "entry_url": "https://careers.sonyindiasoftware.co.in/sonyindiasoftware/",
            "browser_timeout_ms": 90000,
            "browser_max_pages": 20,
            "browser_max_scrolls": 12,
        },
    ],
    "makemytrip": [
        {
            "kind": "slow_official_html",
            "entry_urls": [
                "https://careers.makemytrip.com/prod/careerPlaybook/software-engineering",
                "https://careers.makemytrip.com/prod/",
            ],
            "default_location": "India",
            "require_india": True,
            "http_timeout_seconds": 90,
            "max_pages": 25,
            "job_href_patterns": [r"/prod/opportunity/[^/?#]+"],
        },
        {
            "kind": "recovery_browser",
            "entry_url": "https://careers.makemytrip.com/prod/careerPlaybook/software-engineering",
            "browser_timeout_ms": 90000,
            "browser_max_pages": 15,
            "browser_max_scrolls": 10,
        },
    ],
})
