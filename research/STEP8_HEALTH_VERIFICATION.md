# Step 8 — Company health verification

Added a first-class `verify-all` command. It verifies every enabled company
without changing the jobs database and produces JSON + CSV health reports.

Checks:
- adapter execution success/failure
- zero jobs (failure even when HTTP returned 200)
- title + valid job URL coverage
- browser/XHR fallback usage
- one sample detail URL reachability (optional)
- sudden job-count drops versus the previous report
- DNS-environment-wide outage detection

Default health states:
- `healthy`
- `healthy_with_fallback`
- `suspicious`
- `failed`

The verifier maintains `reports/company_health_baseline.json` with the last
successful non-zero count for each company. Network-wide failures and zero-job
failures do not erase that baseline. Prior run reports are archived in
`reports/history/`.
