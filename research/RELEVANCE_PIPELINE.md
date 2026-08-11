# Deterministic Relevance Pipeline

This repository is intentionally limited to job discovery, JD storage, deterministic relevance filtering, and export. Resume tailoring, ATS scoring, and AI analysis belong to a separate downstream application.

## Inputs

- Normalized active jobs from SQLite.
- `config/profile.example.json` (or private `data/profile.json`).
- Job title, location, full description, posted/first-seen metadata.

## Change detection

Each job stores a content hash of title + location + description and is classified as `new`, `changed`, `unchanged`, or `baseline`. Incremental relevance analysis only recomputes new or changed content.

## Experience policy

The default profile assumes roughly five years of experience:

- 2–5 year requirements and ranges containing 5 years score strongly.
- 6+ years is a stretch, not an automatic rejection.
- 7+ years is weak but can remain visible.
- 8+ years is filtered by default.
- Missing experience text is not rejected.

## Target role families

1. Java Backend
2. Java + React Full Stack
3. Node.js Backend
4. Node.js + React Full Stack
5. General Software Engineering fallback

## Relevance score

```text
Role / stack relevance       40
Experience compatibility     20
Primary skill match          25
Supporting engineering fit   15
```

Default bands:

- 80–100: high
- 65–79: good
- 50–64: possible / relevant
- 35–49: low
- below 35: filtered

Hard exclusions can override the numeric score for clearly out-of-scope titles, explicit foreign-only locations, or seniority beyond the configured threshold.

## Near-duplicate handling

Exact identity remains `company_id + external_id`. Relevance analysis additionally groups jobs by company + normalized title + normalized location and compares JD similarity. Near duplicates remain stored for traceability but are marked `duplicate`; the highest-scoring/earliest-seen row is canonical.

## Outputs

The daily command:

```bash
python -m job_fetcher.cli scan --workers 4
```

writes:

```text
reports/daily/daily_fetch.json
reports/daily/relevant_jobs.csv
reports/daily/relevant_jobs.json
```

The JSON/CSV exports include the normalized job metadata, relevance score/breakdown, matched keywords, change type, direct job URL, and stored job description. These files are suitable as input to a separate downstream application.
