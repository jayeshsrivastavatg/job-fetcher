# Step 7 — Dynamic / policy-sensitive company hardening

Updated 2026-08-11.

## Atlassian

- Explicit `AtlassianSource`.
- Stable public job detail format: `/company/careers/details/<id>`.
- All-jobs listing may be client-rendered; browser fallback captures XHR and traverses Next pages.

## Rippling

- Uses the public `/careers/open-roles` listing.
- `AutoSource` now treats HTTP 403/429 as escalation signals to browser fallback instead of terminal failures.
- Browser path uses XHR capture, scrolling, load-more, and bounded Next pagination.

## LinkedIn

- Disabled by default and routed to `ManualSource`.
- Reason: LinkedIn's User Agreement prohibits automated scraping; approved crawling/partner access is required for automation.
- The documented job-posting APIs are partner-authorized and are not a public anonymous job-read API.

## Snowflake

- Explicit `PhenomSource`.
- Snowflake career pages load Phenom assets and search results are client-rendered.
- Source prefers public structured content, then browser XHR + bounded Next pagination.

## Swiggy

- Entry URL changed to the engineering jobs listing: `https://careers.swiggy.com/list.html?dept=Engineering&loc=1`.
- Static HTML contains client-side templates, so browser XHR capture is the normal completeness path.
- Successful local probes expose `discovered_endpoints` so the actual public jobs API can be identified automatically.

## Dream11

- Current public vacancies source: `https://dream11.hire.trakstar.com/`.
- Added `TrakstarSource` using stable `/jobs/<id>/` detail URLs.

## Goldman Sachs

- Explicit `GoldmanSource` against `https://higher.gs.com/results`.
- Stable public detail format: `https://higher.gs.com/roles/<id>`.
- Dynamic results use browser/XHR and bounded Next pagination when static links are unavailable.

## Urban Company

- Public board is JS-rendered; detail pages use `jobDetail?id=<uuid>`.
- Generic job-link detection now understands `/jobDetail`.
- Browser XHR capture plus discovered endpoint reporting is used for the listings.

## Generic browser hardening added in this step

- Configurable `browser_max_pages`.
- Configurable per-page scroll cap/stability target.
- Repeated Load More handling.
- Next-page navigation for links/buttons, including SPA pagination that keeps the same URL.
- Public JSON/XHR endpoint provenance stored on jobs and surfaced by `probe-company` / fetch reports.
