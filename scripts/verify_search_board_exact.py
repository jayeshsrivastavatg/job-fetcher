from copy import deepcopy
from urllib.parse import urljoin, urlparse
from bs4 import BeautifulSoup
from job_fetcher.config import load_config
from job_fetcher.job_quality import prefer_usable_jobs
from job_fetcher.sources.factory import build_source
from job_fetcher.sources.http_client import session, timeout_seconds


def official(company):
    src = company.get("source") or {}
    entry = src.get("entry_url") or company["career_url"]
    parsed = urlparse(entry)
    origin = f"https://{parsed.netloc}"
    ids, india = set(), set()
    for page in range(1, 101):
        r = session().get(
            f"{origin}/en/search-jobs/results",
            params={"ActiveFacetID": 0, "CurrentPage": page, "RecordsPerPage": 50, "FacetType": 0},
            timeout=timeout_seconds(),
            headers={"X-Requested-With": "XMLHttpRequest", "Accept": "application/json"},
        )
        r.raise_for_status()
        data = r.json()
        soup = BeautifulSoup(data.get("results") or "", "html.parser")
        anchors = soup.select("a[href][data-job-id]")
        if not anchors:
            break
        for anchor in anchors:
            jid = str(anchor.get("data-job-id") or "").strip()
            if not jid:
                continue
            href = urljoin(origin, anchor.get("href") or "")
            if urlparse(href).netloc.casefold() != parsed.netloc.casefold():
                continue
            ids.add(jid)
            container = anchor.find_parent("li") or anchor.parent
            node = container.select_one(".job-location") if container else None
            if node and "india" in node.get_text(" ", strip=True).casefold():
                india.add(jid)
        if len(anchors) < 50:
            break
    if not ids:
        raise RuntimeError("official search-board witness returned zero jobs")
    return ids, india


def main():
    companies = [c for c in load_config()["companies"] if c.get("enabled", True) and urlparse(str((c.get("source") or {}).get("entry_url") or c.get("career_url") or "")).path.rstrip("/").casefold().endswith("/search-jobs")]
    failures = 0
    for company in companies:
        before, india_before = official(company)
        c = deepcopy(company)
        source = build_source(c)
        jobs = list(prefer_usable_jobs(source.fetch(c)) or [])
        by_id = {str(j.external_id): j for j in jobs if j.external_id}
        after, india_after = official(company)
        stable = before & after
        stable_india = india_before & india_after
        missing = stable - set(by_id)
        extras = set(by_id) - (before | after)
        bad_location = [i for i in stable_india if not str(getattr(by_id.get(i), "location", "") or "").strip()]
        bad_jd = [i for i in stable_india if len(str(getattr(by_id.get(i), "description", "") or "").strip()) < 120]
        passed = not missing and not extras and not bad_location and not bad_jd
        print({"id": company["id"], "adapter": type(source).__name__, "official_before": len(before), "official_after": len(after), "production": len(by_id), "missing": len(missing), "extras": len(extras), "india": len(stable_india), "india_location": len(stable_india)-len(bad_location), "india_jd": len(stable_india)-len(bad_jd), "passed": passed}, flush=True)
        failures += not passed
    if not companies or failures:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
