from __future__ import annotations

import os
import time
from datetime import datetime, timezone
from urllib.parse import parse_qs, urlencode, urlparse, urlunparse

from bs4 import BeautifulSoup
from requests.exceptions import HTTPError, RetryError

from job_fetcher.models import Job
from job_fetcher.sources.base import JobSource
from job_fetcher.sources.http_client import session, timeout_seconds


class EightfoldPcsxSource(JobSource):
    """Read the complete public PCS inventory used by an Eightfold careers page.

    The employer UI calls ``/api/pcsx/search`` with an offset named ``start``.
    Eightfold currently caps this public search endpoint at 10 rows per request, so
    large employers require hundreds of requests. We deliberately pace those
    requests and use long 429 backoff rather than hammering the careers service.

    The response reports the current full-board ``count`` and stable position IDs.
    We walk every offset, union stable IDs across a second pass when live-board
    mutation shifts offsets, and fail closed if the current reported inventory is
    still not covered.

    Listing completeness and JD hydration are separate. Every published listing
    is retained; India listings are additionally hydrated from the public
    ``position_details`` endpoint so downstream relevance/AI receives the full JD.
    """

    source_type = "eightfold_pcsx"
    page_size = 10

    def __init__(self):
        self._client = session()
        self._last_request_at = 0.0
        self._pace_seconds = max(
            0.0,
            float(os.getenv("JOB_FETCHER_EIGHTFOLD_PACE_SECONDS", "0.75")),
        )
        self._rate_limit_backoffs = (4.0, 8.0, 16.0, 32.0)

    @staticmethod
    def _entry(company: dict) -> str:
        source = company.get("source") or {}
        return str(source.get("entry_url") or source.get("provider_url") or company.get("career_url") or "").strip()

    @classmethod
    def contract(cls, company: dict) -> tuple[str, str, str]:
        entry = cls._entry(company)
        parsed = urlparse(entry)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            raise RuntimeError("eightfold_pcsx_invalid_entry_url")
        query = parse_qs(parsed.query or "")
        domain = str((query.get("domain") or [""])[0]).strip()
        if not domain:
            source = company.get("source") or {}
            domain = str(source.get("domain") or "").strip()
        if not domain:
            raise RuntimeError("eightfold_pcsx_missing_domain")
        origin = f"{parsed.scheme}://{parsed.netloc}"
        return origin, domain, entry

    @staticmethod
    def _payload_data(payload) -> dict:
        if not isinstance(payload, dict):
            raise RuntimeError("eightfold_pcsx_invalid_payload")
        if int(payload.get("status") or 200) >= 400:
            raise RuntimeError(f"eightfold_pcsx_api_status:{payload.get('status')}")
        data = payload.get("data")
        if not isinstance(data, dict) or not isinstance(data.get("positions"), list):
            raise RuntimeError("eightfold_pcsx_missing_positions")
        return data

    def _pace(self) -> None:
        if self._pace_seconds <= 0:
            return
        elapsed = time.monotonic() - self._last_request_at
        remaining = self._pace_seconds - elapsed
        if remaining > 0:
            time.sleep(remaining)

    @staticmethod
    def _looks_rate_limited(exc: Exception) -> bool:
        response = getattr(exc, "response", None)
        if response is not None and getattr(response, "status_code", None) == 429:
            return True
        return "429" in str(exc) or "too many" in str(exc).casefold()

    def _get_json(self, url: str, *, params: dict) -> dict:
        last_exc: Exception | None = None
        attempts = 1 + len(self._rate_limit_backoffs)
        for attempt in range(attempts):
            self._pace()
            try:
                response = self._client.get(
                    url,
                    params=params,
                    headers={"Accept": "application/json"},
                    timeout=timeout_seconds(),
                )
                self._last_request_at = time.monotonic()
                response.raise_for_status()
                payload = response.json()
                if not isinstance(payload, dict):
                    raise RuntimeError("eightfold_pcsx_invalid_json_object")
                return payload
            except (RetryError, HTTPError) as exc:
                self._last_request_at = time.monotonic()
                last_exc = exc
                if not self._looks_rate_limited(exc) or attempt >= len(self._rate_limit_backoffs):
                    raise
                time.sleep(self._rate_limit_backoffs[attempt])
        raise RuntimeError(f"eightfold_pcsx_rate_limit_exhausted:{last_exc}")

    def _page(self, origin: str, domain: str, start: int) -> tuple[list[dict], int]:
        payload = self._get_json(
            f"{origin}/api/pcsx/search",
            params={
                "domain": domain,
                "query": "",
                "location": "",
                "start": int(start),
                "page_size": self.page_size,
                "hl": "en",
            },
        )
        data = self._payload_data(payload)
        rows = [row for row in data.get("positions") or [] if isinstance(row, dict)]
        try:
            count = int(data.get("count") or 0)
        except Exception as exc:
            raise RuntimeError("eightfold_pcsx_invalid_count") from exc
        if count < 0:
            raise RuntimeError("eightfold_pcsx_invalid_count")
        return rows, count

    def _detail(self, origin: str, domain: str, position_id: str) -> dict | None:
        try:
            payload = self._get_json(
                f"{origin}/api/pcsx/position_details",
                params={"position_id": position_id, "domain": domain, "hl": "en"},
            )
            data = payload.get("data")
            return data if isinstance(data, dict) and str(data.get("id") or "") == position_id else None
        except Exception:
            return None

    @staticmethod
    def _identity(row: dict) -> str | None:
        value = row.get("id")
        if value is None:
            return None
        text = str(value).strip()
        return text or None

    @staticmethod
    def _is_india(row: dict) -> bool:
        standardized = [str(x or "").strip().upper() for x in row.get("standardizedLocations") or []]
        if any(value == "IN" or value.endswith(", IN") for value in standardized):
            return True
        locations = [str(x or "").casefold() for x in row.get("locations") or []]
        return any("india" in value for value in locations)

    @staticmethod
    def _posted_at(row: dict) -> str | None:
        raw = row.get("postedTs")
        try:
            ts = int(raw or 0)
        except Exception:
            return None
        if ts <= 0:
            return None
        try:
            return datetime.fromtimestamp(ts, tz=timezone.utc).isoformat()
        except Exception:
            return None

    @staticmethod
    def _description(row: dict) -> str | None:
        value = row.get("jobDescription")
        if not value:
            return None
        return BeautifulSoup(str(value), "html.parser").get_text("\n", strip=True) or None

    @classmethod
    def _job_url(cls, origin: str, domain: str, row: dict) -> str:
        public = str(row.get("publicUrl") or "").strip()
        if public.startswith(("http://", "https://")):
            return public
        position_id = cls._identity(row)
        path = str(row.get("positionUrl") or "").strip()
        if not path.startswith("/"):
            path = f"/careers/job/{position_id}"
        query = urlencode({"domain": domain, "hl": "en"})
        return urlunparse((urlparse(origin).scheme, urlparse(origin).netloc, path, "", query, ""))

    @classmethod
    def _to_job(cls, company: dict, origin: str, domain: str, row: dict) -> Job:
        position_id = cls._identity(row)
        title = str(row.get("name") or row.get("title") or "").strip()
        if not position_id or not title:
            raise RuntimeError("eightfold_pcsx_record_missing_id_or_title")
        locations = []
        for value in row.get("locations") or []:
            text = str(value or "").strip()
            if text and text not in locations:
                locations.append(text)
        return Job(
            company_id=company["id"],
            company_name=company["name"],
            source_type=cls.source_type,
            external_id=position_id,
            title=title,
            location=" | ".join(locations) or None,
            description=cls._description(row),
            job_url=cls._job_url(origin, domain, row),
            posted_at=cls._posted_at(row),
            raw=row,
        )

    def _walk_once(self, origin: str, domain: str, target_count: int | None = None) -> tuple[dict[str, dict], int, int]:
        by_id: dict[str, dict] = {}
        seen_page_fingerprints: set[tuple[str, ...]] = set()
        start = 0
        current_count = max(0, int(target_count or 0))
        pages = 0

        while pages == 0 or start < current_count:
            rows, count = self._page(origin, domain, start)
            pages += 1
            current_count = count
            ids = tuple(self._identity(row) or "" for row in rows)
            fingerprint = tuple(value for value in ids if value)

            if not rows:
                if start >= current_count:
                    break
                raise RuntimeError(f"eightfold_pcsx_premature_empty:{len(by_id)}/{current_count}@{start}")
            if not fingerprint:
                raise RuntimeError(f"eightfold_pcsx_page_without_ids:{start}")
            if fingerprint in seen_page_fingerprints:
                raise RuntimeError(f"eightfold_pcsx_repeated_page:{start}")
            seen_page_fingerprints.add(fingerprint)

            for row in rows:
                position_id = self._identity(row)
                if position_id:
                    by_id[position_id] = row
            start += len(rows)

            if pages > 10000:
                raise RuntimeError("eightfold_pcsx_pagination_guard")

        return by_id, current_count, pages

    def enumerate_rows(self, company: dict) -> tuple[dict[str, dict], dict]:
        origin, domain, _entry = self.contract(company)
        first_rows, first_count, first_pages = self._walk_once(origin, domain)
        by_id = dict(first_rows)
        reported_count = first_count
        pages = first_pages

        if len(by_id) < reported_count:
            second_rows, second_count, second_pages = self._walk_once(origin, domain, reported_count)
            pages += second_pages
            by_id.update(second_rows)
            reported_count = second_count

        if len(by_id) < reported_count:
            raise RuntimeError(f"eightfold_pcsx_incomplete:{len(by_id)}/{reported_count}")

        return by_id, {
            "origin": origin,
            "domain": domain,
            "reported_count": reported_count,
            "unique_count": len(by_id),
            "pagination_exhausted": True,
            "pages_requested": pages,
        }

    def fetch(self, company: dict) -> list[Job]:
        by_id, evidence = self.enumerate_rows(company)
        origin = evidence["origin"]
        domain = evidence["domain"]
        jobs = []
        for position_id, listing in by_id.items():
            row = dict(listing)
            if self._is_india(row) and not row.get("jobDescription"):
                detail = self._detail(origin, domain, position_id)
                if detail:
                    row.update(detail)
                    row["_pcsx_detail_hydrated"] = True
                else:
                    row["_pcsx_detail_hydrated"] = False
            row["_pcsx_completeness"] = evidence
            jobs.append(self._to_job(company, origin, domain, row))
        return jobs
