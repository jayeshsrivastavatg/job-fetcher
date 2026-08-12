from __future__ import annotations

import os
from copy import deepcopy

from job_fetcher.sources.eightfold_pcsx import EightfoldPcsxSource


class EightfoldPcsxExhaustiveSource(EightfoldPcsxSource):
    """Exact PCS inventory walker with row-vs-vacancy semantics.

    Eightfold's ``data.count`` is a result-row count, not necessarily a unique
    requisition count. Large live boards can also mutate while offset pagination is
    in progress. A posting inserted/removed near the front shifts later offsets and
    can make one complete-looking pass omit a vacancy even though every offset was
    requested.

    We therefore prove two independent properties:
    1. every provider result-row offset is consumed on every pass; and
    2. the union of stable vacancy IDs converges across repeated *full* passes.

    The source requires consecutive full passes that add no previously unseen
    vacancy IDs before publishing the snapshot. This deliberately prefers extra
    recently-closed IDs over silently missing a still-current vacancy, matching the
    product trust rule: extras are acceptable; missing jobs are not.
    """

    source_type = "eightfold_pcsx"

    def __init__(self):
        super().__init__()
        self._max_convergence_passes = max(
            3,
            int(os.getenv("JOB_FETCHER_EIGHTFOLD_MAX_CONVERGENCE_PASSES", "6")),
        )
        self._stable_passes_required = max(
            1,
            int(os.getenv("JOB_FETCHER_EIGHTFOLD_STABLE_PASSES", "2")),
        )

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
            start += len(rows)
            if pages > 10000:
                raise RuntimeError("eightfold_pcsx_pagination_guard")

        if raw_rows < current_count:
            raise RuntimeError(f"eightfold_pcsx_row_space_incomplete:{raw_rows}/{current_count}")

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
        }

    def enumerate_rows(self, company: dict) -> tuple[dict[str, dict], dict]:
        origin, domain, _entry = self.contract(company)
        by_id: dict[str, dict] = {}
        passes: list[dict] = []
        pass_evidence: list[dict] = []
        stable_streak = 0
        converged = False

        for pass_number in range(1, self._max_convergence_passes + 1):
            snapshot = self._walk_exact_once(origin, domain)
            passes.append(snapshot)

            prior_ids = set(by_id)
            current_ids = set(snapshot["by_id"])
            new_ids = current_ids - prior_ids
            prior_ids_not_seen_this_pass = prior_ids - current_ids

            for position_id, row in snapshot["by_id"].items():
                by_id[position_id] = self._merge_row(by_id.get(position_id), row)

            # Pass one creates the baseline. Each later full pass with no unseen ID
            # is one convergence observation. Requiring two observations avoids
            # accepting one lucky-looking offset walk on a busy board.
            if pass_number > 1 and not new_ids:
                stable_streak += 1
            else:
                stable_streak = 0

            pass_evidence.append({
                "pass": pass_number,
                "reported_count": snapshot["reported_count"],
                "provider_row_count": snapshot["raw_rows"],
                "unique_in_pass": snapshot["unique_count"],
                "new_ids_added": len(new_ids),
                "prior_ids_not_seen_this_pass": len(prior_ids_not_seen_this_pass),
                "duplicate_row_occurrences": snapshot["duplicate_row_occurrences"],
            })

            if stable_streak >= self._stable_passes_required:
                converged = True
                break

        if not converged:
            last = passes[-1] if passes else {}
            raise RuntimeError(
                "eightfold_pcsx_inventory_did_not_converge:"
                f"passes={len(passes)} unique={len(by_id)} "
                f"last_reported={last.get('reported_count', 0)}"
            )

        final = passes[-1]
        return by_id, {
            "origin": origin,
            "domain": domain,
            "reported_count": final["reported_count"],
            "provider_row_count": final["raw_rows"],
            "unique_count": len(by_id),
            "duplicate_row_occurrences": final["duplicate_row_occurrences"],
            "duplicate_id_count": len(final["duplicate_ids"]),
            "duplicate_ids_sample": dict(list(final["duplicate_ids"].items())[:25]),
            "pagination_exhausted": True,
            "converged": True,
            "stable_passes_required": self._stable_passes_required,
            "passes": len(passes),
            "pages_requested": sum(item["pages_requested"] for item in passes),
            "pass_evidence": pass_evidence,
        }
