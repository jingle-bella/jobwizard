# Daily Job Agent

Pulls new postings from public Greenhouse / Lever / Ashby job boards (no logins, no scraping),
filters by title and location, optionally scores fit with Claude, and emails a daily digest.

## Setup (about 10 minutes)

1. Create a **private** GitHub repo and push these files.
2. Gmail: turn on 2-Step Verification, then create an App Password
   (Google Account > Security > App passwords).
3. In the repo: Settings > Secrets and variables > Actions, add:
   - `GMAIL_ADDRESS`: your Gmail address
   - `GMAIL_APP_PASSWORD`: the 16-character app password
   - `DIGEST_TO` (optional): where to send the digest; defaults to GMAIL_ADDRESS
   - `ANTHROPIC_API_KEY` (optional): enables fit scoring with Claude Haiku
4. Actions tab > daily-job-digest > Run workflow, to test it once.

The first run only records existing jobs and reports ones posted in the last `window_hours`,
so you won't get flooded.

## Local run

    pip install -r requirements.txt
    python job_agent.py        # prints the digest if Gmail secrets aren't set

## Customizing

Edit `config.yaml`: add companies (verify the slug by opening the board URL),
adjust title/location keywords, and edit the profile used for scoring.

## Notes

- The daily commit of `seen_jobs.json` also keeps the repo active, so GitHub doesn't
  auto-disable the scheduled workflow after 60 days of inactivity.
- Cost: Actions minutes are negligible; Claude Haiku scoring of ~40 jobs/day costs cents.
