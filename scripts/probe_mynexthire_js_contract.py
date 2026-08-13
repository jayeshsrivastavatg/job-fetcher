from __future__ import annotations

import json
import re
from pathlib import Path

import requests


BASE = "https://swiggy.mynexthire.com"
URL = f"{BASE}/employer/ui/js/jobboard/careers.js"
DETAIL = f"{BASE}/employer/careers/req/get"
REQ_ID = 24364
NEEDLES = [
    r"getRequisition",
    r"requisitionPageClass",
    r"reqId",
    r"careersFactory",
    r"/careers/",
    r"/requisition",
    r"job.*detail",
    r"req.*detail",
]


def _try_detail(session: requests.Session, payload: dict) -> dict:
    try:
        r = session.post(
            DETAIL,
            json=payload,
            timeout=45,
            headers={
                "Accept": "application/json, text/plain, */*",
                "Origin": "https://careers.swiggy.com",
                "Referer": "https://careers.swiggy.com/#/careers",
            },
        )
        row = {
            "payload": payload,
            "status": r.status_code,
            "content_type": r.headers.get("content-type"),
            "body": r.text[:50000],
        }
        try:
            parsed = r.json()
            row["json"] = parsed
        except Exception:
            pass
        return row
    except Exception as exc:
        return {"payload": payload, "error": f"{type(exc).__name__}: {exc}"}


def main() -> int:
    s = requests.Session()
    s.headers.update({"User-Agent": "Mozilla/5.0 Chrome/131 Safari/537.36"})
    r = s.get(URL, timeout=45)
    r.raise_for_status()
    text = r.text
    report = {"url": URL, "bytes": len(text), "matches": [], "detail_attempts": []}
    seen = set()
    for needle in NEEDLES:
        for match in re.finditer(needle, text, re.I):
            lo = max(0, match.start() - 1800)
            hi = min(len(text), match.end() + 3500)
            snippet = text[lo:hi]
            key = snippet[:250]
            if key in seen:
                continue
            seen.add(key)
            report["matches"].append({"needle": needle, "offset": match.start(), "snippet": snippet})
            if len(report["matches"]) >= 100:
                break
        if len(report["matches"]) >= 100:
            break

    report["endpoint_literals"] = list(dict.fromkeys(
        m.group(1)
        for m in re.finditer(r"[\"']([^\"']*(?:career|requis|req|job)[^\"']*)[\"']", text, re.I)
        if len(m.group(1)) < 500
    ))[:500]

    # getRequisition() in the public careers.js posts qStringContext with source/id/code
    # plus reqId. Exercise the small set of legitimate empty/default values used by
    # Swiggy's unfiltered public board to pin down the live contract before production
    # code depends on it.
    payloads = [
        {"source": "careers", "id": "", "code": "", "reqId": REQ_ID},
        {"source": "careers", "id": None, "code": "", "reqId": REQ_ID},
        {"source": "careers", "id": "", "code": "", "reqId": str(REQ_ID)},
        {"source": "careers", "code": "", "reqId": REQ_ID},
    ]
    for payload in payloads:
        row = _try_detail(s, payload)
        report["detail_attempts"].append(row)
        print("DETAIL", row.get("status"), json.dumps(payload, separators=(",", ":")))
        body = row.get("body") or row.get("error") or ""
        print("DETAIL_BODY", body[:6000].replace("\n", " "))

    Path("reports/phase4").mkdir(parents=True, exist_ok=True)
    Path("reports/phase4/mynexthire-js-contract.json").write_text(
        json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    print("BYTES", len(text), "MATCHES", len(report["matches"]))
    for literal in report["endpoint_literals"]:
        print("LITERAL", literal)
    for row in report["matches"][:30]:
        print("MATCH", row["needle"], row["offset"])
        print(row["snippet"][:5200].replace("\n", " "))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
