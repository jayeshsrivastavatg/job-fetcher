from __future__ import annotations

from typing import Any

from job_fetcher.storage import RelevanceStore as BaseRelevanceStore, _connect


class RelevanceStore(BaseRelevanceStore):
    """UI-oriented relevance queries with lifecycle/date filters.

    The base relevance store owns persistence and analysis. This small extension
    keeps web-only filtering concerns separate while reusing the same SQLite data.
    """

    def search(
        self, *, query: str = "", company_id: str = "", status: str = "", family: str = "",
        change_type: str = "", relevant_only: bool = False, min_score: float | None = None,
        posted_since: str = "", first_seen_since: str = "",
        page: int = 1, page_size: int = 50,
    ) -> dict[str, Any]:
        clauses = ["j.active=1", "a.company_id IS NOT NULL"]
        args: list[Any] = []
        if query:
            like = f"%{query.strip()}%"
            clauses.append("(j.title LIKE ? OR j.description LIKE ? OR j.company_name LIKE ? OR j.location LIKE ?)")
            args.extend([like, like, like, like])
        if company_id:
            clauses.append("j.company_id=?"); args.append(company_id)
        if status:
            clauses.append("a.relevance_status=?"); args.append(status)
        if family:
            clauses.append("a.role_family=?"); args.append(family)
        if change_type:
            clauses.append("a.change_type=?"); args.append(change_type)
        if relevant_only:
            clauses.append("a.is_relevant=1")
        if min_score is not None:
            clauses.append("a.relevance_score>=?"); args.append(float(min_score))
        if posted_since:
            clauses.append("date(j.posted_at) >= date(?)"); args.append(posted_since)
        if first_seen_since:
            clauses.append("datetime(j.first_seen_at) >= datetime(?)"); args.append(first_seen_since)

        where = " WHERE " + " AND ".join(clauses)
        with _connect() as conn:
            total = int(conn.execute(
                f'''SELECT COUNT(*) FROM jobs j JOIN job_relevance_analysis a
                    ON a.company_id=j.company_id AND a.external_id=j.external_id {where}''', args
            ).fetchone()[0])
            page = max(1, int(page))
            page_size = max(1, min(200, int(page_size)))
            offset = (page - 1) * page_size
            rows = conn.execute(
                f'''SELECT j.*,a.change_type,a.role_family,a.role_label,a.normalized_location,a.min_experience,
                           a.max_experience,a.relevance_score,a.relevance_status,a.is_relevant,a.filter_reason,
                           a.role_score,a.experience_score,a.primary_skill_score,a.supporting_score,
                           a.matched_primary_json,a.matched_supporting_json,a.score_breakdown_json,
                           a.duplicate_of_company_id,a.duplicate_of_external_id,a.analyzed_at
                    FROM jobs j JOIN job_relevance_analysis a
                    ON a.company_id=j.company_id AND a.external_id=j.external_id {where}
                    ORDER BY a.is_relevant DESC,a.relevance_score DESC,j.company_name,j.title LIMIT ? OFFSET ?''',
                [*args, page_size, offset],
            ).fetchall()
        return {
            "rows": rows, "total": total, "page": page, "page_size": page_size,
            "pages": max(1, (total + page_size - 1) // page_size),
        }
