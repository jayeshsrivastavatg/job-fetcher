# Job Fetcher — 100 India SDE-2 Targets

A local-first Python job collector pre-populated with the 100 companies from `Top_100_SDE2_Companies_India.xlsx`.

The project is designed so that **companies are configuration, not code**: you can add, update, enable, disable, or remove a company without changing the scraper implementation.

## Browser UI (P0 operations console)

The project now includes a local browser UI for operating the scraper, inspecting jobs, and debugging failures. It binds to `127.0.0.1` by default and does not require an account.

After normal setup, start it with:

```bash
python -m job_fetcher.web
```

Then open:

```text
http://localhost:8000
```

The P0 UI includes:

- **Dashboard** — company/job counts, current health, latest runs, issues requiring attention.
- **Companies** — search/filter all configured companies; add, edit, enable/disable, fetch or verify one company.
- **Company details** — source configuration, health diagnostics, XHR endpoints, history, and current jobs.
- **Jobs** — searchable/filterable active and inactive job records plus full job details and original source links.
- **Relevant Jobs** — deterministic role/stack/experience scoring, NEW/CHANGED state, role-family classification, filters, and export.
- **Health** — latest health result per enabled company, one-click Verify All, failure categories, count-drop diagnostics.
- **Runs** — persistent fetch/verification history with live progress and per-company results.
- **Settings** — workers, HTTP timeout/retries, browser fallback/concurrency, verification thresholds.

Long-running work executes in background threads managed by the service layer; the web UI does **not** shell out to CLI commands. Refreshing or closing the browser does not start a duplicate run. If the Python server itself stops mid-run, that run is marked `interrupted` on the next startup. P0 intentionally allows only one network-heavy fetch/verification operation at a time to prevent duplicate load against career sites and Chromium.

### Job lifecycle safety

The UI fetch path treats each successful company fetch as a snapshot. Jobs retain `first_seen_at`/`last_seen_at` and an `active` flag. Missing jobs are deactivated only when the snapshot is considered complete. A network/parser failure, zero-job anomaly, or suspicious >configured-threshold count collapse **does not** deactivate previously valid jobs. This prevents a broken scraper from making hundreds of jobs disappear from the database.

Run history and per-company run results are stored in the same local SQLite database (`data/jobs.db`). Existing Step 8 databases are migrated in place; historical job rows are preserved.

## What is in the project

- `config/companies.yaml` — 100 researched companies and their official careers/job entry URLs.
- `src/job_fetcher/` — fetch pipeline, normalized job model, SQLite storage, and CLI.
- `src/job_fetcher/sources/` — ATS adapters and generic fallbacks.
- `research/` — career-page/provider compatibility metadata.
- `logs/` — diagnostics and environment reports.
- `tests/` — deterministic tests for config management and scraper extraction.


## Deterministic relevance pipeline

This repository stops at **job discovery and JD collection**. It does not perform AI matching, ATS scoring, resume tailoring, or resume generation. Those concerns belong in a separate downstream application.

After jobs are fetched, this project can reduce the inventory to openings that match the configured search preferences. The default profile targets Java backend, Java+React full stack, Node.js backend, and Node.js/React/TypeScript full stack roles. Experience matching is intentionally broad: 2–5 year requirements and ranges containing ~5 years score strongly; 6+ years is treated as a stretch rather than an automatic rejection; clearly 8+ year, Staff/Principal/management/mobile/QA-only roles are filtered by default.

The deterministic relevance score is transparent and capped at 100:

```text
Role / stack relevance       40
Experience compatibility     20
Primary skill match          25
Supporting engineering fit   15
```

The runtime preference profile lives at `data/profile.json` and is intentionally git-ignored. `config/profile.example.json` is the versionable template.

Relevance analysis is incremental. Fetching persists a content hash and `new / changed / unchanged` state for each job; unchanged JDs are not rescored. Near-duplicate postings are retained in the database but marked as duplicates so they do not clutter the relevant-job view.

For normal daily CLI usage, run:

```bash
python -m job_fetcher.cli scan --workers 4
```

That performs Fetch → NEW/CHANGED detection → deterministic relevance scoring → near-deduplication → export. By default it writes:

