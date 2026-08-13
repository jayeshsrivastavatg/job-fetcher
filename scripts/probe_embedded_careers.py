from __future__ import annotations

import json
from bs4 import BeautifulSoup

from job_fetcher.config import load_config
from job_fetcher.sources.http_client import session, timeout_seconds


def main():
    for company in load_config().get("companies", []):
        source = company.get("source") or {}
        entry = str(source.get("entry_url") or company.get("career_url") or "").rstrip("/")
        if source.get("type") != "auto" or not entry.endswith("/jobs/Careers"):
            continue
        response = session().get(entry, timeout=timeout_seconds(), allow_redirects=True)
        print(json.dumps({"company_id": company.get("id"), "status": response.status_code, "final_url": response.url}), flush=True)
        response.raise_for_status()
        soup = BeautifulSoup(response.text, "html.parser")
        node = soup.find("input", id="jobs")
        raw = node.get("value") if node else None
        print(json.dumps({"input_found": bool(node), "value_chars": len(raw or "")}), flush=True)
        if not raw:
            continue
        try:
            payload = json.loads(raw)
        except Exception as exc:
            print(json.dumps({"parse_error": f"{type(exc).__name__}: {exc}", "value_prefix": raw[:300]}), flush=True)
            continue
        print(json.dumps({"payload_type": type(payload).__name__, "row_count": len(payload) if isinstance(payload, list) else None}), flush=True)
        if isinstance(payload, list) and payload and isinstance(payload[0], dict):
            sample = payload[0]
            print(json.dumps({"first_row_keys": sorted(sample.keys())}), flush=True)
            for key in sorted(sample):
                low = key.casefold()
                if any(token in low for token in ("id", "title", "name", "publish", "lock", "city", "state", "country")):
                    print(json.dumps({"field": key, "value": sample.get(key)}, ensure_ascii=False), flush=True)


if __name__ == "__main__":
    main()
