from __future__ import annotations

from copy import deepcopy

from job_fetcher.sources.eightfold_pcsx import EightfoldPcsxSource


class EightfoldPcsxExhaustiveSource(EightfoldPcsxSource):
    """Exact PCS inventory walker with row-vs-vacancy semantics.

    Eightfold's ``data.count`` is a result-row count, not necessarily a unique
    requisition count. Morgan Stanley's live board, for example, can repeat the
    same stable position ID in multiple result rows. Treating ``count`` as a unique
    ID count incorrectly reports a gap even after every offset has been read.

    This walker therefore proves two independent properties:
    1. every provider result-row offset was exhausted; and
    2. every stable vacancy identity observed across those rows is retained.

    Repeated rows for one stable position ID are merged (especially locations),
    and a second full pass is performed whenever duplicates are present so a live
    insertion/removal cannot hide a stable ID through offset shifting.
    """

    source_type = "eightfold_pcsx"

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
                # Prefer richer strings while otherwise preserving the later
                # provider value. This keeps titles/URLs current and avoids losing
                # a longer description when duplicate rows differ in richness.
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

        # The provider's count describes result rows. We must have consumed at
        # least that many rows before claiming the offset space was exhausted.
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
        first = self._walk_exact_once(origin, domain)
        by_id = dict(first["by_id"])
        passes = [first]

        # Duplicate result rows make unique_count < provider count by design. A
        # second exhaustive pass distinguishes that legitimate condition from a
        # live-board offset shift and unions any position IDs exposed by the later
        # snapshot. Extras are allowed by the trust contract; missing stable jobs
        # are not.
        if first["duplicate_row_occurrences"] > 0:
            second = self._walk_exact_once(origin, domain)
            passes.append(second)
            for position_id, row in second["by_id"].items():
                by_id[position_id] = self._merge_row(by_id.get(position_id), row)

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
            "passes": len(passes),
            "pages_requested": sum(item["pages_requested"] for item in passes),
        }
