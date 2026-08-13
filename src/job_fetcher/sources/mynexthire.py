from __future__ import annotations

from html import unescape
import re

from job_fetcher.models import Job
from job_fetcher.sources.base import JobSource
from job_fetcher.sources.http_client import session, timeout_seconds


_TAG_RE = re.compile(r"<[^>]+>")
_SPACE_RE = re.compile(r"[ \t\r\f\v]+")


def _text(value) -> str | None:
    if value is None:
        return None
    text = unescape(str(value)).replace("\xa0", " ")
    text = _TAG_RE.sub(" ", text)
    text = "\n".join(_SPACE_RE.sub(" ", line).strip() for line in text.splitlines())
    text = re.sub(r"\n{3,}", "\n\n", text).strip()
    return text or None


def _description_chars(value) -> int:
    return len(re.sub(r"\s+", " ", _text(value) or "").strip())


class MyNextHireSource(JobSource):
    """Public MyNextHire career-board inventory.

    The employer's own careers UI calls ``/employer/careers/reqlist/get`` without
    authentication and receives the complete current requisition list. Rows contain
    stable ``reqId`` values plus title/location/JD metadata. A few tenants publish a
    short/empty list JD, so those rows are re-read through the same public careers
    detail contract used by the provider SPA; we never synthesize missing text.
    """

    def fetch(self, company):
        src = company.get("source") or {}
        tenant = str(src.get("tenant") or "").strip()
        if not tenant:
            raise RuntimeError("mynexthire_missing_tenant")

        base = str(src.get("base_url") or f"https://{tenant}.mynexthire.com").rstrip("/")
        endpoint = f"{base}/employer/careers/reqlist/get"
        payload = {
            "source": str(src.get("source_short_name") or "careers"),
            "code": str(src.get("code") or ""),
            "filterByBuId": int(src.get("filter_by_bu_id", -1)),
        }
        client = session()
        headers = {
            "Origin": str(src.get("origin") or company.get("career_url") or base).rstrip("/"),
            "Referer": str(src.get("referer") or company.get("career_url") or base),
            "Accept": "application/json, text/plain, */*",
        }
        response = client.post(
            endpoint,
            json=payload,
            timeout=timeout_seconds(),
            headers=headers,
        )
        response.raise_for_status()
        body = response.json() or {}
        rows = body.get("reqDetailsBOList")
        if not isinstance(rows, list):
            raise RuntimeError("mynexthire_invalid_requisition_payload")

        jobs = []
        seen = set()
        min_jd_chars = max(1, int(src.get("detail_hydrate_below_chars", 120)))
        for row in rows:
            if not isinstance(row, dict):
                continue
            req_id = row.get("reqId")
            title = _text(row.get("reqTitle") or row.get("designation"))
            if req_id is None or not title:
                continue
            external_id = str(req_id).strip()
            if not external_id or external_id in seen:
                continue
            seen.add(external_id)

            effective = dict(row)
            detail_meta = None
            if _description_chars(effective.get("jdDisplay")) < min_jd_chars:
                detail_meta = self._fetch_detail(
                    client=client,
                    base=base,
                    headers=headers,
                    src=src,
                    external_id=external_id,
                )
                detail_row = (detail_meta or {}).get("row")
                if isinstance(detail_row, dict):
                    # Only use the detail record when the provider returned the exact
                    # requisition we requested. Prefer richer official values; never
                    # overwrite a better list value with an empty/short detail value.
                    if _description_chars(detail_row.get("jdDisplay")) > _description_chars(effective.get("jdDisplay")):
                        effective["jdDisplay"] = detail_row.get("jdDisplay")
                    for key in ("location", "locationAddress", "locationList", "approvedOn"):
                        if not effective.get(key) and detail_row.get(key):
                            effective[key] = detail_row.get(key)

            location = _text(effective.get("location") or effective.get("locationAddress"))
            if not location:
                locations = effective.get("locationList") or []
                pieces = []
                for item in locations:
                    if isinstance(item, dict):
                        value = _text(item.get("office") or item.get("address"))
                        if value and value not in pieces:
                            pieces.append(value)
                location = ", ".join(pieces) or None

            jobs.append(Job(
                company_id=company["id"],
                company_name=company["name"],
                source_type="mynexthire",
                external_id=external_id,
                title=title,
                location=location,
                description=_text(effective.get("jdDisplay")),
                job_url=f"{base}/employer/jobs/careers?reqId={external_id}",
                posted_at=_text(effective.get("approvedOn")),
                raw={
                    "listing": row,
                    "detail": (detail_meta or {}).get("raw"),
                    "detail_status": (detail_meta or {}).get("status"),
                },
            ))

        if rows and not jobs:
            raise RuntimeError("mynexthire_rows_without_valid_requisitions")
        return jobs

    @staticmethod
    def _fetch_detail(*, client, base, headers, src, external_id):
        endpoint = f"{base}/employer/careers/req/get"
        payload = {
            "source": str(src.get("source_short_name") or "careers"),
            "id": str(src.get("id") or ""),
            "code": str(src.get("code") or ""),
            "reqId": int(external_id) if external_id.isdigit() else external_id,
        }
        try:
            response = client.post(
                endpoint,
                json=payload,
                timeout=timeout_seconds(),
                headers=headers,
            )
            response.raise_for_status()
            body = response.json() or {}
            row = body.get("reqDetailsBO")
            if not isinstance(row, dict):
                return {"status": "invalid_payload", "raw": body, "row": None}
            returned = str(row.get("reqId") or "").strip()
            if returned != external_id:
                return {"status": "id_mismatch", "raw": body, "row": None}
            return {"status": "matched", "raw": body, "row": row}
        except Exception as exc:
            return {
                "status": f"error:{type(exc).__name__}",
                "raw": {"error": str(exc)},
                "row": None,
            }
