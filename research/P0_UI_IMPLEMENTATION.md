# P0 Browser UI — Implementation Notes

## Scope

The P0 UI is an operations console for the existing 100-company job fetcher. It intentionally excludes AI matching, resume tools, application tracking, schedulers, notifications, salary analytics, cloud accounts, and multi-user behavior.

## Top-level pages

1. Dashboard
2. Companies
3. Jobs
4. Health
5. Runs
6. Settings

## Operational rules

- UI calls Python service methods directly; it never shells out to CLI commands.
- One network-heavy operation (fetch or verify, global or single-company) can run at a time.
- Accepted work receives a persistent Run ID, so browser refresh cannot duplicate execution.
- Browser/tab closure does not stop a run while the Python process remains alive.
- A server restart marks an unfinished run `interrupted` rather than pretending it is still running.
- Disable preserves jobs and history.
- No run-cancel button exists in P0 because safe cancellation semantics are not implemented yet.

## Job lifecycle rules

- Jobs now have an `active` flag in addition to first/last seen timestamps.
- Complete successful snapshots can deactivate jobs that disappeared.
- Failed, zero-job-anomalous, and suspicious large-drop fetches never deactivate the previous snapshot.
- Known legitimate empty boards can opt in with `source.allow_zero_jobs: true`.

## Persistence

SQLite now stores:

- normalized jobs
- fetch / verification runs
- per-company run results
- error categories, adapters, job counts, browser usage, and diagnostic payloads

Existing databases are migrated in place.
