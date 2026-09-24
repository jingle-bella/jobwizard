#!/usr/bin/env python3
"""Daily job agent.

Pulls postings from public Greenhouse / Lever / Ashby job-board APIs (no login),
keeps new ones that match your filters, optionally scores fit with Claude,
and emails an HTML digest. State lives in seen_jobs.json so nothing repeats.
"""
from __future__ import annotations

import html
import json
import os
import re
import smtplib
import sys
from datetime import datetime, timedelta, timezone
from email.mime.text import MIMEText
from pathlib import Path

import requests
import yaml

ROOT = Path(__file__).parent
SEEN_PATH = ROOT / "seen_jobs.json"
TIMEOUT = 20
SEEN_RETENTION_DAYS = 90
HEADERS = {"User-Agent": "personal-job-digest/1.0"}


# ---------- helpers ----------

def strip_html(text: str) -> str:
    text = re.sub(r"<[^>]+>", " ", html.unescape(text or ""))
    return re.sub(r"\s+", " ", text).strip()


def parse_iso(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def get_json(url: str, params: dict | None = None):
    r = requests.get(url, params=params, headers=HEADERS, timeout=TIMEOUT)
    r.raise_for_status()
    return r.json()


# ---------- fetchers (each yields normalized job dicts) ----------

def fetch_greenhouse(slug: str):
    data = get_json(f"https://boards-api.greenhouse.io/v1/boards/{slug}/jobs",
                    {"content": "true"})
    for j in data.get("jobs", []):
        yield {
            "id": f"gh:{slug}:{j['id']}",
            "company": slug,
            "title": j.get("title", ""),
            "location": (j.get("location") or {}).get("name", ""),
            "url": j.get("absolute_url", ""),
            "posted": parse_iso(j.get("first_published") or j.get("updated_at")),
            "description": strip_html(j.get("content", "")),
        }


def fetch_lever(slug: str):
    data = get_json(f"https://api.lever.co/v0/postings/{slug}", {"mode": "json"})
    for j in data:
        created = j.get("createdAt")
        yield {
            "id": f"lv:{slug}:{j['id']}",
            "company": slug,
            "title": j.get("text", ""),
            "location": (j.get("categories") or {}).get("location", "") or "",
            "url": j.get("hostedUrl", ""),
            "posted": datetime.fromtimestamp(created / 1000, tz=timezone.utc) if created else None,
            "description": j.get("descriptionPlain") or strip_html(j.get("description", "")),
        }


def fetch_ashby(slug: str):
    data = get_json(f"https://api.ashbyhq.com/posting-api/job-board/{slug}")
    for j in data.get("jobs", []):
        if j.get("isListed") is False:
            continue
        loc = j.get("location", "") or ""
        if j.get("isRemote") and "remote" not in loc.lower():
            loc = f"{loc} (Remote)".strip()
        yield {
            "id": f"ab:{slug}:{j['id']}",
            "company": slug,
            "title": j.get("title", ""),
            "location": loc,
            "url": j.get("jobUrl", ""),
            "posted": parse_iso(j.get("publishedAt")),
            "description": j.get("descriptionPlain") or strip_html(j.get("descriptionHtml", "")),
        }


FETCHERS = {"greenhouse": fetch_greenhouse, "lever": fetch_lever, "ashby": fetch_ashby}


# ---------- filtering ----------

def matches(job: dict, f: dict) -> bool:
    title = job["title"].lower()
    loc = job["location"].lower()
    if not any(k.lower() in title for k in f.get("title_include", [])):
        return False
    if any(k.lower() in title for k in f.get("title_exclude", [])):
        return False
    locs = f.get("location_include", [])
    if locs and not any(k.lower() in loc for k in locs):
        return False
    return True


def load_seen() -> dict:
    if SEEN_PATH.exists():
        return json.loads(SEEN_PATH.read_text())
    return {}


def save_seen(seen: dict) -> None:
    cutoff = (datetime.now(timezone.utc) - timedelta(days=SEEN_RETENTION_DAYS)).isoformat()
    pruned = {k: v for k, v in seen.items() if v >= cutoff}
    SEEN_PATH.write_text(json.dumps(pruned, indent=0, sort_keys=True))


# ---------- optional LLM scoring ----------

def score_jobs(jobs: list[dict], cfg: dict) -> None:
    key = os.environ.get("ANTHROPIC_API_KEY")
    if not key:
        return
    for job in jobs[: cfg.get("max_jobs_to_score", 40)]:
        prompt = (
            f"Candidate profile:\n{cfg['profile']}\n\n"
            f"Job: {job['title']} at {job['company']} ({job['location']})\n"
            f"Description:\n{job['description'][:4000]}\n\n"
            'Rate fit from 0-10. Respond with ONLY JSON: {"score": <int>, "reason": "<one sentence>"}'
        )
        try:
            r = requests.post(
                "https://api.anthropic.com/v1/messages",
                headers={"x-api-key": key, "anthropic-version": "2023-06-01",
                         "content-type": "application/json"},
                json={"model": cfg["model"], "max_tokens": 150,
                      "messages": [{"role": "user", "content": prompt}]},
                timeout=60,
            )
            r.raise_for_status()
            text = "".join(b.get("text", "") for b in r.json()["content"])
            parsed = json.loads(re.search(r"\{.*\}", text, re.S).group(0))
            job["score"] = int(parsed.get("score", 0))
            job["reason"] = parsed.get("reason", "")
        except Exception as e:  # scoring is best-effort
            job["reason"] = f"(scoring failed: {e})"
    jobs.sort(key=lambda j: j.get("score", -1), reverse=True)


# ---------- digest ----------

def build_html(jobs: list[dict], errors: list[str]) -> str:
    rows = []
    for j in jobs:
        score = f"<b>{j['score']}/10</b><br>" if "score" in j else ""
        reason = html.escape(j.get("reason", ""))
        posted = j["posted"].strftime("%b %d %H:%M UTC") if j["posted"] else "—"
        rows.append(
            f"<tr><td>{score}<a href='{html.escape(j['url'])}'>{html.escape(j['title'])}</a>"
            f"<br><small>{reason}</small></td>"
            f"<td>{html.escape(j['company'])}</td><td>{html.escape(j['location'])}</td>"
            f"<td>{posted}</td></tr>"
        )
    table = ("<table border='1' cellpadding='6' style='border-collapse:collapse'>"
             "<tr><th>Role</th><th>Company</th><th>Location</th><th>Posted</th></tr>"
             + "".join(rows) + "</table>") if rows else "<p>No new matching jobs today.</p>"
    err = ("<h4>Fetch errors</h4><ul>" + "".join(f"<li>{html.escape(e)}</li>" for e in errors)
           + "</ul>") if errors else ""
    return f"<h2>{len(jobs)} new jobs</h2>{table}{err}"


def send_email(subject: str, body_html: str) -> bool:
    user = os.environ.get("GMAIL_ADDRESS")
    pwd = os.environ.get("GMAIL_APP_PASSWORD")
    if not (user and pwd):
        return False
    to = os.environ.get("DIGEST_TO"） or user
    msg = MIMEText(body_html, "html")
    msg["Subject"], msg["From"], msg["To"] = subject, user, to
    with smtplib.SMTP_SSL("smtp.gmail.com", 465) as s:
        s.login(user, pwd)
        s.send_message(msg)
    return True


# ---------- main ----------

def main() -> int:
    cfg = yaml.safe_load((ROOT / "config.yaml").read_text())
    now = datetime.now(timezone.utc)
    window_start = now - timedelta(hours=cfg.get("window_hours", 26))
    first_run = not SEEN_PATH.exists()
    seen = load_seen()

    new_jobs, errors = [], []
    for ats, slugs in (cfg.get("companies") or {}).items():
        fetch = FETCHERS.get(ats)
        if not fetch:
            errors.append(f"Unknown ATS '{ats}'")
            continue
        for slug in slugs or []:
            try:
                for job in fetch(slug):
                    if job["id"] in seen:
                        continue
                    seen[job["id"]] = now.isoformat()
                    fresh = job["posted"] >= window_start if job["posted"] else not first_run
                    if fresh and matches(job, cfg.get("filters", {})):
                        new_jobs.append(job)
            except Exception as e:
                errors.append(f"{ats}/{slug}: {e}")

    new_jobs.sort(key=lambda j: j["posted"] or now, reverse=True)
    score_jobs(new_jobs, cfg.get("scoring", {}))

    body = build_html(new_jobs, errors)
    subject = f"Job digest {now:%Y-%m-%d}: {len(new_jobs)} new"
    if not send_email(subject, body):
        print("Email not configured; printing digest instead.\n")
        for j in new_jobs:
            print(f"- [{j.get('score', '-')}] {j['title']} | {j['company']} | {j['location']} | {j['url']}")
        for e in errors:
            print(f"! {e}")

    save_seen(seen)
    print(f"Done: {len(new_jobs)} new, {len(errors)} errors, {len(seen)} tracked.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
