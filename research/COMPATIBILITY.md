# Compatibility and validation report

## Build status

- Configured companies: **100**
- Enabled companies: **97**
- Disabled companies: **3** (OYO, MPL, and Flipkart)
- Config validation errors: **0**
- Deterministic automated tests: **39 passed**
- SQLite job rows in packaged starter DB: **0**

## Live validation limitation

The packaging sandbox cannot resolve public DNS from Python/container processes. The network preflight fails before any careers site is reached. Therefore, the build does **not** classify the 97 enabled companies as scraper failures. It records them as `network_dns_environment` / `not_reached`. Run `probe-company` or `fetch` on a normal internet-connected machine to obtain real site-specific outcomes.

## Website coverage strategy

- Native adapters: Greenhouse, Lever, Ashby, SmartRecruiters, Workday, Oracle Candidate Experience, Eightfold public careers, SuccessFactors/jobs2web, Kula, Meta public search, Apple public search, Amazon public search.
- Static fallback: schema.org JobPosting JSON-LD, embedded application JSON / Next.js state, job links/cards, same-host pagination.
- Discovery: ATS URLs from visible links and embedded JavaScript plus high-confidence “all jobs/search jobs” links.
- Dynamic fallback: bounded Playwright with network JSON capture, iframe extraction, lazy scrolling and load-more handling.
- Escape hatch: `custom_api`, `custom_html`, or configured `playwright` selectors without changing Python code.

## Known provider families among the enabled targets

- `auto`: 65
- `proprietary`: 10
- `greenhouse`: 4
- `oracle`: 3
- `workday`: 3
- `smartrecruiters`: 2
- `workday-link`: 2
- `eightfold`: 1
- `kula`: 1
- `kula-link`: 1
- `lever`: 1
- `openings.co`: 1
- `oracle-link`: 1
- `smartrecruiters-link`: 1
- `successfactors/jobs2web`: 1
- `zwayam`: 1

## If a local probe fails

Use the failure category to choose the fix:

| Category | Meaning | Recommended improvement |
|---|---|---|
| `network_dns` | Host cannot be resolved | Network/DNS/proxy fix; not a parser problem |
| `network_timeout` | Site or endpoint timed out | Longer timeout, retries/backoff, provider API adapter |
| `http_403_or_waf` | WAF/bot protection | Prefer an official public ATS/API; browser fallback only where permitted |
| `rate_limited` | HTTP 429 | Lower concurrency, exponential backoff, cache/poll less often |
| `anti_bot_or_captcha` | Human/bot challenge | Do not try to bypass CAPTCHA; use public provider endpoints or manual/configured source |
| `needs_browser_or_provider_adapter` | Static path insufficient | Enable Playwright or add a reusable provider adapter |
| `unsupported_or_empty_layout` | Browser/static extraction found no jobs | Verify the URL, inspect captured JSON, add provider adapter/custom config |
| `browser_runtime_missing` | Chromium not installed | `playwright install chromium` |
| `fetch_or_parse_error` | Unclassified failure | Inspect `probe-company` error and raw site shape, then add the smallest reusable adapter |

## Disabled companies

- **OYO** — retained in config but disabled because the official site routes Careers/Teams externally and no first-party public jobs portal was verified during research.
- **MPL (Mobile Premier League)** — retained in config but disabled because no first-party public careers/jobs portal was verified during research.
- **Flipkart** — retained in config but disabled because its Careers Terms of Use prohibit robots/data-mining/automated extraction; use an approved feed/API or manual import.

## Company management

```bash
python -m job_fetcher.cli add-company --name "New Company" --career-url "https://company.example/careers"
python -m job_fetcher.cli disable-company new_company
python -m job_fetcher.cli enable-company new_company
python -m job_fetcher.cli update-company new_company --career-url "https://company.example/jobs"
python -m job_fetcher.cli probe-company new_company
```


### Twilio / Eightfold

Twilio is now explicitly routed through the reusable `eightfold` source. The official branded entry URL remains `https://jobs.twilio.com/careers?domain=twilio.com&hl=en`; `https://twilio.eightfold.ai/careers?domain=twilio.com&hl=en` is configured only as a provider-domain fallback. The authenticated Eightfold core Positions API is not used. Static public career data is preferred; a bounded browser/XHR capture path is used when the page is incomplete.
