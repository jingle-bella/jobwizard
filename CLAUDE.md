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
- `job_agent.py` — the whole pipeline (single file, stdlib + requests + PyYAML).
- `config.yaml` — window size, company board slugs per ATS, filter keywords, scoring profile.
- `.github/workflows/daily.yml` — cron `0 11 * * *` (UTC) + `workflow_dispatch`; runs the
  script, then commits `seen_jobs.json` back to `main` as `job-agent-bot`.
- `seen_jobs.json` — dedupe state (`job_id -> first_seen ISO timestamp`), pruned after 90 days.
  Created on first successful run. Written by CI; do not hand-edit.
- `requirements.txt`, `README.md` (human setup guide).

## Pipeline (job_agent.py)
1. Load config and `seen_jobs.json`. `first_run = not seen_jobs.json exists`.
2. For each `companies.<ats>.<slug>`, call the fetcher in `FETCHERS`:
   - Greenhouse: `GET https://boards-api.greenhouse.io/v1/boards/{slug}/jobs?content=true`
     → posted date from `first_published`, fallback `updated_at`.
   - Lever: `GET https://api.lever.co/v0/postings/{slug}?mode=json` → `createdAt` (ms epoch).
   - Ashby: `GET https://api.ashbyhq.com/posting-api/job-board/{slug}` → `publishedAt`; skips `isListed == false`.
   Each fetcher yields a normalized dict:
   `{id, company, title, location, url, posted: datetime|None, description}`; ids are `gh:/lv:/ab:<slug>:<id>`.
3. A job is included if: not in seen AND (posted within `window_hours`, or posted is None and not first run)
   AND `matches()` (title_include any / title_exclude none / location_include any, case-insensitive).
   Every fetched job is marked seen regardless of match.
4. Per-company exceptions are caught and listed under "Fetch errors" in the digest (a bad slug never crashes the run).
5. `score_jobs()` — only if `ANTHROPIC_API_KEY` is set. Raw HTTP call to `/v1/messages`
   (model from config, default `claude-haiku-4-5-20251001`), asks for `{"score","reason"}` JSON,
   caps at `max_jobs_to_score`, best-effort (failures recorded in `reason`), sorts by score.
6. `send_email()` — Gmail SMTP_SSL :465. If Gmail env vars are missing, prints the digest to stdout instead.
7. `save_seen()` runs last. Note: if anything before it raises, seen state is not saved for that run.

## Environment / secrets (GitHub Actions repo secrets)
- `GMAIL_ADDRESS`, `GMAIL_APP_PASSWORD` (Google app password, not the account password) — set.
- `ANTHROPIC_API_KEY` — optional; enables scoring.
- `DIGEST_TO` — optional, currently not set. GitHub passes unset secrets as **empty strings**,
  so always read optional env vars as `os.environ.get("X") or default`, never `get("X", default)`.

## History
- Run #1 failed: `SMTPRecipientsRefused {'': ...}` because empty `DIGEST_TO` was used as recipient.
  Fixed with `to = os.environ.get("DIGEST_TO") or user`. Verify the latest Actions run is green
  and that a digest email + a `seen_jobs.json` commit were produced.

## Open items / known gaps
- **ATS response fields and company slugs were never verified against live APIs** (the original
  author's sandbox had no network access to them). Check each slug in `config.yaml` and each
  fetcher's field names against real responses; watch the digest's "Fetch errors".
- GitHub warns that Node.js 20 actions are deprecated: bump `actions/checkout` and
  `actions/setup-python` to their current Node 24 majors.
- `ubuntu-latest` migrates to Ubuntu 26 from Oct 19, 2026 — probably harmless; pin `ubuntu-24.04` if anything breaks.
- No tests yet. Good first additions: unit tests for `matches()`, the inclusion rule in step 3,
  and each fetcher's normalization using recorded JSON fixtures.
- Possible enhancements (only if the owner asks): more companies / ATS sources, Slack delivery,
  salary extraction, caching fetched descriptions.

## Working conventions
- CI commits to `main` daily. **Always `git pull --rebase` before pushing** local changes.
- Keep it a small, dependency-light single script unless a refactor is requested.
- Test locally without secrets: `pip install -r requirements.txt && python job_agent.py`
  (prints the digest; note this creates/updates a local `seen_jobs.json` — don't commit it).
- Never print or commit secrets. Keep the repo private (config + seen jobs reveal the job search).
