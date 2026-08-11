from __future__ import annotations

import inspect
import threading
import traceback
from typing import Any

from job_fetcher.config import load_config
from job_fetcher.delivery import ensure_all_delivery_artifacts, ensure_delivery_artifact
from job_fetcher.relevance_service import analyze_relevance
from job_fetcher.health import verify_all
from job_fetcher.run_history import RunHistoryStore
from job_fetcher.service import fetch_companies_detailed
from job_fetcher.settings import apply_settings, load_settings
from job_fetcher.storage import ROOT, RunStore


class RunConflict(RuntimeError):
    def __init__(self, run_id: str, message: str = "Another scraping operation is already running"):
        super().__init__(message)
        self.run_id = run_id


class OperationManager:
    """Starts long-running fetch/verification work outside request threads.

    P0 deliberately serializes network-heavy operations. This is stricter than
    merely blocking duplicate Fetch-All clicks and prevents Fetch + Verify from
    driving the same providers/browser pool concurrently.
    """

    def __init__(self):
        self.store = RunStore()
        self.history = RunHistoryStore()
        self._lock = threading.RLock()
        self._threads: dict[str, threading.Thread] = {}

    def startup(self):
        # A Python process cannot resume a previous process's worker thread. Mark
        # any leftover active rows explicitly instead of displaying them forever.
        self.store.interrupt_stale_runs()
        # Schema-v1 AI artifacts were always incremental. Upgrade them in run order
        # so the first actual App-2 handoff becomes a baseline without refetching.
        ensure_all_delivery_artifacts()
        apply_settings(load_settings())

    def active(self) -> dict[str, Any] | None:
        row = self.store.active_run()
        return dict(row) if row else None

    def start_fetch(self, company_ids: list[str] | None = None) -> str:
        return self._start("fetch", company_ids)

    def start_verify(self, company_ids: list[str] | None = None) -> str:
        return self._start("verify", company_ids)

    def _start(self, run_type: str, company_ids: list[str] | None) -> str:
        with self._lock:
            active = self.store.active_run()
            if active:
                raise RunConflict(active["id"])

            all_companies = load_config()["companies"]
            enabled = [c for c in all_companies if c.get("enabled", True)]
            if company_ids:
                requested = set(company_ids)
                selected = [c for c in enabled if c.get("id") in requested]
                missing = requested - {c["id"] for c in selected}
                if missing:
                    raise ValueError("Unknown or disabled companies: " + ", ".join(sorted(missing)))
                scope = "company" if len(selected) == 1 else "selection"
            else:
                selected = enabled
                scope = "all"
            if not selected:
                raise ValueError("No enabled companies selected")

            settings = apply_settings(load_settings())
            run_id = self.store.create_run(
                run_type,
                total_companies=len(selected),
                scope=scope,
                settings=settings,
                targets=[c["id"] for c in selected],
            )
            thread = threading.Thread(
                target=self._execute,
                args=(run_id, run_type, selected, settings, scope),
                name=f"job-fetcher-{run_type}-{run_id}",
                daemon=True,
            )
            self._threads[run_id] = thread
            thread.start()
            return run_id

    def _execute(self, run_id: str, run_type: str, companies: list[dict], settings: dict[str, Any], scope: str):
        self.store.mark_running(run_id)
        try:
            if run_type == "fetch":
                before = self.history.capture_inventory([c["id"] for c in companies])

                def record_snapshot(row: dict[str, Any], jobs: list[Any]):
                    company_id = str(row.get("id") or "")
                    self.history.record_company_snapshot(
                        run_id,
                        before.get(company_id, {}),
                        row,
                        jobs,
                    )

                fetch_kwargs = {
                    "max_workers": settings["fetch_workers"],
                    "drop_threshold": settings["verification_drop_threshold"],
                    "on_result": lambda row: self.store.record_company_result(run_id, row),
                }
                # Keep compatibility with existing tests/custom wrappers that mock
                # the older callback surface while using exact snapshot membership
                # whenever the real fetch helper (or an updated wrapper) supports it.
                signature = inspect.signature(fetch_companies_detailed)
                supports_snapshot = "on_snapshot" in signature.parameters or any(
                    p.kind == inspect.Parameter.VAR_KEYWORD for p in signature.parameters.values()
                )
                if supports_snapshot:
                    fetch_kwargs["on_snapshot"] = record_snapshot
                fetch_companies_detailed(companies, **fetch_kwargs)

                # Relevance analysis is local/deterministic and incremental. Once it
                # succeeds we freeze the exact relevance state for this run and build
                # the immutable AI-input artifact. The first handoff is upgraded to a
                # baseline; subsequent handoffs contain only NEW/CHANGED relevant jobs.
                try:
                    analyze_relevance(recompute_all=False)
                    self.history.finalize_run(run_id)
                    ensure_delivery_artifact(run_id)
                except Exception as exc:
                    traceback.print_exc()
                    self.history.mark_artifact_error(run_id, f"{type(exc).__name__}: {exc}")
            elif run_type == "verify":
                previous_counts = None
                write_reports = scope == "all"
                if not write_reports:
                    previous_counts = {
                        c["id"]: count
                        for c in companies
                        if (count := self.store.latest_good_verification_count(c["id"])) is not None
                    }
                verify_all(
                    companies,
                    max_workers=settings["fetch_workers"],
                    output_dir=ROOT / "reports",
                    browser=settings["browser_fallback"],
                    drop_threshold=settings["verification_drop_threshold"],
                    validate_detail=settings["verify_sample_detail"],
                    detail_timeout=settings["detail_timeout"],
                    on_result=lambda row: self.store.record_company_result(run_id, row),
                    previous_counts=previous_counts,
                    write_reports=write_reports,
                )
            else:
                raise ValueError(f"Unknown run type: {run_type}")
            self.store.finish(run_id)
        except Exception as exc:
            detail = f"{type(exc).__name__}: {exc}"
            # Preserve a concise error on the run. Traceback remains available in
            # server stderr while avoiding raw stack traces in the normal UI.
            traceback.print_exc()
            self.store.finish(run_id, error=detail)
        finally:
            with self._lock:
                self._threads.pop(run_id, None)


_MANAGER: OperationManager | None = None
_MANAGER_LOCK = threading.Lock()


def get_manager() -> OperationManager:
    global _MANAGER
    with _MANAGER_LOCK:
        if _MANAGER is None:
            _MANAGER = OperationManager()
        return _MANAGER