```text
reports/daily/daily_fetch.json
reports/daily/relevant_jobs.csv
reports/daily/relevant_jobs.json
```

The lower-level commands are available for debugging or rerunning one stage:

```bash
python -m job_fetcher.cli validate-profile
python -m job_fetcher.cli analyze-relevance
python -m job_fetcher.cli relevance-stats
python -m job_fetcher.cli export-relevance --output reports/relevant_jobs.csv
python -m job_fetcher.cli export-relevance --relevant-only --output reports/relevant_only.json
```

The JSON export includes the full stored JD (`description`) so a separate application can consume the normalized job data later. A successful browser Fetch run automatically triggers incremental deterministic relevance analysis.

## Company lifecycle: add / disable / enable / update

### Add a new company

For most sites, start with `auto`. The ID is generated from the company name and the next rank is assigned automatically:

```bash
python -m job_fetcher.cli add-company \
  --name "Example Company" \
  --career-url "https://example.com/careers"
```

You can provide an explicit ID/rank if you want:

```bash
python -m job_fetcher.cli add-company \
  --id example_company \
  --rank 101 \
  --name "Example Company" \
  --career-url "https://example.com/jobs"
```

### Disable a company without deleting history

```bash
python -m job_fetcher.cli disable-company example_company
```

Disabled companies remain in `companies.yaml` and historical jobs remain in SQLite, but normal `fetch` runs skip them.

### Re-enable it

```bash
python -m job_fetcher.cli enable-company example_company
```

### Update URL/source/rank/name

```bash
python -m job_fetcher.cli update-company example_company \
  --career-url "https://example.com/new-careers"
```

You can also use `--enable` or `--disable` on `update-company`.

### Remove from config

```bash
python -m job_fetcher.cli remove-company example_company
```

This removes the config row only; historical SQLite rows are intentionally retained.

## Advanced source configuration without editing Python

`auto` should be the first choice. If a website needs a precise adapter, you can configure it from the CLI.

### Greenhouse

```bash
python -m job_fetcher.cli add-company \
  --name "Acme" --career-url "https://acme.com/careers" \
  --source greenhouse --board-token acme
```

### Lever

```bash
python -m job_fetcher.cli add-company \
  --name "Acme" --career-url "https://jobs.lever.co/acme" \
  --source lever --site acme
```

### Ashby

```bash
python -m job_fetcher.cli add-company \
  --name "Acme" --career-url "https://jobs.ashbyhq.com/acme" \
  --source ashby --board-name acme
```

### SmartRecruiters

```bash
python -m job_fetcher.cli add-company \
  --name "Acme" --career-url "https://careers.smartrecruiters.com/Acme" \
  --source smartrecruiters --company-identifier Acme
```

### Workday

```bash
python -m job_fetcher.cli add-company \
  --name "Acme" --career-url "https://acme.wd1.myworkdayjobs.com/External" \
  --source workday --host acme.wd1.myworkdayjobs.com \
  --tenant acme --workday-site External
```


### SAP SuccessFactors / jobs2web

For public SuccessFactors Career Site / jobs2web listings:

```bash
python -m job_fetcher.cli add-company \
  --name "Acme" --career-url "https://careers.acme.com/Acme/go/_/12345/" \
  --source successfactors \
  --entry-url "https://careers.acme.com/Acme/go/_/12345/"
```

The adapter follows server-rendered listing pagination and does not require SAP Recruiting OData credentials.

### Kula

For Kula public career boards:

```bash
python -m job_fetcher.cli add-company \
  --name "Acme" --career-url "https://careers.kula.ai/acme" \
  --source kula --tenant acme \
  --entry-url "https://careers.kula.ai/acme"
```

The adapter supports both `/<tenant>/<jobId>` and `/<tenant>/jobs/<jobId>` job-detail URL forms and uses the public Kula board as its source.

### Custom static HTML / custom API / selector-driven Playwright

For unusual sites, `--source-config-json` lets you supply selectors, field mappings, headers, API bodies, etc. without modifying Python.

Example static HTML configuration:

