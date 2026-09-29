# CLAUDE.md — jobwizard (daily job digest agent)

## What this is
A personal automation that emails a daily digest of newly posted AI/ML job openings.
It runs on GitHub Actions (cron), pulls postings from **public** ATS job-board APIs
(Greenhouse, Lever, Ashby — no login, no scraping), filters by title/location keywords,
optionally scores fit with Claude, and sends an HTML email via Gmail SMTP.

Repo: `jingle-bella/jobwizard` (private). The project lives at the repo root.

Hard constraint: **never** add LinkedIn (or any logged-in site) scraping or automated
logins. It violates ToS and risks the owner's account during an active job search.
New sources must be public APIs/feeds.

## Files
- `job_agent.py` — the whole pipeline (~250 lines, single file, stdlib + requests + PyYAML).
- `config.yaml` — `window_hours`, company board slugs per ATS, filter keywords, scoring model/profile.
- `.github/workflows/daily.yml` — the workflow (the only one; GitHub reads only this directory).
  Cron `0 9 * * *` (UTC) + `workflow_dispatch`; `permissions: contents: write`; `actions/checkout@v7`,
  `actions/setup-python@v7` (Node 24); runs the script on `ubuntu-latest` / Python 3.12, then commits
  `seen_jobs.json` back to `main` as `job-agent-bot` (message has `[skip ci]`; commits only if changed;
  `git pull --rebase` before push so a concurrent push to `main` doesn't fail the run).
- `seen_jobs.json` — dedupe state (`job_id -> first_seen ISO timestamp`), pruned after 90 days
  (`SEEN_RETENTION_DAYS`). Written by CI; do not hand-edit.
- `requirements.txt` (`requests`, `PyYAML`), `README.md` (human setup guide).

## Pipeline (job_agent.py)
1. `main()` loads `config.yaml` and `seen_jobs.json`. `first_run = not seen_jobs.json exists`.
   `window_start = now - window_hours` (default 26h; the 2h overlap covers late cron runs).
2. For each `companies.<ats>: [slugs]`, call the fetcher in `FETCHERS` (unknown ATS → fetch error).
   All HTTP goes through `get_json()` (20s timeout, custom User-Agent, `raise_for_status`).
   - Greenhouse: `GET https://boards-api.greenhouse.io/v1/boards/{slug}/jobs?content=true`
     → title `title`, location `location.name`, url `absolute_url`, posted `first_published`
     (fallback `updated_at`), description `content` (HTML-escaped HTML → `strip_html`).
   - Lever: `GET https://api.lever.co/v0/postings/{slug}?mode=json` (top-level list)
     → title `text`, location `categories.location`, url `hostedUrl`, posted `createdAt` (ms epoch),
     description `descriptionPlain`.
   - Ashby: `GET https://api.ashbyhq.com/posting-api/job-board/{slug}` → skips `isListed == false`;
     location `location` (+ " (Remote)" if `isRemote`), url `jobUrl`, posted `publishedAt`,
     description `descriptionPlain`.
   Each fetcher yields a normalized dict:
   `{id, company, title, location, url, posted: datetime|None, description}`;
   ids are `gh:/lv:/ab:<slug>:<ats id>`; `company` is the slug, not a display name.
3. A job is included if: not in seen AND (posted ≥ `window_start`, or posted is None and not first run)
   AND `matches()` (title_include any / title_exclude none / location_include any if non-empty;
   case-insensitive substring match). Every fetched job is marked seen regardless of match.
4. Per-slug exceptions are caught and listed under "Fetch errors" in the digest (a bad slug never
   crashes the run). An empty board (HTTP 200, no jobs) is **not** an error — it's silent.
5. Jobs are sorted newest first, then `score_jobs()` runs — only if `ANTHROPIC_API_KEY` is set.
   Raw HTTP call to `/v1/messages` (`anthropic-version: 2023-06-01`, model from `scoring.model`,
   `max_tokens` 150), prompt = `scoring.profile` + job + first 4000 chars of description, asks for
   `{"score","reason"}` JSON (extracted by regex). Caps at `max_jobs_to_score` (default 40);
   best-effort (failures go into `reason`); re-sorts by score, unscored last.
6. `build_html()` → heading + table (Role/Company/Location/Posted, with score/reason if present) +
   "Fetch errors" list. Subject: `Job digest YYYY-MM-DD: N new`. An email is sent every day, even with 0 jobs.
7. `send_email()` — Gmail SMTP_SSL :465, recipient `DIGEST_TO or GMAIL_ADDRESS`. If Gmail env vars
   are missing, prints a plain-text digest to stdout instead.
8. `save_seen()` runs last. If anything before it raises (e.g. SMTP failure), seen state isn't saved
   for that run, so the next run re-evaluates the same jobs.

## Environment / secrets (GitHub Actions repo secrets)
- `GMAIL_ADDRESS`, `GMAIL_APP_PASSWORD` (Google app password, not the account password) — set.
- `ANTHROPIC_API_KEY` — optional; enables scoring.
- `DIGEST_TO` — optional, currently not set. GitHub passes unset secrets as **empty strings**,
  so always read optional env vars as `os.environ.get("X") or default`, never `get("X", default)`.

## Status / history
- Run #1 failed: `SMTPRecipientsRefused {'': ...}` because empty `DIGEST_TO` was used as recipient.
  Fixed with `to = os.environ.get("DIGEST_TO") or user`.
- First successful run 2026-09-24 03:51 UTC seeded ~3,000 jobs. CI has committed `seen_jobs.json`
  daily since then (checked 2026-09-28), so the workflow is running green. Email delivery itself
  hasn't been confirmed from here.
- Live API check (2026-09-28): field names used by all three fetchers exist in real responses
  (sampled `anthropic` GH, `openai` Ashby, `palantir` Lever). Slugs returning jobs:
  greenhouse `anthropic`, `databricks`, `scaleai`; lever `palantir`; ashby `openai`, `cohere`.

## Open items / known gaps
- **`lever/mistral` returns `[]`** (HTTP 200, so no fetch error). Mistral isn't at `mistral` on
  Ashby or Greenhouse either (404). Find its real board or remove it from `config.yaml`.
- Only the primary location is filtered: Greenhouse multi-location strings are one `name`, but
  Lever `categories.allLocations` and Ashby `secondaryLocations` are ignored, so a US-eligible
  job whose primary location is abroad gets dropped.
- Lever `descriptionPlain` excludes the `lists` (requirements) section, so the scorer sees
  only part of Lever job descriptions.
- A job still listed after 90 days is pruned from seen and re-fetched as new; it's only re-sent if
  `posted` is None (otherwise it falls outside the window).
- `ubuntu-latest` migrates to Ubuntu 26 from Oct 19, 2026 — probably harmless; pin `ubuntu-24.04` if anything breaks.
- No tests yet. Good first additions: unit tests for `matches()`, the inclusion rule in step 3,
  and each fetcher's normalization using recorded JSON fixtures.
- Possible enhancements (only if the owner asks): more companies / ATS sources, Slack delivery,
  salary extraction, caching fetched descriptions.

## Working conventions
- CI commits to `main` daily, so the local clone falls behind quickly.
  **Always `git pull --rebase` before editing or pushing.**
- Keep it a small, dependency-light single script unless a refactor is requested.
- Test locally without secrets: `pip install -r requirements.txt && python job_agent.py`
  (prints the digest; note this updates the local `seen_jobs.json` — don't commit it;
  `git checkout seen_jobs.json` afterwards).
- To check a slug: `curl` the fetcher URL from step 2 and confirm it returns a non-empty job list.
- Never print or commit secrets. Keep the repo private (config + seen jobs reveal the job search).
