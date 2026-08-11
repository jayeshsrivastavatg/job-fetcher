# Step 2: Eightfold + Twilio

## Why a dedicated adapter

Eightfold documents a core Positions API, but its List Positions endpoint requires bearer-token authorization. The job fetcher therefore does not depend on that API for public careers scraping.

Twilio's public careers experience is available at `https://jobs.twilio.com/careers?domain=twilio.com&hl=en`, with a provider-domain mirror at `https://twilio.eightfold.ai/careers?domain=twilio.com&hl=en`. Twilio job details use the `/careers/job/<position-id>` route.

## Fetch strategy

1. Fetch the public branded careers page with HTTP.
2. Extract JobPosting JSON-LD, embedded JSON, Eightfold position objects, and job links.
3. Read the page-declared total job count when available.
4. If the static result is incomplete, launch bounded Playwright.
5. Capture JSON/XHR responses and repeatedly scroll/click load-more controls.
6. Stop when unique `/careers/job/` links reach the declared count or remain stable.
7. Normalize provider-domain URLs back to the employer's branded careers domain.
8. Use `twilio.eightfold.ai` only as a fallback when the branded page cannot be read.

## Twilio config

```yaml
source:
  type: eightfold
  entry_url: https://jobs.twilio.com/careers?domain=twilio.com&hl=en
  provider_url: https://twilio.eightfold.ai/careers?domain=twilio.com&hl=en
  tenant: twilio
  canonical_base_url: https://jobs.twilio.com
  locale: en-US
  browser_max_scrolls: 60
  browser_stable_scrolls: 5
```

## Validation

- Full deterministic suite: 25 passed.
- Company config: 100 configured, 98 enabled, 0 errors.
- Twilio resolves to `EightfoldSource`.
- Local sandbox network probe remains blocked at DNS before either Twilio host can be reached. This is an environment limitation, not a parser failure.
