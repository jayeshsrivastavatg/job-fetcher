# Step 4 - Meta, Apple, Microsoft, Amazon, Flipkart

## Automated adapters added / routed

- Meta -> `MetaSource`
  - public `metacareers.com/jobsearch/`
  - office-scoped pagination for India offices
  - public `/profile/job_details/<id>/` job URLs
  - bounded Playwright fallback with public JSON/XHR capture
- Apple -> `AppleSource`
  - public India search URL
  - deterministic `?page=N` pagination
  - `/details/<role-number>/<slug>` parsing
  - generic browser fallback only if static public pages stop exposing cards
- Microsoft -> existing `EightfoldSource`
  - official candidate experience at `apply.careers.microsoft.com`
  - branded Eightfold public candidate surface
  - configurable canonical job path template for Microsoft careerhub IDs
  - no authenticated Eightfold Positions API dependency
- Amazon -> `AmazonSource`
  - public Amazon.jobs India search
  - public `/jobs/<id>/<slug>` records
  - server-rendered next-page parsing when available
  - bounded browser pagination + public JSON capture fallback

## Flipkart

Flipkart remains in the 100-company config but is disabled by default. Its published Careers Terms of Use prohibit robots, data-mining and similar automated extraction. The project therefore routes it to `ManualSource` instead of trying to bypass the site's rules.

To automate Flipkart later, use an official/company-approved API or feed and configure it through `custom_api`, or import data through a separately approved source.

## Build status

- companies configured: 100
- enabled: 97
- disabled: OYO, MPL, Flipkart
- config errors: 0
- deterministic tests: 39 passed