```bash
python -m job_fetcher.cli add-company \
  --name "Acme" --career-url "https://acme.com/jobs" \
  --source custom_html \
  --source-config-json '{"selectors":{"card":".job","title":".title","location":".location","link":"a"}}'
```

Example API configuration:

```bash
python -m job_fetcher.cli add-company \
  --name "Acme" --career-url "https://acme.com/jobs" \
  --source custom_api \
  --source-config-json '{"endpoint":"https://acme.com/api/jobs","jobs_path":"data.jobs","field_mapping":{"external_id":"id","title":"title","location":"location","job_url":"url"}}'
```

## Discovery pipeline

```text
official careers/job entry URL
  -> native/provider detection (Greenhouse / Lever / Ashby / SmartRecruiters / Workday / Oracle / Eightfold / SuccessFactors / Kula / Avature / Atlassian / Phenom / Trakstar / Goldman)
  -> ATS URL discovery from anchors and embedded JavaScript
  -> schema.org JobPosting JSON-LD
  -> embedded Next.js/application-json state
  -> generic job-card / job-link extraction
  -> conservative same-host pagination
  -> high-confidence "all jobs/search jobs" discovery
  -> Playwright fallback (bounded browser concurrency)
       -> XHR/fetch JSON capture
       -> iframe DOM extraction
       -> lazy-scroll/load-more attempts
       -> bounded Next/numbered-page traversal for SPA listings
       -> records public JSON/XHR endpoints that yielded job records
  -> normalized Job
  -> SQLite upsert
```

### Why this is more resilient than one universal scraper

Career sites generally fall into a few families:

1. **Public ATS with stable APIs** — native adapter is best.
2. **Server-rendered career pages** — JSON-LD/static link extraction works well.
3. **SPA/dynamic ATS** — browser network capture is useful.
4. **Unusual proprietary portals** — use `custom_api`, `custom_html`, or selector-driven Playwright config.
5. **CAPTCHA/auth/private endpoints** — no generic scraper can safely guarantee extraction; the project reports the failure category instead of silently returning an empty result.

No implementation can literally handle every future website automatically. The intended design is **automatic first, configurable fallback second, provider adapter third**.

## Setup

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
pip install -e .
playwright install chromium
```

## Normal usage

```bash
python -m job_fetcher.cli validate-config
python -m job_fetcher.cli companies
python -m job_fetcher.cli companies --enabled-only
python -m job_fetcher.cli probe-company uber
python -m job_fetcher.cli fetch --workers 6
python -m job_fetcher.cli fetch --company uber
python -m job_fetcher.cli stats
python -m job_fetcher.cli export --output data/jobs.json
```

`probe-company` tests one company without modifying SQLite. This is the recommended command immediately after adding or updating a company.

For browser/XHR-backed sites, successful probe JSON also includes `discovered_endpoints`: public response URLs whose JSON produced normalized job records. This is useful for Swiggy, Urban Company, Snowflake, Rippling, and other dynamic sites because the first local run can reveal the stable public job endpoint without manual DevTools inspection.

## Reliability controls

- `JOB_FETCHER_RETRIES` — HTTP retries, default `3`.
- `JOB_FETCHER_HTTP_TIMEOUT` — request timeout in seconds, default `30`.
- `JOB_FETCHER_DISABLE_BROWSER=1` — disable Playwright fallback for CI/fast diagnostics.
- `JOB_FETCHER_BROWSER_CONCURRENCY` — maximum simultaneous Chromium fallbacks, default `2`.
- `JOB_FETCHER_BROWSER_TIMEOUT_MS` — browser navigation timeout, default `60000`.
- `JOB_FETCHER_BROWSER_LOAD_MORE_CLICKS` — load-more click cap, default `10`.
- `JOB_FETCHER_BROWSER_MAX_PAGES` — browser Next-page traversal cap, default `6`.
- `JOB_FETCHER_BROWSER_MAX_SCROLLS` — lazy-scroll cap per browser page, default `10`.
- `JOB_FETCHER_MAX_STATIC_PAGES` — conservative server-rendered pagination cap, default `12`.

HTTP retries/backoff cover 429 and transient 5xx responses. HTTP company fetching can be concurrent while Playwright concurrency is independently bounded to prevent browser explosions.

## Current 100-company status

The configuration contains **100 companies**. **96 are enabled**. OYO, MPL, Flipkart, and LinkedIn are retained but disabled by default. OYO/MPL do not currently have a verified first-party public jobs portal in this research set; Flipkart is disabled because its published careers-site terms restrict automated extraction:

- OYO's official website routes “Teams / Careers” to LinkedIn rather than a first-party public jobs portal.
- MPL's official public site does not expose a verifiable careers/jobs portal.
- Flipkart remains configured for manual/approved-feed handling rather than automated scraping.
- LinkedIn remains configured for manual/approved-feed handling because its User Agreement prohibits automated scraping without permission; approved partner/crawling access can be wired later.

This is deliberate: the fetcher does not silently substitute a third-party aggregator or scrape a consumer homepage and pretend it is a jobs source.

## Testing and diagnostics

```bash
PYTHONPATH=src pytest -q
python -m job_fetcher.cli validate-config
python scripts/network_preflight.py
```

Fast HTTP/native diagnostic (browser disabled):

```bash
PYTHONPATH=src JOB_FETCHER_DISABLE_BROWSER=1 JOB_FETCHER_RETRIES=0 \
  python scripts/diagnose_companies.py --workers 16 --timeout 5
