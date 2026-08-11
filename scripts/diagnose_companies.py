#!/usr/bin/env python3
"""Hard-timeout diagnostic runner for all configured career sources.

Unlike the normal fetch command, this is intentionally fail-fast and is meant to
answer: can the configured source be reached/parsing started, and if not why?
It does not write jobs to SQLite.
"""
import argparse
import json
import os
import signal
from multiprocessing import get_context
from pathlib import Path

from job_fetcher.config import load_config
from job_fetcher.service import classify_error
from job_fetcher.sources.factory import build_source


def _alarm_handler(_sig, _frame):
    raise TimeoutError("diagnostic hard timeout")


def probe(args):
    company, timeout_s = args
    os.environ.setdefault("JOB_FETCHER_RETRIES", "0")
    os.environ.setdefault("JOB_FETCHER_HTTP_TIMEOUT", str(min(timeout_s, 3)))
    os.environ.setdefault("JOB_FETCHER_DISABLE_BROWSER", "1")
    signal.signal(signal.SIGALRM, _alarm_handler)
    signal.alarm(timeout_s)
    try:
        jobs = build_source(company).fetch(company)
        return {
            "rank": company.get("rank"), "id": company["id"], "name": company["name"],
            "career_url": company["career_url"], "status": "success", "jobs_detected": len(jobs),
            "category": None, "error": None,
        }
    except Exception as e:
        category = "diagnostic_hard_timeout" if isinstance(e, TimeoutError) else classify_error(e)
        return {
            "rank": company.get("rank"), "id": company["id"], "name": company["name"],
            "career_url": company["career_url"], "status": "failed", "jobs_detected": 0,
            "category": category, "error": f"{type(e).__name__}: {e}",
        }
    finally:
        signal.alarm(0)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--workers", type=int, default=16)
    ap.add_argument("--timeout", type=int, default=4)
    ap.add_argument("--output", default="logs/diagnostic-report.json")
    ns = ap.parse_args()
    companies = [c for c in load_config()["companies"] if c.get("enabled", True)]
    ctx = get_context("fork")
    with ctx.Pool(processes=max(1, ns.workers)) as pool:
        rows = list(pool.imap_unordered(probe, [(c, ns.timeout) for c in companies], chunksize=1))
    rows.sort(key=lambda r: (r.get("rank") or 10**9, r["id"]))
    out = {
        "mode": "http_and_native_ats_only_browser_disabled",
        "timeout_seconds_per_company": ns.timeout,
        "companies": rows,
    }
    p = Path(ns.output); p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(out, indent=2, ensure_ascii=False), encoding="utf-8")
    from collections import Counter
    counts = Counter(r["category"] or "success" for r in rows)
    print(f"companies={len(rows)} success={sum(r['status']=='success' for r in rows)} failed={sum(r['status']=='failed' for r in rows)}")
    print(json.dumps(dict(counts), indent=2))
    print(f"Report: {p}")

if __name__ == "__main__":
    main()
