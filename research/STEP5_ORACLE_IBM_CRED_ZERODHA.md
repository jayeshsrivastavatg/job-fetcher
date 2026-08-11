# Step 5 — Oracle/OCI, IBM, CRED, Zerodha

Research date: 2026-08-11.

## Oracle Cloud Infrastructure

Oracle's public OCI careers landing page links into Oracle Candidate Experience search. The configured fetch entry is the public `careers.oracle.com/en/sites/jobsearch/jobs` surface filtered to India and the OCI flex-field. `OracleSource` now has `public_search` mode: static public job links first, conservative pagination second, bounded browser/XHR fallback last. This path intentionally does not require Oracle's internal-use recruitingCE API.

## IBM Software Labs

IBM's official careers search is `www.ibm.com/in-en/careers/search`. Public IBM job detail pages resolve under `careers.ibm.com/.../careers/JobDetail/...`; IBM also has Avature-hosted candidate pages under `ibmglobal.avature.net`. A reusable `AvatureSource` was added. It parses stable JobDetail links and embedded data, can synthesize canonical IBM JobDetail URLs from numeric job IDs captured in public browser responses, and respects browser-disable / anti-bot behavior.

## CRED

CRED's official branded careers page is backed by a current public Lever board at `jobs.lever.co/cred`. Configuration now uses the existing `LeverSource` with `site: cred`, eliminating unnecessary proprietary-page scraping.

## Zerodha

The official careers page currently says there are no job openings. Zerodha stays on `auto`; the existing empty-state detector correctly recognizes this message and returns an empty list as a healthy result. A fetch-level test verifies that Playwright is not launched for this legitimate zero-opening state.

## Deterministic verification

- Config: 100 companies, 97 enabled, 0 errors.
- Tests after this step: 49 passing.
- Live HTTP remains unverified from the packaging sandbox because outbound DNS is unavailable there.
