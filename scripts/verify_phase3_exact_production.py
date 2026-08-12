from __future__ import annotations

import argparse
import json
import sys
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path

from playwright.sync_api import sync_playwright

from job_fetcher.config import load_config
from job_fetcher.job_quality import prefer_usable_jobs
from job_fetcher.sources.eightfold_pcsx import EightfoldPcsxSource
from job_fetcher.sources.eightfold_pcsx_exhaustive import EightfoldPcsxExhaustiveSource
from job_fetcher.sources.factory import build_source

TARGETS = {"microsoft", "twilio", "morgan_stanley"}


def utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


def official_snapshot(company: dict) -> dict:
    source = EightfoldPcsxExhaustiveSource()
    rows, evidence = source.enumerate_rows(deepcopy(company))
    return {
        "ids": set(rows),
        "count": len(rows),
        "reported_count": int(evidence["reported_count"]),
        "provider_row_count": int(evidence.get("provider_row_count") or 0),
        "duplicate_row_occurrences": int(evidence.get("duplicate_row_occurrences") or 0),
        "duplicate_id_count": int(evidence.get("duplicate_id_count") or 0),
        "duplicate_ids_sample": evidence.get("duplicate_ids_sample") or {},
        "pagination_exhausted": bool(evidence["pagination_exhausted"]),
        "passes": int(evidence.get("passes") or 1),
        "pages_requested": int(evidence["pages_requested"]),
        "origin": evidence["origin"],
        "domain": evidence["domain"],
    }


def browser_witness(company: dict) -> dict:
    """Prove the employer careers UI itself calls the PCS search inventory."""
    expected_origin, expected_domain, entry = EightfoldPcsxSource.contract(company)
    captured = {}
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(
            user_agent="Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/131 Safari/537.36",
            locale="en-US",
        )
        page = context.new_page()

        def on_response(response):
            if captured:
                return
            url = response.url
            if not url.startswith(f"{expected_origin}/api/pcsx/search"):
                return
            try:
                payload = response.json()
                data = payload.get("data") if isinstance(payload, dict) else None
                if not isinstance(data, dict):
                    return
                positions = data.get("positions") or []
                captured.update({
                    "url": url,
                    "count": int(data.get("count") or 0),
                    "ids": [
                        str(row.get("id"))
                        for row in positions
                        if isinstance(row, dict) and row.get("id") is not None
                    ],
                })
            except Exception:
                return

        page.on("response", on_response)
        page.goto(entry, wait_until="domcontentloaded", timeout=120000)
        page.wait_for_timeout(3000)
        final_url = page.url
        context.close()
        browser.close()

    if not captured:
        raise RuntimeError("phase3_browser_did_not_call_expected_pcsx_search")
    captured["final_url"] = final_url
    captured["expected_origin"] = expected_origin
    captured["expected_domain"] = expected_domain
    return captured


def app_snapshot(company: dict) -> dict:
    source = build_source(deepcopy(company))
    jobs = list(prefer_usable_jobs(source.fetch(deepcopy(company))) or [])
    ids = {
        str(getattr(job, "external_id", "") or "").strip()
        for job in jobs
        if str(getattr(job, "external_id", "") or "").strip()
    }
    india = [
        job
        for job in jobs
        if "india" in str(getattr(job, "location", "") or "").casefold()
        or any(
            token.strip().upper() == "IN" or token.strip().upper().endswith(", IN")
            for token in (getattr(job, "raw", None) or {}).get("standardizedLocations", [])
        )
    ]
    india_with_jd = sum(
        bool(str(getattr(job, "description", "") or "").strip()) for job in india
    )
    return {
        "adapter": type(source).__name__,
        "ids": ids,
        "count": len(jobs),
        "india_count": len(india),
        "india_with_description": india_with_jd,
    }


def verify(company: dict) -> dict:
    before = official_snapshot(company)
    witness = browser_witness(company)
    app = app_snapshot(company)
    after = official_snapshot(company)

    stable_current = before["ids"] & after["ids"]
    missing = stable_current - app["ids"]
    witness_still_current = set(witness["ids"]) & after["ids"]
    missing_witness = witness_still_current - app["ids"]

    # `data.count` is a result-row count. Some Eightfold boards legitimately repeat
    # one vacancy ID in multiple rows, so exact completeness is based on the stable
    # vacancy-ID set *after* proving every result-row offset was exhausted.
    passed = (
        before["pagination_exhausted"]
        and after["pagination_exhausted"]
        and before["provider_row_count"] >= before["reported_count"]
        and after["provider_row_count"] >= after["reported_count"]
        and not missing
        and not missing_witness
        and witness["url"].startswith(f"{before['origin']}/api/pcsx/search")
    )

    return {
        "company_id": company["id"],
        "company": company["name"],
        "verdict": "CERTIFIED" if passed else "FAILED",
        "passed": passed,
        "production_adapter": app["adapter"],
        "official_before_unique_vacancies": before["count"],
        "official_before_reported_rows": before["reported_count"],
        "official_before_rows_exhausted": before["provider_row_count"],
        "official_before_duplicate_rows": before["duplicate_row_occurrences"],
        "official_before_duplicate_id_count": before["duplicate_id_count"],
        "official_before_duplicate_ids_sample": before["duplicate_ids_sample"],
        "official_after_unique_vacancies": after["count"],
        "official_after_reported_rows": after["reported_count"],
        "official_after_rows_exhausted": after["provider_row_count"],
        "official_after_duplicate_rows": after["duplicate_row_occurrences"],
        "stable_current_jobs_checked": len(stable_current),
        "production_jobs": app["count"],
        "missing_count": len(missing),
        "missing_ids": sorted(missing)[:200],
        "extra_count_vs_stable": len(app["ids"] - stable_current),
        "browser_witness_count": witness["count"],
        "browser_witness_first_page_ids": witness["ids"],
        "browser_witness_missing_if_still_current": sorted(missing_witness),
        "browser_called_expected_inventory": witness["url"].startswith(
            f"{before['origin']}/api/pcsx/search"
        ),
        "pagination_exhausted_before": before["pagination_exhausted"],
        "pagination_exhausted_after": after["pagination_exhausted"],
        "passes_before": before["passes"],
        "passes_after": after["passes"],
        "pages_requested_before": before["pages_requested"],
        "pages_requested_after": after["pages_requested"],
        "india_jobs": app["india_count"],
        "india_jobs_with_full_description": app["india_with_description"],
        "generated_at": utcnow(),
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("company_id", choices=sorted(TARGETS))
    args = parser.parse_args()

    companies = {c["id"]: c for c in load_config().get("companies", [])}
    row = verify(companies[args.company_id])
    print(
        f"{row['company']}: verdict={row['verdict']} production={row['production_jobs']} "
        f"stable_official={row['stable_current_jobs_checked']} missing={row['missing_count']} "
        f"browser_count={row['browser_witness_count']} "
        f"rows={row['official_after_rows_exhausted']}/{row['official_after_reported_rows']} "
        f"duplicate_rows={row['official_after_duplicate_rows']} "
        f"india_jd={row['india_jobs_with_full_description']}/{row['india_jobs']}",
        flush=True,
    )
    for job_id in row["missing_ids"][:30]:
        print(f"MISSING {job_id}", flush=True)

    out = Path(f"reports/phase3-{args.company_id}-exact-production.json")
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(row, indent=2, ensure_ascii=False), encoding="utf-8")
    return 0 if row["passed"] else 1


if __name__ == "__main__":
    sys.exit(main())
