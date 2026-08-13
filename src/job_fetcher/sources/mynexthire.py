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


class MyNextHireSource(JobSource):
    """Public MyNextHire career-board inventory.

    The employer's own careers UI calls ``/employer/careers/reqlist/get`` without
    authentication and receives the complete current requisition list. The rows
    contain the stable ``reqId`` plus title, location, approval timestamp and the
    full displayed JD, so no browser/link heuristics are needed.
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
        response = client.post(
            endpoint,
            json=payload,
            timeout=timeout_seconds(),
            headers={
                "Origin": str(src.get("origin") or company.get("career_url") or base).rstrip("/"),
                "Referer": str(src.get("referer") or company.get("career_url") or base),
                "Accept": "application/json, text/plain, */*",
            },
        )
        response.raise_for_status()
        body = response.json() or {}
        rows = body.get("reqDetailsBOList")
        if not isinstance(rows, list):
            raise RuntimeError("mynexthire_invalid_requisition_payload")

        jobs = []
        seen = set()
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

            location = _text(row.get("location") or row.get("locationAddress"))
            if not location:
                locations = row.get("locationList") or []
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
                description=_text(row.get("jdDisplay")),
                job_url=f"{base}/employer/jobs/careers?reqId={external_id}",
                posted_at=_text(row.get("approvedOn")),
                raw=row,
            ))

        if rows and not jobs:
            raise RuntimeError("mynexthire_rows_without_valid_requisitions")
        return jobs
