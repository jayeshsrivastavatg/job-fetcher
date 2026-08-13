from __future__ import annotations

import time

from job_fetcher.models import Job
from job_fetcher.sources.base import JobSource
from job_fetcher.sources.http_client import session, timeout_seconds
from job_fetcher.sources.generic_extract import clean_text


class WorkdaySource(JobSource):
    """Fetch a complete Workday CXS board or fail loudly.

    Some Workday tenants temporarily return an empty page when paged too quickly.
    The old implementation interpreted that as end-of-results, which is how boards
    reporting hundreds/thousands of jobs could silently stop at exactly 40 rows.
    This implementation paces requests, retries premature empty pages, detects
    repeated pages, and refuses to return a known-partial snapshot.

    Workday can also emit an otherwise valid search row with an empty ``title``.
    Those rows used to be discarded later by the job-quality guard, producing
    1999/2000-style completeness gaps on large boards. For only those rare rows we
    hydrate the same vacancy's public CXS detail endpoint and recover the title
    from ``jobPostingInfo.title``. If detail hydration fails or is still blank, the
    row remains blank and is still rejected by the normal quality guard.
    """

    def fetch(self, company):
        src = company["source"]
        host, tenant, site = src["host"], src["tenant"], src["site"]
        locale = src.get("locale", "en-US")
        api = f"https://{host}/wday/cxs/{tenant}/{site}/jobs"
        max_jobs = int(src.get("max_jobs", 5000))
        limit = max(1, min(20, int(src.get("page_size", 20))))
        page_delay = max(0.0, float(src.get("page_delay_seconds", 0.35)))
        empty_retries = max(0, int(src.get("premature_empty_retries", 4)))
        detail_retries = max(0, int(src.get("missing_title_detail_retries", 2)))

        client = session()
        headers = {
            "Accept": "application/json",
            "Content-Type": "application/json",
            "User-Agent": "Mozilla/5.0 PersonalJobFetcher/0.1",
            "Origin": f"https://{host}",
            "Referer": f"https://{host}/{locale}/{site}",
        }

        out = []
        offset = 0
        expected_total = None
        seen_page_fingerprints = set()

        while True:
            data, items = self._fetch_page(
                client,
                api,
                headers,
                offset=offset,
                limit=limit,
                retries=empty_retries,
                expected_total=expected_total,
                page_delay=page_delay,
            )
            reported_total = int(data.get("total") or 0)
            if expected_total is None:
                expected_total = reported_total
            elif reported_total:
                # Workday counts can change while a long fetch is in progress. Use
                # the latest reported total, but never shrink below rows already seen.
                expected_total = max(offset, reported_total)

            if not items:
                if expected_total is not None and offset < expected_total:
                    raise RuntimeError(
                        f"workday_incomplete_pagination: board reported total={expected_total} "
                        f"but returned an empty page at offset={offset}"
                    )
                break

            fingerprint = tuple(
                clean_text(x.get("externalPath")) or clean_text((x.get("bulletFields") or [None])[0])
                for x in items
            )
            if fingerprint in seen_page_fingerprints:
                raise RuntimeError(
                    f"workday_repeated_page: offset={offset} total={expected_total}; "
                    "refusing to publish a partial/looping snapshot"
                )
            seen_page_fingerprints.add(fingerprint)

            for x in items:
                ext = clean_text(x.get("externalPath"))
                job_url = f"https://{host}/{locale}/{site}{ext}" if ext else None
                bullets = x.get("bulletFields") or []
                eid = clean_text(bullets[0]) if isinstance(bullets, list) and bullets else None
                title = clean_text(x.get("title"))
                detail_info = None

                if not title and ext:
                    detail_info = self._fetch_missing_title_detail(
                        client,
                        host=host,
                        tenant=tenant,
                        site=site,
                        external_path=ext,
                        headers=headers,
                        retries=detail_retries,
                    )
                    title = clean_text((detail_info or {}).get("title"))
                    if not eid:
                        eid = clean_text((detail_info or {}).get("jobReqId"))

                raw = x
                if detail_info:
                    raw = dict(x)
                    raw["missing_title_detail"] = detail_info

                out.append(
                    Job(
                        company["id"],
                        company["name"],
                        "workday",
                        eid or job_url,
                        title or "",
                        clean_text(x.get("locationsText")) or clean_text((detail_info or {}).get("location")),
                        None,
                        job_url,
                        clean_text(x.get("postedOn")) or clean_text((detail_info or {}).get("postedOn")),
                        raw,
                    )
                )
                if len(out) >= max_jobs:
                    break

            offset += len(items)
            if offset >= max_jobs:
                if expected_total is not None and expected_total > max_jobs:
                    raise RuntimeError(
                        f"workday_max_jobs_too_low: configured max_jobs={max_jobs} "
                        f"but provider reports total={expected_total}"
                    )
                break
            if expected_total is not None and offset >= expected_total:
                break
            if page_delay:
                time.sleep(page_delay)

        if expected_total is not None and expected_total <= max_jobs and len(out) < expected_total:
            raise RuntimeError(
                f"workday_incomplete_pagination: fetched={len(out)} provider_total={expected_total}"
            )
        return out

    @staticmethod
    def _fetch_page(client, api, headers, *, offset, limit, retries, expected_total, page_delay):
        body = {"appliedFacets": {}, "limit": limit, "offset": offset, "searchText": ""}
        last_data = {}
        last_items = []
        attempts = retries + 1
        for attempt in range(attempts):
            response = client.post(api, json=body, timeout=timeout_seconds(), headers=headers)
            response.raise_for_status()
            data = response.json()
            items = data.get("jobPostings") or []
            last_data, last_items = data, items
            total = int(data.get("total") or expected_total or 0)
            premature_empty = not items and offset < total
            if not premature_empty:
                return data, items
            if attempt < attempts - 1:
                time.sleep(max(0.5, page_delay) * (attempt + 1))
        return last_data, last_items

    @staticmethod
    def _fetch_missing_title_detail(
        client,
        *,
        host,
        tenant,
        site,
        external_path,
        headers,
        retries,
    ):
        path = external_path if str(external_path).startswith("/") else f"/{external_path}"
        url = f"https://{host}/wday/cxs/{tenant}/{site}{path}"
        attempts = retries + 1
        for attempt in range(attempts):
            try:
                response = client.get(url, timeout=timeout_seconds(), headers=headers)
                response.raise_for_status()
                payload = response.json()
                info = payload.get("jobPostingInfo") if isinstance(payload, dict) else None
                if isinstance(info, dict) and clean_text(info.get("title")):
                    return info
            except Exception:
                pass
            if attempt < attempts - 1:
                time.sleep(0.35 * (attempt + 1))
        return None