```

### Important note about the build environment used to prepare this project

The execution sandbox used to package this repo could not resolve public DNS from Python/container processes. Therefore the final report distinguishes **environment-blocked** from **site/parser failure**. Career URLs were independently researched/verified with web access, while end-to-end Python network execution could not be truthfully certified inside this sandbox.

## Storage model

Jobs are normalized to:

- company id/name
- source type
- stable external id
- title
- location
- description
- job URL
- posting date
- raw provider payload

SQLite lives at `data/jobs.db`. Re-running fetches updates `last_seen_at` while preserving `first_seen_at`.

## Oracle Candidate Experience source

For Oracle Fusion Candidate Experience career sites, configure the reusable `oracle` source instead of a company-specific scraper:

```yaml
source:
  type: oracle
  entry_url: https://jpmc.fa.oraclecloud.com/hcmUI/CandidateExperience/en/sites/CX_1001/jobs
  host: jpmc.fa.oraclecloud.com
  site_number: CX_1001
  locale: en
```

CLI example:

```bash
python -m job_fetcher.cli add-company \
  --name "Example Oracle Employer" \
  --career-url "https://example.fa.oraclecloud.com/hcmUI/CandidateExperience/en/sites/CX/jobs" \
  --source oracle \
  --host example.fa.oraclecloud.com \
  --site-number CX \
  --locale en \
  --entry-url "https://example.fa.oraclecloud.com/hcmUI/CandidateExperience/en/sites/CX/jobs"
```

The adapter first tries Oracle Candidate Experience's structured requisition feed. Because Oracle documents that CE endpoint as internal-use, tenants may reject direct anonymous API access; in that case the adapter automatically falls back to Playwright network/DOM discovery on the public careers page.


## Eightfold public careers source (Twilio)

Eightfold's documented `List Positions` API requires bearer-token authorization, so the fetcher does not depend on that authenticated API for public job discovery. The reusable `eightfold` source reads the public candidate careers experience instead, first from server-rendered HTML/JSON-LD/embedded state and then, when necessary, with bounded Playwright scrolling plus public JSON/XHR capture.

Twilio is configured with its branded careers URL as canonical and its Eightfold tenant as a fallback:

```yaml
source:
  type: eightfold
  entry_url: https://jobs.twilio.com/careers?domain=twilio.com&hl=en
  provider_url: https://twilio.eightfold.ai/careers?domain=twilio.com&hl=en
  tenant: twilio
  canonical_base_url: https://jobs.twilio.com
  locale: en-US
  browser_max_scrolls: 60
```

The adapter reads the page-declared job count when available and keeps scrolling until the unique `/careers/job/` links reach that count or remain stable for several rounds. Provider-domain job URLs are normalized back to the employer's branded career domain.

A future Eightfold employer can usually be added without Python changes:

```bash
python -m job_fetcher.cli add-company \
  --name "Example Employer" \
  --career-url "https://example.eightfold.ai/careers" \
  --source eightfold \
  --entry-url "https://example.eightfold.ai/careers"
