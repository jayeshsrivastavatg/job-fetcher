from __future__ import annotations

import os
from copy import deepcopy

from job_fetcher.sources.eightfold_pcsx import EightfoldPcsxSource


class EightfoldPcsxExhaustiveSource(EightfoldPcsxSource):
    """Exact PCS inventory walker with row-vs-vacancy semantics.

    Eightfold's ``data.count`` is a result-row count, not necessarily a unique
    requisition count. Large live boards can mutate while offset pagination is in
    progress. A posting inserted/removed near the front shifts later offsets and
    can make one apparently complete pass omit a vacancy even though every offset
    was requested.

    Exact mode therefore performs several complete row-space walks and unions every
    stable position ID it sees. For very large/high-churn boards (currently
    Microsoft), each walk also overlaps adjacent provider pages by 50%. That makes
    an insertion/removal at an earlier offset unable to create a silent hole at a
    normal 10-row page boundary. Each individual walk must still reach the entire
    provider-reported row space or it fails closed.

    We intentionally do *not* wait for the board to stop changing: employers can
    add/remove jobs continuously. The independent exact verifier brackets production
    with separate official snapshots and requires every vacancy that remains present
    across the verification window to be in production output.

    This deliberately prefers a few extras from adjacent snapshots over silently
    missing a current vacancy, matching the product trust rule: extras are allowed;
    missing current jobs are not.
    """

    source_type = "eightfold_pcsx"

    def __init__(self):
        super().__init__()
        self._full_passes = max(
            2,
            int(os.getenv("JOB_FETCHER_EIGHTFOLD_FULL_PASSES", "3")),
        )
        self._page_stride = self.page_size

    @staticmethod
    def _merge_list(left, right):
        out = []
        for value in list(left or []) + list(right or []):
            if value not in out:
                out.append(value)
        return out

    @classmethod
    def _merge_row(cls, existing: dict | None, row: dict) -> dict:
        if existing is None:
            return deepcopy(row)
        merged = deepcopy(existing)
        for key, value in row.items():
            if key in {"locations", "standardizedLocations", "locationNames"}:
                merged[key] = cls._merge_list(merged.get(key), value)
                continue
            if value not in (None, "", [], {}):
                old = merged.get(key)
                if isinstance(old, str) and isinstance(value, str) and len(old) > len(value):
                    continue
                merged[key] = value
        return merged

    def _walk_exact_once(self, origin: str, domain: str) -> dict:
        by_id: dict[str, dict] = {}
        occurrence_count: dict[str, int] = {}
        seen_page_fingerprints: set[tuple[str, ...]] = set()
        start = 0
        current_count = 0
        pages = 0
        raw_rows = 0
        furthest_covered_offset = 0

        while pages == 0 or start < current_count:
            rows, count = self._page(origin, domain, start)
            pages += 1
            current_count = count

            if not rows:
                if start >= current_count:
                    break
                raise RuntimeError(
                    f"eightfold_pcsx_premature_empty:{raw_rows}/{current_count}@{start}"
                )

            ids = tuple(self._identity(row) or "" for row in rows)
            fingerprint = tuple(value for value in ids if value)
            if len(fingerprint) != len(rows):
                raise RuntimeError(f"eightfold_pcsx_page_without_ids:{start}")
            if fingerprint in seen_page_fingerprints:
                raise RuntimeError(f"eightfold_pcsx_repeated_page:{start}")
            seen_page_fingerprints.add(fingerprint)

            for row in rows:
                position_id = self._identity(row)
                if not position_id:
                    raise RuntimeError(f"eightfold_pcsx_row_without_id:{start}")
                occurrence_count[position_id] = occurrence_count.get(position_id, 0) + 1
                by_id[position_id] = self._merge_row(by_id.get(position_id), row)

            raw_rows += len(rows)
            furthest_covered_offset = max(furthest_covered_offset, start + len(rows))

            # Eightfold currently returns at most ten rows. On Microsoft we use a
            # five-row stride, intentionally re-reading half of every adjacent page.
            # For normal boards stride equals page size, preserving the cheaper walk.
            step = min(len(rows), max(1, int(self._page_stride)))
            start += step
            if pages > 10000:
                raise RuntimeError("eightfold_pcsx_pagination_guard")

        # With overlap, raw_rows is intentionally larger than provider count. What
        # matters for row-space exhaustion is that a returned page covered the final
        # provider offset; without overlap these quantities are equivalent.
        if furthest_covered_offset < current_count:
            raise RuntimeError(
                f"eightfold_pcsx_row_space_incomplete:{furthest_covered_offset}/{current_count}"
            )

        duplicate_ids = {
            position_id: occurrences
            for position_id, occurrences in occurrence_count.items()
            if occurrences > 1
        }
        return {
            "by_id": by_id,
            "reported_count": current_count,
            "raw_rows": raw_rows,
            "unique_count": len(by_id),
            "duplicate_ids": duplicate_ids,
            "duplicate_row_occurrences": raw_rows - len(by_id),
            "pages_requested": pages,
            "furthest_covered_offset": furthest_covered_offset,
            "page_stride": self._page_stride,
        }

    def enumerate_rows(self, company: dict) -> tuple[dict[str, dict], dict]:
        origin, domain, _entry = self.contract(company)

        # Microsoft is both very large and highly active. Its result ordering moves
        # enough during a full walk that non-overlapping offsets were empirically
        # shown to omit stable vacancies. Half-page overlap removes that boundary
        # failure mode while the multi-pass union handles wider live-board mutation.
        self._page_stride = max(1, self.page_size // 2) if company.get("id") == "microsoft" else self.page_size

        by_id: dict[str, dict] = {}
        passes: list[dict] = []
        pass_evidence: list[dict] = []

        for pass_number in range(1, self._full_passes + 1):
            snapshot = self._walk_exact_once(origin, domain)
            passes.append(snapshot)

            prior_ids = set(by_id)
            current_ids = set(snapshot["by_id"])
            new_ids = current_ids - prior_ids
            prior_ids_not_seen_this_pass = prior_ids - current_ids

            for position_id, row in snapshot["by_id"].items():
                by_id[position_id] = self._merge_row(by_id.get(position_id), row)

            pass_evidence.append({
                "pass": pass_number,
                "reported_count": snapshot["reported_count"],
                "provider_row_count": snapshot["raw_rows"],
                "furthest_covered_offset": snapshot["furthest_covered_offset"],
                "page_stride": snapshot["page_stride"],
                "unique_in_pass": snapshot["unique_count"],
                "new_ids_added": len(new_ids),
                "prior_ids_not_seen_this_pass": len(prior_ids_not_seen_this_pass),
                "duplicate_row_occurrences": snapshot["duplicate_row_occurrences"],
            })

        final = passes[-1]
        board_mutated = any(
            evidence["new_ids_added"] or evidence["prior_ids_not_seen_this_pass"]
            for evidence in pass_evidence[1:]
        )
        return by_id, {
            "origin": origin,
            "domain": domain,
            "reported_count": final["reported_count"],
            "provider_row_count": final["raw_rows"],
            "furthest_covered_offset": final["furthest_covered_offset"],
            "page_stride": final["page_stride"],
            "unique_count": len(by_id),
            "duplicate_row_occurrences": final["duplicate_row_occurrences"],
            "duplicate_id_count": len(final["duplicate_ids"]),
            "duplicate_ids_sample": dict(list(final["duplicate_ids"].items())[:25]),
            "pagination_exhausted": final["furthest_covered_offset"] >= final["reported_count"],
            "board_mutated_during_fetch": board_mutated,
            "passes": len(passes),
            "pages_requested": sum(item["pages_requested"] for item in passes),
            "pass_evidence": pass_evidence,
        }
