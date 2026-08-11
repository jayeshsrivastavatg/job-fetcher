import os
from requests import Session
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

USER_AGENT = "Mozilla/5.0 (compatible; PersonalJobFetcher/0.3; +local-personal-use)"


def timeout_seconds(default=30):
    return float(os.getenv("JOB_FETCHER_HTTP_TIMEOUT", str(default)))


def session():
    s = Session()
    retries = int(os.getenv("JOB_FETCHER_RETRIES", "3"))
    retry = Retry(
        total=retries,
        connect=retries,
        read=min(retries, 2),
        status=min(retries, 2),
        backoff_factor=0.5,
        status_forcelist=(429, 500, 502, 503, 504),
        allowed_methods=frozenset({"GET", "POST"}),
        respect_retry_after_header=True,
    )
    s.mount("https://", HTTPAdapter(max_retries=retry, pool_connections=20, pool_maxsize=20))
    s.mount("http://", HTTPAdapter(max_retries=retry, pool_connections=20, pool_maxsize=20))
    s.headers.update({
        "User-Agent": USER_AGENT,
        "Accept-Language": "en-US,en;q=0.9",
        "Accept": "text/html,application/xhtml+xml,application/json;q=0.9,*/*;q=0.8",
    })
    return s