```

If an employer uses a branded domain in front of Eightfold, add `tenant`, `provider_url`, or `canonical_base_url` through `--source-config-json`.

## Step 4: priority proprietary career sites

The project now has explicit handling for Meta, Apple, Microsoft and Amazon:

- `meta`: public Meta job-search pages, India-office pagination, browser/XHR fallback.
- `apple`: server-rendered Apple India search pagination and stable role-number URLs.
- `eightfold`: Microsoft now uses the existing Eightfold adapter on Microsoft's branded candidate domain; a configurable canonical job-path template is supported.
- `amazon`: Amazon.jobs public search/detail links plus bounded browser fallback.
- `manual`: a safety source for a company that should not be automatically scraped. Flipkart uses this source and is disabled by default because its published careers-site terms prohibit automated data extraction.

No adapter attempts login bypass, CAPTCHA bypass, or use of authenticated/private applicant APIs.


## Step 5: Oracle/OCI, IBM, CRED, Zerodha

- **Oracle Cloud (OCI)** uses the reusable `oracle` source in `public_search` mode. Its entry URL is Oracle's public Candidate Experience job search pre-filtered to India + OCI. This avoids depending on Oracle's internal-use CE REST resource for Oracle's own public career site.
- **IBM Software Labs** uses the reusable `avature` source. It understands public Avature-style `careers/JobDetail` URLs, embedded job data, and falls back to bounded browser/XHR extraction without bypassing authentication or CAPTCHA.
- **CRED** is routed directly through the existing public Lever adapter (`site: cred`); no CRED-specific scraper is needed.
- **Zerodha** remains on `auto`, because its official page currently reports zero openings. The generic empty-state detector has a deterministic test so a legitimate zero-job state is returned as success instead of forcing browser fallback.

Example Avature configuration:

```yaml
source:
  type: avature
  entry_url: https://www.example.com/careers/search
  canonical_base_url: https://careers.example.com
  locale: en_US
```

The Step 5 deterministic suite covers provider routing, Oracle public listing parsing, Avature JobDetail normalization, static fetch paths, and Zerodha's healthy empty state.

## Step 6: verified ATS mappings and public listing entry points

The verified batch now bypasses generic discovery where a reusable provider adapter already exists:

- Greenhouse: Stripe, Databricks, PhonePe, MongoDB, Rubrik, Groww.
- Workday: Salesforce, Walmart Global Tech, Adobe, PayPal, Cohesity.
- Eightfold: Morgan Stanley.
- SmartRecruiters: ServiceNow.

The generic source remains appropriate for the verified public listings below, but now starts from the more useful listing URL rather than the marketing careers homepage:

- Uber: `https://jobs.uber.com/en/jobs/`
- Intuit: India jobs listing.
- Meesho: engineering jobs listing.

This reduces browser fallback, improves pagination reliability, and keeps future provider-specific fixes centralized in the shared adapter rather than duplicated per employer.

## Verify every configured company

Run a complete health check without writing jobs into SQLite:

```bash
python -m job_fetcher.cli verify-all --workers 4
```

The verifier treats a source that returns zero jobs as a failure, checks record
quality, opens one sample job-detail URL per company, identifies browser-fallback
usage, and compares counts against the previous run. By default, a drop of more
than 80% is flagged as suspicious.

Reports are written to:

- `reports/company_health.json`
- `reports/company_health.csv`
- `reports/company_health_baseline.json` for the last good per-company counts
- `reports/history/company_health_<timestamp>.json` for prior run reports

Useful diagnostic modes:

```bash
# See which providers work without Playwright
python -m job_fetcher.cli verify-all --no-browser --no-fail-exit

# Faster run without checking one detail URL per company
python -m job_fetcher.cli verify-all --skip-detail-check --no-fail-exit
```

`verify-all` exits with code 2 when any company is failed or suspicious, making
it suitable for CI. Add `--no-fail-exit` when you only want the report.
