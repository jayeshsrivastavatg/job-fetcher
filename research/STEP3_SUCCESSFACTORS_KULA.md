# Step 3 — SuccessFactors/jobs2web + Kula

## PayU — SuccessFactors/jobs2web

`SuccessFactorsSource` consumes the public candidate-facing career listing rather than authenticated SAP Recruiting OData APIs.

Features:
- detects branded SuccessFactors career sites from public-page markers
- extracts stable numeric job IDs from `/job/.../<id>/` URLs
- captures title, location, posting date, and requisition ID where available
- crawls server-rendered pagination, including jobs2web offset pages such as `/25/`, `/50/`, etc.
- stops when the advertised result total is reached
- bounded Playwright fallback when static listing extraction fails
- honors `JOB_FETCHER_DISABLE_BROWSER=1`

Configured company: PayU.

## Kula

`KulaSource` consumes public `careers.kula.ai/<tenant>` boards.

Features:
- tenant inference from Kula URL or explicit config
- supports `/<tenant>/<jobId>`, `/<tenant>/<jobId>/apply`, and `/<tenant>/jobs/<jobId>`
- normalizes all job links to `https://careers.kula.ai/<tenant>/<jobId>`
- extracts listing title, location, employment type, work type, and department where present
- also checks JSON-LD and embedded JSON before card parsing
- bounded Playwright fallback for client-only rendering
- honors `JOB_FETCHER_DISABLE_BROWSER=1`

Configured companies: Cashfree Payments and CleverTap.
