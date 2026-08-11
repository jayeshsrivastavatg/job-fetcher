from __future__ import annotations

import csv
import json
from pathlib import Path
from typing import Any

from job_fetcher.matching import dedup_group_key, description_similarity, job_source_hash, score_job
from job_fetcher.profile import load_profile
from job_fetcher.storage import RelevanceStore, JobStore


def _rowdict(row) -> dict[str, Any]:
    return dict(row) if not isinstance(row, dict) else dict(row)


def _change_type(row: dict[str, Any], previous: dict[str, Any] | None, source_hash: str) -> str:
    fetch_change = str(row.get("last_change_type") or "").lower()
    if previous is None:
        if fetch_change in {"new", "changed"}:
            return fetch_change
        return "baseline"
    if previous.get("source_hash") != source_hash:
        return "changed"
    if fetch_change in {"new", "changed", "unchanged", "baseline"}:
        return fetch_change
    return "unchanged"


def analyze_relevance(*, recompute_all: bool = False, profile_path: str | Path | None = None) -> dict[str, Any]:
    """Score active jobs using deterministic role, experience and skill rules."""
    profile = load_profile(profile_path)
    jobs = JobStore()
    relevance = RelevanceStore()
    analyzed = skipped = 0
    changes = {"new": 0, "changed": 0, "baseline": 0, "unchanged": 0}
    try:
        rows = [_rowdict(r) for r in jobs.all(active_only=True)]
        for row in rows:
            source_hash = job_source_hash(row)
            previous = relevance.get(row["company_id"], row["external_id"])
            change = _change_type(row, previous, source_hash)
            if not recompute_all and previous and previous.get("source_hash") == source_hash:
                relevance.update_change_type(row["company_id"], row["external_id"], change)
                skipped += 1
                changes[change] = changes.get(change, 0) + 1
                continue
            result = score_job(row, profile).to_dict()
            relevance.upsert(row["company_id"], row["external_id"], source_hash, change, result)
            analyzed += 1
            changes[change] = changes.get(change, 0) + 1
    finally:
        jobs.close()

    duplicate_count = apply_near_dedup(profile=profile)
    stats = relevance.stats()
    return {
        **stats,
        "total_analyzed": stats.get("analyzed", 0),
        "analyzed_this_run": analyzed,
        "skipped_unchanged": skipped,
        "changes": changes,
        "duplicates_marked": duplicate_count,
    }


def apply_near_dedup(*, profile: dict[str, Any] | None = None) -> int:
    profile = profile or load_profile()
    threshold = float((profile.get("scoring") or {}).get("nearDuplicateSimilarity", 0.90))
    store = RelevanceStore()
    store.reset_duplicate_markers(profile.get("scoring") or {})
    rows = [_rowdict(r) for r in store.rows_for_dedup()]
    groups: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        key = dedup_group_key(row["company_id"], row.get("title"), row.get("location"))
        groups.setdefault(key, []).append(row)
    marked = 0
    for group in groups.values():
        if len(group) < 2:
            continue
        group.sort(key=lambda r: (-float(r.get("relevance_score") or 0), str(r.get("first_seen_at") or "")))
        canonicals: list[dict[str, Any]] = []
        for row in group:
            duplicate_of = None
            for canonical in canonicals:
                if description_similarity(row.get("description"), canonical.get("description")) >= threshold:
                    duplicate_of = canonical
                    break
            if duplicate_of:
                store.mark_duplicate(
                    row["company_id"], row["external_id"],
                    duplicate_of["company_id"], duplicate_of["external_id"],
                )
                marked += 1
            else:
                canonicals.append(row)
    return marked


def relevance_stats() -> dict[str, Any]:
    return RelevanceStore().stats()


def _relevance_rows(*, relevant_only: bool = False, min_score: float | None = None) -> list[dict[str, Any]]:
    store = RelevanceStore()
    result = store.search(relevant_only=relevant_only, min_score=min_score, page=1, page_size=200)
    rows = [_rowdict(r) for r in result["rows"]]
    for page in range(2, result["pages"] + 1):
        more = store.search(relevant_only=relevant_only, min_score=min_score, page=page, page_size=200)
        rows.extend(_rowdict(r) for r in more["rows"])
    return rows


def export_relevance(
    output: str | Path,
    *,
    format: str | None = None,
    relevant_only: bool = False,
    min_score: float | None = None,
) -> Path:
    path = Path(output)
    fmt = (format or path.suffix.lstrip(".") or "json").lower()
    rows = _relevance_rows(relevant_only=relevant_only, min_score=min_score)
    for row in rows:
        for src, dest in (("matched_primary_json", "matched_primary"), ("matched_supporting_json", "matched_supporting")):
            raw = row.get(src)
            try:
                items = json.loads(raw or "[]") if isinstance(raw, str) else (raw or [])
            except Exception:
                items = []
            row[dest] = ", ".join(str(x.get("name")) for x in items if isinstance(x, dict) and x.get("name"))
            row.pop(src, None)
        row.pop("score_breakdown_json", None)
    path.parent.mkdir(parents=True, exist_ok=True)
    fields = [
        "company_name", "title", "location", "normalized_location", "posted_at", "first_seen_at",
        "last_seen_at", "active", "change_type", "role_label", "min_experience", "max_experience",
        "role_score", "experience_score", "primary_skill_score", "supporting_score", "relevance_score",
        "relevance_status", "is_relevant", "matched_primary", "matched_supporting", "filter_reason",
        "job_url", "company_id", "external_id", "description",
    ]
    if fmt == "json":
        path.write_text(json.dumps(rows, indent=2, ensure_ascii=False), encoding="utf-8")
    elif fmt == "csv":
        with path.open("w", newline="", encoding="utf-8-sig") as f:
            writer = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
            writer.writeheader()
            writer.writerows(rows)
    else:
        raise ValueError("Supported relevance export formats: json, csv")
    return path
